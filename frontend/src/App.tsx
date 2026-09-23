import { useCallback, useEffect, useRef, useState } from 'react';
import { api, emptyState, errorText, seconds } from './api';
import type { Hypothesis, Json, State, Trial } from './api';
import { ErrorNotice, Icon } from './ui';
import { CampaignForm, HypothesisForm, TrialActionForm, TrialForm, campaignUsage } from './forms';
import { Comparison, Decisions, Experiments, Hypotheses, Overview, Problem } from './views';
import type { WorkspaceActions } from './views';
import { Notebook, ResearchPanel } from './research';

const navigation = [
  ['overview', 'Overview', 'overview'], ['problem', 'Problem workbench', 'problem'],
  ['hypotheses', 'Hypotheses', 'hypothesis'], ['experiments', 'Experiments', 'experiments'],
  ['comparison', 'Compare results', 'comparison'], ['decisions', 'Decision inbox', 'decisions'],
  ['notebook', 'Research notebook', 'notebook'],
];
type ModalState = { type: 'campaign'; edit?: boolean } | { type: 'hypothesis'; parent?: Hypothesis } | { type: 'trial'; hypothesis?: Hypothesis; taskId?: string } | { type: 'trialAction'; trial: Trial; action: 'extend' | 'validate' | 'prioritize' } | null;

export default function App() {
  const [state, setState] = useState<State>(emptyState), [campaignId, setCampaignId] = useState('');
  const [view, setView] = useState(navigation.some(([id]) => id === location.hash.slice(1)) ? location.hash.slice(1) : 'overview');
  const [connected, setConnected] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [modal, setModal] = useState<ModalState>(null), [chat, setChat] = useState(window.innerWidth > 1200), [sidebar, setSidebar] = useState(false);
  const [toast, setToast] = useState(''), [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const mounted = useRef(true), requestId = useRef(0), toastTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const refresh = useCallback(async () => {
    const request = ++requestId.current;
    try {
      const data = await api<State>(`/api/state${campaignId ? `?campaign_id=${encodeURIComponent(campaignId)}` : ''}`);
      if (mounted.current && request === requestId.current) { setState({ ...emptyState, ...data, campaign: data.campaign ? { ...data.campaign, compute_used_seconds: data.budget?.spent_seconds ?? data.campaign.compute_used_seconds, llm_used_usd: data.budget?.llm_spent_usd ?? data.campaign.llm_used_usd } : null }); setError(''); setUpdatedAt(new Date()); setLoading(false); }
    } catch (e) { if (mounted.current && request === requestId.current) { setError(errorText(e)); setLoading(false); } }
  }, [campaignId]);
  useEffect(() => { mounted.current = true; void refresh(); const interval = window.setInterval(refresh, 6000); return () => { mounted.current = false; window.clearInterval(interval); }; }, [refresh]);
  useEffect(() => {
    let pending: ReturnType<typeof setTimeout> | undefined;
    const stream = new EventSource('/api/events');
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    const update = () => { if (!pending) pending = setTimeout(() => { pending = undefined; void refresh(); }, 400); };
    stream.addEventListener('update', update);
    stream.onmessage = update;
    return () => { stream.close(); clearTimeout(pending); };
  }, [refresh]);
  useEffect(() => { const change = () => { const v = location.hash.slice(1); if (navigation.some(([id]) => id === v)) setView(v); }; window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change); }, []);
  useEffect(() => () => clearTimeout(toastTimer.current), []);
  function notify(message: string) { setToast(message); clearTimeout(toastTimer.current); toastTimer.current = setTimeout(() => setToast(''), 6000); }
  function navigate(value: string) { setView(value); location.hash = value; setSidebar(false); }
  async function research(message: string, mode: string, hypothesisId?: string, feedbackReviewIds?: string[]): Promise<boolean> {
    if (!state.campaign) return false; setChat(true);
    try {
      await api('/api/research', { campaign_id: state.campaign.id, message, mode, hypothesis_id: hypothesisId, feedback_review_ids: feedbackReviewIds });
      await refresh(); notify('Research request recorded. Follow its progress in the conversation.'); return true;
    } catch (e) { notify(`Research request failed: ${errorText(e)}`); return false; }
  }
  const actions: WorkspaceActions = { navigate, refresh, notify, research, campaign: edit => setModal({ type: 'campaign', edit }), hypothesis: parent => setModal({ type: 'hypothesis', parent }), launch: (hypothesis, taskId) => setModal({ type: 'trial', hypothesis, taskId }), trialAction: (trial, action) => setModal({ type: 'trialAction', trial, action }) };
  function done(message: string, result?: Json, newCampaign = false) { setModal(null); notify(message); if (newCampaign && result?.id) setCampaignId(result.id); else void refresh(); }
  const pending = state.decisions.filter(d => d.status === 'pending').length, usage = campaignUsage(state.campaign, state.trials);
  return <div className={`app ${chat ? 'chat-open' : ''} ${sidebar ? 'nav-open' : ''}`}><a className="skip-link" href="#workspace-content">Skip to workspace</a>{sidebar && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setSidebar(false)} />}<aside className="sidebar"><a className="brand" href="#overview" onClick={() => navigate('overview')}><span className="brand-mark"><i /><i /><i /><i /><i /></span><div>Grating Lab<span>RESEARCH WORKSPACE</span></div></a><div className="campaign-switcher"><label htmlFor="campaign-picker">CAMPAIGN</label><select id="campaign-picker" aria-label="Active campaign" value={state.campaign?.id || ''} onChange={e => setCampaignId(e.target.value)}>{!state.campaign && <option value="">No campaign yet</option>}{state.campaigns.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select><button className="new-campaign-link" onClick={() => setModal({ type: 'campaign' })}><Icon name="plus" size={13} />New campaign</button></div><nav aria-label="Workspace navigation">{navigation.map(([id, label, icon]) => <button key={id} className={view === id ? 'active' : ''} onClick={() => navigate(id)} aria-current={view === id ? 'page' : undefined}><Icon name={icon} size={19} /><span>{label}</span>{id === 'decisions' && pending > 0 && <span className="nav-count">{pending}</span>}</button>)}</nav><div className="sidebar-bottom"><div className="sidebar-budget"><div><span>CAMPAIGN COMPUTE</span><Icon name="clock" size={14} /></div><strong>{seconds(usage.compute)}<span> / {state.campaign ? seconds(state.campaign.compute_budget_seconds) : '—'}</span></strong><div className="meter"><i style={{ width: `${state.campaign ? Math.min(100, usage.compute / state.campaign.compute_budget_seconds * 100) : 0}%` }} /></div><p>{state.campaign ? 'Budget enforced across all workers' : 'Create a campaign to allocate resources'}</p></div><div className="local-status"><span className={`dot ${error ? 'red' : 'green'}`} /><span>Local workspace</span><span className="version">v0.1</span></div></div></aside><div className="main-shell"><header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setSidebar(true)}><Icon name="menu" /></button><span>Workspace</span><Icon name="chevron" size={13} /><strong>{navigation.find(([id]) => id === view)?.[1]}</strong></div><div className="topbar-right"><span className="connection-status"><i className={`dot ${connected && !error ? 'green' : 'amber'}`} />{error ? 'Connection issue' : connected ? 'Live' : 'Polling'}{updatedAt && <time title="Last successful state update">{updatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>}</span><button className={`button small ${chat ? 'chat-toggle active' : 'secondary'}`} onClick={() => setChat(!chat)} aria-expanded={chat}><Icon name="spark" size={16} />Research partner</button><span className="user-avatar" title="Local researcher">R</span></div></header><main id="workspace-content" className="workspace" tabIndex={-1}>{error && <div className="connection-error"><ErrorNotice text={`Unable to update the workspace: ${error}`} /><button className="button small secondary" onClick={() => void refresh()}><Icon name="refresh" size={14} />Retry</button></div>}{loading ? <div className="loading-state"><span className="loading-spinner" /><h2>Opening the research workspace</h2><p>Connecting to the experiment service…</p></div> : view === 'overview' ? <Overview state={state} actions={actions} /> : view === 'problem' ? <Problem state={state} actions={actions} /> : view === 'hypotheses' ? <Hypotheses state={state} actions={actions} /> : view === 'experiments' ? <Experiments state={state} actions={actions} /> : view === 'comparison' ? <Comparison state={state} actions={actions} /> : view === 'decisions' ? <Decisions state={state} actions={actions} /> : <Notebook state={state} refresh={refresh} />}</main><footer className="workspace-footer"><span>GRATING LAB</span><p>Ideas are hypotheses. Curves are observations. Decisions are yours.</p><span>1D inverse design</span></footer></div>{chat && <ResearchPanel state={state} refresh={refresh} onClose={() => setChat(false)} />}{toast && <div className="toast" role="status"><Icon name="check" size={17} /><span>{toast}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setToast('')}><Icon name="close" size={15} /></button></div>}{modal?.type === 'campaign' && <CampaignForm state={state} editing={modal.edit} onClose={() => setModal(null)} onDone={result => done(modal.edit ? 'Charter revision saved.' : 'Campaign created. Your workspace is ready.', result, !modal.edit)} />}{modal?.type === 'hypothesis' && <HypothesisForm state={state} parent={modal.parent} onClose={() => setModal(null)} onDone={() => done('Hypothesis saved with its rationale and lineage.')} />}{modal?.type === 'trial' && <TrialForm state={state} hypothesis={modal.hypothesis} taskId={modal.taskId} onClose={() => setModal(null)} onDone={() => { done('Experiment queued. Open Experiments to follow its progress.'); navigate('experiments'); }} />}{modal?.type === 'trialAction' && <TrialActionForm trial={modal.trial} action={modal.action} onClose={() => setModal(null)} onDone={() => done(modal.action === 'validate' ? 'Physical validation requested.' : 'Trial allocation updated.')} />}</div>;
}
