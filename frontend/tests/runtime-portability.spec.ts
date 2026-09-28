import { test, expect } from '@playwright/test';
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

test('an explicit runtime resolution restores availability without rebuilding or changing evidence', async ({ page, request }, info) => {
  const base = process.env.COMMISSIONING_TEST_URL;
  const directory = process.env.RUNTIME_TEST_DIRECTORY;
  test.skip(!base || !directory, 'Requires isolated supplied-code services and their RUNTIME_TEST_DIRECTORY.');
  test.setTimeout(90000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const suffix = Date.now();
  const manifest = { id: `runtime_quadratic_${suffix}`, name: `Portable quadratic ${suffix}`,
    candidate_schema: { representation: 'continuous', dimensions: 2, bounds: [[-3, 3], [-3, 3]] },
    primary_objective: { name: 'energy', direction: 'minimize', units: 'arbitrary energy units' },
    configuration_schema: { type: 'object', additionalProperties: false,
      properties: { center: { type: 'number' }, offset: { type: 'number' } } },
    configuration: { center: 1.5, offset: -2 },
    fidelity_schema: { type: 'object', additionalProperties: false, properties: { digits: { type: 'integer' } } },
    fidelity: { digits: 8 } };
  const created = await request.post(`${base}/api/campaigns`, { data: {
    name: `Portable runtime ${suffix}`, compute_budget_seconds: 60, validation_reserve_seconds: 0,
    implementation_compute_budget_seconds: 60,
    tasks: [{ name: 'Independent quadratic', problem_id: manifest.id, evaluator_manifest: manifest }],
  } });
  expect(created.ok(), await created.text()).toBe(true);
  const campaign = await created.json();
  const state = async () => (await request.get(`${base}/api/v1/state?campaign_id=${campaign.id}`)).json();
  const command = async (operation: string, payload: any) => {
    const current = await state();
    const response = await request.post(`${base}/api/v1/commands`, { data: {
      id: `runtime_${randomUUID()}`, campaign_id: campaign.id, expected_revision: current.campaign.version, operation, payload } });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  const task = (await state()).tasks[0];
  const name = `Runtime evaluator ${suffix}`;
  await command('evaluator.commission', { task_id: task.id, compute_seconds: 30, max_calls: 1,
    spec: { kind: 'evaluator', name, manifest,
      mechanism: 'Sum squared distance to center plus offset, rounded to digits.',
      acceptance_criteria: ['Return raw energy', 'Restore evaluator state'],
      correctness_cases: [
        { name: 'origin', candidate: [0, 0], objectives: { energy: 2.5 }, basis: 'Analytical sum of squares' },
        { name: 'minimum', candidate: [1.5, 1.5], objectives: { energy: -2 }, basis: 'Analytical minimum' },
      ] },
    package: { kind: 'evaluator', contract: 'evaluator_v1', entrypoint: 'evaluator:create_evaluator',
      files: [{ path: 'evaluator.py', content: readFileSync('../examples/implementation-reference/continuous_evaluator.py', 'utf8') }] },
  });
  await expect.poll(async () => (await state()).tasks[0].evaluator_readiness?.runnable, { timeout: 30000 }).toBe(true);
  const before = await state();
  const version = before.implementation_library.versions.find((row: any) => row.name === name);
  expect(version).toBeTruthy();
  expect(version.artifact_digest).toMatch(/^[a-f0-9]{64}$/);
  const artifactDirectory = join(resolve(directory!), 'library', 'artifacts', version.artifact_digest);
  const artifactBytes = readFileSync(join(artifactDirectory, 'artifact.json'), 'utf8');
  const artifact = JSON.parse(artifactBytes);
  expect(artifact.schema_version).toBe(2);
  expect(artifact.runtime_root).toBeUndefined();
  // Remove only this version's local binding. Other fixture versions/runtimes
  // remain available while the browser exercises the explicit recovery action.
  unlinkSync(join(artifactDirectory, 'runtime-location.json'));
  await page.goto(`${base}/#implementations`);
  await page.getByLabel('Active campaign').selectOption(campaign.id);
  const entry = page.locator('article').filter({ has: page.getByRole('heading', { name, exact: true }) })
    .filter({ has: page.getByRole('button', { name: 'Check local runtime', exact: true }) });
  await entry.getByRole('button', { name: 'Check local runtime', exact: true }).click();
  await expect(entry.getByText('Runtime unavailable', { exact: true })).toBeVisible();
  const acceptedRequest = page.waitForRequest(r => r.method() === 'POST' && r.url().endsWith('/api/v1/commands')
    && r.postDataJSON()?.operation === 'implementation.resolve_runtime');
  await entry.getByRole('button', { name: 'Resolve compatible runtime', exact: true }).click();
  const envelope = (await acceptedRequest).postDataJSON();
  await expect(entry.getByText('Runtime available', { exact: true })).toBeVisible();
  await entry.getByText('Runtime resolution receipts', { exact: true }).click();
  const runtime = await (await request.get(`${base}/api/v1/implementations/${version.id}/runtime?campaign_id=${campaign.id}`)).json();
  expect(runtime.receipts).toHaveLength(1);
  expect(runtime.receipts[0].costs.model_calls).toBe(0);
  expect(runtime.receipts[0].costs.downloaded_bytes).toBe(0);
  expect(runtime.operations[0].status).toBe('completed');
  const replay = await request.post(`${base}/api/v1/commands`, { data: envelope });
  expect(replay.ok(), await replay.text()).toBe(true);
  const after = await state();
  expect(after.implementation_library.versions.find((row: any) => row.id === version.id)).toEqual(version);
  expect(after.implementation_jobs).toHaveLength(before.implementation_jobs.length);
  expect(readFileSync(join(artifactDirectory, 'artifact.json'), 'utf8')).toBe(artifactBytes);
  const experiment = await command('trial.create', { task_id: task.id, algorithm: 'coordinate', max_steps: 3, wall_seconds: 10 });
  await expect.poll(async () => (await state()).trials.find((row: any) => row.id === experiment.outcome.trial_id)?.status,
    { timeout: 30000 }).toBe('completed');
  const evidence = { campaign_id: campaign.id, version_id: version.id, artifact_digest: version.artifact_digest,
    runtime_digest: version.runtime_digest, validation_report_id: version.validation_report.id,
    command: envelope, runtime, trial_id: experiment.outcome.trial_id,
    limitations: 'Supplied source and mocked semantic review; real HTTP, sandbox execution, runtime resolution and command replay.' };
  writeFileSync(info.outputPath('runtime-resolution-evidence.json'), JSON.stringify(evidence, null, 2));
  await entry.screenshot({ path: info.outputPath('runtime-resolution.png') });
  expect(errors).toEqual([]);
});
