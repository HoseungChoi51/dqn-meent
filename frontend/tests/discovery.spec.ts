import { test, expect } from '@playwright/test';

test('discovery controls use durable commands and item comments go to the manager', async ({ page }) => {
  const campaign = { id: 'campaign-discovery', version: 1, name: 'Discovery fixture', autonomy: 'guided',
    objective: 'Find an optimizer for a bounded problem', compute_budget_seconds: 100, llm_budget_usd: 2 };
  const state = { workspace_id: 'discovery-fixture', campaign, campaigns: [campaign], tasks: [
    { id: 'problem', name: 'Quadratic problem', split: 'development', physics: {}, evaluator_readiness: { runnable: true } }],
    trials: [], hypotheses: [], decisions: [], messages: [], events: [], algorithms: [], research_runs: [],
    settings: { llm_configured: false }, budget: {} };
  let session: any;
  const writes: any[] = [];
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/discovery')) return route.fulfill({ json: { sessions: session ? [session] : [], artifacts: [],
      tasks: session ? [{ id: 'analysis-task', session_id: session.id, brief: { role: 'problem_analyst', objective: 'Inspect the evaluator' }, status: 'waiting', wait_reason: 'provider' }] : [] } });
    if (request.method() === 'POST') {
      const command = request.postDataJSON();
      writes.push(command);
      if (command.operation === 'discovery.start') session = { id: 'session', status: 'waiting_for_provider', control_revision: 0, policy: command.payload };
      if (command.operation === 'discovery.control') {
        expect(command.payload.expected_control_revision).toBe(session.control_revision);
        session = { ...session, status: { pause: 'paused', resume: 'running', stop: 'stopped' }[command.payload.action as 'pause'], control_revision: session.control_revision + 1 };
      }
      return route.fulfill({ json: { id: command.id, status: 'completed', outcome: { session } } });
    }
    return route.fulfill({ json: state });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Research notebook', exact: true }).click();
  await page.getByRole('button', { name: 'Discovery', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Optimizer discovery' });
  await expect(panel).toContainText('Model configuration is required');
  expect(writes).toEqual([]);
  await panel.getByLabel('Model call limit', { exact: true }).fill('12');
  await panel.getByRole('button', { name: 'Start discovery', exact: true }).click();
  await expect(panel).toContainText('Inspect the evaluator');
  expect(writes[0].payload).toMatchObject({ task_id: 'problem', model_call_limit: 12, source_request_limit: 64 });
  await panel.getByRole('button', { name: 'Pause discovery' }).click();
  await panel.getByRole('button', { name: 'Resume discovery' }).click();
  await panel.getByRole('button', { name: 'Discuss this task' }).click();
  await panel.getByLabel('Discovery message to manager').fill('Include noisy evaluations in the analysis.');
  await panel.getByRole('button', { name: 'Send to manager' }).click();
  await expect.poll(() => writes.filter(item => item.operation === 'research.start').length).toBe(1);
  expect(writes.find(item => item.operation === 'research.start').payload.message).toContain('Regarding analysis-task:');
  await panel.getByRole('button', { name: 'Stop discovery' }).click();
  await expect(panel.getByRole('button', { name: 'Start discovery', exact: true })).toBeVisible();
  expect(writes.map(item => item.operation)).toEqual(['discovery.start', 'discovery.control', 'discovery.control', 'research.start', 'discovery.control']);
});
