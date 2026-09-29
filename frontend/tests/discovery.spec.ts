import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

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
  const discussion = page.getByRole('dialog');
  await expect(discussion.getByRole('heading', { name: 'Discuss this task' })).toBeVisible();
  await expect(discussion.getByLabel('Discovery message to manager')).toBeFocused();
  await discussion.getByLabel('Discovery message to manager').fill('Include noisy evaluations in the analysis.');
  await discussion.getByRole('button', { name: 'Send to manager' }).click();
  await expect.poll(() => writes.filter(item => item.operation === 'research.start').length).toBe(1);
  expect(writes.find(item => item.operation === 'research.start').payload.message).toContain('Regarding analysis-task:');
  await expect(discussion).toContainText('Research request saved');
  await discussion.getByRole('button', { name: 'Done', exact: true }).click();
  await panel.getByRole('button', { name: 'Stop discovery' }).click();
  await expect(panel.getByRole('button', { name: 'Start discovery', exact: true })).toBeVisible();
  expect(writes.map(item => item.operation)).toEqual(['discovery.start', 'discovery.control', 'discovery.control', 'research.start', 'discovery.control']);
});

async function existingDiscovery(page: Page, failFirstSend = false) {
  const campaign = { id: 'discussion-campaign', version: 1, name: 'Discussion fixture', autonomy: 'guided',
    objective: 'Develop an optimizer', compute_budget_seconds: 100, llm_budget_usd: 0 };
  const state = { workspace_id: 'discussion-workspace', campaign, campaigns: [campaign], tasks: [], trials: [],
    hypotheses: [], decisions: [], messages: [], events: [], algorithms: [], research_runs: [],
    settings: { llm_configured: true }, budget: {} };
  const session = { id: 'session', status: 'paused', control_revision: 0,
    policy: { objective: campaign.objective, model_call_limit: 96, source_request_limit: 64, max_concurrent_tasks: 3 } };
  const view = { sessions: [session], tasks: [
    { id: 'blocked-specialist', session_id: session.id, status: 'blocked',
      brief: { role: 'methodology_specialist', key: 'recover_generation', objective: 'Continue the saved local-model findings.' },
      dependencies: ['partial-generation'], error: 'A prerequisite did not complete. The campaign manager must revise this assignment.' },
    { id: 'partial-generation', session_id: session.id, status: 'handed_off',
      brief: { role: 'methodology_specialist', key: 'local_model_generation', objective: 'Generate a local-model proposal.' } },
    ...Array.from({ length: 40 }, (_, i) => ({ id: `history-${i}`, session_id: session.id, status: 'completed',
      brief: { role: 'source_analyst', objective: `Earlier study ${i}` }, result: { summary: 'Saved literature evidence.' } })),
  ], artifacts: [{ id: 'saved-result', session_id: session.id, kind: 'synthesis', title: 'Saved research findings', content: {} }],
    handoffs: [{ id: 'saved-handoff', session_id: session.id, objective: 'Finish local-model generation', summary: 'Partial findings retained.' }],
    assessments: [{ id: 'saved-assessment', session_id: session.id, plan: { question: 'Compare methods', seeds: [1], evaluations_per_trial: 10 }, configurations: [{}] }] };
  const writes: any[] = [];
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() === 'POST') {
      const command = request.postDataJSON(); writes.push(command);
      if (failFirstSend && writes.length === 1) return route.fulfill({ status: 503, json: { detail: 'Temporarily unavailable' } });
      return route.fulfill({ json: { id: command.id, status: 'completed', outcome: {} } });
    }
    if (path.includes('/api/v1/commands/')) return route.fulfill({ status: 404, json: { detail: 'No saved result yet' } });
    return route.fulfill({ json: path.endsWith('/discovery') ? view : state });
  });
  await page.goto('/#notebook/discovery');
  await expect(page.getByRole('button', { name: 'Discuss this task', exact: true }).first()).toBeVisible();
  return writes;
}

test('discussing a blocked task opens a focused editor above a long agenda and sends its blocker and prerequisite', async ({ page }) => {
  const writes = await existingDiscovery(page);
  await expect(page.getByLabel('Discovery message to manager')).not.toBeInViewport();
  await page.getByRole('button', { name: 'Discuss this task', exact: true }).first().click();
  const discussion = page.getByRole('dialog'), editor = discussion.getByLabel('Discovery message to manager');
  await expect(editor).toBeInViewport();
  await expect(editor).toBeFocused();
  await expect(discussion).toContainText('A prerequisite did not complete.');
  await expect(discussion).toContainText('local model generation · handed off');
  expect(writes).toHaveLength(0);
  await editor.fill('Please revise this assignment to continue from the saved handoff.');
  await discussion.getByRole('button', { name: 'Send to manager', exact: true }).click();
  await expect(discussion.getByRole('status')).toContainText('Discovery is paused');
  expect(writes).toHaveLength(1);
  expect(writes[0].operation).toBe('research.start');
  expect(writes[0].payload.mode).toBe('discuss');
  expect(writes[0].payload.message).toContain('Regarding blocked-specialist:');
  expect(writes[0].payload.message).toContain('Reported blocker: A prerequisite did not complete.');
  expect(writes[0].payload.message).toContain('Prerequisite partial-generation: handed_off');
  expect(writes[0].payload.message).toContain('Please revise this assignment');
  await discussion.getByRole('button', { name: 'Done', exact: true }).click();
  await expect(discussion).toHaveCount(0);
});

test('discussion errors stay visible with the draft and a retry reuses the saved command', async ({ page }) => {
  const writes = await existingDiscovery(page, true);
  await page.getByRole('button', { name: 'Discuss this task', exact: true }).first().click();
  const discussion = page.getByRole('dialog'), editor = discussion.getByLabel('Discovery message to manager');
  await editor.fill('What is needed to continue?');
  await discussion.getByRole('button', { name: 'Send to manager', exact: true }).click();
  await expect(discussion.getByRole('alert')).toContainText('Temporarily unavailable');
  await expect(editor).toHaveValue('What is needed to continue?');
  await discussion.getByRole('button', { name: 'Send to manager', exact: true }).click();
  await expect(discussion.getByRole('status')).toContainText('Message saved');
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
});

test('each saved item opens a discussion with its own reference, and cancelling sends nothing', async ({ page }) => {
  const writes = await existingDiscovery(page);
  const panel = page.getByRole('region', { name: 'Optimizer discovery' });
  for (const [kind, id, summary] of [
    ['continuation', 'saved-handoff', 'Continuation: Finish local-model generation'],
    ['result', 'saved-result', 'synthesis: Saved research findings'],
    ['assessment', 'saved-assessment', ''],
  ]) {
    if (summary) await panel.locator(':scope > details > summary').filter({ hasText: summary }).click();
    await panel.getByRole('button', { name: `Discuss this ${kind}`, exact: true }).click();
    const discussion = page.getByRole('dialog');
    await expect(discussion.getByRole('heading', { name: `Discuss this ${kind}` })).toBeVisible();
    await expect(discussion.getByLabel('Discovery message to manager')).toBeFocused();
    await discussion.locator('summary').filter({ hasText: 'Attached reference and context' }).click();
    await expect(discussion.locator('pre')).toContainText(`Regarding ${id}:`);
    await discussion.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(discussion).toHaveCount(0);
  }
  expect(writes).toHaveLength(0);
});
