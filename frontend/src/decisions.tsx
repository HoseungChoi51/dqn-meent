import { useState } from 'react';
import { ApiError, errorText, when } from './api';
import type { Decision, DecisionReviewProgress, Json, State } from './api';
import { useCommand } from './commands';
import { Badge, Empty, ErrorNotice, Field, Icon } from './ui';
import { TextContent } from './research';
import type { WorkspaceActions } from './views';

type DecisionReadiness = { stale: boolean; reason: string; blockedChoices: string[]; refreshing: boolean;
  canRefresh: boolean; reviewed: boolean; reviewRunId?: string; reviewDecisionIds?: string[] };

function readiness(decision: Decision, state: State): DecisionReadiness {
  if (decision.freshness) {
    const freshness = decision.freshness;
    return { stale: freshness.stale, reason: freshness.reason, blockedChoices: freshness.blocked_choice_ids,
      refreshing: freshness.state === 'updating', canRefresh: freshness.can_refresh,
      reviewed: freshness.state === 'reviewed' || freshness.review_completed === true,
      reviewRunId: freshness.research_run_id, reviewDecisionIds: freshness.review_decision_ids };
  }
  const action = state.actions?.find((item: Json) => item.id === decision.action_id);
  const run = state.research_runs.find(item => item.id === decision.research_run_id);
  const executable = !!decision.action_id || !!(decision.trial_id && decision.incremental_solver_calls);
  const originalCharter = decision.charter_version ?? action?.charter_version ?? run?.charter_version;
  const currentCharter = state.campaign?.version;
  const originalGuidance = action?.guidance_revision ?? decision.guidance_revision ?? run?.guidance_revision;
  const currentGuidance = state.manager_context?.guidance_revision;
  const reasons: string[] = [];
  if (executable && originalCharter != null && currentCharter != null && originalCharter !== currentCharter) {
    reasons.push(`This recommendation used charter v${originalCharter}; the campaign is now on v${currentCharter}.`);
  }
  if (executable && originalGuidance != null && currentGuidance != null && originalGuidance !== currentGuidance) {
    reasons.push(`Researcher guidance changed from revision ${originalGuidance} to ${currentGuidance}.`);
  }
  const stale = decision.status === 'pending' && reasons.length > 0;
  return { stale, reason: reasons.join(' '), blockedChoices: stale ? (decision.action_id ? ['accept'] : executable ? ['0'] : []) : [],
    refreshing: false, canRefresh: stale, reviewed: false };
}

function options(decision: Decision) {
  return decision.options.map(option => typeof option === 'string' ? { id: option, label: option } : option);
}

function isManagerFollowup(decision: Decision) { return decision.presentation?.audience === 'manager'; }

function readyToDecide(decision: Decision, state: State) {
  const current = readiness(decision, state);
  return decision.status === 'pending' && !decision.presentation?.needs_clarification && !current.stale
    && !current.canRefresh && !current.refreshing && !current.reviewed && decision.freshness?.can_accept !== false;
}

function reviewPreference(campaignId: string) {
  try {
    const value = Number(localStorage.getItem(`optimization.decision-review-parallelism:${campaignId}`));
    return Number.isInteger(value) && value >= 1 && value <= 8 ? value : 3;
  } catch { return 3; }
}

export function Decisions({ state, actions }: { state: State; actions: WorkspaceActions }) {
  const command = useCommand(state.campaign);
  const campaignId = state.campaign?.id || '';
  const [reviewSetting, setReviewSetting] = useState(() => ({ campaignId, value: reviewPreference(campaignId) }));
  const parallelReviews = reviewSetting.campaignId === campaignId ? reviewSetting.value : reviewPreference(campaignId);
  const [filter, setFilter] = useState('pending');
  const [comments, setComments] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const pending = state.decisions.filter(decision => ['pending', 'executing'].includes(decision.status));
  const researcherPending = pending.filter(decision => !isManagerFollowup(decision));
  const managerPending = pending.filter(isManagerFollowup);
  const unclear = researcherPending.filter(decision => decision.presentation?.needs_clarification);
  const outdated = researcherPending.filter(decision => !decision.presentation?.needs_clarification
    && (readiness(decision, state).stale || decision.action_id) && readiness(decision, state).canRefresh);
  const visible = state.decisions.filter(decision => filter === 'all'
    || (filter === 'pending' ? researcherPending.includes(decision)
      : filter === 'manager' ? managerPending.includes(decision) : !pending.includes(decision)));
  if (filter === 'pending') visible.sort((left, right) => Number(!readyToDecide(left, state)) - Number(!readyToDecide(right, state))
    || (Date.parse(right.created_at || '') || 0) - (Date.parse(left.created_at || '') || 0));
  const readyCount = researcherPending.filter(decision => readyToDecide(decision, state)).length;
  const reviewCount = researcherPending.filter(decision => decision.status === 'pending' && !readyToDecide(decision, state)).length;
  const commentFor = (decision: Decision) => comments[decision.id] ?? decision.comment ?? '';
  const reviewBatches = new Map<string, { progress: DecisionReviewProgress; decisions: Decision[] }>();
  for (const decision of pending) {
    const progress = decision.freshness?.review_progress;
    if (!progress) continue;
    const id = progress.parent_run_id || decision.freshness?.research_run_id || decision.freshness?.refresh_command_id || decision.id;
    const batch = reviewBatches.get(id);
    if (batch) batch.decisions.push(decision);
    else reviewBatches.set(id, { progress, decisions: [decision] });
  }

  function setParallelReviews(value: number) {
    setReviewSetting({ campaignId, value });
    try { localStorage.setItem(`optimization.decision-review-parallelism:${campaignId}`, String(value)); } catch { /* The current selection still applies. */ }
  }

  async function update(decisions: Decision[], desiredChoices: Record<string, string | undefined> = {}, retryRunId?: string) {
    setBusy(true); setError('');
    try {
      await command('decision.refresh', { max_parallel_reviews: parallelReviews, ...(retryRunId ? { retry_run_id: retryRunId } : {}), decisions: decisions.map(decision => ({ decision_id: decision.id,
        expected_resolution_revision: decision.resolution_revision ?? 0,
        ...(!retryRunId ? { comment: commentFor(decision), ...(desiredChoices[decision.id] ? { desired_choice: desiredChoices[decision.id] } : {}) } : {}) })) });
      await actions.refresh();
      actions.notify(retryRunId ? 'Retry requested. Completed reviewer reports will be reused.' : decisions.length === 1 ? 'Campaign manager update requested.' : 'Campaign manager will reassess the outdated decisions together.');
    } catch (failure) {
      setError(errorText(failure));
      await actions.refresh();
    } finally { setBusy(false); }
  }

  return <>
    <div className="page-heading"><div><span className="eyebrow">Decision inbox</span><h1>Some questions need your judgment.</h1>
      <p>Review the proposal, what each choice changes, and the manager’s recommendation.</p></div><Badge>{researcherPending.length} awaiting your input</Badge></div>
    {pending.length > 0 && <section className="decision-review-settings" aria-label="Manager reassessment settings">
      <div className="decision-review-limit"><label htmlFor="decision-review-parallelism">Parallel reviewers</label><select id="decision-review-parallelism" value={parallelReviews}
        disabled={busy} onChange={event => setParallelReviews(Number(event.target.value))}>
        {[1, 2, 3, 4, 5, 6, 7, 8].map(value => <option key={value} value={value}>{value}</option>)}</select></div>
      <p>Maximum simultaneous reviewer calls for your next reassessment. The manager consolidates their reports afterward. Saved for this campaign in this browser.</p>
    </section>}
    {[...reviewBatches].map(([id, batch]) => <ReviewProgress key={id} id={id} progress={batch.progress} decisions={batch.decisions} busy={busy}
      retry={() => update(batch.decisions, {}, batch.progress.parent_run_id || id)} />)}
    {researcherPending.length > 0 && <p className="decision-clarity-summary">{readyCount} ready to decide · {reviewCount} need review or clarification</p>}
    {outdated.length > 0 && <section className="panel decision-update-summary" aria-label="Outdated decisions">
      <div><strong>{outdated.length} {outdated.length === 1 ? 'decision needs' : 'decisions need'} an update</strong>
        <p>The Campaign manager can reassess these recommendations against the current charter, guidance and evidence. Your notes and the original decisions stay in the record.</p></div>
      <button className="button secondary" disabled={busy} onClick={() => void update(outdated.slice(0, 50))}>
        <Icon name="refresh" size={16} />{busy ? 'Requesting update…' : outdated.length > 50 ? 'Update next 50 outdated decisions' : 'Update outdated decisions'}</button>
    </section>}
    {unclear.length > 0 && <p className="decision-clarity-summary" role="status">{unclear.length} {unclear.length === 1 ? 'request needs' : 'requests need'} clearer choices. Ask the manager to clarify each request before deciding.</p>}
    <ErrorNotice text={error} />
    <div className="segment-tabs">{[['pending', 'Awaiting your input'], ['manager', `Manager follow-ups (${managerPending.length})`], ['resolved', 'Decision history'], ['all', 'All decisions']].map(([key, label]) =>
      <button key={key} className={filter === key ? 'selected' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div>
    {filter === 'manager' && <p className="decision-clarity-summary">Internal questions for the campaign manager; no researcher choice is required yet.</p>}
    {visible.length ? <div className="decision-list">{visible.map(decision => <DecisionCard key={decision.id}
      decision={decision} state={state} actions={actions} readiness={readiness(decision, state)} busyUpdating={busy}
      comment={commentFor(decision)} setComment={comment => setComments(previous => ({ ...previous, [decision.id]: comment }))}
      update={desiredChoice => update([decision], { [decision.id]: desiredChoice })} />)}</div> : <section className="panel"><Empty icon="decisions"
        title={filter === 'pending' ? 'Room to keep exploring' : filter === 'manager' ? 'No manager follow-ups pending' : 'No decisions recorded yet'}>
        When a choice needs your expertise, the Campaign manager will bring the options here. Independent work can continue.
      </Empty></section>}
  </>;
}

function ReviewProgress({ id, progress, decisions, busy, retry }: { id: string; progress: DecisionReviewProgress; decisions: Decision[];
  busy: boolean; retry: () => Promise<void> }) {
  const active = ['queued', 'reviewing', 'synthesizing'].includes(progress.phase);
  const heading = progress.phase === 'synthesizing' ? 'Manager consolidating' : progress.phase === 'completed' ? 'Manager review complete'
    : progress.phase === 'queued' ? 'Reassessment queued' : progress.phase === 'partial' ? 'Reassessment needs attention'
    : progress.phase === 'failed' ? 'Reassessment failed' : progress.phase === 'stopped' ? 'Reassessment stopped' : 'Reviewers reassessing decisions';
  return <section className="panel decision-review-progress" aria-labelledby={`review-${id}-title`}>
    <div className="row-between"><div><h2 id={`review-${id}-title`}>{heading}</h2>
      <p role="status">{progress.completed} of {progress.total} reviews complete{progress.running > 0 && ` · ${progress.running} running`}
        {progress.failed > 0 && ` · ${progress.failed} failed`}</p></div>
      <a className="button small secondary" href="#notebook/agent-log"><Icon name="notebook" size={14} />View agent log</a></div>
    <p className="help-text">{decisions.length} {decisions.length === 1 ? 'decision' : 'decisions'} in this reassessment · Up to {progress.max_parallel_reviews} reviewers at once</p>
    {progress.phase === 'synthesizing' && <p>The campaign manager is reconciling the reports and dependencies before publishing current requests.</p>}
    {progress.phase === 'completed' && <p>The manager has finished consolidating the review. Read the report and any current recommendations before deciding.</p>}
    {['partial', 'failed', 'stopped'].includes(progress.phase) && <p>Completed reports remain saved. Open the agent log to inspect the interruption.</p>}
    {progress.can_retry && <div className="decision-review-retry"><button className="button secondary" disabled={busy} onClick={() => void retry()}>
      <Icon name="refresh" size={16} />{busy ? 'Requesting retry…' : progress.completed === progress.total ? 'Retry manager consolidation' : 'Retry unfinished reviews'}</button>
      <p>Reuses successful reports. Uses the original request and notes. Request a new review to change direction. Only unfinished calls will run again.</p></div>}
    {!active && progress.phase !== 'completed' && !progress.can_retry && progress.retry_reason && <p>{progress.retry_reason}</p>}
    {progress.tasks.length > 0 && <details open={active || undefined} className="decision-review-tasks"><summary>Reviewer activity</summary>
      <ul>{progress.tasks.map(task => <li key={task.id}><div><strong>{task.title || task.role.replaceAll('_', ' ')}</strong>
        <Badge tone={task.status === 'completed' ? 'green' : 'amber'}>{task.status.replaceAll('_', ' ')}</Badge></div>
        <small>{task.role.replaceAll('_', ' ')}</small>{task.error && <p>{task.error}</p>}</li>)}</ul></details>}
  </section>;
}

function DecisionCard({ decision, state, actions, readiness: status, busyUpdating, comment, setComment, update }: {
  decision: Decision; state: State; actions: WorkspaceActions; readiness: DecisionReadiness; busyUpdating: boolean;
  comment: string; setComment: (comment: string) => void; update: (desiredChoice?: string) => Promise<void>;
}) {
  const command = useCommand(state.campaign);
  const [choice, setChoice] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const pending = decision.status === 'pending';
  const blocked = (option: string) => status.blockedChoices.includes(option);
  const originalOptions = options(decision);
  const presentation = decision.presentation;
  // Presentation changes wording only. Authority and submitted option IDs
  // always come from the original decision, including historical answers.
  const available = originalOptions.map(option => {
    const display = presentation?.options.find(item => item.id === option.id);
    return display ? { ...option, label: display.label, description: display.description ?? option.description } : option;
  });
  const managerFollowup = isManagerFollowup(decision);
  const needsClarification = presentation?.needs_clarification ?? false;
  const visibleOptions = managerFollowup ? [] : available.filter(option => !needsClarification || !blocked(option.id));
  const title = presentation?.title || decision.title;
  const originalContext = decision.context || '';
  const details = presentation?.details || originalContext;
  const briefContext = originalContext.replace(/\s+/g, ' ').trim();
  const background = presentation?.background || (briefContext.length <= 260 ? briefContext : `${briefContext.slice(0, 257)}…`);
  const proposal = presentation?.proposal || 'Review the original request and its choices below.';
  const recommendationId = originalOptions.find(option => option.id === decision.recommendation || option.label === decision.recommendation)?.id;
  const recommendation = available.find(option => option.id === recommendationId)?.label || decision.recommendation;
  const label = decision.status === 'executing' ? 'Recording decision' : !pending ? 'Decision recorded'
    : status.refreshing ? 'Manager update requested' : status.reviewed ? 'Review available'
    : managerFollowup ? 'For the campaign manager' : needsClarification ? 'Needs clarification'
    : status.stale || status.canRefresh ? 'Needs update' : 'Your input requested';

  async function resolve() {
    if (blocked(choice)) return;
    setBusy(true); setError('');
    try {
      await command('decision.resolve', { decision_id: decision.id, choice, comment,
        expected_resolution_revision: decision.resolution_revision ?? 0 });
      await actions.refresh(); actions.notify('Decision recorded. Research can continue from your direction.');
    } catch (failure) {
      setError(errorText(failure) + (failure instanceof ApiError && [400, 409].includes(failure.status)
        ? ' The inbox has been refreshed. Your comment is still here.' : ''));
      await actions.refresh();
    } finally { setBusy(false); }
  }

  return <article className="panel decision-card" aria-label={title}>
    <div className="row-between"><Badge tone={pending || decision.status === 'executing' ? 'amber' : 'green'}>{label}</Badge><time>{when(decision.created_at)}</time></div>
    <h2>{title}</h2>
    {presentation?.scope_label && <p className="decision-scope">{presentation.scope_label}</p>}
    <div className="decision-brief">
      <section aria-labelledby={`${decision.id}-background`}><h3 id={`${decision.id}-background`}>Background</h3>
        <TextContent text={background || 'The manager has requested a decision for this campaign.'} />
        {presentation?.background_is_excerpt && <p className="decision-excerpt-note">Excerpt from the original background. Full text is available below.</p>}</section>
      <section className="decision-proposal" aria-labelledby={`${decision.id}-proposal`}><h3 id={`${decision.id}-proposal`}>Proposal</h3>
        <TextContent text={proposal} /></section>
    </div>
    {pending && needsClarification && !managerFollowup && <div className="callout amber decision-stale-notice"><strong>The choices need clarification.</strong>
      <p>Ask the manager to state the proposal and what each choice would authorize. The original wording remains in the details below.</p></div>}
    {pending && (status.stale || status.refreshing || status.reviewed || (status.canRefresh && !needsClarification && !managerFollowup)) && <div className="callout amber decision-stale-notice"><strong>
      {status.reviewed ? 'The manager has reviewed this recommendation.' : status.refreshing ? 'The Campaign manager is updating this recommendation.' : 'This recommendation needs a fresh manager review.'}</strong>
      <p>{status.reason}</p>{visibleOptions.some(option => !blocked(option.id)) && <p>You can still choose among the available options below.</p>}</div>}
    {status.reviewed && <div className="decision-review-links"><a className="button secondary" href="#notebook/conversation">View current review<Icon name="chat" size={15} /></a>
      {!!status.reviewDecisionIds?.length && <p className="help-text">The review produced {status.reviewDecisionIds.length} current {status.reviewDecisionIds.length === 1 ? 'decision' : 'decisions'} in this inbox. Review those choices before committing resources.</p>}
      {status.reviewRunId && <small>Research run: {status.reviewRunId}</small>}</div>}
    {pending ? <>
      {visibleOptions.length > 0 && <fieldset className="decision-options"><legend>Choose an option</legend>{visibleOptions.map((option, index) => <label key={option.id}
        className={`${choice === option.id ? 'selected' : ''} ${blocked(option.id) ? 'unavailable' : ''}`}>
        <input type="radio" name={`decision-${decision.id}`} value={option.id} checked={choice === option.id}
          aria-labelledby={`${decision.id}-choice-${index}-label`} aria-describedby={`${decision.id}-choice-${index}-description`}
          disabled={busy || blocked(option.id)} onChange={() => setChoice(option.id)} />
        <span><span className="decision-option-heading"><strong id={`${decision.id}-choice-${index}-label`}>{option.label}</strong>
          {option.id === recommendationId && !needsClarification && <Badge>{status.stale ? 'Previous recommendation' : 'Recommended'}</Badge>}</span>
          <span id={`${decision.id}-choice-${index}-description`}>{option.description && <small>{option.description}</small>}
            {blocked(option.id) && <small>{status.reviewed ? 'Use a current recommendation from the review.' : 'Needs a current recommendation from the manager.'}</small>}</span></span>
      </label>)}</fieldset>}
      {decision.recommendation && !needsClarification && !managerFollowup && <div className="recommendation decision-recommendation"><Icon name="spark" size={17} /><div>
        <strong>{status.stale ? 'Previous recommendation' : 'Manager recommendation'}: {recommendation}</strong>
        {presentation?.recommendation_reason && <TextContent text={presentation.recommendation_reason} />}
      </div></div>}
      <Field label="Your reasoning (optional)"><textarea value={comment} onChange={event => setComment(event.target.value)} rows={2}
        placeholder="Leave context for the next research step…" /></Field>
      <ErrorNotice text={error || decision.delivery_error || ''} />
      <div className="row-between decision-footer"><button className="text-button" disabled={busy || busyUpdating}
        onClick={() => void actions.research(`Help me reason about decision ${decision.id}, "${decision.title}". Explain the alternatives and incremental costs without resolving it on my behalf.${comment ? `\nMy notes: ${comment}` : ''}`, 'discuss')}>
        {managerFollowup ? 'Discuss with manager' : 'Discuss this choice'}<Icon name="chat" size={15} /></button>
        {(status.canRefresh || status.refreshing) && <button className={`button ${managerFollowup || needsClarification ? 'primary' : 'secondary'}`} disabled={busy || busyUpdating || !status.canRefresh} onClick={() => { setError(''); void update(choice || undefined); }}>
          <Icon name="refresh" size={16} />{status.refreshing ? 'Manager update requested' : managerFollowup ? 'Send to manager' : needsClarification ? 'Ask manager to clarify' : 'Ask manager to update'}</button>}
        {visibleOptions.length > 0 && <button className="button primary" disabled={!choice || busy || busyUpdating || blocked(choice)} onClick={() => void resolve()}>
          {busy ? 'Recording…' : 'Record decision'}<Icon name="check" size={16} /></button>}
      </div>
    </> : <div className="decision-outcome"><strong>{decision.status === 'executing' ? 'Your decision is being applied.'
      : `Choice: ${originalOptions.find(option => option.id === decision.choice)?.label || decision.choice || 'Recorded'}`}</strong>
      {decision.comment && <p>{decision.comment}</p>}</div>}
    <details className="decision-details"><summary>Original context and decision details</summary>
      <h3>Original request</h3><p>{decision.title}</p><TextContent text={details} />
      {originalContext && details !== originalContext && !details.includes(originalContext) && <><h3>Original recorded context</h3><TextContent text={originalContext} /></>}
      {originalOptions.length > 0 && <><h3>Original choices</h3><ul>{originalOptions.map(option => <li key={option.id}>
        <strong>{option.label}</strong>{option.description && <p>{option.description}</p>}</li>)}</ul></>}
      {decision.recommendation && <p>Original recommendation: {originalOptions.find(option => option.id === recommendationId)?.label || decision.recommendation}</p>}
      {presentation?.action_details && <><h3>Original proposed action</h3><pre>{JSON.stringify(presentation.action_details, null, 2)}</pre></>}
      <p className="decision-record-id">Decision: {decision.id}{decision.charter_version != null && ` · Charter v${decision.charter_version}`}</p>
    </details>
  </article>;
}
