import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

const now = '2026-09-29T00:00:00Z';
function fixture(): any {
  const campaign = { id: 'progress-campaign', name: 'Final comparison', objective: 'Compare finalists on fresh seeds.',
    active_study_id: 'confirmation-study', autonomy: 'guided', version: 1, compute_budget_seconds: 3600, validation_reserve_seconds: 180, llm_budget_usd: 0 };
  const methods = Object.fromEntries(['annealing', 'dqn', 'block_tabu', 'population', 'hillclimb'].map((algorithm, index) => [`method-${algorithm}`, {
    algorithm, algorithm_config: {}, max_steps: 50000, schedule_steps: 50000, wall_seconds: [1000, 1800, 1000, 10000, 10000][index],
    completion: { unit: 'evaluation_requests', count: 50000 },
  }]));
  const cells = Object.keys(methods).flatMap(method_id => [100, 101, 102].map(seed => ({ method_id, seed, instance_digest: 'grating-instance',
    instance_name: 'Silicon grating', trial_id: null, task_id: null, status: 'not_allocated', scientific_complete: false,
    evidence_complete: false, best_objective: 0.987654321, objective: { name: 'efficiency', units: 'fraction of incident power' },
    diagnostics: { complete: true, jobs: [], grants: [] }, required_validation: [] })));
  const assessment = { protocol_id: 'confirmation-protocol', study_id: campaign.active_study_id, campaign_id: campaign.id,
    cells, methods, kind: 'seed_replication', complete: false, required_recipes: [], release: null, report: null,
    interpretation: 'Descriptive confirmation evidence.' };
  const state = { workspace_id: 'progress-workspace', campaign, campaigns: [campaign], event_cursor: 1,
    tasks: [{ id: 'grating-task', name: 'Silicon grating', physics: {}, split: 'development', problem: { definition_id: 'meent_grating' } }],
    studies: [{ id: 'old-study', goal: 'Earlier exploration', scope: 'exploratory' }, { id: campaign.active_study_id,
      goal: 'Scientific confirmation question. '.repeat(20), scope: 'confirmation', comparison: { cost_axis: 'worker_seconds' },
      confirmation: { id: assessment.protocol_id, methods, prototypes: {}, seeds: [100, 101, 102], instances: [{}] } }],
    trials: [], hypotheses: [], decisions: [], events: [], messages: [], research_runs: [], algorithms: [],
    settings: { llm_configured: false }, budget: { allocated_seconds: 750, spent_seconds: 750, cap_seconds: 3600 } };
  return { state, assessment };
}

function addTrial(data: any, cellIndex: number, status: string, progress: any, extra: any = {}) {
  const cell = data.assessment.cells[cellIndex], method = data.assessment.methods[cell.method_id];
  const trial = { id: `trial_${cellIndex}`, campaign_id: data.state.campaign.id, study_id: 'confirmation-study',
    task_id: 'grating-task', algorithm: method.algorithm, seed: cell.seed, status, progress, result: null,
    max_steps: method.max_steps, wall_seconds: method.wall_seconds, schedule_steps: method.schedule_steps,
    completion: method.completion, control_revision: 0, execution_seconds: progress.elapsed_seconds || 0,
    updated_at: now, ...extra };
  cell.trial_id = trial.id; cell.status = status; cell.task_id = trial.task_id;
  data.state.trials.push(trial);
  return trial;
}

async function setup(page: Page, data: any) {
  let stateReads = 0, assessmentReads = 0;
  const writes: string[] = [];
  await page.clock.install({ time: new Date(now) });
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') { writes.push(path); return route.fulfill({ json: {} }); }
    if (path === '/api/state') { stateReads++; return route.fulfill({ json: data.state }); }
    if (path.includes('/confirmations/')) { assessmentReads++; return route.fulfill({ json: data.assessment }); }
    if (path.endsWith('/assets')) return route.fulfill({ json: [] });
    if (path.endsWith('/problems')) return route.fulfill({ json: { problems: [] } });
    if (path.endsWith('/study-templates')) return route.fulfill({ json: { templates: [] } });
    if (path.endsWith('/discovery')) return route.fulfill({ json: { sessions: [] } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#studies');
  await expect(page.getByRole('region', { name: 'Study run progress' })).toBeVisible();
  return { stateReads: () => stateReads, assessmentReads: () => assessmentReads, writes };
}

test('current study appears first and an unscheduled roster explains its budget blocker without starting work', async ({ page }) => {
  const data = fixture(), reads = await setup(page, data);
  const monitor = page.getByRole('region', { name: 'Study run progress' });
  await expect(page.locator('.evidence-panel-body').first()).toContainText('confirmation-study');
  await expect(page.getByText('Current study · confirmation', { exact: true })).toBeVisible();
  await expect(page.getByText('Full study question', { exact: true })).toBeVisible();
  await expect(monitor.getByText('Not started', { exact: true })).toBeVisible();
  await expect(monitor.getByRole('status')).toContainText('15 planned runs');
  await expect(monitor.getByRole('status')).toContainText('15 not scheduled');
  await expect(monitor).toContainText('Missing optimizer run caps: 71,400 seconds · campaign room: 2,670 seconds.');
  await expect(monitor).toContainText('exceed available room by 68,730 seconds');
  await expect(monitor.getByRole('button', { name: 'Revise campaign budget' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Schedule missing cells', exact: true })).toBeDisabled();
  await expect(monitor).not.toContainText('0.987654321');
  await expect(monitor).not.toContainText('98.8%');
  expect(reads.writes).toEqual([]);
});

test('six-second state polling updates a resumed run using fresh progress and separate request and decision counters', async ({ page }) => {
  const data = fixture();
  data.assessment.cells = [data.assessment.cells[3]];
  data.assessment.methods['method-dqn'].completion = { unit: 'optimizer_decisions', count: 1000 };
  const trial = addTrial(data, 0, 'running', { evaluations: 300, budget_requests: 300, step: 290, diagnostics: { decisions: 250 },
    elapsed_seconds: 5, updated_at: now, scientific_complete: false }, {
    result: { evaluations: 20, budget_requests: 20, step: 19, diagnostics: { decisions: 12 }, elapsed_seconds: 1, scientific_complete: false },
  });
  const reads = await setup(page, data), monitor = page.getByRole('region', { name: 'Study run progress' });
  await expect(monitor.getByText('Running', { exact: true })).toBeVisible();
  const requests = monitor.getByRole('progressbar', { name: 'Evaluation request allocation · dqn seed 100' });
  const completion = monitor.getByRole('progressbar', { name: 'Scientific completion target · dqn seed 100' });
  await expect(requests).toHaveAttribute('value', '300');
  await expect(completion).toHaveAttribute('value', '250');
  trial.progress = { ...trial.progress, evaluations: 650, budget_requests: 650, step: 620, diagnostics: { decisions: 550 }, elapsed_seconds: 11,
    updated_at: '2026-09-29T00:00:06Z' };
  trial.execution_seconds = 11;
  await page.clock.fastForward(6000);
  await expect.poll(reads.stateReads).toBeGreaterThan(1);
  await expect(requests).toHaveAttribute('value', '650');
  await expect(completion).toHaveAttribute('value', '550');
  await expect(monitor).toContainText('11 / 1,800 seconds');
  const assessmentReads = reads.assessmentReads(); // Initial StrictMode reads may be coalesced.
  await page.clock.fastForward(66000);
  await expect(monitor.getByText('No new report for over a minute;', { exact: false })).toBeVisible();
  expect(reads.assessmentReads()).toBe(assessmentReads); // Operational updates do not need repeated evidence assessments.
  expect(reads.writes).toEqual([]);
});

test('finished processes remain distinct from scientific completion, evidence readiness and release', async ({ page }) => {
  const data = fixture(); data.assessment.cells = [data.assessment.cells[0]];
  const trial = addTrial(data, 0, 'completed', { budget_requests: 50000, evaluations: 50000, step: 49000,
    scientific_complete: false, elapsed_seconds: 100, updated_at: now }, { result: { scientific_complete: false } });
  const reads = await setup(page, data), monitor = page.getByRole('region', { name: 'Study run progress' });
  await expect(monitor.getByText('Needs attention', { exact: true })).toBeVisible();
  await expect(monitor.getByRole('progressbar', { name: 'Finished worker processes' })).toHaveAttribute('value', '1');
  await expect(monitor).toContainText('0 / 1 met the scientific completion target');
  await expect(monitor.getByText('Process ended before scientific completion')).toBeVisible();
  trial.result = { ...trial.progress, step: 50000, scientific_complete: true };
  await page.clock.fastForward(6000);
  await expect(monitor.getByText('Finished · evidence pending', { exact: true })).toBeVisible();
  await expect(monitor).toContainText('1 / 1 met the scientific completion target · 0 / 1 have complete evidence');
  data.assessment.complete = true; data.assessment.cells[0].evidence_complete = true; data.state.event_cursor++;
  await page.clock.fastForward(6000);
  await expect(monitor.getByText('Finished · ready for release', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Release completed evidence', exact: true })).toBeEnabled();
  data.assessment.release = { outcome: 'completed_roster', rationale: 'Released after review' }; data.state.event_cursor++;
  await page.clock.fastForward(6000);
  await expect(monitor.getByText('Released', { exact: true })).toBeVisible();
  expect(reads.writes).toEqual([]);
});

test('failed and paused cells show attention and links while isolated worker cost uses the host measurement', async ({ page }) => {
  const data = fixture(); data.assessment.cells = [data.assessment.cells[0], data.assessment.cells[3]];
  addTrial(data, 0, 'failed', { step: 5, budget_requests: 7, elapsed_seconds: 400, updated_at: '2026-09-28T23:55:00Z' },
    { isolation_policy: { backend: 'container' }, execution_seconds: 8 });
  addTrial(data, 1, 'paused', { step: 10, budget_requests: 11, elapsed_seconds: 12, updated_at: '2026-09-28T23:55:00Z' });
  const reads = await setup(page, data), monitor = page.getByRole('region', { name: 'Study run progress' });
  await expect(monitor.getByText('Needs attention', { exact: true })).toBeVisible();
  await expect(monitor.getByRole('status')).toContainText('2 need attention');
  await expect(monitor).toContainText('Reported optimizer worker time: 20 seconds');
  await expect(monitor.getByRole('link', { name: /Open experiment trial_0/ })).toHaveAttribute('href', '#experiments/trial_0');
  await expect(monitor.getByText('No new report for over a minute;', { exact: false })).not.toBeVisible();
  await expect(page.getByRole('button', { name: 'Close as inconclusive', exact: true })).toBeDisabled();
  expect(reads.writes).toEqual([]);
});

test('manual refresh reloads an unchanged event cursor and never schedules cells automatically', async ({ page }) => {
  const data = fixture(); data.assessment.cells = [data.assessment.cells[0]];
  const reads = await setup(page, data), monitor = page.getByRole('region', { name: 'Study run progress' });
  addTrial(data, 0, 'queued', {});
  await monitor.getByRole('button', { name: 'Refresh run progress' }).click();
  await expect.poll(reads.assessmentReads).toBe(2);
  await expect(monitor.getByText('Queued', { exact: true })).toBeVisible();
  await expect(monitor.getByRole('status')).toContainText('1 queued');
  await expect(page.getByRole('button', { name: 'Schedule missing cells', exact: true })).toBeDisabled();
  expect(reads.writes).toEqual([]);
});

test('template execution cells do not charge an already reserved grant against campaign room twice', async ({ page }) => {
  const data = fixture(); data.assessment.execution_id = 'reserved-template-execution';
  data.state.budget.allocated_seconds = data.state.campaign.compute_budget_seconds;
  const reads = await setup(page, data), monitor = page.getByRole('region', { name: 'Study run progress' });
  await expect(monitor).toContainText('Template cells use their execution grant and admission rules.');
  await expect(monitor).not.toContainText('Scheduling is blocked');
  await expect(monitor.getByRole('button', { name: 'Revise campaign budget' })).not.toBeVisible();
  await expect(page.getByRole('button', { name: 'Schedule missing cells', exact: true })).not.toBeVisible();
  expect(reads.writes).toEqual([]);
});
