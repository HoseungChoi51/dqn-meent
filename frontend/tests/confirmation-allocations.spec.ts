import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

function fixture(dqnOverrides: any = {}) {
  const campaign = { id: 'allocation-campaign', name: 'Main-run budget study', objective: 'Compare selected optimizers with sufficient training.',
    active_study_id: 'development-study', autonomy: 'guided', version: 1, compute_budget_seconds: 3600, validation_reserve_seconds: 180, llm_budget_usd: 0 };
  const trial = (id: string, algorithm: string, config: any, extra: any = {}) => ({
    id, campaign_id: campaign.id, study_id: 'development-study', task_id: 'grating-one', task_name: 'First grating',
    hypothesis_id: `proposal-${algorithm}`, algorithm, algorithm_config: config, training: { seed: 1 }, seed: 1,
    status: 'completed', control_revision: 1, max_steps: 1536, schedule_steps: 1536, wall_seconds: 45, execution_contract: 1,
    scientific_source_hash: 'verified-source', scientific_environment: { python: '3.12' },
    completion: { unit: 'evaluation_requests', count: 1536 }, progress: { best_objective: 0.93 },
    problem: { definition_id: 'meent_grating', primary_objective: { name: 'efficiency', direction: 'maximize', units: 'fraction of incident power' } }, ...extra,
  });
  const dqn = trial('trial_dqn', 'dqn', { learning_rate: 0.0001 }, dqnOverrides);
  const hill = trial('trial_hill', 'hillclimb', { restart_patience: 16 });
  const control = trial('trial_random', 'random', {});
  const resolved = (source: any) => ({ algorithm: source.algorithm, algorithm_config: source.algorithm_config, training: {},
    max_steps: source.max_steps, schedule_steps: source.schedule_steps, wall_seconds: source.wall_seconds, completion: source.completion,
    implementation_version_id: null, implementation_digest: null, scientific_source_hash: source.scientific_source_hash,
    runtime: source.scientific_environment, initial_assets: [], contribution_asset_ids: [], asset_digests: {} });
  const saved = { id: 'finalist-selection', study_id: campaign.active_study_id, campaign_id: campaign.id, revision: 3, label: 'DQN and local search',
    trial_ids: [dqn.id, hill.id], prototype_trial_ids: [dqn.id, hill.id],
    entries: [dqn, hill].map(source => ({ procedure_id: `procedure-${source.id}`, method_id: `method-${source.id}`,
      trial_id: source.id, source_trial_ids: [source.id], resolved_procedure: resolved(source) })), updated_at: '2026-09-29T00:00:00Z' };
  return { workspace_id: 'allocation-workspace', campaign, campaigns: [campaign],
    tasks: [{ id: 'grating-one', name: 'First grating', physics: {}, split: 'development', problem: dqn.problem },
      { id: 'grating-two', name: 'Second grating', physics: {}, split: 'development', problem: dqn.problem }],
    studies: [{ id: campaign.active_study_id, goal: 'Rough tuning', scope: 'exploratory' }],
    hypotheses: [{ id: 'proposal-dqn', title: 'Deep Q learning' }, { id: 'proposal-hillclimb', title: 'Restart hill climbing' }],
    trials: [dqn, hill, control], finalist_selections: [saved], nominations: [] as any[], decisions: [], messages: [], events: [],
    research_runs: [], algorithms: [], settings: { llm_configured: false }, event_cursor: 1,
    budget: { cap_seconds: 3600, allocated_seconds: 400, spent_seconds: 400 }, resolved };
}

async function setup(page: Page, state: any, commands: any[]) {
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/commands') {
      commands.push(route.request().postDataJSON());
      return route.fulfill({ json: { status: 'completed', outcome: {} } });
    }
    if (path === '/api/state') return route.fulfill({ json: state });
    if (path.endsWith('/assets')) return route.fulfill({ json: [] });
    if (path.endsWith('/study-rules')) return route.fulfill({ json: { rules: [] } });
    if (path.endsWith('/study-templates')) return route.fulfill({ json: { templates: [] } });
    if (path.endsWith('/problems')) return route.fulfill({ json: { problems: [] } });
    if (path.endsWith('/discovery')) return route.fulfill({ json: { sessions: [] } });
    if (path.includes('/confirmations/')) return route.fulfill({ json: { cells: [], kind: 'seed_replication', required_recipes: [], complete: false } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#studies');
  await page.getByRole('button', { name: 'Define a new study' }).click();
  await page.getByLabel('Study scope', { exact: true }).selectOption('confirmation');
}

test('only DQN receives a larger main allocation and the roster preview includes every seed and instance', async ({ page }) => {
  const state = fixture(), commands: any[] = [], pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await setup(page, state, commands);
  await page.getByRole('button', { name: 'Use saved finalists' }).click();
  const panel = page.getByRole('region', { name: 'Main-run allocations' });
  await page.getByLabel('Fresh seeds', { exact: true }).fill('100, 101');
  await panel.getByLabel('Request cap · dqn trial_dqn', { exact: true }).fill('12000');
  await panel.getByLabel('Time cap (seconds) · dqn trial_dqn', { exact: true }).fill('180');
  await panel.getByLabel('DQN exploration schedule horizon · dqn trial_dqn', { exact: true }).fill('12000');
  await expect(panel.getByText('12,000 evaluation requests', { exact: true })).toBeVisible();
  await expect(panel.getByText('Completion target changes from 1,536 to 12,000.')).toBeVisible();
  await expect(panel.getByLabel('Request cap · hillclimb trial_hill', { exact: true })).toHaveValue('1536');
  await expect(panel.getByLabel('Time cap (seconds) · hillclimb trial_hill', { exact: true })).toHaveValue('45');
  await expect(panel.getByRole('status')).toContainText('2 methods × 2 instances × 2 fresh seeds = 8 runs.');
  await expect(panel.getByRole('status')).toContainText('450 worker-seconds per seed');
  await expect(panel.getByRole('status')).toContainText('900 worker-seconds (15.0m) total time cap');
  await expect(panel.getByRole('status')).toContainText('3,020 worker-seconds');
  await expect(panel.getByText("The source run's checkpoint is not resumed automatically", { exact: false })).toBeVisible();

  await page.getByRole('checkbox', { name: 'Select random prototype trial_random', exact: true }).check();
  await expect(panel.getByLabel('Request cap · dqn trial_dqn', { exact: true })).toHaveValue('12000');
  await expect(panel.getByRole('status')).toContainText('1,080 worker-seconds (18.0m) total time cap');
  expect(commands).toHaveLength(0);
  await page.getByRole('button', { name: 'Freeze and activate study' }).click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].operation).toBe('study.create');
  const payload = commands[0].payload;
  expect(payload.prototype_trial_ids).toEqual(['trial_dqn', 'trial_hill', 'trial_random']);
  expect(payload.prototype_allocations).toEqual({
    trial_dqn: { expected_control_revision: 1, max_steps: 12000, wall_seconds: 180, schedule_steps: 12000, completion_count: 12000 },
    trial_hill: { expected_control_revision: 1, max_steps: 1536, wall_seconds: 45, schedule_steps: 1536, completion_count: 1536 },
    trial_random: { expected_control_revision: 1, max_steps: 1536, wall_seconds: 45, schedule_steps: 1536, completion_count: 1536 },
  });
  expect(payload.finalist_selection_revision).toBe(3);
  expect(payload.seeds).toEqual([100, 101]);
  expect(payload.task_ids).toEqual(['grating-one', 'grating-two']);
  expect(pageErrors).toEqual([]);
});

test('an extended source gets an explicit full completion target and an honest over-budget warning', async ({ page }) => {
  const state = fixture({ max_steps: 50000, wall_seconds: 2800 }), commands: any[] = [];
  await setup(page, state, commands);
  await page.getByRole('button', { name: 'Use saved finalists' }).click();
  const panel = page.getByRole('region', { name: 'Main-run allocations' });
  await expect(panel.getByLabel('Request cap · dqn trial_dqn')).toHaveValue('50000');
  await expect(panel.getByLabel('DQN exploration schedule horizon · dqn trial_dqn')).toHaveValue('1536');
  await expect(panel.getByText('Completion target changes from 1,536 to 50,000.')).toBeVisible();
  await expect(panel.getByRole('status')).toContainText('17,070 worker-seconds');
  await expect(panel.getByRole('status')).toContainText('exceed current campaign room by 14,050 worker-seconds');
  await expect(panel.getByRole('status')).toContainText('Freezing records the design and does not start runs or reserve this estimate.');
  await panel.getByLabel('Time cap (seconds) · dqn trial_dqn').fill('120');
  await expect(panel.getByRole('status')).not.toContainText('exceed current campaign room');
  await page.getByRole('button', { name: 'Freeze and activate study' }).click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].payload.prototype_allocations.trial_dqn).toEqual({ expected_control_revision: 1,
    max_steps: 50000, wall_seconds: 120, schedule_steps: 1536, completion_count: 50000 });
});

test('removing a prototype prunes its edits and importing a saved group explicitly resets allocations', async ({ page }) => {
  const state = fixture(), commands: any[] = [];
  await setup(page, state, commands);
  await page.getByRole('button', { name: 'Use saved finalists' }).click();
  const panel = page.getByRole('region', { name: 'Main-run allocations' });
  await panel.getByLabel('Time cap (seconds) · dqn trial_dqn').fill('300');
  await page.getByRole('checkbox', { name: 'Select dqn prototype trial_dqn', exact: true }).uncheck();
  await expect(panel.getByLabel('Time cap (seconds) · dqn trial_dqn')).not.toBeVisible();
  await page.getByRole('checkbox', { name: 'Select dqn prototype trial_dqn', exact: true }).check();
  await expect(panel.getByLabel('Time cap (seconds) · dqn trial_dqn')).toHaveValue('45');
  await panel.getByLabel('Request cap · dqn trial_dqn').fill('12000');
  await page.getByRole('button', { name: 'Use saved finalists' }).click();
  await expect(panel.getByLabel('Request cap · dqn trial_dqn')).toHaveValue('1536');
  expect(commands).toHaveLength(0);
});

test('optimizer-decision completion remains a separate target when the request cap changes', async ({ page }) => {
  const state = fixture({ max_steps: 1024, schedule_steps: 256, completion: { unit: 'optimizer_decisions', count: 256 } }), commands: any[] = [];
  await setup(page, state, commands);
  await page.getByRole('button', { name: 'Use saved finalists' }).click();
  const panel = page.getByRole('region', { name: 'Main-run allocations' });
  await panel.getByLabel('Request cap · dqn trial_dqn').fill('2048');
  await expect(panel.getByLabel('Required optimizer decisions · dqn trial_dqn')).toHaveValue('256');
  await expect(panel.getByText('Allow evaluation requests for initialization and episode resets in addition to optimizer decisions.')).toBeVisible();
  await panel.getByLabel('Required optimizer decisions · dqn trial_dqn').fill('512');
  await page.getByRole('button', { name: 'Freeze and activate study' }).click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].payload.prototype_allocations.trial_dqn).toEqual({ expected_control_revision: 1,
    max_steps: 2048, wall_seconds: 45, schedule_steps: 256, completion_count: 512 });
  expect(commands[0].payload.prototype_allocations.trial_dqn).not.toHaveProperty('completion_unit');
});

test('a rule-based nomination locks its allocation while additional controls remain editable', async ({ page }) => {
  const state = fixture(), commands: any[] = [];
  state.nominations.push({ id: 'frozen-nomination', study_id: 'development-study', rule: { rule_id: 'fixed-selection' },
    prototypes: { 'dqn-method': 'trial_dqn' }, methods: { 'dqn-method': state.resolved(state.trials[0]) } });
  await setup(page, state, commands);
  await page.getByLabel('Frozen development nomination', { exact: true }).selectOption('frozen-nomination');
  const panel = page.getByRole('region', { name: 'Main-run allocations' });
  await expect(panel.getByLabel('Request cap · dqn trial_dqn')).toBeDisabled();
  await expect(panel.getByLabel('Time cap (seconds) · dqn trial_dqn')).toBeDisabled();
  await expect(panel.getByLabel('DQN exploration schedule horizon · dqn trial_dqn')).toBeDisabled();
  await expect(panel.getByText('A rule-based nomination freezes its methods and allocations.', { exact: false })).toBeVisible();
  await page.getByRole('checkbox', { name: 'Select random prototype trial_random', exact: true }).check();
  await panel.getByLabel('Request cap · random trial_random').fill('3000');
  await page.getByRole('button', { name: 'Freeze and activate study' }).click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].payload.nomination_id).toBe('frozen-nomination');
  expect(Object.keys(commands[0].payload.prototype_allocations)).toEqual(['trial_random']);
  expect(commands[0].payload.prototype_allocations.trial_random.completion_count).toBe(3000);
});

test('equal final procedures share one set of cells in the allocation preview', async ({ page }) => {
  const state = fixture(), commands: any[] = [];
  state.trials.push({ ...state.trials[1], id: 'trial_hill_long', max_steps: 3072, schedule_steps: 3072, wall_seconds: 90,
    completion: { unit: 'evaluation_requests', count: 3072 } });
  await setup(page, state, commands);
  await page.getByRole('button', { name: 'Use saved finalists' }).click();
  await page.getByRole('checkbox', { name: 'Select hillclimb prototype trial_hill_long', exact: true }).check();
  const panel = page.getByRole('region', { name: 'Main-run allocations' });
  await expect(panel.getByRole('status')).toContainText('3 methods × 2 instances × 3 fresh seeds = 18 runs.');
  await panel.getByLabel('Request cap · hillclimb trial_hill_long').fill('1536');
  await panel.getByLabel('Time cap (seconds) · hillclimb trial_hill_long').fill('45');
  await panel.getByLabel('Search schedule horizon · hillclimb trial_hill_long').fill('1536');
  await expect(panel.getByRole('status')).toContainText('2 methods × 2 instances × 3 fresh seeds = 12 runs.');
  await expect(panel.getByRole('status')).toContainText('540 worker-seconds (9.0m) total time cap');
  await expect(panel.getByRole('status')).toContainText('the frozen roster includes that procedure once');
  expect(commands).toHaveLength(0);
});

test('a frozen study shows the main allocations and original source without scheduling work', async ({ page }) => {
  const state = fixture(), commands: any[] = [];
  state.studies.push({ id: 'confirmation-study', scope: 'confirmation', goal: 'Replicate finalists', confirmation: {
    id: 'protocol-final', seeds: [100, 101], instances: [{ id: 'grating-one' }, { id: 'grating-two' }],
    prototypes: { 'final-dqn-method': 'trial_dqn' }, methods: { 'final-dqn-method': { ...state.resolved(state.trials[0]),
      max_steps: 12000, wall_seconds: 180, schedule_steps: 10000, completion: { unit: 'evaluation_requests', count: 12000 } } },
  } } as any);
  await setup(page, state, commands);
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.getByText('Frozen main-run allocations · 1 method', { exact: true }).click();
  const summary = page.locator('.frozen-confirmation-allocations');
  await expect(summary.getByRole('cell', { name: '12,000', exact: true })).toBeVisible();
  await expect(summary.getByRole('cell', { name: '180 seconds', exact: true })).toBeVisible();
  await expect(summary.getByRole('cell', { name: '10,000 steps', exact: true })).toBeVisible();
  await expect(summary.getByText('Source: trial_dqn')).toBeVisible();
  await expect(summary.getByText('2 instances × 2 fresh seeds per method.', { exact: false })).toBeVisible();
  expect(commands).toHaveLength(0);
});

test('positive fractional wall limits remain valid allocations', async ({ page }) => {
  const state = fixture({ wall_seconds: 0.25 }), commands: any[] = [];
  await setup(page, state, commands);
  await page.getByRole('button', { name: 'Use saved finalists' }).click();
  const input = page.getByRole('region', { name: 'Main-run allocations' }).getByLabel('Time cap (seconds) · dqn trial_dqn');
  await expect(input).toHaveValue('0.25');
  expect(await input.evaluate((element: HTMLInputElement) => element.checkValidity())).toBe(true);
  await page.getByRole('button', { name: 'Freeze and activate study' }).click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].payload.prototype_allocations.trial_dqn.wall_seconds).toBe(0.25);
});
