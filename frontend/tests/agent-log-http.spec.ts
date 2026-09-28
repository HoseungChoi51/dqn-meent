import { test, expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';

test('actual HTTP role workflow is visible through the file-backed SSE panel', async ({ page, request }) => {
  const base = process.env.AGENT_LOG_HTTP_TEST_URL, output = process.env.AGENT_LOG_HTTP_OUTPUT;
  test.skip(!base || !output, 'Requires scripts/qualify_agent_log_http.py; provider outputs are fixtures.');
  await page.goto(base!);
  await page.getByRole('button', { name: 'Research notebook', exact: true }).click();
  await page.getByRole('button', { name: 'Agent log', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Agent work log' });
  await expect(panel).toContainText('trace.jsonl');
  await expect(page.getByLabel('Live JSONL content')).toContainText('provider.request');
  await expect.poll(async () => {
    const state = await (await request.get(`${base}/api/state`)).json();
    return state.research_runs[0]?.status;
  }, { timeout: 20_000 }).toBe('completed');
  await expect(page.getByLabel('Live JSONL content')).toContainText('research_synthesizer');
  await expect(page.getByLabel('Live JSONL content')).toContainText('message.handoff');
  await page.screenshot({ path: join(output!, 'agent-log.png'), fullPage: true });
  await writeFile(join(output!, 'browser-view.txt'), await panel.innerText());
  await panel.getByRole('button', { name: 'Pause scrolling' }).click();
  await expect(panel).toContainText('Paused view');
  await page.reload();
  await page.getByRole('button', { name: 'Agent log', exact: true }).click();
  await expect(page.getByLabel('Live JSONL content')).toContainText('message.handoff');
});
