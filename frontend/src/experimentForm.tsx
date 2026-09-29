import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorText, problemCatalogPath, problemDefinition } from './api';
import type { Hypothesis, Json, State } from './api';
import { useCommand } from './commands';
import { DiagnosticEditor, completionNames, normalizedDiagnostics } from './diagnosticForm';
import { Badge, Empty, ErrorNotice, Field, Icon, Modal, Panel } from './ui';
import type { WorkspaceActions } from './views';
import { ReproductionDraftForm } from './reproduction';

function object(text: string): Json {
  const value = JSON.parse(text || '{}');
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Parameters must be a JSON object.');
  return value;
}

type TrialFormProps = {
  state: State; hypothesis?: Hypothesis; taskId?: string; draft?: Json; onClose: () => void;
  onDone: (result?: Json) => void; onSaved?: () => void; onDiscuss?: (message: string) => void;
};

export function TrialForm(props: TrialFormProps) {
  return props.draft?.reproduction ? <ReproductionDraftForm {...props} draft={props.draft} /> : <OptimizerTrialForm {...props} />;
}

function OptimizerTrialForm({ state, hypothesis, taskId, draft, onClose, onDone, onSaved, onDiscuss }: TrialFormProps) {
  const initial = draft?.procedure || {};
  const proposal = state.hypotheses.find(h => h.id === (initial.hypothesis_id || hypothesis?.id)) || hypothesis;
  const [saved, setSaved] = useState<Json | undefined>(draft), [readiness, setReadiness] = useState<Json | null>(null);
  const [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [title, setTitle] = useState(draft?.title || (proposal ? `${proposal.title} probe` : 'Optimizer experiment'));
  const [studyId, setStudyId] = useState(draft?.study_id || state.campaign?.active_study_id || '');
  const [task, setTask] = useState(initial.task_id || taskId || state.tasks.find(t => t.split === 'development')?.id || state.tasks[0]?.id || '');
  const [algorithm, setAlgorithm] = useState(draft?.follow_proposal_implementation || (!draft && proposal) ? 'proposal'
    : initial.implementation_version_id || initial.algorithm || state.algorithms[0]?.id || '');
  const [config, setConfig] = useState(JSON.stringify(initial.algorithm_config || proposal?.algorithm_config || {}, null, 2));
  const [seed, setSeed] = useState(String(initial.seed ?? 0)), [steps, setSteps] = useState(String(initial.max_steps ?? 100));
  const [wall, setWall] = useState(String(initial.wall_seconds ?? 60)), [schedule, setSchedule] = useState(String(initial.schedule_steps ?? 1000));
  const [question, setQuestion] = useState(initial.question || ''), [unit, setUnit] = useState(initial.completion?.unit || 'evaluation_requests');
  const [decisions, setDecisions] = useState(String(initial.completion?.count ?? 100));
  const [checkpointCount, setCheckpointCount] = useState(String(initial.recovery?.every_observations ?? 100));
  const [checkpointSeconds, setCheckpointSeconds] = useState(String(initial.recovery?.every_seconds ?? 30));
  const [diagnostics, setDiagnostics] = useState(JSON.stringify(initial.diagnostics || [], null, 2));
  const [problemCatalog, setProblemCatalog] = useState<Json[]>([]), [inferenceAdapters, setInferenceAdapters] = useState<Json[]>([]);
  const command = useCommand(state.campaign);
  const selected = state.tasks.find(t => t.id === task);
  const native = state.algorithms.filter(a => !selected?.problem ||
    ((!a.representations || a.representations.includes(selected.problem.candidate_schema.representation)) &&
     (!a.problem_ids || a.problem_ids.includes(selected.problem.definition_id))));
  const versions: Json[] = (state.implementation_library?.versions || []).filter((v: Json) => v.status === 'validated' && v.kind !== 'evaluator');
  const implementation = algorithm === 'proposal' ? proposal?.implementation_version_id || proposal?.algorithm || '' : algorithm;
  const optimizerReady = algorithm === 'proposal' ? !!proposal?.implementation_readiness?.runnable
    : native.some(a => a.id === algorithm) || versions.some(v => v.id === algorithm);
  const evaluatorReady = selected?.evaluator_readiness?.runnable !== false;
  const runnable = optimizerReady && evaluatorReady;
  const follow = algorithm === 'proposal';
  const definition = problemDefinition(problemCatalog, selected?.problem);
  const method = native.find(item => item.id === implementation);
  const version = versions.find(item => item.id === implementation);
  const executionCapabilities = method?.execution_capabilities || version?.spec?.execution_capabilities || { completion_units: ['evaluation_requests'], exports: [] };
  const completionUnits: string[] = executionCapabilities.completion_units;
  useEffect(() => {
    let current = true;
    Promise.all([api(problemCatalogPath(state.campaign?.id)), api('/api/v1/inference-adapters')]).then(([problems, inference]) => {
      if (!Array.isArray(problems?.problems) || !Array.isArray(inference?.adapters)) throw new Error('The diagnostic catalog is unavailable. Saved declarations can still be reviewed.');
      if (current) { setProblemCatalog(problems.problems); setInferenceAdapters(inference.adapters); }
    }).catch(e => { if (current) setError(errorText(e)); });
    return () => { current = false; };
  }, [state.campaign?.id, state.tasks.map(item => `${item.id}:${item.problem?.definition_version}:${item.problem?.evaluator_version}`).join(',')]);
  useEffect(() => {
    if (!saved) return;
    let current = true;
    api(`/api/v1/drafts/${saved.id}`).then(result => { if (current) setReadiness(result.readiness); }).catch(e => { if (current) setError(errorText(e)); });
    return () => { current = false; };
  }, [saved?.id, saved?.revision, state.event_cursor]);
  function procedure(): Json {
    const parsed = normalizedDiagnostics(diagnostics, definition, selected?.problem, inferenceAdapters);
    return { ...initial, campaign_id: state.campaign!.id, task_id: task,
      algorithm: implementation.startsWith('impl_') ? 'package' : implementation,
      implementation_version_id: implementation.startsWith('impl_') ? implementation : undefined,
      hypothesis_id: proposal?.id, algorithm_config: object(config), seed: Number(seed), max_steps: Number(steps),
      schedule_steps: Number(schedule), wall_seconds: Number(wall), question,
      completion: { unit, count: Number(unit === 'evaluation_requests' ? steps : decisions) }, diagnostics: parsed,
      recovery: { ...initial.recovery, every_observations: Number(checkpointCount), every_seconds: Number(checkpointSeconds) } };
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const outcome = await command('draft.save', { draft_id: saved?.id, expected_draft_revision: saved?.revision,
        study_id: studyId || undefined, title, procedure: procedure(), follow_proposal_implementation: follow,
        required_capabilities: saved?.required_capabilities || [] });
      const result = await api(`/api/v1/drafts/${outcome.draft_id}`);
      setSaved(result.draft); setReadiness(result.readiness); setDirty(false); onSaved?.();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function launch() {
    setBusy(true); setError('');
    try {
      const result = saved ? await command('draft.launch', { draft_id: saved.id, expected_draft_revision: saved.revision,
        expected_readiness_hash: readiness?.readiness_hash }) : await command('trial.create', procedure());
      onDone(result);
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <Modal wide title={saved ? 'Review experiment draft' : 'Design an experiment'}
    description="Save the intended procedure while requirements are unresolved. Launch pins the executable, inputs, and procedure." onClose={onClose}>
    <form onSubmit={save} onChange={() => setDirty(true)}><div className="form-grid">
      <Field label="Experiment title" wide><input value={title} onChange={e => setTitle(e.target.value)} required maxLength={300} /></Field>
      {!!state.studies?.length && <Field label="Study" wide><select value={studyId} onChange={e => setStudyId(e.target.value)}>
        {state.studies.map((study: Json) => <option key={study.id} value={study.id}>{study.goal}{study.id === state.campaign?.active_study_id ? ' · Active study' : ' · Earlier study'}</option>)}</select></Field>}
      {studyId && studyId !== state.campaign?.active_study_id && <p className="callout amber wide">This design belongs to an earlier study. Save it for review, or explicitly select the active study before launching.</p>}
      <Field label="Optimization strategy" wide><select value={algorithm} onChange={e => { setAlgorithm(e.target.value); setConfig('{}'); }}>
        <option value="">Implementation needed</option>{proposal && <option value="proposal">Implementation of {proposal.title}</option>}
        {!['', 'proposal', ...native.map(a => a.id), ...versions.map(v => v.id)].includes(algorithm) && <option value={algorithm}>{algorithm} · implementation needed</option>}
        {versions.map(v => <option key={v.id} value={v.id}>{v.name} · {v.id.slice(-8)}</option>)}
        {native.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
      {!optimizerReady && <div className="callout amber wide"><strong>Implementation needed</strong><p>{proposal?.implementation_readiness?.reason || 'Save this design while an implementation is built, validated, or selected.'}</p></div>}
      {!evaluatorReady && <div className="callout amber wide"><strong>{selected?.evaluator_version_id ? 'Evaluator evidence needed' : 'Evaluator needed'}</strong><p>{selected?.evaluator_readiness?.reason}</p><p>{selected?.evaluator_version_id ? 'Save the draft and review evaluator evidence in Validation. Its study policy determines whether an exploratory waiver is allowed.' : 'Save the draft, then build or reuse an evaluator from the problem workbench.'}</p></div>}
      {selected?.evaluator_readiness?.state === 'ready_with_waiver' && <p className="callout amber wide">{selected.evaluator_readiness.reason}</p>}
      <Field label="Probe configuration" wide><select value={task} onChange={e => setTask(e.target.value)} required>{state.tasks.map(t => <option key={t.id} value={t.id}>{t.name} · {t.split}</option>)}</select></Field>
      {selected?.split === 'test' && <p className="callout amber wide">Save this design as a draft. Launch protected instances from a frozen confirmation study.</p>}
      <Field label="What should this experiment tell us?" wide><textarea value={question} onChange={e => setQuestion(e.target.value)} rows={2} /></Field>
      <Field label="Evaluation requests" hint="Includes initialization, resets, and cached requests."><input type="number" min="1" step="1" value={steps} onChange={e => setSteps(e.target.value)} required /></Field>
      <Field label="Time cap (seconds)"><input type="number" min="1" value={wall} onChange={e => setWall(e.target.value)} required /></Field>
      <Field label="Random seed"><input type="number" min="0" step="1" value={seed} onChange={e => setSeed(e.target.value)} required /></Field>
      <Field label="Search schedule horizon"><input type="number" min="1" step="1" value={schedule} onChange={e => setSchedule(e.target.value)} required /></Field>
      <Field label="Scientific completion"><select value={unit} onChange={e => setUnit(e.target.value)}>
        {!completionUnits.includes(unit) && <option value={unit}>{completionNames[unit]} · unsupported</option>}
        {completionUnits.map(value => <option key={value} value={value}>Declared {completionNames[value]?.toLowerCase()}</option>)}</select></Field>
      {!completionUnits.includes(unit) && <p className="callout amber">The selected implementation does not declare this completion counter. Save the draft to retain the requirement.</p>}
      {unit === 'optimizer_decisions' && <Field label="Required optimizer decisions"><input type="number" min="1" value={decisions} onChange={e => setDecisions(e.target.value)} required /></Field>}
      <Field label="Algorithm parameters (JSON)" wide><textarea className="code-input" value={config} onChange={e => setConfig(e.target.value)} rows={3} spellCheck={false} /></Field>
    </div><details><summary>Recovery and diagnostic schedules</summary><div className="form-grid">
      <Field label="Checkpoint every observations"><input type="number" min="1" value={checkpointCount} onChange={e => setCheckpointCount(e.target.value)} required /></Field>
      <Field label="Checkpoint every seconds"><input type="number" min="0.1" step="any" value={checkpointSeconds} onChange={e => setCheckpointSeconds(e.target.value)} required /></Field>
    </div><DiagnosticEditor value={diagnostics} onChange={value => { setDiagnostics(value); setDirty(true); }} capabilities={executionCapabilities}
      adapters={inferenceAdapters} definition={definition} problem={selected?.problem} />
      <details><summary>Advanced diagnostic schedule</summary><Field label="Diagnostic schedules (JSON)" wide hint="Inspect or edit the complete declaration, including previously saved schedules."><textarea className="code-input" value={diagnostics} onChange={e => setDiagnostics(e.target.value)} rows={5} spellCheck={false} /></Field></details>
    </details>
    {saved && <div className="callout"><strong>Draft saved · revision {saved.revision}</strong>
      {dirty ? <p>Save the changed design to refresh its readiness.</p> : readiness?.trial_id ? <p>This revision already produced an experiment. Save a revision to try a changed procedure.</p>
        : readiness?.ready ? <p>Ready to freeze and launch. Executable availability and budget are checked again at launch.</p> :
          <ul>{readiness?.blockers?.map((item: Json) => <li key={item.code}>{item.message}</li>)}</ul>}
      {readiness?.waiting_for?.length > 0 && <p>The queued experiment will wait for its declared dependencies.</p>}
      {!!readiness?.blockers?.length && onDiscuss && <button className="text-button" type="button" onClick={() => onDiscuss(`Help resolve the missing requirements of experiment draft ${saved.id}: ${readiness.blockers.map((item: Json) => item.message).join('; ')}`)}>Discuss requirements with manager</button>}
    </div>}
    <ErrorNotice text={error} /><div className="modal-actions"><button type="submit" className="button secondary" disabled={busy || !task}>{busy ? 'Working…' : saved ? 'Save draft revision' : 'Save draft'}</button>
      <button type="button" className="button primary" disabled={busy || !task || selected?.split === 'test' || (!!studyId && studyId !== state.campaign?.active_study_id) || (saved ? dirty || !readiness?.ready || !!readiness?.trial_id : !runnable)} onClick={() => void launch()}>
        {saved ? 'Freeze and launch' : 'Launch experiment'}<Icon name="play" size={16} /></button></div></form>
  </Modal>;
}

export function DraftList({ state, actions }: { state: State; actions: WorkspaceActions }) {
  return <><div className="page-heading"><div><span className="eyebrow">Experiment design</span><h1>Develop the procedure.</h1><p>Drafts retain missing requirements and revisions until an experiment is ready to freeze.</p></div>
    <button className="button primary" onClick={() => actions.launch()}>Design an experiment</button></div>
    {!state.drafts?.length ? <Empty title="No saved drafts">Open a proposal or design an experiment to record the intended procedure, even while its implementation is missing.</Empty> :
      <Panel title="Saved experiment drafts"><div className="table-scroll"><table className="data-table"><thead><tr><th>Design</th><th>Problem</th><th>Revision</th><th>Execution</th></tr></thead>
        <tbody>{state.drafts.map((draft: Json) => <tr key={draft.id}><td><button className="table-link" onClick={() => actions.editDraft(draft)}>{draft.title}</button></td>
          <td>{state.tasks.find(t => t.id === draft.procedure.task_id)?.name || 'Earlier problem version'}</td><td>{draft.revision}</td>
          <td><Badge>{state.draft_launches?.some((binding: Json) => binding.draft_revision_id === draft.revision_id) ? 'Experiment created' : 'Draft'}</Badge></td></tr>)}</tbody>
      </table></div></Panel>}</>;
}
