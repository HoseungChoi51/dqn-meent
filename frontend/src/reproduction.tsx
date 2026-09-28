import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorText } from './api';
import type { Json, State } from './api';
import { useCommand } from './commands';
import { ErrorNotice, Field, Modal, Panel } from './ui';
import type { WorkspaceActions } from './views';

export function ReproductionPanel({ state, actions }: { state: State; actions: WorkspaceActions }) {
  const [sources, setSources] = useState<Json[]>([]), [sourceKey, setSourceKey] = useState('');
  const [task, setTask] = useState(''), [wall, setWall] = useState('60'), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const command = useCommand(state.campaign);
  useEffect(() => {
    let current = true;
    setSources([]); setSourceKey(''); setTask('');
    if (state.campaign) api(`/api/v1/reproduction-sources?campaign_id=${encodeURIComponent(state.campaign.id)}`)
      .then(result => { if (current) { setSources(result.sources); setError(''); } })
      .catch(e => { if (current) setError(errorText(e)); });
    return () => { current = false; };
  }, [state.campaign?.id, state.events.filter(e => e.kind === 'bundle.import_published').map(e => e.id).join(','), state.tasks.map(t => `${t.id}:${t.problem?.evaluator_version}`).join(',')]);
  const source = sources.find(item => item.key === sourceKey);
  async function create(event: FormEvent) {
    event.preventDefault(); if (!source || !task) return;
    setBusy(true); setError('');
    try {
      const result = await command('reproduction.draft', { reference: source.reference, task_id: task, wall_seconds: Number(wall) });
      const loaded = await api(`/api/v1/drafts/${result.draft_id}`);
      await actions.refresh(); actions.editDraft(loaded.draft);
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <Panel title="Reproduce historical result">
    <p>Choose an imported snapshot to create a new experiment with its original procedure, seed and inputs. Readiness checks the required source, runtimes and validation.</p>
    <ErrorNotice text={error} />
    {!sources.length ? <p className="help-text">Import an evidence bundle containing an experiment to make its snapshots available here.</p> :
      <form onSubmit={create}><div className="form-grid">
        <Field label="Historical experiment snapshot"><select required value={sourceKey} onChange={e => {
          setSourceKey(e.target.value); const selected = sources.find(item => item.key === e.target.value);
          setTask(selected?.compatible_task_ids[0] || '');
        }}><option value="">Select a snapshot</option>{sources.map(item => <option key={item.key} value={item.key}>
          {item.task_name || item.reference.id} · {item.algorithm} · seed {item.seed} · {item.reference.content_digest.slice(0, 12)}
        </option>)}</select></Field>
        <Field label="Reproduction problem"><select required value={task} onChange={e => setTask(e.target.value)}>
          <option value="">Select the matching problem</option>{state.tasks.map(item => <option key={item.id} value={item.id}>{item.name}{source?.compatible_task_ids.includes(item.id) ? ' · exact match' : ''}</option>)}
        </select></Field>
        <Field label="Reproduction wall allowance (seconds)"><input type="number" min="1" max="86400" required value={wall} onChange={e => setWall(e.target.value)} /></Field>
      </div>
      {source?.unsupported_reason && <p className="help-text">{source.unsupported_reason}</p>}
      {source && !source.compatible_task_ids.length && <p className="help-text">This campaign needs the original problem, fidelity and exact evaluator version. You can save a draft to review the missing requirements.</p>}
      <button className="button primary" disabled={busy || !source || !task || !!source?.unsupported_reason}>{busy ? 'Saving…' : 'Design reproduction'}</button>
    </form>}
    {!!state.reproduction_comparisons?.length && <div className="table-scroll"><table className="data-table"><thead><tr><th>New experiment / attempt</th><th>Outcome</th><th>Evidence</th></tr></thead><tbody>
      {state.reproduction_comparisons.map((result: Json) => <tr key={result.id}><td>{result.trial_id}<small>Attempt {result.attempt}</small></td>
        <td>{result.outcome}</td><td>{result.reasons.join('; ') || 'Best primary objective and declared candidate comparison.'}
          <details><summary>Comparison evidence</summary><pre>{JSON.stringify(result, null, 2)}</pre></details></td></tr>)}
    </tbody></table></div>}
  </Panel>;
}

export function ReproductionDraftForm({ state, draft, onClose, onDone, onSaved, onDiscuss }: {
  state: State; draft: Json; onClose: () => void; onDone: (result?: Json) => void;
  onSaved?: () => void; onDiscuss?: (message: string) => void;
}) {
  const [saved, setSaved] = useState(draft), [readiness, setReadiness] = useState<Json | null>(null);
  const [title, setTitle] = useState(draft.title), [task, setTask] = useState(draft.procedure.task_id);
  const [wall, setWall] = useState(String(draft.procedure.wall_seconds)), [steps, setSteps] = useState(String(draft.procedure.max_steps));
  const [rule, setRule] = useState<Json>(draft.reproduction.comparison), [decisions, setDecisions] = useState<string[]>(draft.procedure.reuse_decision_ids);
  const [busy, setBusy] = useState(false), [dirty, setDirty] = useState(false), [error, setError] = useState('');
  const command = useCommand(state.campaign);
  useEffect(() => {
    let current = true;
    setReadiness(null);
    api(`/api/v1/drafts/${saved.id}`).then(result => { if (current) setReadiness(result.readiness); })
      .catch(e => { if (current) setError(errorText(e)); });
    return () => { current = false; };
  }, [saved.id, saved.revision]);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const result = await command('draft.save', { draft_id: saved.id, expected_draft_revision: saved.revision,
        study_id: state.campaign?.active_study_id, title, reproduction: { ...saved.reproduction, comparison: rule },
        procedure: { ...saved.procedure, task_id: task, wall_seconds: Number(wall), max_steps: Number(steps),
          reuse_decision_ids: decisions } });
      const loaded = await api(`/api/v1/drafts/${result.draft_id}`);
      setSaved(loaded.draft); setReadiness(loaded.readiness); setDirty(false); onSaved?.();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function launch() {
    if (!readiness) return; setBusy(true); setError('');
    try { const result = await command('draft.launch', { draft_id: saved.id, expected_draft_revision: saved.revision,
      expected_readiness_hash: readiness.readiness_hash }); onDone(result); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function refresh() {
    setBusy(true); setError('');
    try { setReadiness((await api(`/api/v1/drafts/${saved.id}`)).readiness); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <Modal wide title="Historical reproduction draft" description="Review the fixed procedure and choose operational allowances and comparison tolerances before launch." onClose={onClose}>
    <p>Original experiment: <strong>{saved.reproduction.reference.id}</strong> · snapshot {saved.reproduction.reference.content_digest.slice(0, 12)}</p>
    <p>{saved.procedure.algorithm} · seed {saved.procedure.seed} · complete {saved.procedure.completion.count} {saved.procedure.completion.unit.replaceAll('_', ' ')}</p>
    <form onSubmit={save} onChange={() => setDirty(true)}><div className="form-grid">
      <Field label="Reproduction title"><input required value={title} onChange={e => setTitle(e.target.value)} /></Field>
      <Field label="Destination problem"><select required value={task} onChange={e => setTask(e.target.value)}>{state.tasks.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
      <Field label="Wall allowance (seconds)"><input required type="number" min="1" max="86400" value={wall} onChange={e => setWall(e.target.value)} /></Field>
      <Field label="Evaluation request allowance"><input required type="number" min="1" max="10000000" value={steps} onChange={e => setSteps(e.target.value)} /></Field>
      {(readiness?.reproduction_inputs || []).map((input: Json) => <Field key={input.asset_id} wide label={`Reuse authorization: ${input.title}`}
        hint="Record an optimizer-input reuse decision for this study in the asset library if none is available.">
        <select value={input.decisions.find((item: Json) => decisions.includes(item.id))?.id || ''} onChange={e => setDecisions([
          ...decisions.filter(id => !input.decisions.some((item: Json) => item.id === id)), ...(e.target.value ? [e.target.value] : [])])}>
          <option value="">Select a reuse decision</option>{input.decisions.map((item: Json) => <option key={item.id} value={item.id}>{item.rationale}</option>)}
        </select></Field>)}
      <Field label="Objective absolute tolerance"><input required type="number" min="0" step="any" value={rule.objective_absolute_tolerance} onChange={e => setRule({ ...rule, objective_absolute_tolerance: Number(e.target.value) })} /></Field>
      <Field label="Objective relative tolerance"><input required type="number" min="0" step="any" value={rule.objective_relative_tolerance} onChange={e => setRule({ ...rule, objective_relative_tolerance: Number(e.target.value) })} /></Field>
      <Field label="Candidate comparison"><select value={String(rule.compare_candidate)} onChange={e => setRule({ ...rule, compare_candidate: e.target.value === 'true' })}><option value="true">Compare best candidate</option><option value="false">Compare primary objective only</option></select></Field>
      {rule.compare_candidate && <><Field label="Candidate absolute tolerance"><input required type="number" min="0" step="any" value={rule.candidate_absolute_tolerance} onChange={e => setRule({ ...rule, candidate_absolute_tolerance: Number(e.target.value) })} /></Field>
        <Field label="Candidate relative tolerance"><input required type="number" min="0" step="any" value={rule.candidate_relative_tolerance} onChange={e => setRule({ ...rule, candidate_relative_tolerance: Number(e.target.value) })} /></Field></>}
    </div><button className="button secondary" disabled={busy || !dirty}>Save reproduction revision</button></form>
    <details><summary>Original procedure and input assets</summary><pre>{JSON.stringify(saved.procedure, null, 2)}</pre></details>
    <p className="help-text">The comparison assesses the best primary objective and selected candidate values. Trajectories, optimizer superiority and independent evaluator correctness require separate evidence.</p>
    {(readiness?.reproduction?.runtime_conversions || []).map((conversion: Json) => <p className="help-text" key={conversion.id}>{conversion.limitations.join(' ')}</p>)}
    <ErrorNotice text={error} />
    <div aria-live="polite">{!readiness ? <p>Checking reproduction requirements…</p> : readiness.blockers.length ? <ul>{readiness.blockers.map((blocker: Json, i: number) => <li key={i}>{blocker.message}</li>)}</ul> : <p>Ready to reproduce the selected snapshot.</p>}</div>
    <div className="button-row"><button type="button" className="button secondary" disabled={busy} onClick={() => void refresh()}>Check requirements again</button>
      {onDiscuss && !!readiness?.blockers.length && <button className="button secondary" onClick={() => onDiscuss(`Resolve readiness for historical reproduction draft ${saved.id}: ${readiness.blockers.map((b: Json) => b.message).join('; ')}`)}>Discuss requirements with manager</button>}
      <button className="button primary" disabled={busy || dirty || !readiness?.ready || !!readiness?.trial_id} onClick={() => void launch()}>{readiness?.trial_id ? 'Reproduction already launched' : 'Launch reproduction'}</button></div>
  </Modal>;
}
