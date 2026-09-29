import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const stamp = '2026-09-28T08:00:00Z';
function fixture() {
  const campaign = { id: 'progress-campaign', version: 1, name: 'Grating inverse design', autonomy: 'guided',
    objective: 'Develop an optimizer for this problem', compute_budget_seconds: 100, llm_budget_usd: 0 };
  return { workspace_id: 'progress-workspace', campaign, campaigns: [campaign], tasks: [], trials: [], hypotheses: [],
    decisions: [], messages: [], events: [], algorithms: [], manager_issues: [], manager_commands: [], research_runs: [],
    settings: { llm_configured: true, provider: { configured: true, enabled: true, provider: 'codex', model: 'gpt-6-astra' } },
    budget: {}, research_progress: { status: 'paused', headline: 'Discovery is paused', message: 'No agents are working. Resume discovery to process saved requests.',
      active: false, can_resume: false, session: { id: 'discovery-session', status: 'paused', control_revision: 7 },
      request: null, agents: [], updated_at: stamp,
      task_counts: { total: 0, queued: 0, running: 0, waiting: 0, completed: 0, failed: 0, blocked: 0, cancelled: 0, superseded: 0 } } } as any;
}

async function mock(page: Page, state: any, write?: (body: any) => any) {
  await page.addInitScript(() => {
    const streams: any[] = [];
    (window as any).__updateWorkspace = () => streams.forEach(stream => stream.dispatchEvent(new MessageEvent('update')));
    (window as any).EventSource = class extends EventTarget {
      onopen: any; constructor(public url: string) { super(); streams.push(this); setTimeout(() => this.onopen?.({}), 1); }
      close() {}
    };
  });
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() === 'POST') {
      const body = request.postDataJSON();
      const result = await write?.(body);
      return route.fulfill(result || { json: { id: body.id, status: 'completed', outcome: {} } });
    }
    if (path.endsWith('/agent-log')) return route.fulfill({ json: { events: [], lines: [], path: 'agents/trace.jsonl', next_cursor: 0, latest_seq: 0, projected_seq: 0, lag: 0 } });
    return route.fulfill({ json: state });
  });
}
async function update(page: Page) { await page.evaluate(() => (window as any).__updateWorkspace()); }

function awaitingDirection() {
  const state = fixture();
  state.research_progress = { ...state.research_progress, status: 'waiting',
    headline: 'Campaign manager is waiting for your direction', message: 'Read the manager’s request below and send your reply here.',
    session: { id: 'discovery-session', status: 'waiting_for_direction', control_revision: 7 },
    request: { id: 'previous-user-message', message: 'Implement H12.', status: 'completed' },
    direction_request: { id: 'manager_task_step_5', session_id: 'discovery-session', task_id: 'manager_task', kind: 'questions',
      summary: 'The H12 implementation handoff is saved. Please confirm the method and choose an allocation.',
      questions: ['Does H12 mean Mask-aware tangent-space covariance adaptation?', 'What implementation allocation do you authorize?'] } };
  return state;
}

test('the notebook shows the manager request with a direct reply form and queues the answer once', async ({ page }) => {
  const state = awaitingDirection(), writes: any[] = [];
  await mock(page, state, body => {
    writes.push(body);
    state.research_progress = { ...state.research_progress, status: 'queued', headline: 'Request queued',
      message: 'The campaign manager will process your reply.', direction_request: null,
      session: { ...state.research_progress.session, status: 'running' }, task_counts: { total: 1, queued: 1 } };
    return { json: { id: body.id, status: 'completed', outcome: { manager_command_id: 'reply-request' } } };
  });
  await page.goto('/#notebook');
  const request = page.getByRole('region', { name: 'Manager request and reply' });
  await expect(request).toContainText('Does H12 mean Mask-aware tangent-space covariance adaptation?');
  await expect(request).toContainText('What implementation allocation do you authorize?');
  await expect(page.getByRole('region', { name: 'Campaign research progress' })).toContainText('Your latest request:');
  const reply = request.getByLabel('Your reply to the campaign manager');
  await expect(reply).toBeInViewport();
  await expect(request.getByRole('button', { name: 'Send reply', exact: true })).toBeDisabled();
  await reply.fill('Yes, that is H12. Explain the required allocation before starting implementation.');
  await request.getByRole('button', { name: 'Send reply', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Campaign research progress' })).toContainText('Reply saved for the campaign manager.');
  await expect(request).toHaveCount(0);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ operation: 'research.start', payload: { mode: 'discuss' } });
  expect(writes[0].payload.message).toContain('manager_task_step_5');
  expect(writes[0].payload.message).toContain('discovery-session');
  expect(writes[0].payload.message).toContain('Yes, that is H12. Explain the required allocation');
});

test('a failed direction reply keeps the request and draft visible and retries the same saved command', async ({ page }) => {
  const state = awaitingDirection(), writes: any[] = [];
  await mock(page, state, body => {
    writes.push(body);
    if (writes.length === 1) return { status: 503, json: { detail: 'Temporarily unavailable' } };
    return { json: { id: body.id, status: 'completed', outcome: {} } };
  });
  await page.route('**/api/v1/commands/*', route => route.fulfill({ status: 404, json: { detail: 'No saved result yet' } }));
  await page.goto('/#notebook');
  const request = page.getByRole('region', { name: 'Manager request and reply' });
  const reply = request.getByLabel('Your reply to the campaign manager');
  await reply.fill('Please clarify the implementation allocation.');
  await update(page);
  await expect(reply).toHaveValue('Please clarify the implementation allocation.');
  await request.getByRole('button', { name: 'Send reply', exact: true }).click();
  await expect(request.getByRole('alert')).toContainText('Temporarily unavailable');
  await expect(reply).toHaveValue('Please clarify the implementation allocation.');
  await request.getByRole('button', { name: 'Send reply', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Campaign research progress' })).toContainText('Reply saved');
  await expect(request.getByRole('button', { name: 'Send reply', exact: true })).toHaveCount(0);
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
});

test('waiting without a saved question offers direction without inventing a request', async ({ page }) => {
  const state = awaitingDirection();
  state.research_progress.direction_request = { ...state.research_progress.direction_request,
    kind: 'open_direction', summary: 'Assigned work has finished. Choose the next focus.', questions: [] };
  await mock(page, state);
  await page.goto('/#notebook');
  const request = page.getByRole('region', { name: 'Manager request and reply' });
  await expect(request).toContainText('The manager did not leave a specific question.');
  await expect(request.getByLabel('Your reply to the campaign manager')).toBeVisible();
  await expect(request).not.toContainText('Does H12 mean');
});

test('allocation wrap-up shows saved handoffs without retrying or claiming success', async ({ page }) => {
  const state = fixture(), writes: any[] = [];
  state.research_progress = { ...state.research_progress, status: 'waiting',
    headline: 'Discovery wrapped up at its allocation',
    message: 'Partial findings and continuation notes are saved. Review them with the campaign manager; further research needs an allocation change.',
    session: { id: 'discovery-session', status: 'waiting_for_direction', control_revision: 7 },
    task_counts: { total: 3, completed: 2, handed_off: 1 },
    agents: [{ task_id: 'partial', role: 'source_analyst', stage: 'study', status: 'handed_off', active: false,
      activity: 'Partial findings and remaining work saved for the campaign manager.' }] };
  await mock(page, state, body => { writes.push(body); });
  await page.goto('/#hypotheses');
  const progress = page.getByRole('region', { name: 'Campaign research progress' });
  await expect(progress).toContainText('Discovery wrapped up at its allocation');
  await expect(progress).toContainText('2 completed');
  await expect(progress).toContainText('1 handed off');
  await expect(progress).not.toContainText('Research work is complete');
  await expect(progress.getByRole('button', { name: 'Retry failed tasks' })).toHaveCount(0);
  await expect(progress.locator('.research-progress-spinner')).toHaveCount(0);
  expect(writes).toHaveLength(0);
});

test('Develop strategies shows the paused queue before and after submission; Resume is explicit and revision guarded', async ({ page }) => {
  const state = fixture(), writes: any[] = [];
  await mock(page, state, body => {
    writes.push(body);
    if (body.operation === 'research.start') {
      const request = { id: 'turn-proposal', status: 'waiting_discovery', created_at: stamp, message: body.payload.message };
      state.research_progress.request = request; state.research_progress.can_resume = true;
      state.manager_commands = [{ ...request, request: body.payload }];
      return { json: { id: body.id, status: 'completed', outcome: { manager_command_id: request.id } } };
    }
    state.research_progress = { ...state.research_progress, status: 'queued', headline: 'Request queued for campaign manager',
      message: 'Waiting for the manager to dispatch work.', can_resume: false, session: { id: 'discovery-session', status: 'running', control_revision: 8 } };
    state.manager_commands[0].status = 'admitted';
  });
  await page.goto('/#hypotheses');
  const progress = page.getByRole('region', { name: 'Campaign research progress' });
  await expect(progress).toBeVisible();
  await expect(progress).toContainText('No agents are working');
  await expect(progress.locator('.research-progress-spinner')).toHaveCount(0);
  await page.getByRole('button', { name: 'Develop strategies', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Discovery is paused');
  await dialog.getByLabel('Direction or constraints').fill('Algorithms with reliable implementations');
  await dialog.getByRole('button', { name: 'Send to campaign manager' }).click();
  await expect(progress).toContainText('Your latest request:');
  await expect(progress).toContainText('Algorithms with reliable implementations');
  await expect(page.locator('.toast')).toContainText('Request saved. Discovery is paused');
  expect(writes).toHaveLength(1);
  await expect(progress.locator('.research-progress-spinner')).toHaveCount(0);
  await progress.getByRole('button', { name: 'Resume discovery', exact: true }).click();
  await expect(progress).toContainText('Request queued for campaign manager');
  expect(writes[1]).toMatchObject({ operation: 'discovery.control', payload: { session_id: 'discovery-session', action: 'resume', expected_control_revision: 7 } });
  await expect(progress.getByRole('button', { name: 'Resume discovery', exact: true })).toHaveCount(0);
  await expect(progress.locator('.research-progress-spinner')).toHaveCount(0);
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  await expect(progress).toBeVisible();
});

test('active calls show live elapsed time, model and evidence heartbeat; completed and blocked work stops animating', async ({ page }) => {
  await page.clock.install({ time: new Date(Date.parse(stamp) - 60_000) });
  const state = fixture();
  state.research_progress = { ...state.research_progress, status: 'running', headline: 'Campaign manager is working',
    message: 'A model call is in progress.', active: true, session: { id: 'discovery-session', status: 'running', control_revision: 8 },
    agents: [{ task_id: 'task-manager', role: 'campaign_manager', stage: 'synthesize', status: 'running', active: true,
      model: 'gpt-6-astra', reasoning_effort: 'xhigh', started_at: '2026-09-28T07:59:00Z', last_activity_at: '2026-09-28T07:59:50Z',
      activity: 'Provider process is alive; waiting for new output.' }],
    task_counts: { total: 2, completed: 1, running: 1, queued: 0 } };
  await mock(page, state);
  await page.goto('/#hypotheses');
  const progress = page.getByRole('region', { name: 'Campaign research progress' });
  await expect(progress).toContainText('gpt-6-astra · Extra high');
  await page.clock.pauseAt(new Date(stamp));
  await expect(progress).toContainText('Elapsed 1m 0s');
  await expect(progress).toContainText('Last recorded activity 10s ago');
  await expect(progress).toContainText('tasks in this discovery session');
  await expect(progress.locator('.research-progress-spinner')).toHaveCount(1);
  await page.clock.fastForward(2000);
  await expect(progress).toContainText('Elapsed 1m 2s');
  await expect(progress).not.toContainText('%');
  state.research_progress = { ...state.research_progress, status: 'completed', headline: 'Requested work completed', active: false, agents: [],
    message: 'The latest written review is available.', task_counts: { total: 2, completed: 2, running: 0, queued: 0 } };
  await update(page); await page.clock.fastForward(500);
  await expect(progress).toContainText('Requested work completed');
  await expect(progress.locator('.research-progress-spinner')).toHaveCount(0);
  state.research_progress = { ...state.research_progress, status: 'blocked', headline: 'Research needs attention', message: 'Model call allowance exhausted.' };
  await update(page); await page.clock.fastForward(500);
  await expect(progress).toContainText('Model call allowance exhausted');
  await expect(progress.locator('.research-progress-spinner')).toHaveCount(0);
  await progress.getByRole('link', { name: 'View agent log' }).click();
  await expect(page).toHaveURL(/#notebook\/agent-log$/);
  await expect(page.getByRole('region', { name: 'Agent work log' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Agent log', exact: true })).toHaveClass('selected');
  await page.reload();
  await expect(page.getByRole('region', { name: 'Agent work log' })).toBeVisible();
});

test('resume failure is visible and retry uses refreshed control revision', async ({ page }) => {
  const state = fixture(), writes: any[] = [];
  state.research_progress.can_resume = true;
  await mock(page, state, body => {
    writes.push(body);
    if (writes.length === 1) {
      state.research_progress.session.control_revision = 8;
      return { status: 409, json: { detail: 'Discovery changed; review its current state and retry.' } };
    }
  });
  await page.goto('/#hypotheses');
  const progress = page.getByRole('region', { name: 'Campaign research progress' });
  await progress.getByRole('button', { name: 'Resume discovery', exact: true }).click();
  await expect(progress.getByRole('alert')).toContainText('Discovery changed');
  await expect(progress.getByRole('button', { name: 'Resume discovery', exact: true })).toBeEnabled();
  await progress.getByRole('button', { name: 'Resume discovery', exact: true }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes.map(body => body.payload.expected_control_revision)).toEqual([7, 8]);
});

function timeoutFailure(state: any, paused = false) {
  state.research_progress = { ...state.research_progress, status: paused ? 'paused' : 'waiting',
    headline: 'Research needs attention', message: 'Failed model calls need another attempt.', active: false, can_resume: paused,
    session: { id: 'discovery-session', status: paused ? 'paused' : 'running', control_revision: 11 },
    retryable_task_ids: ['task-manager-timeout', 'task-specialist-timeout'],
    request: { id: 'request-proposals', message: 'Request 3 proposals.', status: 'admitted', created_at: stamp },
    agents: [
      { task_id: 'task-manager-timeout', role: 'campaign_manager', stage: 'manage', status: 'failed', active: false,
        error_code: 'timeout', activity: 'Model call timed out after 120 seconds.', model: 'gpt-6-astra', reasoning_effort: 'xhigh' },
      { task_id: 'task-specialist-timeout', role: 'cross_domain_methodology_specialist', stage: 'study', status: 'failed', active: false,
        error_code: 'timeout', error_message: 'Model call timed out after 120 seconds.', model: 'gpt-6-luna', reasoning_effort: 'xhigh' },
    ], task_counts: { total: 4, failed: 2, waiting: 2, queued: 0, running: 0 } };
}

test('retry sends the failed tasks from this request and shows a waiting timeout without an activity spinner', async ({ page }) => {
  const state = fixture(), writes: any[] = [];
  timeoutFailure(state);
  await mock(page, state, body => {
    writes.push(body);
    state.research_progress.retryable_task_ids = [];
    state.research_progress.session.control_revision = 12;
    state.research_progress.status = 'queued';
  });
  await page.goto('/#hypotheses');
  const progress = page.getByRole('region', { name: 'Campaign research progress' });
  await expect(progress).toHaveAttribute('data-status', 'waiting');
  await expect(progress.locator('.research-progress-agents').getByText('Model call timed out after 120 seconds.', { exact: true })).toHaveCount(2);
  await expect(progress.locator('.research-progress-spinner')).toHaveCount(0);
  await progress.getByRole('button', { name: 'Retry failed tasks', exact: true }).click();
  await expect(progress).toContainText('Failed tasks were queued for another attempt.');
  await expect(progress.getByRole('button', { name: 'Retry failed tasks', exact: true })).toHaveCount(0);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ campaign_id: 'progress-campaign', expected_revision: 1, operation: 'discovery.retry',
    payload: { session_id: 'discovery-session', expected_control_revision: 11,
      task_ids: ['task-manager-timeout', 'task-specialist-timeout'], reason: 'Researcher retried failed tasks from progress panel' } });
  await expect(progress.locator('.research-progress-spinner')).toHaveCount(0);
});

test('paused retry stays paused and disables both retry and resume while the command is pending', async ({ page }) => {
  const state = fixture(), writes: any[] = [];
  timeoutFailure(state, true);
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  await mock(page, state, async body => {
    writes.push(body);
    await pending;
    state.research_progress.retryable_task_ids = [];
    state.research_progress.session.control_revision = 12;
  });
  await page.goto('/#hypotheses');
  const progress = page.getByRole('region', { name: 'Campaign research progress' });
  await expect(progress).toContainText('Discovery stays paused until you resume.');
  await progress.getByRole('button', { name: 'Retry failed tasks', exact: true }).click();
  await expect(progress.getByRole('button', { name: 'Queuing retry…', exact: true })).toBeDisabled();
  await expect(progress.getByRole('button', { name: 'Resume discovery', exact: true })).toBeDisabled();
  expect(writes).toHaveLength(1);
  finish();
  await expect(progress).toContainText('Failed tasks were queued for another attempt. Discovery is paused; resume when ready.');
  await expect(progress).toHaveAttribute('data-status', 'paused');
  await expect(progress.getByRole('button', { name: 'Resume discovery', exact: true })).toBeEnabled();
  expect(writes.map(body => body.operation)).toEqual(['discovery.retry']);
});

test('retry conflict refreshes the revision and waits for another explicit click', async ({ page }) => {
  const state = fixture(), writes: any[] = [];
  timeoutFailure(state);
  await mock(page, state, body => {
    writes.push(body);
    if (writes.length === 1) {
      state.research_progress.session.control_revision = 12;
      return { status: 409, json: { detail: 'Discovery changed; review its current state and retry.' } };
    }
    state.research_progress.retryable_task_ids = [];
  });
  await page.goto('/#hypotheses');
  const progress = page.getByRole('region', { name: 'Campaign research progress' });
  await progress.getByRole('button', { name: 'Retry failed tasks', exact: true }).click();
  await expect(progress.getByRole('alert')).toContainText('Discovery changed');
  await expect(progress.getByRole('button', { name: 'Retry failed tasks', exact: true })).toBeEnabled();
  expect(writes).toHaveLength(1);
  await progress.getByRole('button', { name: 'Retry failed tasks', exact: true }).click();
  await expect(progress.getByRole('button', { name: 'Retry failed tasks', exact: true })).toHaveCount(0);
  await expect(progress.getByRole('alert')).toHaveCount(0);
  expect(writes.map(body => body.payload.expected_control_revision)).toEqual([11, 12]);
  expect(new Set(writes.map(body => body.id)).size).toBe(2);
});
