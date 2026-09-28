import { test, expect } from '@playwright/test';

test('parallel reviewer children remain inspectable while stop and retry belong to the parent reassessment', async ({ page }) => {
  const campaign = { id: 'parallel-controls', version: 1, name: 'Grating mechanism comparison', autonomy: 'guided',
    objective: 'Develop a suitable optimizer', compute_budget_seconds: 3600, llm_budget_usd: 0 };
  const parent: any = { id: 'review-parent', status: 'running', control_revision: 3, created_at: '2026-09-28T10:00:00Z',
    decision_review: { phase: 'reviewing', task_ids: ['review-a', 'review-b', 'review-c'], max_parallel_reviews: 3 },
    request: { message: 'Reassess comparison designs and allocation.' } };
  const children = ['running', 'interrupted', 'failed'].map((status, index) => ({ id: `review-child-${index}`,
    status, parent_review_run_id: parent.id, review_task_id: `review-${index}`, control_revision: 0,
    error: status === 'failed' ? 'One private reviewer failed while the others continue.' : undefined,
    request: { message: parent.request.message } }));
  const state: any = { workspace_id: 'parallel-controls-workspace', campaign, campaigns: [campaign], tasks: [], trials: [],
    hypotheses: [], decisions: [], messages: [], events: [], algorithms: [], manager_issues: [], manager_commands: [],
    research_runs: [parent, ...children], settings: { llm_configured: true, provider: {
      configured: true, enabled: true, provider: 'codex', model: 'gpt-6-astra', billing_mode: 'subscription' } } };
  const writes: any[] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      constructor() { super(); (window as any).testStream = this; } close() {}
    };
  });
  await page.route('**/api/**', async route => {
    const request = route.request();
    if (request.method() === 'POST') {
      const command = request.postDataJSON(); writes.push(command);
      const run = state.research_runs.find((item: any) => item.id === command.payload.run_id);
      run.status = command.payload.action === 'stop' ? 'stopping' : 'running';
      return route.fulfill({ json: { id: command.id, status: 'completed', outcome: {} } });
    }
    if (new URL(request.url()).pathname.endsWith('/discovery')) return route.fulfill({ json: { sessions: [] } });
    return route.fulfill({ json: state });
  });
  await page.goto('/#notebook/research');
  const manager = page.getByRole('complementary', { name: 'Research conversation' });
  await expect(manager.locator('.research-working')).toHaveCount(1);
  await expect(manager.getByRole('status')).toHaveText('Parallel reviewers working');
  await expect(manager.getByRole('button', { name: 'Stop decision reassessment', exact: true })).toHaveCount(1);
  await expect(manager.getByRole('button', { name: 'Stop research run', exact: true })).toHaveCount(0);
  await expect(manager).not.toContainText(children[2].error!);
  const notebook = page.locator('main');
  await expect(notebook.getByRole('button', { name: 'Stop decision reassessment', exact: true })).toHaveCount(1);
  await expect(notebook.getByRole('button', { name: 'Resume research', exact: true })).toHaveCount(0);
  await expect(notebook.getByText('Comparative reviewer', { exact: true })).toHaveCount(3);
  for (const child of children) {
    const row = notebook.locator(`[data-run-id="${child.id}"]`);
    await expect(row.getByRole('button')).toHaveCount(0);
    await expect(row.getByRole('link', { name: 'View parent reassessment', exact: true })).toHaveAttribute('href', '#decisions');
  }
  const failed = notebook.locator(`[data-run-id="${children[2].id}"]`);
  await failed.getByText('Inspect research record', { exact: true }).click();
  await expect(failed.locator('pre')).toContainText('"parent_review_run_id": "review-parent"');
  await expect(failed.locator('pre')).toContainText(children[2].error!);

  parent.decision_review.phase = 'synthesizing';
  children.forEach(child => { child.status = 'completed'; });
  await page.evaluate(() => (window as any).testStream.dispatchEvent(new Event('update')));
  await expect(manager.getByRole('status')).toHaveText('Manager consolidating');
  await manager.getByRole('button', { name: 'Stop decision reassessment', exact: true }).click();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ operation: 'research.control', payload: {
    run_id: parent.id, action: 'stop', expected_control_revision: 3 } });
  await expect(manager.getByRole('status')).toHaveText('Stopping decision reassessment');
  await expect(manager.getByRole('button', { name: 'Stop decision reassessment', exact: true })).toBeDisabled();

  parent.status = 'interrupted';
  parent.decision_review.phase = 'partial';
  await page.evaluate(() => (window as any).testStream.dispatchEvent(new Event('update')));
  await expect(manager.locator('.research-working')).toHaveCount(0);
  const parentRow = notebook.locator(`[data-run-id="${parent.id}"]`);
  await expect(parentRow.getByRole('button')).toHaveCount(0);
  await expect(parentRow.getByRole('link', { name: 'Review or retry in Decision inbox', exact: true })).toHaveAttribute('href', '#decisions');

  state.research_runs.push({ id: 'legacy-interrupted', status: 'interrupted', mode: 'discuss', control_revision: 2 });
  await page.evaluate(() => (window as any).testStream.dispatchEvent(new Event('update')));
  await notebook.getByRole('button', { name: 'Resume research', exact: true }).click();
  expect(writes).toHaveLength(2);
  expect(writes[1]).toMatchObject({ operation: 'research.control', payload: {
    run_id: 'legacy-interrupted', action: 'resume', expected_control_revision: 2 } });
  expect(writes.every(command => !command.payload.run_id.startsWith('review-child-'))).toBe(true);
});
