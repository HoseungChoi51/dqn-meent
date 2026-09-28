import { useEffect, useState } from 'react';
import { errorText, when } from './api';
import type { Json, State } from './api';
import { useCommand } from './commands';
import { Badge, ErrorNotice, Icon } from './ui';

export type ResearchProgressState = {
  status: 'idle' | 'queued' | 'running' | 'paused' | 'waiting' | 'blocked' | 'completed' | 'failed';
  headline: string; message: string; active: boolean; can_resume?: boolean; updated_at?: string | null;
  session?: { id: string; status: string; control_revision: number } | null;
  request?: { id: string; message: string; created_at?: string; status: string } | null;
  retryable_task_ids?: string[];
  task_counts: Record<string, number>;
  agents: { task_id: string; role: string; stage: string; status: string; model?: string | null;
    reasoning_effort?: string | null; started_at?: string | null; last_activity_at?: string | null;
    activity?: string | null; wait_reason?: string | null; active?: boolean;
    error_code?: string | null; error_message?: string | null }[];
};

const words = (value: string) => value.replaceAll('_', ' ');
const shortened = (value: string, maximum = 180) => value.length > maximum ? `${value.slice(0, maximum - 1).trimEnd()}…` : value;
function timestamp(value?: string | null) {
  return value ? Date.parse(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`) : NaN;
}
function age(value: string | null | undefined, now: number) {
  const elapsed = Math.max(0, Math.floor((now - timestamp(value)) / 1000));
  if (!Number.isFinite(elapsed)) return 'unknown';
  if (elapsed < 60) return `${elapsed}s`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
  return `${Math.floor(elapsed / 3600)}h ${Math.floor(elapsed / 60) % 60}m`;
}

export function researchRequestNotice(state: State, outcome?: Json): string {
  const progress = state.research_progress;
  const request = (state.manager_commands || []).find((item: Json) => item.id === outcome?.manager_command_id);
  if (request?.status === 'waiting_discovery' || progress?.session?.status === 'paused')
    return 'Request saved. Discovery is paused; select Resume discovery in the progress panel to begin.';
  if (request?.status === 'waiting_provider') return 'Request saved. Waiting for model access; see the progress panel.';
  if (request?.status === 'blocked') return 'Request saved but blocked. See the progress panel for the reason.';
  return 'Research request saved for the campaign manager. Follow its status in the progress panel.';
}

export function ResearchProgress({ state, refresh, connectionError }: {
  state: State; refresh: () => Promise<void>; connectionError?: string;
}) {
  const progress: ResearchProgressState | undefined = state.research_progress;
  const command = useCommand(state.campaign);
  const [now, setNow] = useState(Date.now()), [busy, setBusy] = useState<'resume' | 'retry' | null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => { setError(''); setNotice(''); }, [state.campaign?.id]);
  if (!state.campaign || !progress || progress.status === 'idle' && !progress.request && !progress.session) return null;

  const stopped = progress.session?.status === 'stopped' && !progress.active;
  const status = stopped ? 'stopped' : progress.status;
  const tone = ['blocked', 'failed', 'stopped'].includes(status) ? 'red'
    : ['paused', 'waiting', 'queued'].includes(status) ? 'amber' : status === 'running' || status === 'completed' ? 'green' : '';
  const activeAgents = progress.agents.filter(agent => agent.active);
  const shownAgents = (activeAgents.length ? activeAgents : progress.agents).slice(0, 2);
  const counts = progress.task_counts || {};
  const showResume = progress.session?.status === 'paused' && progress.can_resume !== false;
  const showRetry = !!progress.retryable_task_ids?.length && !!progress.session
    && !['completed', 'stopped', 'exhausted'].includes(progress.session.status);
  const requestLines = progress.request?.message.split('\n').filter(Boolean) || [];
  const direction = requestLines.find(line => line.startsWith('Researcher direction:'));

  async function resume() {
    if (!progress?.session || busy) return;
    setBusy('resume'); setError(''); setNotice('');
    try {
      await command('discovery.control', { session_id: progress.session.id, action: 'resume', expected_control_revision: progress.session.control_revision });
      await refresh();
    } catch (failure) {
      setError(errorText(failure));
      await refresh();
    } finally { setBusy(null); }
  }

  async function retry() {
    if (!progress?.session || !progress.retryable_task_ids?.length || busy) return;
    setBusy('retry'); setError(''); setNotice('');
    try {
      await command('discovery.retry', {
        session_id: progress.session.id, expected_control_revision: progress.session.control_revision,
        task_ids: progress.retryable_task_ids, reason: 'Researcher retried failed tasks from progress panel',
      });
      setNotice(progress.session.status === 'paused'
        ? 'Failed tasks were queued for another attempt. Discovery is paused; resume when ready.'
        : 'Failed tasks were queued for another attempt.');
      await refresh();
    } catch (failure) {
      setError(errorText(failure));
      await refresh();
    } finally { setBusy(null); }
  }

  return <section className={`research-progress research-progress-${status}`} aria-label="Campaign research progress" data-status={status}>
    <div className="research-progress-heading">
      <div className="research-progress-title" role="status" aria-live="polite">
        <Badge tone={tone}>{progress.active && !connectionError ? <span className="research-progress-spinner" aria-hidden="true" /> : <Icon name={status === 'paused' ? 'pause' : status === 'completed' ? 'check' : ['failed', 'blocked'].includes(status) ? 'warning' : 'clock'} size={13} />}{words(status)}</Badge>
        <h2>{progress.headline}</h2>
      </div>
      <div className="research-progress-actions">
        {showRetry && <button className="button small secondary" disabled={!!busy} onClick={() => void retry()}><Icon name="refresh" size={14} />{busy === 'retry' ? 'Queuing retry…' : 'Retry failed tasks'}</button>}
        {showResume && <button className="button small primary" disabled={!!busy} onClick={() => void resume()}><Icon name="play" size={14} />{busy === 'resume' ? 'Resuming…' : 'Resume discovery'}</button>}
        <a className="button small secondary" href="#notebook/agent-log"><Icon name="notebook" size={14} />View agent log</a>
      </div>
    </div>
    <p className="research-progress-message">{progress.message}</p>
    {showRetry && progress.session?.status === 'paused' && <p>Retry queues another attempt. Discovery stays paused until you resume.</p>}
    {notice && <p role="status">{notice}</p>}
    {connectionError && <p className="research-progress-stale">Workspace connection interrupted. Showing the last received agent status.</p>}
    {progress.request && <details className="research-progress-request">
      <summary><span><strong>Latest request:</strong> {shortened(requestLines[0] || progress.request.message, 155)}{direction && <small>{shortened(direction, 155)}</small>}</span></summary>
      <p className="research-progress-prompt">{progress.request.message}</p>
      <small>Requested {when(progress.request.created_at)} · {words(progress.request.status)}</small>
    </details>}
    {!!shownAgents.length && <div className="research-progress-agents">{shownAgents.map(agent => <article key={agent.task_id}>
      <div className="research-progress-agent-title"><strong>{words(agent.role)}</strong><span>{words(agent.stage)} · {words(agent.status)}</span></div>
      <p>{agent.activity || agent.error_message || agent.wait_reason || 'Awaiting the next recorded activity.'}</p>
      {agent.wait_reason && agent.activity && agent.wait_reason !== agent.activity && <p>{agent.wait_reason}</p>}
      <div className="research-progress-meta">
        {agent.model && <span>{agent.model}{agent.reasoning_effort ? ` · ${agent.reasoning_effort === 'xhigh' ? 'Extra high' : words(agent.reasoning_effort)}` : ''}</span>}
        {agent.active && agent.started_at && !connectionError && <span>Elapsed {age(agent.started_at, now)}</span>}
        {agent.last_activity_at && <span>Last recorded activity {age(agent.last_activity_at, now)} ago</span>}
      </div>
    </article>)}</div>}
    <div className="research-progress-footer">
      {counts.total > 0 && <span>{counts.completed || 0} completed · {counts.running || 0} running · {counts.queued || 0} queued{counts.waiting ? ` · ${counts.waiting} waiting` : ''}{counts.blocked ? ` · ${counts.blocked} blocked` : ''}{counts.failed ? ` · ${counts.failed} failed` : ''} <span className="research-progress-scope">{progress.request ? 'tasks for this request' : 'tasks in this discovery session'}</span></span>}
      {progress.updated_at && <span>Latest event {age(progress.updated_at, now)} ago</span>}
      <a href="#notebook/discovery">Discovery details</a>
    </div>
    {progress.agents.length > 2 && <details className="research-progress-roster"><summary>Recent agent activity</summary><ul>{progress.agents.map(agent => <li key={agent.task_id}><strong>{words(agent.role)}</strong> · {words(agent.stage)} · {words(agent.status)}{agent.model && ` · ${agent.model}`}{(agent.activity || agent.error_message || agent.wait_reason) && <p>{agent.activity || agent.error_message || agent.wait_reason}</p>}</li>)}</ul><a href="#notebook/discovery">View the full discovery agenda</a></details>}
    <ErrorNotice text={error} />
  </section>;
}
