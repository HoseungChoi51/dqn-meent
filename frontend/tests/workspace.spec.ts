import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

const campaign = { id: 'campaign-test', name: 'Browser test campaign', objective: 'Compare optimization mechanisms under a common physical objective.', compute_budget_seconds: 600, llm_budget_usd: 5, validation_reserve_seconds: 30, autonomy: 'guided', version: 1 };
const task = { id: 'task-test', name: 'Test configuration', physics: { n_cells: 16, wavelength_nm: 1100, deflection_angle_deg: 50, thickness_nm: 325, material: 'constant', fourier_order: 5 }, split: 'development', campaign_id: campaign.id, charter_version: 1, created_at: '2026-01-01T00:00:00Z', exposed: false };
const algorithms = [{ id: 'random', name: 'Uniform random', description: 'Reference baseline.', parameters: {} }, { id: 'hillclimb', name: 'Restart hill climbing', description: 'Restarting local search.', parameters: {} }];
const blank = { campaigns: [], campaign: null, tasks: [], algorithms, hypotheses: [], decisions: [], events: [], trials: [], messages: [], research_runs: [], settings: { llm_configured: false, model: 'gpt-6-sol', provider: { provider: 'codex', billing_mode: 'subscription', model: 'gpt-6-sol', enabled: false, configured: false } } };
const base = { ...blank, campaigns: [campaign], campaign, tasks: [task], budget: { spent_seconds: 4, llm_spent_usd: .001 } };

async function mockWorkspace(page: Page, initial: Record<string, any>) {
  const state = structuredClone(initial);
  const writes: Array<{ url: string; method: string; body: any }> = [];
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (path === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'retry: 60000\n\n' });
    if (req.method() !== 'GET') {
      const body = req.postDataJSON(); writes.push({ url: path, method: req.method(), body });
      if (path === '/api/campaigns') { state.campaign = { ...campaign, ...body }; state.campaigns = [state.campaign]; state.tasks = body.tasks.map((t: any) => ({ ...t, id: 'task-new' })); }
      if (path.endsWith('/control')) { const trial = state.trials.find((t: any) => path.includes(t.id)); if (trial) trial.status = body.action === 'pause' ? 'paused' : body.action === 'resume' ? 'running' : 'stopped'; }
      if (path.endsWith('/resolve')) { const d = state.decisions.find((d: any) => path.includes(d.id)); if (d) Object.assign(d, { ...body, status: 'resolved' }); }
      return route.fulfill({ json: path === '/api/campaigns' ? state.campaign : { id: 'new-record', ...body } });
    }
    if (path === '/api/state') return route.fulfill({ json: state });
    if (path.endsWith('/metrics')) return route.fulfill({ json: [] });
    if (path.endsWith('/analysis')) return route.fulfill({ json: { groups: [], paired_comparisons: [], thresholds: [], aggregate_comparisons: [] } });
    return route.fulfill({ json: {} });
  });
  return { state, writes };
}

test('a new researcher can create a charter with editable configurations and explicit budgets', async ({ page }) => {
  const { writes } = await mockWorkspace(page, blank);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'A better way to find the next design.' })).toBeVisible();
  await expect(page.getByText('No campaign yet')).toBeAttached();
  await page.screenshot({ path: test.info().outputPath('empty-workspace.png'), fullPage: true });
  await page.getByRole('button', { name: 'Create campaign', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Campaign name').fill('My inverse design study');
  await dialog.getByLabel('Compute cap (seconds)').fill('240');
  await dialog.getByLabel('Validation reserve (seconds)').fill('24');
  await dialog.getByRole('button', { name: 'Add configuration' }).click();
  await dialog.getByRole('button', { name: 'Create campaign', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText('Campaign created. Your workspace is ready.')).toBeVisible();
  expect(writes[0].body.tasks).toHaveLength(2);
  expect(writes[0].body.compute_budget_seconds).toBe(240);
  expect(writes[0].body.validation_reserve_seconds).toBe(24);
});

test('charter revision sends only permitted task fields and preserves explicit physics', async ({ page }) => {
  const { writes } = await mockWorkspace(page, base);
  await page.goto('/#problem');
  await page.getByRole('button', { name: 'Revise charter' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Save new version' }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(writes[0].method).toBe('PUT');
  expect(Object.keys(writes[0].body.tasks[0]).sort()).toEqual(['id', 'name', 'physics', 'split']);
  expect(writes[0].body.tasks[0].physics).toEqual(task.physics);
});

test('experiment controls call the backend and update the visible lifecycle', async ({ page }) => {
  const trial = { id: 'trial-test', campaign_id: campaign.id, task_id: task.id, algorithm: 'random', status: 'running', seed: 7, max_steps: 100, wall_seconds: 60, priority: 0, created_at: '2026-01-01T00:00:00Z', progress: { step: 3, best_efficiency: .2, best_design: [1, 0, 1, 0], elapsed_seconds: 1, solver_calls: 3, cache_hits: 0 } };
  const { writes } = await mockWorkspace(page, { ...base, trials: [trial] });
  await page.goto('/#experiments');
  await page.getByRole('button', { name: 'Uniform random', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('20.00%')).toBeVisible();
  await dialog.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Resume', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Resume', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Pause', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect(dialog.getByText('stopped', { exact: true })).toBeVisible();
  expect(writes.map(w => w.body.action)).toEqual(['pause', 'resume', 'stop']);
});

test('decisions require an explicit selection and retain researcher rationale', async ({ page }) => {
  const decision = { id: 'decision-test', title: 'Surrogate startup needs a longer probe', context: 'The initial design set used the short allocation; guided proposals have not been tested.', options: [{ id: 'extend', label: 'Extend through guided proposals', description: 'Allocate 30 additional seconds.' }, { id: 'defer', label: 'Defer this strategy' }], recommendation: 'Extend through guided proposals', status: 'pending' };
  const { writes } = await mockWorkspace(page, { ...base, decisions: [decision] });
  await page.goto('/#decisions');
  await expect(page.getByRole('button', { name: 'Record decision' })).toBeDisabled();
  await page.getByRole('radio', { name: 'Extend through guided proposals' }).check();
  await page.getByLabel('Your reasoning (optional)').fill('We need to observe the proposed mechanism before evaluating it.');
  await page.getByRole('button', { name: 'Record decision' }).click();
  await expect(page.getByRole('heading', { name: 'Room to keep exploring' })).toBeVisible();
  expect(writes[0].body).toEqual({ choice: 'extend', comment: 'We need to observe the proposed mechanism before evaluating it.' });
});

test('mobile navigation and dialog escape work without a horizontal page overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockWorkspace(page, blank);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('button', { name: 'Hypotheses', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Competing ideas. Visible reasoning.' })).toBeVisible();
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('button', { name: 'New campaign', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('deferred Codex setup keeps experiments available and distinguishes the API budget', async ({ page }) => {
  const { writes } = await mockWorkspace(page, { ...base, budget: { ...base.budget, subscription_calls: 4 } });
  await page.goto('/#problem');
  const panel = page.getByRole('complementary', { name: 'Research conversation' });
  if (!(await panel.isVisible())) await page.getByRole('button', { name: 'Research partner', exact: true }).click();
  await expect(panel.getByText('Codex · GPT6-sol', { exact: true })).toBeVisible();
  await expect(panel.getByText('Model configuration deferred', { exact: true })).toBeVisible();
  await expect(panel.getByText(/assistant uses labeled curated guidance/)).toBeVisible();
  await expect(page.getByText('API spending', { exact: true })).toBeVisible();
  await expect(page.getByText('Subscription usage', { exact: true })).toBeVisible();
  await expect(page.getByText('4 calls', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Revise charter' }).click();
  await expect(page.getByRole('dialog').getByLabel('API spending cap (USD)')).toBeVisible();
  expect(writes).toEqual([]);
});

test('research notebook separates subscription calls from historical API estimates', async ({ page }) => {
  await mockWorkspace(page, {
    ...base,
    settings: { llm_configured: true, model: 'gpt-6-sol', provider: { provider: 'codex', billing_mode: 'subscription', model: 'gpt-6-sol', enabled: true, configured: true } },
    research_runs: [
      { id: 'subscription-run', mode: 'review', status: 'completed', model: 'gpt-6-sol', request: { message: 'Subscription review' }, usage: { billing_mode: 'subscription', cost_usd: null, api_cost_usd: 0, calls: 2, subscription_calls: 2, input_tokens: 100, output_tokens: 50 } },
      { id: 'old-api-run', mode: 'discuss', status: 'completed', model: 'gpt-6-luna', request: { message: 'Earlier API discussion' }, usage: { cost_usd: .025, calls: 1 } },
    ],
  });
  await page.goto('/#notebook');
  await page.getByRole('button', { name: 'Agent runs', exact: true }).click();
  const subscription = page.locator('.run-list article').filter({ hasText: 'Subscription review' });
  await expect(subscription.getByText('2 subscription calls', { exact: true })).toBeVisible();
  await expect(subscription.getByText('100 input tokens', { exact: true })).toBeVisible();
  await expect(subscription.getByText('Uses subscription allowance', { exact: true })).toBeVisible();
  await expect(subscription.locator('.meta-row')).not.toContainText('$');
  await expect(page.getByText('$0.0250 estimated API cost', { exact: true })).toBeVisible();
});
