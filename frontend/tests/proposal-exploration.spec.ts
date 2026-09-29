import { test, expect } from '@playwright/test';

test('proposal actions preserve exact parents and implementation budget is actionable', async ({ page }) => {
  const campaign = { id: 'proposal-campaign', version: 1, name: 'Scientific optimization', objective: 'Find an optimizer',
    autonomy: 'guided', compute_budget_seconds: 200, implementation_compute_budget_seconds: 0, llm_budget_usd: 0 };
  const hypotheses = ['Local search', 'Surrogate pool'].map((title, i) => ({ id: `h${i}`, title,
    mechanism: `Mechanism ${i}`, rationale: 'A conjecture to test', assumptions: [], risks: [], sources: [],
    parent_ids: [], algorithm: 'custom', algorithm_config: {}, status: 'proposed', reviews: [],
    implementation_readiness: { state: 'missing', runnable: false, reason: 'Needs an implementation' } }));
  const state = { workspace_id: 'proposal-workspace', campaign, campaigns: [campaign], tasks: [{ id: 'task', name: 'Test problem',
    split: 'development', physics: { n_cells: 64 } }], hypotheses, trials: [], decisions: [], messages: [], events: [],
    algorithms: [{ id: 'surrogate', name: 'Surrogate-guided search', description: 'Fit observations to select candidates' }],
    manager_issues: [], manager_commands: [], research_runs: [], settings: { llm_configured: true }, budget: {},
    research_progress: { status: 'running', headline: 'Discovery is running', active: false,
      session: { id: 'discovery-session', status: 'running', control_revision: 0 }, agents: [], task_counts: {} } };
  const writes: any[] = [];
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'POST') {
      const command = route.request().postDataJSON(); writes.push(command);
      return route.fulfill({ json: { id: command.id, status: 'completed', outcome: {} } });
    }
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/discovery')) return route.fulfill({ json: { sessions: [] } });
    if (path.endsWith('/implementations')) return route.fulfill({ json: { versions: [] } });
    return route.fulfill({ json: state });
  });
  await page.goto('/#hypotheses');
  await page.getByRole('button', { name: 'Combine proposals', exact: true }).click();
  let dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('independent review');
  await dialog.getByLabel('Direction or constraints').fill('Use the surrogate to rank local moves');
  await dialog.getByRole('button', { name: 'Send to campaign manager' }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].operation).toBe('research.start');
  expect(writes[0].payload).toMatchObject({ mode: 'generate', proposal_operation: 'hybrid', parent_hypothesis_ids: ['h0', 'h1'] });
  expect(writes[0].payload.message).toContain('Use the surrogate to rank local moves');
  await page.getByRole('button', { name: /H01.*Local search/ }).click();
  await page.getByRole('button', { name: 'Diversify proposal', exact: true }).click();
  await page.getByRole('button', { name: 'Send to campaign manager' }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].payload).toMatchObject({ proposal_operation: 'diversify', parent_hypothesis_ids: ['h0'] });
  await page.getByRole('button', { name: /H02.*Surrogate pool/ }).click();
  await page.getByRole('button', { name: 'Request implementation', exact: true }).click();
  dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Assign implementation compute in the campaign charter first');
  await expect(dialog.getByRole('button', { name: 'Commission implementation', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Implementations', exact: true }).click();
  await page.getByLabel('Search installed implementations').fill('surrogate');
  await expect(page.getByRole('heading', { name: 'Surrogate-guided search' })).toBeVisible();
});

for (const sessionStatus of [null, 'completed', 'stopped', 'exhausted']) {
  test(`proposal requests lead to discovery setup when session is ${sessionStatus || 'missing'}`, async ({ page }) => {
    const campaign = { id: 'setup-campaign', version: 1, name: '2D grating', objective: 'Design a beam deflector',
      autonomy: 'guided', compute_budget_seconds: 7200, llm_budget_usd: 0 };
    const state = { workspace_id: 'setup-workspace', campaign, campaigns: [campaign], tasks: [{ id: 'task',
      name: '2D MEENT', split: 'development', evaluator_readiness: { runnable: true } }], hypotheses: [], trials: [],
      decisions: [], messages: [], events: [], algorithms: [], manager_issues: [], manager_commands: [], research_runs: [],
      settings: { llm_configured: true }, budget: {}, research_progress: { status: sessionStatus || 'idle',
        headline: 'Discovery setup', active: false, agents: [], task_counts: {},
        session: sessionStatus ? { id: 'old-session', status: sessionStatus, control_revision: 0 } : null } };
    const writes: unknown[] = [];
    await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
    await page.route('**/api/**', route => {
      if (route.request().method() === 'POST') writes.push(route.request().postDataJSON());
      return route.fulfill({ json: new URL(route.request().url()).pathname.endsWith('/discovery')
        ? { sessions: [], tasks: [], artifacts: [], candidates: [] } : state });
    });
    await page.goto('/#hypotheses');
    await page.getByRole('button', { name: 'Develop strategies', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Start discovery to generate proposals');
    await expect(dialog.getByRole('button', { name: 'Send to campaign manager' })).toBeDisabled();
    await dialog.getByRole('link', { name: 'Set up discovery' }).click();
    await expect(page).toHaveURL(/#notebook\/discovery$/);
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('region', { name: 'Optimizer discovery' }).getByRole('button', { name: 'Start discovery', exact: true })).toBeEnabled();
    expect(writes).toHaveLength(0);
  });
}
