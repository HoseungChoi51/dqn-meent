import { test, expect } from '@playwright/test';

test('agent log follows real event shapes, pauses the view, catches up and filters without writes', async ({ page }) => {
  const campaign = { id: 'campaign-log', name: 'Log campaign', version: 1, autonomy: 'guided', objective: 'Debug observable work', compute_budget_seconds: 100, llm_budget_usd: 1 };
  const state = { workspace_id: 'log-fixture', campaign, campaigns: [campaign], tasks: [], trials: [], hypotheses: [], decisions: [],
    messages: [], events: [], algorithms: [], research_runs: [], settings: { llm_configured: false }, budget: {} };
  const events = Array.from({ length: 3 }, (_, n) => ({ schema_version: 1, seq: n + 1, event_id: `event-${n + 1}`,
    agent_id: n === 2 ? 'reviewer' : 'analyst', role: n === 2 ? 'reviewer' : 'analyst', task_id: 'task-1',
    event_type: n === 2 ? 'message.handoff' : 'agent.report', summary: `Report ${n + 1}`, payload: { text: `evidence-${n + 1}` } }));
  const writes: string[] = [];
  await page.addInitScript(() => {
    const instances: any[] = [];
    (window as any).__streams = instances;
    (window as any).__newEvent = (event: any) => {
      instances.filter(stream => !stream.closed && stream.url.includes('/agent-log/')).forEach(stream =>
        stream.dispatchEvent(new MessageEvent('line', { data: JSON.stringify(event), lastEventId: String(event.seq) })));
    };
    class Stream extends EventTarget {
      closed = false; onopen: any; onerror: any; onmessage: any;
      constructor(public url: string) { super(); instances.push(this); setTimeout(() => this.onopen?.({}), 5); }
      close() { this.closed = true; }
    }
    (window as any).EventSource = Stream;
  });
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url());
    if (req.method() !== 'GET') writes.push(url.pathname);
    if (url.pathname.endsWith('/agent-log')) {
      const selected = url.searchParams.has('limit') ? events.slice(-1) : events.slice(0, 2);
      return route.fulfill({ json: { events: selected, lines: selected.map(event => JSON.stringify(event) + '\n'), path: '/workspace/campaigns/campaign-log/agents/trace.jsonl',
        latest_seq: events.length, projected_seq: events.length, lag: 0, error: null, next_cursor: selected.at(-1)!.seq } });
    }
    return route.fulfill({ json: state });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Research notebook', exact: true }).click();
  await page.getByRole('button', { name: 'Agent log', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Agent work log' });
  const content = page.getByLabel('Live JSONL content');
  await expect(content).toContainText('evidence-2');
  await expect(panel).toContainText('trace.jsonl');
  await page.evaluate(event => (window as any).__newEvent(event), events[2]);
  await expect(content).toContainText('evidence-3');
  await panel.getByLabel('Agent', { exact: true }).selectOption('reviewer');
  await expect(content).not.toContainText('evidence-1');
  await panel.getByLabel('Agent', { exact: true }).selectOption('');
  await panel.getByRole('button', { name: 'Pause scrolling' }).click();
  await expect(panel).toContainText('Paused view');
  events.push({ ...events[0], seq: 4, event_id: 'event-4', summary: 'Later result', payload: { text: 'evidence-4' } });
  await page.evaluate(event => (window as any).__newEvent(event), events[3]);
  await expect(content).not.toContainText('evidence-4');
  await expect(panel).toContainText('1 new events');
  await panel.getByRole('button', { name: 'Resume live' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__streams.filter((s: any) => !s.closed && s.url.includes('/agent-log/')).at(-1)?.url)).toContain('after=3');
  await page.evaluate(event => (window as any).__newEvent(event), events[3]);
  await page.evaluate(event => (window as any).__newEvent(event), events[3]); // replay is deduplicated
  await expect(content).toContainText('evidence-4');
  await expect(panel).toContainText('4 matching / 4 loaded events');
  await panel.getByLabel('Search loaded lines').fill('handoff');
  await expect(content).toContainText('evidence-3');
  await expect(content).not.toContainText('evidence-4');
  expect(writes).toEqual([]);
});
