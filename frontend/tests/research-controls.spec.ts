import { test, expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

test('research, decisions and sources recover accepted replies after a browser reload', async ({ page, request }, info) => {
  const base = process.env.RESEARCH_CONTROLS_TEST_URL;
  test.skip(!base, 'Requires the isolated research-controls fixture with mocked inference and retrieval.');
  test.setTimeout(90_000);
  const writes: any[] = [], lookups: string[] = [];
  const lose = new Set(['research.control', 'research.start', 'decision.resolve', 'source.ingest', 'literature.search']);
  page.on('request', req => {
    if (req.method() === 'GET' && new URL(req.url()).pathname.startsWith('/api/v1/commands/')) lookups.push(req.url().split('/').at(-1)!);
  });
  await page.route('**/api/v1/commands', async route => {
    const body = route.request().postDataJSON();
    writes.push(body);
    if (lose.delete(body.operation)) {
      const accepted = await route.fetch();
      expect(accepted.ok(), await accepted.text()).toBeTruthy();
      return route.abort('failed');
    }
    return route.continue();
  });
  async function state() { return (await request.get(`${base}/api/state`)).json(); }
  async function recover() {
    await expect(page.getByRole('region', { name: 'Unconfirmed actions' }).getByRole('button', { name: 'Check result' })).toBeVisible();
    const id = writes.at(-1).id;
    await page.reload();
    const pending = page.getByRole('region', { name: 'Unconfirmed actions' });
    await pending.getByRole('button', { name: 'Check result' }).click();
    await expect(pending.getByRole('button', { name: 'Check result' })).not.toBeVisible();
    expect(lookups).toContain(id);
    expect(writes.filter(item => item.id === id)).toHaveLength(1);
  }
  await page.goto(base!);
  const initial = await state();
  await page.getByRole('button', { name: 'Research notebook', exact: true }).click();
  await page.getByRole('button', { name: 'Agent runs', exact: true }).click();
  await page.getByRole('button', { name: 'Resume research', exact: true }).click();
  await recover();
  await page.getByRole('button', { name: 'Agent runs', exact: true }).click();
  await page.locator('main').getByRole('button', { name: 'Stop research', exact: true }).click();
  await expect.poll(async () => (await state()).research_runs[0].control_revision).toBe(2);
  const stale = await request.post(`${base}/api/v1/commands`, { data: { ...writes[0], id: 'stale_fixture_resume' } });
  expect(stale.status()).toBe(409);

  const manager = page.getByRole('complementary', { name: 'Research conversation' });
  await manager.locator('textarea').fill('Retain the current comparison and wait for my next direction.');
  await manager.getByRole('button', { name: 'Send research message' }).click();
  await recover();
  expect((await state()).manager_commands).toHaveLength(initial.manager_commands.length + 1);

  await page.getByRole('button', { name: /^Decision inbox/ }).click();
  await page.getByLabel('Defer this comparison', { exact: true }).check();
  await page.getByLabel('Your reasoning (optional)').fill('Keep this rationale through a lost reply.');
  await page.getByRole('button', { name: 'Record decision', exact: true }).click();
  await recover();
  expect((await state()).decisions[0]).toMatchObject({ status: 'resolved', resolution_revision: 1,
    comment: 'Keep this rationale through a lost reply.' });

  await page.getByRole('button', { name: 'Research notebook', exact: true }).click();
  await page.getByRole('button', { name: 'Source library', exact: true }).click();
  await page.getByLabel('Add a paper by DOI or URL').fill('https://example.org/paper');
  await page.getByRole('button', { name: 'Retrieve paper', exact: true }).click();
  await recover();
  await expect.poll(async () => (await state()).sources.length).toBe(1);
  await page.getByRole('button', { name: 'Source library', exact: true }).click();
  await page.getByLabel('Search scientific literature').fill('Specialized bounded optimizer');
  await page.getByRole('button', { name: 'Search sources', exact: true }).click();
  await recover();
  await expect.poll(async () => (await state()).source_requests.every((item: any) => item.status === 'completed')).toBe(true);
  const final = await state();
  expect(final.research_runs).toHaveLength(1); // Queued direction cannot create a concurrent manager turn.
  expect(final.trials).toHaveLength(0);
  const accepted = [];
  for (const command of writes) accepted.push(await (await request.get(`${base}/api/v1/commands/${command.id}`)).json());
  await writeFile(info.outputPath('research-controls-evidence.json'), JSON.stringify({ writes, lookups, accepted,
    final, model_calls: 0, mocked: ['manager inference', 'source retrieval'] }, null, 2));
  await page.screenshot({ path: info.outputPath('research-controls.png'), fullPage: true });
});
