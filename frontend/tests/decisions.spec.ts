import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

function decision(id: string, title: string, freshness: any = {}) {
  return { id, title, status: 'pending', context: 'Original scientific rationale remains available.', action_id: `action_${id}`,
    charter_version: 1, resolution_revision: 0, recommendation: 'accept', created_at: '2026-09-28T10:00:00Z',
    options: [{ id: 'accept', label: 'Proceed' }, { id: 'defer', label: 'Defer' }, { id: 'reject', label: 'Decline' }],
    freshness: { state: 'ready', stale: false, can_accept: true, can_refresh: false, blocked_choice_ids: [], reason: '',
      current_charter_version: 2, proposal_charter_version: 2, current_guidance_revision: 4, proposal_guidance_revision: 4,
      ...freshness } };
}

function state(decisions: any[]) {
  const campaign = { id: 'decision-campaign', version: 2, name: 'Grating optimizer research', autonomy: 'guided',
    objective: 'Compare optimization mechanisms', compute_budget_seconds: 3600, llm_budget_usd: 0 };
  return { workspace_id: 'decision-workspace', campaign, campaigns: [campaign], tasks: [], hypotheses: [], trials: [],
    decisions, messages: [], events: [], research_runs: [], algorithms: [], actions: [], manager_context: { guidance_revision: 4 },
    settings: { llm_configured: false } };
}

async function mock(page: Page, current: any, post: (command: any) => { status?: number; json: any }) {
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      constructor() { super(); (window as any).testStream = this; } close() {}
    };
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST') return route.fulfill(post(route.request().postDataJSON()));
    if (path.endsWith('/state')) return route.fulfill({ json: current });
    if (path.endsWith('/discovery')) return route.fulfill({ json: { sessions: [] } });
    return route.fulfill({ json: {} });
  });
}

test('bulk refresh preserves notes, disables stale execution and keeps current review and decision history', async ({ page }) => {
  const charter = decision('charter', 'Increase comparison allocation', { state: 'stale', stale: true,
    can_accept: false, can_refresh: true, blocked_choice_ids: ['accept'], reason: 'This recommendation used charter v1; the campaign is now on v2.' });
  const guidance = decision('guidance', 'Compare three mechanisms', { state: 'stale', stale: true,
    can_accept: false, can_refresh: true, blocked_choice_ids: ['accept'], reason: 'Researcher guidance changed from revision 3 to 4.' });
  const current = state([charter, guidance]);
  const writes: any[] = [];
  await mock(page, current, command => {
    writes.push(command);
    if (command.operation === 'decision.refresh') {
      for (const item of [charter, guidance]) item.freshness = { ...item.freshness, state: 'updating', can_refresh: false,
        reason: 'Campaign manager reassessment is queued.', research_run_id: 'research_current_review' };
    } else {
      expect(command.operation).toBe('decision.resolve');
      Object.assign(charter, { status: 'resolved', choice: command.payload.choice, comment: command.payload.comment, resolution_revision: 1 });
      charter.freshness = { ...charter.freshness, state: 'stale', stale: true, can_refresh: false, review_completed: false,
        reason: 'This historical recommendation used an earlier charter.' };
    }
    return { json: { id: command.id, status: 'completed', outcome: {} } };
  });
  await page.goto('/#decisions');
  const first = page.getByRole('article', { name: charter.title });
  const second = page.getByRole('article', { name: guidance.title });
  await expect(first.getByText('Needs update', { exact: true })).toBeVisible();
  await expect(first.getByRole('radio', { name: /^Proceed/ })).toBeDisabled();
  await expect(second.getByText('Researcher guidance changed from revision 3 to 4.')).toBeVisible();
  await first.getByLabel('Your reasoning (optional)').fill('Keep the existing baseline and cap.');
  await second.getByLabel('Your reasoning (optional)').fill('Account for the new measurements.');
  await page.getByRole('button', { name: 'Update outdated decisions', exact: true }).click();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ operation: 'decision.refresh', expected_revision: 2, payload: { max_parallel_reviews: 3, decisions: [
    { decision_id: 'charter', expected_resolution_revision: 0, comment: 'Keep the existing baseline and cap.' },
    { decision_id: 'guidance', expected_resolution_revision: 0, comment: 'Account for the new measurements.' },
  ] } });
  await expect(first.getByRole('button', { name: 'Manager update requested', exact: true })).toBeDisabled();
  await expect(first.getByLabel('Your reasoning (optional)')).toHaveValue('Keep the existing baseline and cap.');
  for (const item of [charter, guidance]) item.freshness = { ...item.freshness, state: 'reviewed', review_completed: true,
    reason: 'A current manager review is available. The original recommendation remains unchanged.', review_decision_ids: ['current'] };
  current.decisions.push(decision('current', 'Current bounded comparison'));
  await page.evaluate(() => (window as any).testStream.dispatchEvent(new Event('update')));
  await expect(first.getByRole('link', { name: 'View current review' })).toHaveAttribute('href', '#notebook/conversation');
  await expect(first.getByRole('radio', { name: /^Proceed/ })).toBeDisabled();
  await expect(page.getByRole('article', { name: 'Current bounded comparison' }).getByRole('radio', { name: /^Proceed/ })).toBeEnabled();
  await expect(page.locator('main .decision-card').first()).toHaveAccessibleName('Current bounded comparison');
  await expect(page.getByText('1 ready to decide · 2 need review or clarification', { exact: true })).toBeVisible();
  await first.getByRole('radio', { name: 'Defer', exact: true }).check();
  await first.getByRole('button', { name: 'Record decision', exact: true }).click();
  expect(writes[1]).toMatchObject({ operation: 'decision.resolve', payload: { decision_id: 'charter', choice: 'defer',
    comment: 'Keep the existing baseline and cap.', expected_resolution_revision: 0 } });
  await page.getByRole('button', { name: 'Decision history', exact: true }).click();
  await expect(first.getByText('Decision recorded', { exact: true })).toBeVisible();
  await expect(first.getByText('Needs update', { exact: true })).not.toBeVisible();
  await expect(first.getByText('This historical recommendation used an earlier charter.')).not.toBeVisible();
  await expect(first.getByText('Choice: Defer', { exact: true })).toBeVisible();
  await expect(first.getByText('Keep the existing baseline and cap.', { exact: true })).toBeVisible();
  await first.getByText('Original context and decision details', { exact: true }).click();
  await expect(first.locator('details').getByText('Original scientific rationale remains available.')).toBeVisible();
});

test('an authority race refreshes the card and retains the comment without retrying stale execution', async ({ page }) => {
  const item = decision('race', 'Launch the selected comparison');
  const current = state([item]), writes: any[] = [];
  await mock(page, current, command => {
    writes.push(command);
    if (command.operation === 'decision.resolve') {
      current.campaign.version = 3;
      item.freshness = { ...item.freshness, state: 'stale', stale: true, can_accept: false,
        can_refresh: true, blocked_choice_ids: ['accept'], reason: 'The recommendation used charter v2; the current charter is v3.' };
      return { status: 409, json: { detail: 'This recommendation used an older charter; request a current proposal' } };
    }
    expect(command.operation).toBe('decision.refresh');
    item.freshness = { ...item.freshness, state: 'updating', can_refresh: false, reason: 'Campaign manager reassessment is queued.' };
    return { json: { id: command.id, status: 'completed', outcome: {} } };
  });
  await page.goto('/#decisions');
  const card = page.getByRole('article', { name: item.title });
  await card.getByRole('radio', { name: 'Proceed', exact: true }).check();
  await card.getByLabel('Your reasoning (optional)').fill('Keep this note even if the campaign changes.');
  await card.getByRole('button', { name: 'Record decision', exact: true }).click();
  await expect(card.getByRole('alert')).toContainText('The inbox has been refreshed. Your comment is still here.');
  await expect(card.getByRole('radio', { name: /^Proceed/ })).toBeDisabled();
  await expect(card.getByRole('button', { name: 'Record decision', exact: true })).toBeDisabled();
  await expect(card.getByLabel('Your reasoning (optional)')).toHaveValue('Keep this note even if the campaign changes.');
  await card.getByRole('button', { name: 'Ask manager to update' }).click();
  expect(writes).toHaveLength(2);
  expect(writes[1]).toMatchObject({ operation: 'decision.refresh', expected_revision: 3, payload: { decisions: [
    { decision_id: 'race', expected_resolution_revision: 0, desired_choice: 'accept', comment: 'Keep this note even if the campaign changes.' },
  ] } });
  await expect(card.getByRole('alert')).not.toBeVisible();
  expect(writes.filter(command => command.operation === 'decision.resolve')).toHaveLength(1);
});

test('a current plan needs a concrete design and can be updated without claiming the charter is stale', async ({ page }) => {
  const item = decision('plan', 'Design a multiseed comparison', { state: 'blocked', stale: false, can_accept: false,
    can_refresh: true, blocked_choice_ids: ['accept'], reason: 'This is a plan. The manager must specify executable trial procedures.' });
  const current = state([item]), writes: any[] = [];
  await mock(page, current, command => { writes.push(command); return { json: { id: command.id, status: 'completed', outcome: {} } }; });
  await page.goto('/#decisions');
  const card = page.getByRole('article', { name: item.title });
  await expect(card.getByText('Needs update', { exact: true })).toBeVisible();
  await expect(card.getByText(item.freshness.reason)).toBeVisible();
  await expect(card.getByText(/charter v/)).not.toBeVisible();
  await expect(card.getByRole('radio', { name: /^Proceed/ })).toBeDisabled();
  await expect(card.getByRole('radio', { name: 'Decline', exact: true })).toBeEnabled();
  await card.getByRole('button', { name: 'Ask manager to update' }).click();
  expect(writes).toHaveLength(1);
  expect(writes[0].operation).toBe('decision.refresh');
});

for (const [label, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]] as const) {
  test(`${label}: concise proposal and explicit consequences precede collapsed evidence without changing authority`, async ({ page }, info) => {
    await page.setViewportSize({ width, height });
    const detail = `${'Original detailed observations, comparison limitations, and scientific disagreement. '.repeat(45)}\n\nUNABRIDGED_EVIDENCE_MARKER`;
    const item = { ...decision('specific', 'Original technical request: bounded comparison qualification'), context: detail,
      presentation: { title: 'Compare annealing with the hill-climbing baseline',
        background: 'Annealing has not yet been compared with the baseline on shared fresh seeds.',
        proposal: 'Run both methods on two new seeds with a total allocation of 180 worker-seconds.',
        options: [
          { id: 'accept', label: 'Approve: run the four-trial comparison', description: 'Reserve 180 worker-seconds and queue four trials using the current implementations.' },
          { id: 'defer', label: 'Keep this comparison for later', description: 'Save the proposal without allocating compute or queuing trials.' },
          { id: 'reject', label: 'Decline this comparison', description: 'Record the decision without changing other experiments.' },
          { id: 'invented', label: 'Apply this approval to all future campaigns', description: 'This ID is not in the original authority.' },
        ], recommendation_reason: 'Shared seeds would address the uncertainty in the current comparison.',
        scope_label: 'This comparison only · 180 worker-seconds', needs_clarification: false,
        basis: 'action', audience: 'researcher', details: detail,
        action_details: { operation: 'study.create', allocation_seconds: 180 } } };
    const current = state([item]), writes: any[] = [];
    await mock(page, current, command => {
      writes.push(command);
      Object.assign(item, { status: 'resolved', choice: command.payload.choice, comment: command.payload.comment });
      return { json: { id: command.id, status: 'completed', outcome: {} } };
    });
    await page.goto('/#decisions');
    const card = page.getByRole('article', { name: item.presentation.title });
    await expect(card.getByRole('region', { name: 'Background', exact: true })).toContainText(item.presentation.background);
    await expect(card.getByRole('region', { name: 'Proposal', exact: true })).toContainText(item.presentation.proposal);
    await expect(card.getByRole('group', { name: 'Choose an option' })).toBeVisible();
    await expect(card.getByRole('radio')).toHaveCount(3);
    await expect(card.locator('input:checked')).toHaveCount(0);
    await expect(card.getByRole('button', { name: 'Record decision', exact: true })).toBeDisabled();
    await expect(card.getByText('Recommended', { exact: true })).toBeVisible();
    await expect(card.getByText(item.presentation.options[0].description, { exact: true })).toBeVisible();
    await expect(card.getByText('UNABRIDGED_EVIDENCE_MARKER', { exact: true })).not.toBeVisible();
    await expect(card.getByText('Apply this approval to all future campaigns', { exact: true })).not.toBeVisible();
    const choices = await card.getByRole('group', { name: 'Choose an option' }).boundingBox();
    const details = await card.locator('details').boundingBox();
    expect(choices!.y).toBeLessThan(details!.y);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`decision-brief-${label}.png`), fullPage: true });
    await card.getByText('Original context and decision details', { exact: true }).click();
    await expect(card.getByText('UNABRIDGED_EVIDENCE_MARKER', { exact: true })).toBeVisible();
    await expect(card.getByRole('heading', { name: 'Original proposed action' })).toBeVisible();
    await card.getByRole('radio', { name: item.presentation.options[0].label, exact: true }).check();
    await card.getByLabel('Your reasoning (optional)').fill('Use the matched fresh-seed comparison.');
    await card.getByRole('button', { name: 'Record decision', exact: true }).click();
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ operation: 'decision.resolve', expected_revision: 2,
      payload: { decision_id: 'specific', choice: 'accept', expected_resolution_revision: 0, comment: 'Use the matched fresh-seed comparison.' } });
    await page.getByRole('button', { name: 'Decision history', exact: true }).click();
    await expect(card.getByText('Choice: Proceed', { exact: true })).toBeVisible();
    await expect(card.getByText('Choice: Approve: run the four-trial comparison', { exact: true })).not.toBeVisible();
  });
}

test('vague choices are clarified individually and internal follow-ups stay with the manager', async ({ page }) => {
  const legacyPresentation = { title: 'Clarify the scope of the next experiment', background: 'A prior request left the scope unclear.',
    proposal: 'The manager has not stated what these choices authorize.', options: [{ id: '0', label: 'Yes' }, { id: '1', label: 'No' }],
    needs_clarification: true, basis: 'legacy', audience: 'researcher', background_is_excerpt: true,
    details: 'The complete legacy discussion remains available without interpreting Yes or No.' };
  const vague = { ...decision('vague', 'Legacy direction request', { state: 'blocked', stale: false,
    can_accept: false, can_refresh: true, blocked_choice_ids: ['0', '1'], reason: 'The choices need clarification.' }),
    action_id: undefined, recommendation: '0', options: [{ id: '0', label: 'Yes' }, { id: '1', label: 'No' }], presentation: legacyPresentation };
  const internal = { ...decision('internal', 'Manager: determine which comparison follows'), action_id: undefined,
    presentation: { ...legacyPresentation, title: 'Choose the manager’s next comparison', audience: 'manager', options: [] },
    freshness: { ...vague.freshness } };
  const current = state([vague, internal]), writes: any[] = [];
  await mock(page, current, command => {
    writes.push(command);
    return { json: { id: command.id, status: 'completed', outcome: {} } };
  });
  await page.goto('/#decisions');
  const card = page.getByRole('article', { name: legacyPresentation.title });
  await expect(card.getByText('Needs clarification', { exact: true })).toBeVisible();
  await expect(card.getByText('Excerpt from the original background. Full text is available below.')).toBeVisible();
  await expect(card.getByRole('radio')).toHaveCount(0);
  await expect(card.getByRole('button', { name: 'Record decision' })).not.toBeVisible();
  await expect(page.getByRole('button', { name: 'Update outdated decisions', exact: true })).not.toBeVisible();
  await expect(page.getByText('1 request needs clearer choices.', { exact: false })).toBeVisible();
  await expect(page.getByRole('article', { name: internal.presentation.title })).not.toBeVisible();
  await card.getByText('Original context and decision details', { exact: true }).click();
  await expect(card.locator('details').getByText('Yes', { exact: true })).toBeVisible();
  await expect(card.locator('details').getByText('No', { exact: true })).toBeVisible();
  await card.getByLabel('Your reasoning (optional)').fill('Specify the allocation before asking me to decide.');
  await card.getByRole('button', { name: 'Ask manager to clarify' }).click();
  expect(writes[0]).toMatchObject({ operation: 'decision.refresh', payload: { decisions: [
    { decision_id: 'vague', expected_resolution_revision: 0, comment: 'Specify the allocation before asking me to decide.' },
  ] } });
  await page.getByRole('button', { name: 'Manager follow-ups (1)', exact: true }).click();
  await expect(page.getByText('Internal questions for the campaign manager; no researcher choice is required yet.')).toBeVisible();
  const followup = page.getByRole('article', { name: internal.presentation.title });
  await expect(followup.getByRole('radio')).toHaveCount(0);
  await expect(followup.getByRole('button', { name: 'Record decision' })).not.toBeVisible();
  await followup.getByRole('button', { name: 'Send to manager' }).click();
  expect(writes[1]).toMatchObject({ operation: 'decision.refresh', payload: { decisions: [
    { decision_id: 'internal', expected_resolution_revision: 0, comment: '' },
  ] } });
  expect(writes.every(command => command.operation === 'decision.refresh')).toBe(true);
  await page.getByRole('button', { name: 'All decisions', exact: true }).click();
  await expect(card).toBeVisible();
  await expect(followup).toBeVisible();
});

test('parallel reviewer preference persists per campaign and applies to bulk and individual reassessment', async ({ page }) => {
  const stale = { state: 'stale', stale: true, can_accept: false, can_refresh: true, blocked_choice_ids: ['accept'] };
  const first = decision('first', 'Review the comparison budget', stale);
  const current = state([first]), writes: any[] = [];
  const originalCampaign = current.campaign;
  const otherCampaign = { ...originalCampaign, id: 'other-campaign', name: 'A different optimization problem' };
  current.campaigns.push(otherCampaign);
  await mock(page, current, command => { writes.push(command); return { json: { id: command.id, status: 'completed', outcome: {} } }; });
  await page.goto('/#decisions');
  await expect(page.getByLabel('Parallel reviewers', { exact: true })).toHaveValue('3');
  await page.getByLabel('Parallel reviewers', { exact: true }).selectOption('5');
  await page.getByRole('button', { name: 'Update outdated decisions', exact: true }).click();
  expect(writes[0]).toMatchObject({ operation: 'decision.refresh', payload: { max_parallel_reviews: 5 } });
  await page.reload();
  await expect(page.getByLabel('Parallel reviewers', { exact: true })).toHaveValue('5');
  current.campaign = otherCampaign;
  await page.getByLabel('Active campaign').selectOption(otherCampaign.id);
  await expect(page.getByLabel('Parallel reviewers', { exact: true })).toHaveValue('3');
  await page.getByLabel('Parallel reviewers', { exact: true }).selectOption('2');
  await page.getByRole('article', { name: first.title }).getByRole('button', { name: 'Ask manager to update' }).click();
  expect(writes[1]).toMatchObject({ operation: 'decision.refresh', campaign_id: otherCampaign.id, payload: { max_parallel_reviews: 2 } });
  current.campaign = originalCampaign;
  await page.getByLabel('Active campaign').selectOption(originalCampaign.id);
  await expect(page.getByLabel('Parallel reviewers', { exact: true })).toHaveValue('5');
  expect(writes).toHaveLength(2);
  expect(writes.every(command => command.operation === 'decision.refresh')).toBe(true);
});

test('one batch displays parallel work, failed-call retry, manager synthesis and completion without approving work', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const progress: any = { phase: 'reviewing', parent_run_id: 'parallel_review_run', total: 3, completed: 1, running: 2, failed: 0,
    max_parallel_reviews: 3, can_retry: false, tasks: [
      { id: 'review-science', role: 'comparative_reviewer', title: 'Scientific evidence and comparison design', status: 'completed' },
      { id: 'review-budget', role: 'comparative_reviewer', title: 'Resources and permission boundaries', status: 'running' },
      { id: 'review-related', role: 'comparative_reviewer', title: 'Related hypotheses and duplicated requests', status: 'running' },
    ] };
  const outdated = { state: 'updating', stale: true, can_accept: false, can_refresh: false, blocked_choice_ids: ['accept'],
    research_run_id: progress.parent_run_id, review_progress: progress };
  const first = decision('parallel-first', 'Compare leading mechanisms', outdated);
  const second = decision('parallel-second', 'Set the comparison allocation', outdated);
  const current = state([first, second]), writes: any[] = [];
  await mock(page, current, command => {
    writes.push(command);
    Object.assign(progress, { phase: progress.completed === progress.total ? 'synthesizing' : 'reviewing',
      running: progress.completed === progress.total ? 0 : 1, failed: 0, can_retry: false });
    if (progress.completed < progress.total) progress.tasks[2].status = 'running';
    delete progress.tasks[2].error;
    return { json: { id: command.id, status: 'completed', outcome: {} } };
  });
  await page.goto('/#decisions');
  const batch = page.locator('.decision-review-progress');
  await expect(batch).toHaveCount(1);
  await expect(batch.getByRole('heading', { name: 'Reviewers reassessing decisions' })).toBeVisible();
  await expect(batch.getByRole('status')).toHaveText('1 of 3 reviews complete · 2 running');
  await expect(batch.getByText('2 decisions in this reassessment · Up to 3 reviewers at once')).toBeVisible();
  await expect(batch.getByText('Resources and permission boundaries')).toBeVisible();
  await expect(batch.getByRole('link', { name: 'View agent log' })).toHaveAttribute('href', '#notebook/agent-log');
  await expect(page.getByRole('radio', { name: /^Proceed/ }).first()).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('parallel-decision-reviews-mobile.png'), fullPage: true });

  Object.assign(progress, { phase: 'partial', completed: 2, running: 0, failed: 1, can_retry: true });
  progress.tasks[1].status = 'completed';
  Object.assign(progress.tasks[2], { status: 'failed', error: 'Provider disconnected after the saved reports completed.' });
  await page.evaluate(() => (window as any).testStream.dispatchEvent(new Event('update')));
  await expect(batch.getByRole('heading', { name: 'Reassessment needs attention' })).toBeVisible();
  await expect(batch.getByRole('status')).toHaveText('2 of 3 reviews complete · 1 failed');
  await batch.getByText('Reviewer activity', { exact: true }).click();
  await expect(batch.getByText(progress.tasks[2].error)).toBeVisible();
  await page.getByLabel('Parallel reviewers', { exact: true }).selectOption('2');
  await batch.getByRole('button', { name: 'Retry unfinished reviews', exact: true }).click();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ operation: 'decision.refresh', payload: { retry_run_id: 'parallel_review_run', max_parallel_reviews: 2,
    decisions: [{ decision_id: first.id, expected_resolution_revision: 0 }, { decision_id: second.id, expected_resolution_revision: 0 }] } });
  await expect(batch.getByRole('status')).toHaveText('2 of 3 reviews complete · 1 running');

  Object.assign(progress, { phase: 'synthesizing', completed: 3, running: 0, failed: 0 });
  progress.tasks[2].status = 'completed';
  await page.evaluate(() => (window as any).testStream.dispatchEvent(new Event('update')));
  await expect(batch.getByRole('heading', { name: 'Manager consolidating', exact: true })).toBeVisible();
  await expect(batch.getByRole('status')).toHaveText('3 of 3 reviews complete');
  await expect(batch.getByText('The campaign manager is reconciling the reports and dependencies before publishing current requests.')).toBeVisible();
  await expect(batch.getByRole('button', { name: /^Retry/ })).not.toBeVisible();

  Object.assign(progress, { phase: 'failed', can_retry: true });
  await page.evaluate(() => (window as any).testStream.dispatchEvent(new Event('update')));
  await batch.getByRole('button', { name: 'Retry manager consolidation', exact: true }).click();
  expect(writes[1]).toMatchObject({ operation: 'decision.refresh', payload: { retry_run_id: 'parallel_review_run', max_parallel_reviews: 2 } });
  expect(writes[1].payload.decisions.every((item: any) => item.comment === undefined && item.desired_choice === undefined)).toBe(true);
  await expect(batch.getByRole('heading', { name: 'Manager consolidating', exact: true })).toBeVisible();
  await expect(batch.getByRole('status')).toHaveText('3 of 3 reviews complete');

  Object.assign(progress, { phase: 'completed' });
  for (const item of [first, second]) Object.assign(item.freshness, { state: 'reviewed', review_completed: true, review_decision_ids: ['parallel-current'] });
  current.decisions.push(decision('parallel-current', 'Current bounded comparison'));
  await page.evaluate(() => (window as any).testStream.dispatchEvent(new Event('update')));
  await expect(batch.getByRole('heading', { name: 'Manager review complete', exact: true })).toBeVisible();
  await expect(batch.getByText('The manager has finished consolidating the review. Read the report and any current recommendations before deciding.')).toBeVisible();
  await expect(page.getByRole('article', { name: 'Current bounded comparison' }).getByRole('radio', { name: /^Proceed/ })).toBeEnabled();
  await expect(page.getByRole('article', { name: first.title }).getByRole('radio', { name: /^Proceed/ })).toBeDisabled();
  expect(writes).toHaveLength(2);
});

test('a failed reassessment explains why retry is unavailable after the campaign changes', async ({ page }) => {
  const item = decision('changed-review', 'Reassess a previous comparison', { state: 'stale', stale: true, can_accept: false,
    can_refresh: true, blocked_choice_ids: ['accept'], review_progress: { phase: 'partial', parent_run_id: 'old_review', total: 3,
      completed: 2, running: 0, failed: 1, max_parallel_reviews: 3, tasks: [], can_retry: false,
      retry_reason: 'Campaign guidance changed. Request a new reassessment using the current campaign snapshot.' } });
  const current = state([item]), writes: any[] = [];
  await mock(page, current, command => { writes.push(command); return { json: { id: command.id, status: 'completed', outcome: {} } }; });
  await page.goto('/#decisions');
  const batch = page.locator('.decision-review-progress');
  await expect(batch.getByText(item.freshness.review_progress.retry_reason)).toBeVisible();
  await expect(batch.getByRole('button', { name: /^Retry/ })).not.toBeVisible();
  await page.getByRole('article', { name: item.title }).getByRole('button', { name: 'Ask manager to update' }).click();
  expect(writes[0]).toMatchObject({ operation: 'decision.refresh', payload: { max_parallel_reviews: 3 } });
  expect(writes[0].payload.retry_run_id).toBeUndefined();
});
