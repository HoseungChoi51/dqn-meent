import { useState } from 'react';
import type { Json, State } from './api';
import { errorText, seconds } from './api';
import { Badge, ErrorNotice, Status } from './ui';
import type { WorkspaceActions } from './views';
import './studyProgress.css';

const runningStatuses = new Set(['running', 'pausing', 'stopping']);
const endedStatuses = new Set(['completed', 'failed', 'stopped', 'budget_exhausted']);
const attentionStatuses = new Set(['failed', 'interrupted', 'paused', 'stopped', 'budget_exhausted']);
const measured = (...values: unknown[]): number | null => values.find(value => typeof value === 'number' && Number.isFinite(value)) as number | undefined ?? null;
const number = (value: number | null) => value === null ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: 1 });
function timestamp(value: unknown): number | null {
  if (typeof value !== 'string' || !value) return null;
  const result = Date.parse(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isFinite(result) ? result : null;
}

function operationalRows(assessment: Json, state: State) {
  const trials = new Map(state.trials.map(trial => [trial.id, trial]));
  return (assessment.cells || []).map((cell: Json) => {
    const trial: Json | undefined = cell.trial_id ? trials.get(cell.trial_id) : undefined;
    const method = assessment.methods?.[cell.method_id] || {};
    const status = trial?.status || cell.status;
    const progress = endedStatuses.has(status) ? { ...trial?.progress, ...trial?.result } : { ...trial?.result, ...trial?.progress };
    const values = [trial?.execution_seconds, trial?.progress?.elapsed_seconds, trial?.result?.elapsed_seconds]
      .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
    const workerSeconds = trial?.isolation_policy ? measured(trial.execution_seconds) : values.length ? Math.max(...values) : cell.trial_id ? null : 0;
    const completion = trial?.completion || method.completion || { unit: 'evaluation_requests', count: trial?.max_steps ?? method.max_steps };
    const requests = measured(progress.budget_requests, progress.evaluations);
    const completedCount = completion.unit === 'optimizer_decisions' ? measured(progress.diagnostics?.decisions) : measured(progress.step);
    const scientificComplete = trial ? status === 'completed' && progress.scientific_complete === true : cell.scientific_complete === true;
    return { cell, trial, method, progress, status, workerSeconds, completion, requests, completedCount, scientificComplete,
      requestCap: measured(trial?.max_steps, method.max_steps), timeCap: measured(trial?.wall_seconds, method.wall_seconds),
      reportedAt: timestamp(trial?.progress?.updated_at), ended: endedStatuses.has(status),
      needsAttention: attentionStatuses.has(status) || status === 'completed' && !scientificComplete };
  });
}

export function studySchedulingBudget(assessment: Json, state: State) {
  const rows = operationalRows(assessment, state);
  const missing = rows.filter((row: Json) => !row.cell.trial_id);
  const known = missing.every((row: Json) => row.timeCap !== null);
  const required = known ? missing.reduce((sum: number, row: Json) => sum + row.timeCap, 0) : null;
  const cap = measured(state.campaign?.compute_budget_seconds), allocated = measured(state.budget?.allocated_seconds);
  const reserve = measured(state.campaign?.validation_reserve_seconds);
  const available = !assessment.execution_id && cap !== null && allocated !== null && reserve !== null ? Math.max(0, cap - allocated - reserve) : null;
  return { required, available, missing: missing.length, cap, allocated, reserve,
    blocked: required !== null && available !== null && required > available + 1e-6 };
}

function Counter({ label, value, target }: { label: string; value: number | null; target: number | null }) {
  return <div className="study-progress-counter"><span>{number(value)} / {number(target)}</span>
    {value !== null && target !== null && target > 0 && <progress aria-label={label} max={target} value={Math.min(target, Math.max(0, value))} />}</div>;
}

export function StudyProgress({ assessment, state, actions, onRefresh }: {
  assessment: Json; state: State; actions: WorkspaceActions; onRefresh: () => Promise<void>;
}) {
  const [refreshing, setRefreshing] = useState(false), [error, setError] = useState('');
  const rows = operationalRows(assessment, state), budget = studySchedulingBudget(assessment, state);
  const running = rows.filter((row: Json) => runningStatuses.has(row.status)).length;
  const queued = rows.filter((row: Json) => ['queued', 'resuming'].includes(row.status)).length;
  const ended = rows.filter((row: Json) => row.ended).length;
  const scientific = rows.filter((row: Json) => row.scientificComplete).length;
  const evidence = rows.filter((row: Json) => row.cell.evidence_complete).length;
  const attention = rows.filter((row: Json) => row.needsAttention).length;
  const phase = assessment.release ? 'Released' : running ? 'Running' : queued ? 'Queued' : attention ? 'Needs attention'
    : budget.missing === rows.length ? 'Not started' : ended === rows.length
      ? assessment.complete ? 'Finished · ready for release' : 'Finished · evidence pending' : 'Partly scheduled';
  const knownSpend = rows.reduce((sum: number, row: Json) => sum + (row.workerSeconds ?? 0), 0);
  const unknownSpend = rows.some((row: Json) => row.workerSeconds === null || row.progress.unknown_worker_cost);
  const totalCap = rows.every((row: Json) => row.timeCap !== null)
    ? rows.reduce((sum: number, row: Json) => sum + row.timeCap, 0) : null;
  const latest = rows.reduce((value: number | null, row: Json) => row.reportedAt === null ? value : Math.max(value ?? 0, row.reportedAt), null);
  const now = Date.now();
  async function refresh() {
    setRefreshing(true); setError('');
    try { await onRefresh(); } catch (e) { setError(errorText(e)); } finally { setRefreshing(false); }
  }
  return <section className="study-progress" aria-label="Study run progress">
    <div className="study-progress-heading"><div><h3>Run progress</h3><Badge tone={attention ? 'amber' : running || assessment.release ? 'green' : ''}>{phase}</Badge></div>
      <button type="button" className="button small secondary" disabled={refreshing} onClick={() => void refresh()}>{refreshing ? 'Refreshing…' : 'Refresh run progress'}</button></div>
    <p className="help-text">The current study is the selected scientific scope. Its runs begin when they are scheduled.</p>
    {budget.missing === rows.length && !assessment.release && <p className="study-progress-not-started">No runs have been queued for this study.{assessment.execution_id
      ? ' Its frozen execution controls admission.' : ' Use “Schedule missing cells” after reviewing the available campaign budget.'}</p>}
    <div className="study-progress-counts" role="status"><span><strong>{rows.length}</strong> planned runs</span><span><strong>{budget.missing}</strong> not scheduled</span>
      <span><strong>{queued}</strong> queued</span><span><strong>{running}</strong> running or stopping</span><span><strong>{ended}</strong> processes finished</span>
      {!!attention && <span><strong>{attention}</strong> need attention</span>}</div>
    <progress className="study-progress-total" aria-label="Finished worker processes" max={Math.max(1, rows.length)} value={ended} />
    <p className="help-text">{scientific} / {rows.length} met the scientific completion target · {evidence} / {rows.length} have complete evidence. A finished process can still be incomplete or need checks.</p>
    <div className="study-progress-budget"><p>Reported optimizer worker time: <strong>{number(knownSpend)} seconds ({seconds(knownSpend)})</strong>{unknownSpend && ' plus unreported or uncertain time'} · frozen run caps: <strong>{number(totalCap)} seconds{totalCap !== null && ` (${seconds(totalCap)})`}</strong>.</p>
      <p>Missing optimizer run caps: {number(budget.required)} seconds{!assessment.execution_id && ` · campaign room: ${number(budget.available)} seconds`}.</p>
      {assessment.execution_id && <p className="help-text">Template cells use their execution grant and admission rules. The campaign budget is not charged a second time here.</p>}
      {budget.available !== null && <p className="help-text">Campaign cap {number(budget.cap)} − already allocated or spent {number(budget.allocated)} − validation reserve {number(budget.reserve)} seconds.</p>}
      {budget.blocked && <div className="study-progress-budget-warning"><p>Scheduling is blocked by the current campaign budget: the missing run caps exceed available room by {number(budget.required! - budget.available!)} seconds.</p>
        <button type="button" className="button small secondary" onClick={() => actions.campaign(true)}>Revise campaign budget</button>
        <p className="help-text">Increase the campaign cap or define a linked study with smaller allocations. Existing frozen run caps stay fixed.</p></div>}
      <p className="help-text">Worker time sums concurrent runs. Run caps are limits, not an estimated finish time. Missing-run totals exclude additional diagnostic and validation allocations.</p></div>
    <div className="table-scroll"><table className="data-table study-progress-table"><thead><tr><th>Method / seed</th><th>Run state</th><th>Evaluation requests</th><th>Scientific target</th><th>Worker time / cap</th><th>Latest worker report</th></tr></thead>
      <tbody>{rows.map((row: Json) => {
        const age = row.reportedAt === null ? null : Math.max(0, (now - row.reportedAt) / 1000);
        const stale = row.status === 'running' && age !== null && age > 60;
        const completionTarget = measured(row.completion.count);
        const label = `${row.method.algorithm || row.trial?.algorithm || 'Method'} seed ${row.cell.seed}`;
        return <tr key={`${row.cell.method_id}-${row.cell.instance_digest}-${row.cell.seed}`}>
          <td><strong>{row.method.algorithm || row.trial?.algorithm || 'Method'} · seed {row.cell.seed}</strong><small>{row.cell.instance_name}</small>
            {row.cell.trial_id ? <a href={`#experiments/${encodeURIComponent(row.cell.trial_id)}`}>Open experiment <small>{row.cell.trial_id}</small></a>
              : <small>{row.cell.method_id?.slice(0, 10)} · no experiment allocated</small>}</td>
          <td><Status status={row.status} />{row.scientificComplete && <small>Scientific target met</small>}
            {row.ended && !row.scientificComplete && <small>Process ended before scientific completion</small>}
            {row.cell.evidence_complete && <small>Evidence complete</small>}</td>
          <td><Counter label={`Evaluation request allocation · ${label}`} value={row.requests} target={row.requestCap} /><small>Requests used / request cap</small></td>
          <td><Counter label={`Scientific completion target · ${label}`} value={row.completedCount} target={completionTarget} />
            <small>{row.completion.unit === 'optimizer_decisions' ? 'Optimizer decisions' : 'Completed trajectory observations'} / target</small></td>
          <td>{number(row.workerSeconds)} / {number(row.timeCap)} seconds<small>Reported time / time cap</small></td>
          <td>{row.reportedAt === null ? 'Awaiting first report' : <><time dateTime={new Date(row.reportedAt).toISOString()}>{new Date(row.reportedAt).toLocaleTimeString()}</time>
            <small>{seconds(age)} ago</small></>}
            {stale && <small className="study-progress-stale">No new report for over a minute; the worker may be in a long evaluation or update. Inspect the experiment if this persists.</small>}</td>
        </tr>;
      })}</tbody></table></div>
    <p className="help-text">Updates follow workspace events and the six-second state poll. Values are reported measurements; no finish-time prediction is made.
      {latest !== null && <> Latest worker report: <time dateTime={new Date(latest).toISOString()}>{new Date(latest).toLocaleString()}</time>.</>}</p>
    <ErrorNotice text={error} />
  </section>;
}
