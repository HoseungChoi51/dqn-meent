import { test, expect } from '@playwright/test';

test('slow state reads coalesce polls, refresh after a command, and cannot overwrite a campaign switch', async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget { close() {} };
  });
  const campaigns = ['a', 'b'].map(id => ({ id, version: 1, name: `Campaign ${id.toUpperCase()}`,
    objective: 'Compare optimizer mechanisms', autonomy: 'guided', compute_budget_seconds: 3600, llm_budget_usd: 0 }));
  const choice = { id: 'decision-a', title: 'Retain this comparison', status: 'pending', context: 'A scientific direction.',
    resolution_revision: 0, options: [{ id: 'defer', label: 'Defer this comparison' }], recommendation: 'defer' };
  let reads = 0, writes = 0;
  const releases = new Map<number, () => void>();
  const held = new Set([1, 2, 4]);
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (request.method() === 'POST') {
      const command = request.postDataJSON();
      expect(command.operation).toBe('decision.resolve');
      writes++;
      Object.assign(choice, { status: 'resolved', choice: command.payload.choice, comment: command.payload.comment });
      return route.fulfill({ json: { id: command.id, status: 'completed', outcome: {} } });
    }
    if (url.pathname.endsWith('/state')) {
      const index = ++reads;
      const campaign = campaigns.find(item => item.id === url.searchParams.get('campaign_id')) ?? campaigns[0];
      // Capture the snapshot before waiting, just as a server read may precede
      // a later mutation even though its HTTP response arrives afterwards.
      const snapshot = structuredClone({ workspace_id: 'refresh-workspace', campaign, campaigns, tasks: [], hypotheses: [],
        trials: [], decisions: campaign.id === 'a' ? [choice] : [], messages: [], events: [], research_runs: [], algorithms: [],
        settings: { llm_configured: false } });
      if (held.has(index)) await new Promise<void>(resolve => releases.set(index, resolve));
      return route.fulfill({ json: snapshot });
    }
    if (url.pathname.endsWith('/discovery')) return route.fulfill({ json: { sessions: [] } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#decisions');
  await expect.poll(() => releases.has(1)).toBe(true);
  await page.clock.fastForward(13_000);
  expect(reads).toBe(1); // Multiple polling intervals must not invalidate this read.
  releases.get(1)!();
  await expect(page.getByRole('heading', { name: 'Opening the research workspace' })).not.toBeVisible();
  await expect(page.getByRole('heading', { name: choice.title })).toBeVisible();

  await page.clock.fastForward(6000);
  await expect.poll(() => releases.has(2)).toBe(true);
  const card = page.getByRole('article', { name: choice.title });
  await card.getByRole('radio', { name: 'Defer this comparison', exact: true }).check();
  await card.getByLabel('Your reasoning (optional)').fill('Preserve this scientific rationale.');
  await card.getByRole('button', { name: 'Record decision', exact: true }).click();
  await expect.poll(() => writes).toBe(1);
  expect(reads).toBe(2); // Post-command refresh waits for the old poll, then rereads.
  releases.get(2)!();
  await expect.poll(() => reads).toBe(3);
  await expect(card).not.toBeVisible();
  await page.getByRole('button', { name: 'Decision history', exact: true }).click();
  await expect(page.getByText('Preserve this scientific rationale.', { exact: true })).toBeVisible();

  await page.clock.fastForward(6000);
  await expect.poll(() => releases.has(4)).toBe(true);
  await page.getByLabel('Active campaign').selectOption('b');
  await expect.poll(() => reads).toBe(5);
  await expect(page.getByLabel('Active campaign')).toHaveValue('b');
  releases.get(4)!();
  await expect(page.getByLabel('Active campaign')).toHaveValue('b');
  await expect(page.getByText('Preserve this scientific rationale.', { exact: true })).not.toBeVisible();
  expect(writes).toBe(1);
});
