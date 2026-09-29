import { expect, test } from '@playwright/test';

test('manager issue actions work without randomUUID and retain identity after a lost reply', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(crypto, 'randomUUID', { value: undefined, configurable: true });
    (window as any).EventSource = class extends EventTarget { close() {} };
  });
  const campaign = { id: 'http-campaign', version: 3, name: 'HTTP campaign', autonomy: 'manual',
    objective: 'Evaluate the 2D grating', compute_budget_seconds: 3600, llm_budget_usd: 0 };
  const issues = ['first', 'second'].map(id => ({ id, revision: 1, status: 'pending',
    message: `Resolved integration issue ${id}`, code: 'operation_blocked' }));
  const state = { workspace_id: 'http-workspace', campaign, campaigns: [campaign], tasks: [], trials: [],
    hypotheses: [], decisions: [], events: [], algorithms: [], research_runs: [], messages: [],
    manager_issues: issues, settings: { llm_configured: false } };
  const writes: any[] = [], lookups: string[] = [], receipts = new Map<string, any>();
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() === 'POST') {
      expect(path).toBe('/api/v1/commands');
      const body = request.postDataJSON();
      writes.push(body);
      expect(body.operation).toBe('issue.resolve');
      const issue = issues.find(item => item.id === body.payload.issue_id)!;
      issue.status = body.payload.choice;
      receipts.set(body.id, { id: body.id, request: body, actor: 'researcher', status: 'completed',
        outcome: { issue_id: issue.id } });
      if (writes.length === 1) return route.abort('failed');
      return route.fulfill({ json: receipts.get(body.id) });
    }
    if (path.startsWith('/api/v1/commands/')) {
      const id = path.split('/').at(-1)!;
      lookups.push(id);
      return route.fulfill({ json: receipts.get(id) });
    }
    if (path.endsWith('/discovery')) return route.fulfill({ json: { sessions: [] } });
    return route.fulfill({ json: state });
  });
  await page.goto('/');
  expect(await page.evaluate(() => typeof crypto.randomUUID)).toBe('undefined');
  const manager = page.getByRole('complementary', { name: 'Research conversation' });
  await manager.getByRole('button', { name: 'Mark resolved', exact: true }).first().click();
  await expect.poll(() => writes.length).toBe(1);
  const first = writes[0];
  expect(first.id).toMatch(/^ui_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(first).toMatchObject({ campaign_id: campaign.id, expected_revision: 3,
    payload: { issue_id: 'first', expected_revision: 1, choice: 'resolved' } });
  await expect(page.getByRole('region', { name: 'Unconfirmed actions' })).toBeVisible();
  await page.reload();
  const pending = page.getByRole('region', { name: 'Unconfirmed actions' });
  await pending.getByRole('button', { name: 'Check result' }).click();
  await expect(pending.getByRole('status')).toContainText('was recorded');
  expect(lookups).toEqual([first.id]);
  expect(writes).toHaveLength(1);
  await manager.getByRole('button', { name: 'Mark resolved', exact: true }).click();
  await expect(manager.getByRole('button', { name: 'Mark resolved', exact: true })).toHaveCount(0);
  expect(writes).toHaveLength(2);
  expect(writes[1].id).not.toBe(first.id);
  expect(issues.every(issue => issue.status === 'resolved')).toBe(true);
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('optimization.commands.v1:')))).toEqual([]);
});
