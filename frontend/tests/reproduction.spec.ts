import { test, expect } from '@playwright/test';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

test('an exact imported snapshot becomes a new reproduction with a frozen comparison and a recoverable launch', async ({ page, request }, info) => {
  const base = process.env.REPRODUCTION_TEST_URL, fixturePath = process.env.REPRODUCTION_TEST_FIXTURE;
  test.skip(!base || !fixturePath, 'Requires an isolated workspace and a portable source fixture whose original directory is unavailable.');
  test.setTimeout(120000);
  const fixture = JSON.parse(readFileSync(fixturePath!, 'utf8'));
  expect(existsSync(fixture.original_directory)).toBe(false);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const created = await request.post(`${base}/api/campaigns`, { data: { name: `Historical reproduction ${Date.now()}`,
    validation_reserve_seconds: 0, compute_budget_seconds: 90,
    tasks: [{ name: 'Quadratic reproduction', problem_id: 'bounded_continuous' }] } });
  expect(created.ok(), await created.text()).toBe(true);
  const campaign = await created.json();
  const state = async () => (await request.get(`${base}/api/v1/state?campaign_id=${campaign.id}`)).json();
  await page.goto(`${base}/#assets`);
  await page.getByLabel('Active campaign').selectOption(campaign.id);
  await page.getByLabel('Evidence bundle', { exact: true }).setInputFiles(fixture.archive);
  await page.getByRole('button', { name: 'Inspect bundle', exact: true }).click();
  await page.getByRole('button', { name: 'Import inspected history', exact: true }).click();
  await expect(page.getByLabel('Historical experiment snapshot').locator('option')).toHaveCount(2);
  expect((await state()).trials).toEqual([]);
  const sources = await (await request.get(`${base}/api/v1/reproduction-sources?campaign_id=${campaign.id}`)).json();
  expect(sources.sources[0].reference.id).toBe(fixture.trial_id);
  await page.getByLabel('Historical experiment snapshot').selectOption(sources.sources[0].key);
  await page.getByRole('button', { name: 'Design reproduction', exact: true }).click();
  const modal = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: 'Historical reproduction draft', exact: true }) });
  await expect(modal.getByText('Ready to reproduce the selected snapshot.', { exact: true })).toBeVisible();
  await expect(modal.getByText(`Original experiment: ${fixture.trial_id}`, { exact: false })).toBeVisible();
  await modal.getByLabel('Reproduction title', { exact: true }).fill('Exact quadratic reproduction');
  await modal.getByLabel('Objective absolute tolerance', { exact: true }).fill('0.000000001');
  await modal.getByRole('button', { name: 'Save reproduction revision', exact: true }).click();
  await expect(modal.getByRole('button', { name: 'Save reproduction revision', exact: true })).toBeDisabled();
  await expect(modal.getByRole('button', { name: 'Launch reproduction', exact: true })).toBeEnabled();
  await modal.screenshot({ path: info.outputPath('reproduction-draft.png') });
  let envelope: any = null;
  await page.route('**/api/v1/commands', async route => {
    const body = route.request().postDataJSON();
    if (body.operation === 'draft.launch' && !envelope) {
      envelope = body;
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBe(true);
      await route.abort('failed');
    } else await route.continue();
  });
  await modal.getByRole('button', { name: 'Launch reproduction', exact: true }).click();
  await expect(modal.getByRole('button', { name: 'Launch reproduction', exact: true })).toBeEnabled();
  await modal.getByRole('button', { name: 'Launch reproduction', exact: true }).click();
  await expect(modal).not.toBeVisible();
  await expect.poll(async () => (await state()).reproduction_comparisons[0]?.outcome, { timeout: 45000 }).toBe('agreement');
  const completed = await state();
  expect(completed.trials).toHaveLength(1);
  expect(completed.draft_launches).toHaveLength(1);
  const trial = completed.trials[0], comparison = completed.reproduction_comparisons[0];
  expect(trial.id).not.toBe(fixture.trial_id);
  expect(trial.reproduction.reference).toEqual(sources.sources[0].reference);
  expect(trial.result.scientific_complete).toBe(true);
  expect(trial.isolation_policy.kind).toBe('supervised_capture');
  expect(comparison.comparison.objective_absolute_tolerance).toBe(1e-9);
  expect(comparison.measurements.primary_objective.agrees).toBe(true);
  expect(comparison.measurements.best_candidate.agrees).toBe(true);
  const replay = await request.post(`${base}/api/v1/commands`, { data: envelope });
  expect(replay.ok(), await replay.text()).toBe(true);
  expect((await state()).trials).toHaveLength(1);
  await page.getByRole('button', { name: 'Research assets', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'agreement', exact: true })).toBeVisible();
  const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: 'agreement', exact: true }) });
  await row.getByText('Comparison evidence', { exact: true }).click();
  await expect(row.locator('pre')).toContainText(fixture.trial_id);
  await page.screenshot({ path: info.outputPath('reproduction-comparison.png'), fullPage: true });
  const report = await (await request.get(`${base}/api/v1/campaigns/${campaign.id}/comparison?cost_axis=evaluation_requests`)).json();
  expect(report.actual_campaign_costs.quantities.evaluation_requests.total).toBe(fixture.requests);
  expect(report.groups[0].trials[0].full_cost.quantities.evaluation_requests.total).toBe(fixture.requests);
  expect(errors).toEqual([]);
  writeFileSync(info.outputPath('reproduction-evidence.json'), JSON.stringify({ campaign_id: campaign.id, trial,
    comparison, draft: completed.drafts[0], launch_command: envelope, original: fixture,
    costs: report.actual_campaign_costs, errors }, null, 2));
});
