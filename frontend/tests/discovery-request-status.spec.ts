import { test, expect } from '@playwright/test';

test('Develop strategies exposes a paused request without presenting an old failure as its result', async ({ page }) => {
  const campaign = { id: 'campaign-status', version: 1, name: 'Scientific problem', autonomy: 'guided',
    objective: 'Develop an optimizer', compute_budget_seconds: 100, llm_budget_usd: 2 };
  const state: any = { workspace_id: 'status-workspace', campaign, campaigns: [campaign], tasks: [], trials: [],
    hypotheses: [], decisions: [], messages: [], events: [], algorithms: [], manager_issues: [], manager_commands: [],
    research_runs: [{ id: 'old-failure', status: 'failed', error: 'Historical literature validation failure' }],
    settings: { llm_configured: true, provider: { configured: true, enabled: true, provider: 'codex', model: 'gpt-6-luna' } }, budget: {} };
  const writes: any[] = [];
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route('**/api/**', async route => {
    const request = route.request();
    if (new URL(request.url()).pathname.endsWith('/discovery')) return route.fulfill({ json: {
      sessions: [{ id: 'session', status: 'paused', control_revision: 7 }] } });
    if (request.method() === 'POST') {
      const command = request.postDataJSON(); writes.push(command);
      if (command.operation === 'research.start') state.manager_commands = [{ id: 'request', status: 'waiting_discovery', request: command.payload }];
      if (command.operation === 'discovery.control') state.manager_commands[0].status = 'admitted';
      return route.fulfill({ json: { id: command.id, status: 'completed', outcome: {} } });
    }
    return route.fulfill({ json: state });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Hypotheses', exact: true }).click();
  await page.getByRole('button', { name: 'Develop strategies', exact: true }).click();
  const panel = page.getByRole('complementary', { name: 'Research conversation' });
  await expect(panel).toContainText('Request saved · discovery paused');
  await expect(panel).not.toContainText('Historical literature validation failure');
  await panel.getByRole('button', { name: 'Resume discovery and process request' }).click();
  await expect.poll(() => writes.map(item => item.operation)).toEqual(['research.start', 'discovery.control']);
  expect(writes[1].payload).toEqual({ session_id: 'session', action: 'resume', expected_control_revision: 7 });
  await expect(panel).toContainText('Queued for manager');
});
