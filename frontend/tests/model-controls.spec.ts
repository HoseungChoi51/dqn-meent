import { test, expect } from '@playwright/test';

test('campaign model policy preserves edits, explains conflicts, and saves role defaults and overrides', async ({ page }) => {
  const campaign = { id: 'models-campaign', version: 1, name: 'Grating inverse design', autonomy: 'guided',
    objective: 'Find a suitable optimizer', compute_budget_seconds: 900, llm_budget_usd: 0 };
  const provider = { provider: 'codex', model: 'gpt-6-luna', billing_mode: 'subscription', configured: true, enabled: true };
  let models = { revision: 1, saved: true, configured: true, provider,
    policy: { default: { model: 'gpt-6-luna', reasoning_effort: 'low' }, roles: {} as Record<string, any> },
    roles: [
      ['campaign_manager', 'Campaign manager'], ['research_synthesizer', 'Research synthesizer'], ['problem_analyst', 'Problem analyst'],
      ['literature_investigator', 'Literature investigator'], ['methodology_specialist', 'Methodology specialist'],
      ['cross_domain_methodology_specialist', 'Cross domain methodology specialist'],
      ['novel_researcher', 'Novel researcher'],
    ].map(([role, label]) => ({ role, label, model: 'gpt-6-luna', reasoning_effort: 'low', source: 'default' })),
  };
  const state = { workspace_id: 'models-workspace', campaign, campaigns: [campaign], tasks: [], hypotheses: [],
    trials: [], decisions: [], messages: [], events: [], research_runs: [], algorithms: [], budget: {},
    settings: { llm_configured: true, provider } };
  const writes: any[] = [];
  let conflict = true, stateReads = 0;
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget { constructor() { super(); (window as any).testStream = this; } close() {} };
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST') {
      const command = route.request().postDataJSON(); writes.push(command);
      if (conflict) {
        conflict = false; models = { ...models, revision: 2 };
        return route.fulfill({ status: 409, json: { detail: 'Model policy revision changed' } });
      }
      models = { ...models, revision: models.revision + 1, policy: command.payload.policy };
      return route.fulfill({ json: { id: command.id, status: 'completed', outcome: models } });
    }
    if (path.endsWith('/models')) return route.fulfill({ json: models });
    if (path.endsWith('/state')) { stateReads++; return route.fulfill({ json: { ...state, settings: { ...state.settings, model_policy: models } } }); }
    if (path.endsWith('/discovery')) return route.fulfill({ json: { sessions: [] } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#models');
  await expect(page.getByRole('heading', { name: 'Choose the models behind your agents.' })).toBeVisible();
  await page.getByRole('button', { name: 'Use Astra / Sol / Luna preset' }).click();
  await expect(page.getByLabel('Campaign manager model', { exact: true })).toHaveValue('gpt-6-astra');
  await expect(page.getByLabel('Default reasoning effort')).toHaveValue('xhigh');
  await expect(page.getByRole('row', { name: /Cross domain methodology specialist/ })).toContainText('gpt-6-luna');
  await expect(page.getByRole('row', { name: /Research synthesizer/ })).toContainText('gpt-6-astra');
  await expect(page.getByRole('row', { name: /Novel researcher/ })).toContainText('gpt-6-sol');
  await page.getByLabel('Role to override').fill('novel_researcher');
  await page.getByRole('button', { name: 'Add override' }).click();
  await page.getByLabel('Novel researcher model', { exact: true }).fill('future-model-name');
  await page.getByLabel('Novel researcher reasoning effort', { exact: true }).selectOption('high');
  const previousReads = stateReads;
  await page.evaluate(() => (window as any).testStream.dispatchEvent(new Event('update')));
  await expect.poll(() => stateReads).toBeGreaterThan(previousReads);
  await expect(page.getByLabel('Novel researcher model', { exact: true })).toHaveValue('future-model-name');
  await page.getByRole('button', { name: 'Save model settings' }).click();
  await expect(page.getByRole('alert')).toContainText('Your edits are still here');
  await expect(page.getByLabel('Novel researcher model', { exact: true })).toHaveValue('future-model-name');
  await page.getByRole('button', { name: 'Reload saved settings' }).click();
  await expect(page.getByLabel('Default model', { exact: true })).toHaveValue('gpt-6-luna');
  await page.getByRole('button', { name: 'Use Astra / Sol / Luna preset' }).click();
  await page.getByLabel('Reason for change (optional)').fill('Use stronger reasoning for this research stage');
  await page.getByRole('button', { name: 'Save model settings' }).click();
  await expect(page.getByRole('status')).toContainText('Model settings saved for this campaign');
  expect(writes).toHaveLength(2);
  expect(writes[1]).toMatchObject({ campaign_id: campaign.id, expected_revision: 1, operation: 'models.configure',
    payload: { expected_revision: 2, reason: 'Use stronger reasoning for this research stage', policy: {
      default: { model: 'gpt-6-sol', reasoning_effort: 'xhigh' },
      roles: { campaign_manager: { model: 'gpt-6-astra', reasoning_effort: 'xhigh' },
        literature_investigator: { model: 'gpt-6-luna', reasoning_effort: 'xhigh' },
        methodology_specialist: { model: 'gpt-6-luna', reasoning_effort: 'xhigh' } },
    } },
  });
  await expect(page.getByRole('button', { name: 'Save model settings' })).toBeDisabled();
});

test('API model changes clear old rate estimates and expose explicit pricing fields', async ({ page }) => {
  const campaign = { id: 'api-models', version: 1, name: 'API campaign', compute_budget_seconds: 100, llm_budget_usd: 2 };
  const provider = { provider: 'openai_api', model: 'old-model', billing_mode: 'api', configured: true, enabled: true };
  const models = { revision: 0, saved: false, configured: true, provider,
    policy: { default: { model: 'old-model', reasoning_effort: 'low', input_usd_per_million: 1, output_usd_per_million: 2 }, roles: {} }, roles: [] };
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route('**/api/**', async route => {
    if (new URL(route.request().url()).pathname.endsWith('/models')) return route.fulfill({ json: models });
    return route.fulfill({ json: { workspace_id: 'api-workspace', campaign, campaigns: [campaign], tasks: [], hypotheses: [],
      trials: [], decisions: [], messages: [], events: [], research_runs: [], algorithms: [], settings: { llm_configured: true, provider } } });
  });
  await page.goto('/#models');
  await expect(page.getByLabel('Default input USD / million tokens')).toHaveValue('1');
  await page.getByLabel('Default model', { exact: true }).fill('custom-model');
  await expect(page.getByLabel('Default input USD / million tokens')).toHaveValue('');
  await expect(page.getByLabel('Default output USD / million tokens')).toHaveValue('');
  await expect(page.getByText('Calls require both rates.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save model settings' })).toBeEnabled();
});
