import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const question = 'What should I do to make an even rough decision of which method to proceed to the final validation?';
function fixture() {
  const campaign = { id: 'manager-replies', version: 3, name: 'Silicon grating inverse design', autonomy: 'guided',
    objective: 'Choose a method for final validation', compute_budget_seconds: 3600, llm_budget_usd: 0 };
  return { workspace_id: 'reply-workspace', campaign, campaigns: [campaign], tasks: [], trials: [], hypotheses: [],
    decisions: [], events: [], algorithms: [], manager_issues: [], research_runs: [], budget: {},
    messages: [{ id: 'message_turn-question', role: 'user', content: question, manager_command_id: 'turn-question' }],
    manager_commands: [{ id: 'turn-question', status: 'queued', request: { message: question, mode: 'discuss' } }],
    settings: { llm_configured: true, provider: { configured: true, enabled: true, provider: 'codex',
      model: 'gpt-6-astra', billing_mode: 'subscription' } } } as any;
}

async function mock(page: Page, state: any, write?: (body: any) => any) {
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      constructor() { super(); (window as any).workspaceStream = this; }
      close() {}
    };
  });
  await page.route('**/api/**', async route => {
    const request = route.request();
    if (request.method() === 'POST') {
      const body = request.postDataJSON();
      return route.fulfill(await write?.(body) || { json: { id: body.id, status: 'completed', outcome: {} } });
    }
    return route.fulfill({ json: state });
  });
  await page.goto('/');
}
async function update(page: Page) {
  await page.evaluate(() => (window as any).workspaceStream.dispatchEvent(new Event('update')));
}

test('a blocked saved question shows its own error and retries without posting the question again', async ({ page }) => {
  const state = fixture(), writes: any[] = [];
  const error = 'Task context exceeds the bounded allowance; split the task or request smaller passage batches';
  Object.assign(state.manager_commands[0], { status: 'blocked', error });
  state.manager_issues = [{ id: 'context-issue', code: 'manager_request', affected: 'turn-question', status: 'pending', message: error }];
  state.research_runs = [{ id: 'previous-failure', status: 'failed', error: 'Historical provider failure' }];
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await mock(page, state, async body => {
    writes.push(body); await pending;
    state.manager_commands[0].status = 'queued';
    delete state.manager_commands[0].error;
  });
  const panel = page.getByRole('complementary', { name: 'Research conversation' });
  const message = panel.locator('.message.user');
  const status = message.locator('[data-manager-command-id="turn-question"]');
  await expect(message).toContainText(question);
  await expect(status).toContainText('Request blocked before the manager could answer');
  await expect(status).toContainText('No model call started for this request.');
  await expect(panel.getByText(error, { exact: true })).toHaveCount(1);
  await expect(panel).not.toContainText('Historical provider failure');
  await expect(status.getByRole('link', { name: 'View agent log' })).toHaveAttribute('href', '#notebook/agent-log');
  await status.getByRole('button', { name: 'Retry saved request', exact: true }).click();
  await expect(status.getByRole('button', { name: 'Queuing retry…' })).toBeDisabled();
  release();
  await expect(status).toHaveAttribute('data-request-status', 'queued');
  await expect(status).toContainText('Queued for manager');
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ campaign_id: state.campaign.id, expected_revision: 3,
    operation: 'research.retry', payload: { manager_command_id: 'turn-question' } });
  await expect(panel.locator('.message.user')).toHaveCount(1);

  state.manager_commands[0].status = 'dispatched';
  state.manager_commands[0].research_run_id = 'answer-run';
  state.research_runs.push({ id: 'answer-run', manager_command_id: 'turn-question', status: 'running', stage: 'compare' });
  await update(page);
  await expect(status).toContainText('compare in progress');
  await expect(status.getByRole('button', { name: 'Retry saved request' })).toHaveCount(0);
  state.research_runs[1].status = 'completed';
  state.messages.push({ id: 'answer', role: 'assistant', research_run_id: 'answer-run', origin: 'llm',
    content: 'Compare the leading methods on fresh matched seeds before final validation.' });
  await update(page);
  await expect(status).toContainText('Response available below');
  await expect(panel).toContainText('Compare the leading methods on fresh matched seeds');
  await expect(panel.locator('.research-working')).toHaveCount(0);
});

test('each question keeps its own status when an older run failed', async ({ page }) => {
  const state = fixture();
  state.messages.unshift({ id: 'message_turn-previous', role: 'user', content: question }); // Legacy ID linkage.
  state.manager_commands.unshift({ id: 'turn-previous', status: 'dispatched', research_run_id: 'old-run', request: { message: question } });
  state.research_runs.push({ id: 'old-run', manager_command_id: 'turn-previous', status: 'failed', error: 'Earlier model response failed validation.' });
  state.manager_commands[1].status = 'waiting_provider';
  await mock(page, state);
  const panel = page.getByRole('complementary', { name: 'Research conversation' });
  const old = panel.locator('[data-manager-command-id="turn-previous"]');
  const current = panel.locator('[data-manager-command-id="turn-question"]');
  await expect(old).toContainText('Manager response failed');
  await expect(old).toContainText('Earlier model response failed validation.');
  await expect(old.getByRole('button', { name: 'Retry saved request' })).toHaveCount(0);
  await expect(current).toContainText('Waiting for model configuration');
  await expect(current).not.toContainText('failed');
  await expect(panel.getByText('Earlier model response failed validation.', { exact: true })).toHaveCount(1);
});

test('a retry conflict refreshes status without silently creating another request', async ({ page }) => {
  const state = fixture(), writes: any[] = [];
  Object.assign(state.manager_commands[0], { status: 'blocked', error: 'Context allowance exceeded' });
  await mock(page, state, body => {
    writes.push(body);
    state.manager_commands[0].status = 'queued';
    return { status: 409, json: { detail: 'This saved request has already been retried.' } };
  });
  const status = page.locator('[data-manager-command-id="turn-question"]');
  await status.getByRole('button', { name: 'Retry saved request' }).click();
  await expect(status).toContainText('Queued for manager');
  await expect(status.getByRole('alert')).toContainText('This saved request has already been retried.');
  await expect(status.getByRole('button')).toHaveCount(0);
  expect(writes.map(body => body.operation)).toEqual(['research.retry']);
});

test('completed work without a conversational reply is not labelled answered', async ({ page }) => {
  const state = fixture();
  state.manager_commands[0].research_run_id = 'completed-run';
  state.manager_commands[0].status = 'dispatched';
  state.research_runs = [{ id: 'completed-run', manager_command_id: 'turn-question', status: 'completed' }];
  state.messages.push({ id: 'system', role: 'assistant', research_run_id: 'completed-run', origin: 'manager_system', content: 'Research finished.' });
  state.messages.push({ id: 'stale', role: 'assistant', research_run_id: 'completed-run', origin: 'llm', stale_charter: true, content: 'Earlier context response.' });
  await mock(page, state);
  const status = page.locator('[data-manager-command-id="turn-question"]');
  await expect(status).toContainText('No conversational reply was recorded.');
  await expect(status).not.toContainText('Response available below');
});
