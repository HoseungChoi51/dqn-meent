import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

test('independent revalidation preserves prior authorization and executable reuse records its scope', async ({ page, request }, info) => {
  const base = process.env.COMMISSIONING_TEST_URL;
  test.skip(!base, 'Set COMMISSIONING_TEST_URL to the isolated supplied-code qualification workspace.');
  test.setTimeout(120000);
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  const suffix = Date.now();
  const manifest = { id: `revalidation_quadratic_${suffix}`, name: `Revalidation quadratic ${suffix}`,
    candidate_schema: { representation: 'continuous', dimensions: 2, bounds: [[-3, 3], [-3, 3]] },
    primary_objective: { name: 'energy', direction: 'minimize', units: 'arbitrary energy units' },
    configuration_schema: { type: 'object', additionalProperties: false,
      properties: { center: { type: 'number' }, offset: { type: 'number' } } },
    configuration: { center: 1.5, offset: -2 },
    fidelity_schema: { type: 'object', additionalProperties: false, properties: { digits: { type: 'integer' } } },
    fidelity: { digits: 8 } };
  const create = async (name: string, tasks: any[]) => {
    const response = await request.post(`${base}/api/campaigns`, { data: { name, tasks,
      compute_budget_seconds: 90, validation_reserve_seconds: 0, implementation_compute_budget_seconds: 90 } });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  const campaign = await create(`Standalone revalidation ${suffix}`, [
    { name: 'Declared quadratic', problem_id: manifest.id, evaluator_manifest: manifest }]);
  const state = async (id = campaign.id) => (await request.get(`${base}/api/v1/state?campaign_id=${id}`)).json();
  const validations = async () => (await request.get(`${base}/api/v1/campaigns/${campaign.id}/validations`)).json();
  const command = async (operation: string, payload: any, campaignId = campaign.id) => {
    const current = await state(campaignId);
    const body = { id: `qualification_${randomUUID()}`, campaign_id: campaignId,
      expected_revision: current.campaign.version, operation, payload };
    const response = await request.post(`${base}/api/v1/commands`, { data: body });
    expect(response.ok(), await response.text()).toBe(true);
    return { body, receipt: await response.json() };
  };
  const task = (await state()).tasks[0];
  const spec = { kind: 'evaluator', name: `Independently rechecked evaluator ${suffix}`, manifest,
    mechanism: 'Sum squared distance to the configured center plus offset, rounded to digits.',
    acceptance_criteria: ['Return the declared raw energy', 'Restore evaluator state'],
    validation_mode: 'contract_only', correctness_cases: [], contract_cases: [{ name: 'initial probe', candidate: [0, 0] }] };
  await command('evaluator.commission', { task_id: task.id, spec, compute_seconds: 30, max_calls: 1,
    package: { kind: 'evaluator', contract: 'evaluator_v1', entrypoint: 'evaluator:create_evaluator',
      files: [{ path: 'evaluator.py', content: readFileSync('../examples/implementation-reference/continuous_evaluator.py', 'utf8') }] } });
  await expect.poll(async () => (await state()).tasks[0].evaluator_readiness?.state, { timeout: 30000 }).toBe('numerical_evidence_required');
  await command('study.create', { goal: 'Explore under a scoped numerical-evidence waiver',
    validation_policy: { waivable_kinds: ['evaluator_correctness'] } });
  let current = await state();
  const ready = current.tasks[0].evaluator_readiness;
  const requirement = (await validations()).find((row: any) => row.requirement.id === ready.requirement_id);
  const waiver = await command('validation.waive', { requirement_id: ready.requirement_id,
    rationale: 'Exercise the declared exploratory workflow while numerical evidence is unavailable',
    evidence_ids: requirement.requirement.scope.contract_evidence_ids });
  await command('trial.create', { campaign_id: campaign.id, task_id: task.id, algorithm: 'coordinate', max_steps: 4, wall_seconds: 10 });
  await expect.poll(async () => (await state()).trials.filter((item: any) => item.status === 'completed' && item.latest_output_asset_ids?.length).length,
    { timeout: 25000 }).toBe(1);
  current = await state();
  const original = current.trials[0], versionId = current.tasks[0].evaluator_version_id;
  const originalVersion = current.implementation_library.versions.find((item: any) => item.id === versionId);
  expect(original.evaluator_eligibility.basis).toBe('waiver');

  await page.goto(`${base}/#validation`);
  await page.getByLabel('Active campaign').selectOption(campaign.id);
  await page.getByRole('button', { name: 'Revalidate evaluator', exact: true }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Revalidation rationale').fill('Independent analytical origin and minimizer values');
  await dialog.getByRole('button', { name: 'Queue independent revalidation', exact: true }).click();
  await expect(dialog.getByText('Add at least one independently derived numerical reference with its basis.')).toBeVisible();
  await dialog.getByLabel('Additional numerical fixtures (JSON)').fill(JSON.stringify([
    { name: 'analytical origin', basis: '2*(0-1.5)^2-2 = 2.5', candidate: [0, 0], objectives: { energy: 2.5 } },
    { name: 'analytical minimum', basis: 'Both squared differences are zero', candidate: [1.5, 1.5], objectives: { energy: -2 } },
  ]));
  const submitted = page.waitForResponse(response => response.url().endsWith('/api/v1/commands') &&
    response.request().method() === 'POST' && response.request().postDataJSON().operation === 'implementation.revalidate');
  await dialog.getByRole('button', { name: 'Queue independent revalidation', exact: true }).click();
  const recheckResponse = await submitted, recheckEnvelope = recheckResponse.request().postDataJSON(), recheckReceipt = await recheckResponse.json();
  expect(recheckResponse.ok()).toBe(true);
  await expect(dialog).not.toBeVisible();
  await expect.poll(async () => (await state()).implementation_jobs.find((item: any) => item.id === recheckReceipt.outcome.grant_id)?.evidence_reconciled,
    { timeout: 25000 }).toBe(true);
  const checked = await state(), job = checked.implementation_jobs.find((item: any) => item.id === recheckReceipt.outcome.grant_id);
  expect(job.status).toBe('completed');
  expect(job.usage?.calls || 0).toBe(0);
  const version = checked.implementation_library.versions.find((item: any) => item.id === versionId);
  expect(version.artifact_digest).toBe(originalVersion.artifact_digest);
  expect(version.runtime_digest).toBe(originalVersion.runtime_digest);
  expect(version.spec).toEqual(originalVersion.spec);
  expect(version.validation_history).toContainEqual(originalVersion.validation_report);
  expect(version.validation_report.costs.evaluation_requests).toBe(15);
  const measured = (await validations()).find((row: any) => row.requirement.id === ready.requirement_id);
  expect(measured.measured_pass).toBe(true);
  expect(measured.active_waiver_ids).toContain(waiver.receipt.outcome.waiver_id);
  expect(checked.trials.find((item: any) => item.id === original.id).experiment_spec_hash).toBe(original.experiment_spec_hash);

  await page.goto(`${base}/#drafts`);
  await page.getByRole('button', { name: 'Design an experiment', exact: true }).click();
  await dialog.getByLabel('Optimization strategy').selectOption('coordinate');
  await dialog.getByLabel('Evaluation requests', { exact: true }).fill('4');
  await dialog.getByLabel('Time cap (seconds)', { exact: true }).fill('10');
  await dialog.getByRole('button', { name: 'Launch experiment', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect.poll(async () => (await state()).trials.filter((item: any) => item.status === 'completed' && item.latest_output_asset_ids?.length).length,
    { timeout: 25000 }).toBe(2);
  const completed = await state(), newer = completed.trials.find((item: any) => item.id !== original.id);
  expect(newer.evaluator_eligibility.basis).toBe('numerical_validation');
  expect(newer.evaluator_eligibility.validation_report_id).toBe(version.validation_report.id);
  expect(completed.trials.find((item: any) => item.id === original.id).evaluator_eligibility).toEqual(original.evaluator_eligibility);
  const costs: any[] = [];
  for (const [trial, total] of [[original, 9], [newer, 24]] as const) {
    const asset = await (await request.get(`${base}/api/v1/assets/${trial.latest_output_asset_ids[0]}`)).json();
    expect(asset.full_attributed_cost.quantities.evaluation_requests.total).toBe(total);
    costs.push({ trial_id: trial.id, ...asset.full_attributed_cost });
  }
  const replay = await request.post(`${base}/api/v1/commands`, { data: recheckEnvelope });
  expect(await replay.json()).toEqual(recheckReceipt);
  expect((await state()).implementation_jobs.filter((item: any) => item.request.operation === 'revalidation')).toHaveLength(1);

  const altered = { ...manifest, primary_objective: { ...manifest.primary_objective, units: 'a different problem definition' } };
  const reuseCampaign = await create(`Deliberate evaluator reuse ${suffix}`, [
    { name: 'Different declared definition', problem_id: manifest.id, evaluator_manifest: altered },
    { name: 'Matching declared definition', problem_id: manifest.id, evaluator_manifest: manifest }]);
  await page.reload();
  await page.getByLabel('Active campaign').selectOption(reuseCampaign.id);
  await page.goto(`${base}/#implementations`);
  const reuseTasks = (await state(reuseCampaign.id)).tasks;
  const incompatible = reuseTasks.find((item: any) => item.name === 'Different declared definition');
  const compatible = reuseTasks.find((item: any) => item.name === 'Matching declared definition');
  const card = page.locator('article').filter({ has: page.getByRole('heading', { name: spec.name, exact: true }) })
    .filter({ has: page.getByRole('button', { name: 'Revalidate this executable', exact: true }) });
  await page.getByLabel('Attach an evaluator to a problem').selectOption(incompatible.id);
  await page.getByLabel('Reason for reuse or decline').fill('The full declared problem differs; commission a specialized evaluator for it.');
  await expect(card.getByRole('button', { name: 'Use this evaluator for the selected problem', exact: true })).toBeDisabled();
  await card.getByRole('button', { name: 'Record decision to decline reuse', exact: true }).click();
  await expect.poll(async () => (await state(reuseCampaign.id)).executable_reuse_decisions.length).toBe(1);
  await page.getByLabel('Attach an evaluator to a problem').selectOption(compatible.id);
  await page.getByLabel('Reason for reuse or decline').fill('This exact evaluator has independent numerical evidence for the declared problem.');
  await card.getByRole('button', { name: 'Use this evaluator for the selected problem', exact: true }).click();
  await expect.poll(async () => (await state(reuseCampaign.id)).executable_reuse_decisions.length).toBe(2);
  const reused = await state(reuseCampaign.id);
  expect(reused.tasks.find((item: any) => item.id === compatible.id).evaluator_version_id).toBe(versionId);
  expect(reused.tasks.find((item: any) => item.id === incompatible.id).evaluator_version_id).toBeUndefined();
  expect(reused.implementation_jobs).toHaveLength(0);
  expect(reused.trials).toHaveLength(0);
  expect(reused.executable_reuse_decisions.map((item: any) => item.decision).sort()).toEqual(['decline', 'reuse']);
  expect(reused.executable_reuse_decisions.every((item: any) => item.study_id === reused.campaign.active_study_id &&
    item.consequences.version_id === versionId && item.rationale)).toBe(true);
  await expect(page.getByRole('heading', { name: 'Executable reuse decisions', exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('executable-reuse-decisions.png'), fullPage: true });
  expect(browserErrors).toEqual([]);
  writeFileSync(info.outputPath('revalidation-reuse-evidence.json'), JSON.stringify({ campaign_id: campaign.id,
    reuse_campaign_id: reuseCampaign.id, original, newer, original_version: originalVersion, version, job,
    recheckEnvelope, recheckReceipt, costs, decisions: reused.executable_reuse_decisions, browserErrors }, null, 2));
});
