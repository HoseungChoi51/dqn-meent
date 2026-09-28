import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

function fixture() {
  const campaign = { id: 'prototype-campaign', name: 'Scientific optimizer study', objective: 'Confirm a promising optimizer.',
    active_study_id: 'development-study', autonomy: 'guided', version: 1, compute_budget_seconds: 3600, llm_budget_usd: 0 };
  const trial = (id: string, seed: number, algorithm: string, config: any, extra: any = {}) => ({
    id, campaign_id: campaign.id, study_id: 'development-study', task_id: 'prototype-task', task_name: 'Binary grating',
    hypothesis_id: algorithm === 'hillclimb' ? 'proposal-hill' : null,
    algorithm, algorithm_config: config, training: { seed }, seed, status: 'completed',
    max_steps: 1536, schedule_steps: 1536, wall_seconds: 45, execution_contract: 1,
    scientific_source_hash: 'verified-source', scientific_environment: { python: '3.12', packages: {} },
    completion: { unit: 'evaluation_requests', count: 1536 }, progress: { best_objective: 0.94 },
    problem: { definition_id: 'meent_grating', primary_objective: { name: 'efficiency', direction: 'maximize', units: 'fraction of incident power' } },
    ...extra,
  });
  const a = trial('trial_hill16_seed0', 0, 'hillclimb', { restart_patience: 16 });
  const b = trial('trial_hill16_seed1', 1, 'hillclimb', { restart_patience: 16 });
  const c = trial('trial_hill32_seed0', 0, 'hillclimb', { restart_patience: 32 }, { max_steps: 3072, schedule_steps: 3072, wall_seconds: 90,
    completion: { unit: 'evaluation_requests', count: 3072 } });
  const control = trial('trial_random_seed0', 0, 'random', {});
  const previous = trial('trial_previous_seed0', 0, 'hillclimb', { restart_patience: 8 }, { study_id: 'previous-study' });
  const resolved = { algorithm: a.algorithm, algorithm_config: a.algorithm_config, training: {},
    max_steps: a.max_steps, schedule_steps: a.schedule_steps, wall_seconds: a.wall_seconds, completion: a.completion,
    implementation_version_id: null, implementation_digest: null, scientific_source_hash: a.scientific_source_hash,
    runtime: a.scientific_environment, initial_assets: [], contribution_asset_ids: [], asset_digests: {} };
  const saved = { id: 'finalist-selection', study_id: campaign.active_study_id, campaign_id: campaign.id,
    revision: 2, label: 'Promising local search', trial_ids: [a.id, b.id], prototype_trial_ids: [b.id],
    selected_method_ids: ['comparison-method'], selected_procedure_ids: ['confirmation-procedure'],
    entries: [{ procedure_id: 'confirmation-procedure', method_id: 'comparison-method', trial_id: b.id,
      source_trial_ids: [a.id, b.id], resolved_procedure: resolved, hypothesis_title: 'Restart hill climbing' }],
    updated_at: '2026-09-29T00:00:00Z' };
  return { workspace_id: 'prototype-workspace', campaign, campaigns: [campaign],
    tasks: [{ id: 'prototype-task', name: 'Binary grating', physics: {}, split: 'development', problem: a.problem }],
    studies: [{ id: 'previous-study', goal: 'Initial screen', scope: 'exploratory' },
      { id: campaign.active_study_id, goal: 'Rough tuning', scope: 'exploratory' }],
    hypotheses: [{ id: 'proposal-hill', title: 'Restart hill climbing' }], trials: [a, b, c, control, previous],
    finalist_selections: [saved], nominations: [], decisions: [], messages: [], events: [], research_runs: [], algorithms: [],
    settings: { llm_configured: false }, event_cursor: 1 };
}

async function setup(page: Page, state: any, commands: any[]) {
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/v1/commands') {
      commands.push(route.request().postDataJSON());
      return route.fulfill({ json: { status: 'completed', outcome: {} } });
    }
    if (url.pathname === '/api/state') return route.fulfill({ json: state });
    if (url.pathname.endsWith('/assets')) return route.fulfill({ json: [] });
    if (url.pathname.endsWith('/study-rules')) return route.fulfill({ json: { rules: [] } });
    if (url.pathname.endsWith('/study-templates')) return route.fulfill({ json: { templates: [] } });
    if (url.pathname.endsWith('/problems')) return route.fulfill({ json: { problems: [] } });
    if (url.pathname.endsWith('/discovery')) return route.fulfill({ json: { sessions: [] } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#studies');
  await page.getByRole('button', { name: 'Define a new study' }).click();
  await page.getByLabel('Study scope', { exact: true }).selectOption('confirmation');
}

test('saved finalists import exact prototype IDs and retain additional controls with explicit budgets', async ({ page }) => {
  const state = fixture(), commands: any[] = [];
  await setup(page, state, commands);
  const dialog = page.getByRole('dialog'), picker = page.getByRole('region', { name: 'Prototype experiments' });
  await expect(picker.getByLabel('Prototype source study')).toHaveValue('development-study');
  await expect(picker.getByRole('checkbox', { name: /^Select / })).toHaveCount(3);
  await expect(picker.getByText('Completion: 1,536 evaluation requests').first()).toBeVisible();
  await expect(picker.getByText('Allocation: 3,072 steps · 1.5m wall limit')).toBeVisible();
  await expect(picker.getByText('Optimizer schedule: 3,072 steps')).toBeVisible();
  await expect(picker.getByText('2 source seeds: 0, 1')).toBeVisible();
  await expect(picker.getByText("Confirmation copies each selected procedure's exact parameters", { exact: false })).toBeVisible();

  // A saved shortlist is visible but never silently selects or freezes methods.
  await expect(picker.getByRole('status')).toHaveText('0 prototypes selected.');
  await picker.getByRole('button', { name: 'Use saved finalists', exact: true }).click();
  await expect(picker.getByRole('checkbox', { name: 'Select hillclimb prototype trial_hill16_seed1', exact: true })).toBeChecked();
  await expect(picker.getByRole('checkbox', { name: 'Select hillclimb prototype trial_hill32_seed0', exact: true })).not.toBeChecked();
  await picker.getByRole('checkbox', { name: 'Select random prototype trial_random_seed0', exact: true }).check();
  await expect(picker.getByRole('status')).toHaveText('2 prototypes selected.');
  expect(commands).toHaveLength(0);
  await dialog.getByRole('button', { name: 'Freeze and activate study' }).click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].operation).toBe('study.create');
  expect(commands[0].payload.prototype_trial_ids).toEqual(['trial_hill16_seed1', 'trial_random_seed0']);
  expect(commands[0].payload.seeds).toEqual([100, 101, 102]);
  expect(commands[0].payload.finalist_selection_id).toBe('finalist-selection');
  expect(commands[0].payload.finalist_selection_revision).toBe(2);
});

test('previous shortlists remain accessible after activation and seed replicas use one prototype', async ({ page }) => {
  const state = fixture(), commands: any[] = [];
  state.campaign.active_study_id = 'confirmation-study';
  state.studies.push({ id: 'confirmation-study', scope: 'confirmation', goal: 'Frozen confirmation' });
  await setup(page, state, commands);
  const picker = page.getByRole('region', { name: 'Prototype experiments' });
  await expect(picker.getByLabel('Prototype source study')).toHaveValue('development-study');
  await picker.getByRole('button', { name: 'Use saved finalists' }).click();
  await picker.getByLabel('Source experiment for hillclimb trial_hill16_seed0', { exact: true }).selectOption('trial_hill16_seed0');
  await expect(picker.getByRole('status')).toHaveText('1 prototype selected.');
  await picker.getByLabel('Prototype source study').selectOption('previous-study');
  await expect(picker.getByRole('status')).toHaveText('1 prototype selected · 1 outside the current filter.');
  await picker.getByRole('checkbox', { name: 'Select hillclimb prototype trial_previous_seed0', exact: true }).check();
  await page.getByRole('button', { name: 'Freeze and activate study' }).click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].payload.prototype_trial_ids).toEqual(['trial_hill16_seed0', 'trial_previous_seed0']);
  expect(commands[0].payload.finalist_selection_id).toBe('finalist-selection');
  expect(commands[0].payload.finalist_selection_revision).toBe(2);
});

test('changed saved procedures cannot be imported and an empty confirmation shows a useful error', async ({ page }) => {
  const state = fixture(), commands: any[] = [];
  state.trials.find(trial => trial.id === 'trial_hill16_seed1')!.wall_seconds = 60;
  await setup(page, state, commands);
  const picker = page.getByRole('region', { name: 'Prototype experiments' });
  await expect(picker.getByRole('button', { name: 'Use saved finalists' })).toBeDisabled();
  await expect(picker.getByText('A saved procedure has changed.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Freeze and activate study' }).click();
  await expect(page.getByText('Choose at least one prototype experiment or a frozen development nomination.')).toBeVisible();
  expect(commands).toHaveLength(0);
});
