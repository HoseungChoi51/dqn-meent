import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

test('portable evidence is inspected and imported across workspaces without starting its historical producers', async ({ page, request }, info) => {
  const source = process.env.COMMISSIONING_TEST_URL, destination = process.env.BUNDLE_TEST_URL;
  test.skip(!source || !destination || source === destination, 'Requires two isolated workspaces, each with its own supplied-code library.');
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const suffix = Date.now();
  const manifest = { id: `bundle_quadratic_${suffix}`, name: `Portable evidence quadratic ${suffix}`,
    candidate_schema: { representation: 'continuous', dimensions: 2, bounds: [[-3, 3], [-3, 3]] },
    primary_objective: { name: 'energy', direction: 'minimize', units: 'arbitrary energy units' },
    configuration_schema: { type: 'object', additionalProperties: false,
      properties: { center: { type: 'number' }, offset: { type: 'number' } } },
    configuration: { center: 1.5, offset: -2 },
    fidelity_schema: { type: 'object', additionalProperties: false, properties: { digits: { type: 'integer' } } },
    fidelity: { digits: 8 } };
  const create = async (base: string, tasks: any[]) => {
    const response = await request.post(`${base}/api/campaigns`, { data: {
      name: `Portable evidence ${base === source ? 'source' : 'destination'} ${suffix}`,
      compute_budget_seconds: 60, validation_reserve_seconds: 0, implementation_compute_budget_seconds: 60, tasks } });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  const owner = await create(source!, [{ name: 'Independent quadratic', problem_id: manifest.id, evaluator_manifest: manifest }]);
  const recipient = await create(destination!, [{ name: 'Destination problem', problem_id: 'bounded_continuous' }]);
  const state = async (base: string, id: string) => (await request.get(`${base}/api/v1/state?campaign_id=${id}`)).json();
  const task = (await state(source!, owner.id)).tasks[0];
  const command = async (base: string, id: string, operation: string, payload: any) => {
    const current = await state(base, id);
    const response = await request.post(`${base}/api/v1/commands`, { data: {
      id: `bundle_${randomUUID()}`, campaign_id: id, expected_revision: current.campaign.version, operation, payload } });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  await command(source!, owner.id, 'evaluator.commission', { task_id: task.id, compute_seconds: 30, max_calls: 1,
    spec: { kind: 'evaluator', name: `Bundle evaluator ${suffix}`, manifest,
      mechanism: 'Sum squared distance to center plus offset, rounded to digits.',
      acceptance_criteria: ['Return raw energy', 'Restore evaluator state'],
      correctness_cases: [
        { name: 'origin', candidate: [0, 0], objectives: { energy: 2.5 }, basis: 'Analytical sum of squares' },
        { name: 'minimum', candidate: [1.5, 1.5], objectives: { energy: -2 }, basis: 'Analytical minimum' } ] },
    package: { kind: 'evaluator', contract: 'evaluator_v1', entrypoint: 'evaluator:create_evaluator',
      files: [{ path: 'evaluator.py', content: readFileSync('../examples/implementation-reference/continuous_evaluator.py', 'utf8') }] } });
  await expect.poll(async () => (await state(source!, owner.id)).tasks[0].evaluator_readiness?.runnable, { timeout: 30000 }).toBe(true);
  const experiment = await command(source!, owner.id, 'trial.create', { task_id: task.id, algorithm: 'coordinate', max_steps: 3, wall_seconds: 10 });
  await expect.poll(async () => (await state(source!, owner.id)).trials.find((row: any) => row.id === experiment.outcome.trial_id)?.status,
    { timeout: 30000 }).toBe('completed');
  await expect.poll(async () => !!(await state(source!, owner.id)).trials.find((row: any) => row.id === experiment.outcome.trial_id)?.output_asset_ids?.length,
    { timeout: 30000 }).toBe(true);
  const finished = await state(source!, owner.id), trial = finished.trials.find((row: any) => row.id === experiment.outcome.trial_id);
  const assets = await (await request.get(`${source}/api/v1/assets?campaign_id=${owner.id}`)).json();
  const root = assets.find((asset: any) => asset.producer_id === trial.id && asset.kind === 'solution');
  expect(root).toBeTruthy();
  const original = await (await request.get(`${source}/api/v1/assets/${root.id}`)).json();
  await page.goto(`${source}/#assets`);
  await page.getByLabel('Active campaign').selectOption(owner.id);
  await page.getByLabel('Asset to export', { exact: true }).selectOption(root.id);
  await page.getByRole('button', { name: 'Export asset and dependencies', exact: true }).click();
  const downloadable = page.getByRole('link', { name: 'Download evidence bundle', exact: true });
  await expect(downloadable).toBeVisible();
  const downloading = page.waitForEvent('download');
  await downloadable.click();
  const archive = info.outputPath('portable-evidence.zip');
  await (await downloading).saveAs(archive);
  const sourceOperations = await (await request.get(`${source}/api/v1/bundles/operations?campaign_id=${owner.id}`)).json();
  const exported = sourceOperations.find((operation: any) => operation.action === 'export');
  expect(exported.status).toBe('completed');

  await page.goto(`${destination}/#assets`);
  await page.getByLabel('Active campaign').selectOption(recipient.id);
  await page.getByLabel('Evidence bundle', { exact: true }).setInputFiles(archive);
  await page.getByRole('button', { name: 'Inspect bundle', exact: true }).click();
  const inspected = page.locator('article').filter({ has: page.getByRole('heading', { name: 'Bundle inspection', exact: true }) });
  await expect(inspected.getByRole('button', { name: 'Import inspected history', exact: true })).toBeVisible();
  expect(await (await request.get(`${destination}/api/v1/assets?campaign_id=${recipient.id}`)).json()).toEqual([]);
  const staged = await state(destination!, recipient.id);
  expect(staged.trials).toEqual([]);
  expect(staged.implementation_jobs).toEqual([]);
  expect(staged.implementation_library.versions.find((row: any) => row.id === trial.evaluator_version_id)).toBeUndefined();
  const sending = page.waitForRequest(r => r.method() === 'POST' && r.url().endsWith('/api/v1/commands')
    && r.postDataJSON()?.operation === 'bundle.publish');
  await inspected.getByRole('button', { name: 'Import inspected history', exact: true }).click();
  const envelope = (await sending).postDataJSON();
  const published = page.locator('article').filter({ has: page.getByRole('heading', { name: 'Historical import', exact: true }) });
  await expect(published.getByText('completed', { exact: true })).toBeVisible();
  const imported = await (await request.get(`${destination}/api/v1/assets/${root.id}`)).json();
  expect(imported.asset).toEqual(original.asset);
  expect(imported.full_attributed_cost).toEqual(original.full_attributed_cost);
  expect(imported.local_availability.status).toBe('available');
  expect(imported.historical_producers[0].problem.primary_objective).toEqual(original.historical_producers[0].problem.primary_objective);
  expect(imported.historical_producers[0].problem.primary_objective).toMatchObject(manifest.primary_objective);
  const after = await state(destination!, recipient.id);
  expect(after.trials).toEqual([]);
  expect(after.implementation_jobs).toEqual([]);
  const version = after.implementation_library.versions.find((row: any) => row.id === trial.evaluator_version_id);
  expect(version.status).toBe('validation_required');
  const replay = await request.post(`${destination}/api/v1/commands`, { data: envelope });
  expect(replay.ok(), await replay.text()).toBe(true);
  await inspected.getByRole('button', { name: 'Import inspected history', exact: true }).click();
  const operations = async () => (await request.get(`${destination}/api/v1/bundles/operations?campaign_id=${recipient.id}`)).json();
  await expect.poll(async () => (await operations()).filter((row: any) => row.action === 'publish' && row.status === 'completed').length).toBe(2);
  const finalOperations = await operations();
  expect(new Set(finalOperations.filter((row: any) => row.action === 'publish').map((row: any) => row.receipt_id)).size).toBe(1);
  await page.getByRole('row').filter({ hasText: manifest.id }).getByRole('button', { name: root.title, exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Local artifacts: available', { exact: true })).toBeVisible();
  await dialog.getByText('Historical producers and objective interpretation', { exact: true }).click();
  const historical = dialog.locator('details').filter({ has: page.getByText('Historical producers and objective interpretation', { exact: true }) });
  await expect(historical.locator('pre')).toContainText('"direction": "minimize"');
  await expect(historical.locator('pre')).toBeVisible();
  await dialog.screenshot({ path: info.outputPath('imported-evidence.png') });

  // A later receipt changes the accounting projection of the same imported
  // result, preserving its immutable content and original production costs.
  const costSource = imported.accounting_sources.find((row: any) => row.source_id.startsWith('implementation:'));
  expect(costSource).toBeTruthy();
  const finding = await command(destination!, recipient.id, 'finding.record', {
    content: 'Synthetic delayed fixture receipt: 123 input tokens for the original mocked review. No live provider claim.',
    source_ids: [root.id], interpretation: 'observation' });
  await dialog.getByText('Recorded costs and later receipts', { exact: true }).click();
  await dialog.getByLabel('Cost source', { exact: true }).selectOption(costSource.source_id);
  await dialog.getByLabel('Receipt covers first cost entries', { exact: true }).fill(String(costSource.observed_stop));
  await dialog.getByLabel('Measured model input tokens', { exact: true }).fill('123');
  await dialog.getByLabel('Supporting receipt record IDs', { exact: true }).fill(finding.outcome.finding_id);
  await dialog.getByLabel('Cost reconciliation rationale', { exact: true }).fill('Reconcile the recorded fixture receipt without allocating any new execution.');
  const recording = page.waitForRequest(r => r.method() === 'POST' && r.url().endsWith('/api/v1/commands')
    && r.postDataJSON()?.operation === 'cost.reconcile');
  await dialog.getByRole('button', { name: 'Record cost receipt', exact: true }).click();
  const receiptEnvelope = (await recording).postDataJSON();
  await expect(dialog.getByRole('status')).toContainText('Cost receipt recorded:');
  const tokenRow = dialog.getByRole('row').filter({ has: page.getByRole('cell', { name: 'model input tokens', exact: true }) });
  await expect(tokenRow.getByRole('cell').last()).toHaveText('123');
  const reconciled = await (await request.get(`${destination}/api/v1/assets/${root.id}`)).json();
  expect(reconciled.asset).toEqual(original.asset);
  expect(reconciled.full_attributed_cost.event_count).toBe(original.full_attributed_cost.event_count);
  expect(reconciled.full_attributed_cost.quantities.model_input_tokens.total).toBe(123);
  expect(reconciled.full_attributed_cost.quantities.model_calls).toEqual(original.full_attributed_cost.quantities.model_calls);
  const replayReceipt = await request.post(`${destination}/api/v1/commands`, { data: receiptEnvelope });
  expect(replayReceipt.ok(), await replayReceipt.text()).toBe(true);
  expect((await (await request.get(`${destination}/api/v1/assets/${root.id}`)).json()).full_attributed_cost).toEqual(reconciled.full_attributed_cost);
  await dialog.getByLabel('Measured model input tokens', { exact: true }).fill('124');
  await dialog.getByRole('button', { name: 'Record cost receipt', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('contradicts');
  await expect(tokenRow.getByRole('cell').last()).toHaveText('123');
  await dialog.screenshot({ path: info.outputPath('reconciled-cost-evidence.png') });
  await page.keyboard.press('Escape');
  const returnedOperation = await command(destination!, recipient.id, 'bundle.export', { asset_ids: [root.id] });
  const returned = (await operations()).find((row: any) => row.id === returnedOperation.outcome.operation_id);
  await expect.poll(async () => (await operations()).find((row: any) => row.id === returned.id)?.status).toBe('completed');
  writeFileSync(info.outputPath('bundle-transfer-evidence.json'), JSON.stringify({ source: { campaign_id: owner.id, trial_id: trial.id,
    version_id: trial.evaluator_version_id, export: exported }, destination: { campaign_id: recipient.id, version,
      operations: await operations(), imported, reconciled, reexport: returned }, command: envelope, receipt_command: receiptEnvelope,
    limitations: 'Supplied evaluator source and mocked semantic review; real HTTP, package execution, archive transfer, inspection and independent service publication.' }, null, 2));
  expect(errors).toEqual([]);
});
