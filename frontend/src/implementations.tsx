import { useEffect, useState } from 'react';
import { api, errorText, seconds, when } from './api';
import type { Hypothesis, Json, State } from './api';
import { Badge, Empty, ErrorNotice, Field, Modal, Panel, Status } from './ui';
import { TextContent } from './research';
import { useCommand } from './commands';

export function readiness(h: Hypothesis): Json {
  return h.implementation_readiness || { state: 'unavailable', runnable: false, reason: 'Implementation status is unavailable. Refresh the campaign.' };
}

export function ImplementationBadge({ hypothesis }: { hypothesis: Hypothesis }) {
  const status = readiness(hypothesis);
  const labels: Record<string, string> = { missing: 'Implementation missing', ready: 'Implementation available',
    validation_required: 'Implementation validation required', unavailable: 'Implementation unavailable',
    incompatible: 'Implementation incompatible', queued: 'Implementation queued', dispatching: 'Connecting to implementation service',
    building: 'Implementing', validating: 'Validating implementation', reviewing: 'Reviewing implementation',
    repairing: 'Repairing implementation', blocked: 'Implementation blocked', interrupted: 'Implementation interrupted' };
  return <Badge tone={status.runnable ? 'green' : 'amber'}>{labels[status.state] || status.state}</Badge>;
}

export function ImplementationRequest({ hypothesis, state, onClose, onDone }: { hypothesis: Hypothesis; state: State; onClose: () => void; onDone: () => void }) {
  const tasks = state.tasks.filter(task => task.split !== 'test');
  const [taskId, setTaskId] = useState(tasks[0]?.id || '');
  const task = tasks.find(item => item.id === taskId);
  const domain = task?.problem?.candidate_schema || task?.evaluator_manifest?.candidate_schema;
  const dimensions = domain?.dimensions || task?.physics?.n_cells || 8;
  const suggestedCapabilities: string[] = task?.problem?.capabilities || (task?.evaluator_manifest
    ? [domain.representation, 'scalar_objective'] : ['binary_forward']);
  const [mechanism, setMechanism] = useState(hypothesis.mechanism);
  const [criteria, setCriteria] = useState('Implements the declared mechanism\nReproduces seeded proposals and restores the complete optimizer state');
  const [dependencies, setDependencies] = useState('{}');
  const [capabilities, setCapabilities] = useState(suggestedCapabilities.join(', '));
  const [minimum, setMinimum] = useState(String(dimensions)), [maximum, setMaximum] = useState(String(dimensions));
  const [behavior, setBehavior] = useState('[]');
  const [constraints, setConstraints] = useState(!!domain?.constraints?.length);
  const [parameters, setParameters] = useState(JSON.stringify(hypothesis.algorithm_config || {}, null, 2));
  const [schema, setSchema] = useState(JSON.stringify({ type: 'object', properties: Object.fromEntries(Object.entries(hypothesis.algorithm_config || {}).map(([name, value]) => [name, { enum: [value] }])), additionalProperties: false }, null, 2));
  const [sourcePackage, setSourcePackage] = useState('');
  const [compute, setCompute] = useState('120'), [calls, setCalls] = useState('12'), [apiCap, setApiCap] = useState('0');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const command = useCommand(state.campaign);
  const [candidates, setCandidates] = useState<Json[]>([]);
  useEffect(() => { let active = true;
    api(`/api/implementations?campaign_id=${state.campaign?.id || ''}&hypothesis_id=${hypothesis.id}`).then(result => {
      if (active) setCandidates((result.versions || []).filter((version: Json) => result.candidates?.[version.id]?.eligible));
    }).catch(e => { if (active) setError(errorText(e)); });
    return () => { active = false; };
  }, [hypothesis.id, state.campaign?.id]);
  useEffect(() => {
    setCapabilities(suggestedCapabilities.join(', ')); setMinimum(String(dimensions)); setMaximum(String(dimensions));
    setConstraints(!!domain?.constraints?.length);
  }, [taskId]);
  const evaluatorReady = !task?.evaluator_manifest || !!task.evaluator_readiness?.runnable || !!task.evaluator_readiness?.mandatory_contract_validated;
  const budget = state.budget || {};
  const remainingCompute = Math.max(0, (state.campaign?.implementation_compute_budget_seconds || 0) - (budget.implementation_compute_committed_seconds || 0));
  const allocationFits = Number(compute) > 0 && Number(compute) <= remainingCompute;
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try {
      if (!task || !evaluatorReady) throw new Error('Select a problem with an available evaluator before commissioning optimizer correctness checks.');
      const result = await command('implementation.commission', {
        hypothesis_id: hypothesis.id, compute_seconds: Number(compute), max_calls: Number(calls), api_budget_usd: Number(apiCap),
        spec: { name: hypothesis.title, mechanism, acceptance_criteria: criteria.split('\n').map(s => s.trim()).filter(Boolean),
          problem_id: task.problem_id || task.problem?.definition_id || 'meent_grating',
          problem_configuration: task.configuration || task.physics || {}, supports_constraints: constraints,
          n_cells_min: Number(minimum), n_cells_max: Number(maximum), behavior_checks: JSON.parse(behavior),
          ...(task.evaluator_version_id ? { kind: 'optimizer', evaluator_version_id: task.evaluator_version_id } : {}),
          dependencies: JSON.parse(dependencies), capabilities: capabilities.split(',').map(s => s.trim()).filter(Boolean),
          parameters: JSON.parse(parameters), parameter_schema: JSON.parse(schema),
          provenance: [{ hypothesis_id: hypothesis.id, campaign_id: state.campaign!.id }] },
        package: sourcePackage.trim() ? JSON.parse(sourcePackage) : null,
      });
      if (result?.error) setError(result.error);
      else onClose();
      onDone();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <Modal wide title="Commission an implementation" description="Campaign manager · freeze the intended behavior and a bounded allocation before the implementation service starts." onClose={onClose}>
    {!!candidates.length && <div className="callout"><p>Compatible library versions: {candidates.map(version => version.name).join(', ')}.</p>
      <button className="text-button" type="button" onClick={() => { onClose(); window.location.hash = 'implementations'; }}>Review reuse or record why a new implementation is needed</button></div>}
    <form onSubmit={submit}>
      <h3>{hypothesis.title}</h3>
      <p className="help-text">Implementation validation checks correctness and reproducibility. Experiments will measure performance separately.</p>
      <div className="callout">Implementation compute: {seconds(budget.implementation_compute_committed_seconds || 0)} allocated or used / {seconds(state.campaign?.implementation_compute_budget_seconds || 0)} cap. Revise the charter to assign or increase this separate allocation.</div>
      {!allocationFits && <div className="callout amber"><p>This request exceeds the remaining implementation allocation ({seconds(remainingCompute)}). Assign implementation compute in the campaign charter first.</p><button type="button" className="text-button" onClick={() => { onClose(); window.location.hash = 'problem'; }}>Open problem workbench to revise charter</button></div>}
      <div className="form-grid">
        <Field wide label="Optimizer validation problem"><select required value={taskId} onChange={e => setTaskId(e.target.value)}>
          {!tasks.length && <option value="">No development problem available</option>}{tasks.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
        {!evaluatorReady && <p className="callout amber wide">{task?.evaluator_readiness?.reason || 'Commission or reuse an evaluator from the problem workbench first.'}</p>}
        {task?.evaluator_version_id && <p className="help-text wide">Correctness checks will use evaluator {task.evaluator_version_id}.</p>}
        {evaluatorReady && task?.evaluator_readiness?.state !== 'ready' && task?.evaluator_version_id && <p className="callout amber wide">Optimizer integration checks can use this evaluator, but its numerical correctness is unverified. Experiment eligibility is checked separately.</p>}
        <Field wide label="Algorithm specification"><textarea required rows={5} value={mechanism} onChange={e => setMechanism(e.target.value)} /></Field>
        <Field wide label="Acceptance criteria" hint="One behavioral requirement per line. These stay fixed throughout repairs."><textarea required rows={4} value={criteria} onChange={e => setCriteria(e.target.value)} /></Field>
        <Field label="Required evaluator capabilities"><input value={capabilities} onChange={e => setCapabilities(e.target.value)} /></Field>
        <Field label="Minimum candidate dimensions"><input type="number" required min="1" max="1024" value={minimum} onChange={e => setMinimum(e.target.value)} /></Field>
        <Field label="Maximum candidate dimensions"><input type="number" required min={minimum} max="1024" value={maximum} onChange={e => setMaximum(e.target.value)} /></Field>
        <Field label="Supports declared constraints"><input type="checkbox" checked={constraints} onChange={e => setConstraints(e.target.checked)} /></Field>
        <Field label="Pinned Python dependencies (JSON)" hint={'For example {"numpy":"2.5.3"}'}><textarea value={dependencies} onChange={e => setDependencies(e.target.value)} spellCheck={false} /></Field>
        <Field label="Default parameters (JSON)"><textarea value={parameters} onChange={e => setParameters(e.target.value)} spellCheck={false} /></Field>
        <Field label="Supported parameter schema (JSON)"><textarea value={schema} onChange={e => setSchema(e.target.value)} spellCheck={false} /></Field>
        <Field label="Implementation time allocation (seconds)"><input type="number" min="1" max="86400" value={compute} onChange={e => setCompute(e.target.value)} required /></Field>
        <Field label="Maximum model calls"><input type="number" min="1" max="20" value={calls} onChange={e => setCalls(e.target.value)} required /></Field>
        <Field label="API allocation (USD)" hint="Uses the campaign API cap. Subscription calls are counted separately."><input type="number" min="0" step="0.01" value={apiCap} onChange={e => setApiCap(e.target.value)} required /></Field>
      </div>
      <details><summary>Independent behavioral checks</summary><Field wide label="Optimizer behavior checks (JSON)" hint="Optional protected proposal fixtures, frozen before the candidate is built."><textarea className="code-input" rows={5} value={behavior} onChange={e => setBehavior(e.target.value)} /></Field></details>
      <details><summary>Import an existing package instead of building the first candidate</summary><Field wide label="Package JSON" hint={'{"entrypoint":"optimizer:create_optimizer","files":[{"path":"optimizer.py","content":"..."}]}'}><textarea className="code-input" rows={7} value={sourcePackage} onChange={e => setSourcePackage(e.target.value)} spellCheck={false} /></Field></details>
      <p className="help-text">Up to three candidates can be built and repaired within this allocation. Questions and unresolved failures return to the campaign manager.</p>
      <ErrorNotice text={error} />
      <div className="modal-actions"><button className="button secondary" type="button" onClick={onClose}>Cancel</button><button className="button primary" disabled={busy || !task || !evaluatorReady || !allocationFits}>{busy ? 'Commissioning…' : 'Commission implementation'}</button></div>
    </form>
  </Modal>;
}

export function RevalidationForm({ version, state, onClose, onDone }: { version: Json; state: State; onClose: () => void; onDone: () => Promise<void> }) {
  const evaluator = version.kind === 'evaluator';
  const prior = version.validation_report?.validation_spec || version.spec;
  const [mode, setMode] = useState('numerical'), [checks, setChecks] = useState('[]');
  const [rationale, setRationale] = useState(''), [compute, setCompute] = useState(30);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const command = useCommand(state.campaign);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const additions = JSON.parse(checks);
      if (!Array.isArray(additions)) throw new Error('Supply a JSON array of independent checks.');
      if (evaluator && mode === 'numerical' && !prior.correctness_cases?.length && !additions.length)
        throw new Error('Add at least one independently derived numerical reference with its basis.');
      await api('/api/implementations');
      await command('implementation.revalidate', { version_id: version.id, compute_seconds: compute,
        checks: evaluator ? { kind: 'evaluator', rationale, validation_mode: mode,
          [mode === 'numerical' ? 'correctness_cases' : 'contract_cases']: additions }
          : { kind: 'optimizer', rationale, behavior_checks: additions } });
      await onDone();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <Modal title={`Revalidate ${version.name}`} description="Run independent checks against this exact published executable. Reports and costs are retained alongside its earlier evidence." onClose={onClose}>
    <form onSubmit={submit}><p className="help-text">This job uses the implementation allocation and makes no model calls. Earlier fixtures remain part of the checks. A known disagreement blocks further use until resolved.</p>
      {evaluator && !prior.correctness_cases?.length && <Field label="Revalidation scope"><select value={mode} onChange={e => { setMode(e.target.value); setChecks('[]'); }}>
        <option value="numerical">Independent numerical correctness</option><option value="contract_only">Repeat executable contract checks</option></select></Field>}
      <Field label={evaluator ? mode === 'numerical' ? 'Additional numerical fixtures (JSON)' : 'Additional contract probes (JSON)' : 'Additional behavior checks (JSON)'}
        hint={evaluator && mode === 'numerical' ? 'Each fixture declares name, candidate, expected objectives, and an independent basis; configuration, fidelity, and tolerances are optional.' : 'Leave [] to repeat the existing checks. New checks must stay within the executable’s frozen specification.'}>
        <textarea className="code-input" rows={9} value={checks} onChange={e => setChecks(e.target.value)} spellCheck={false} /></Field>
      <Field label="Revalidation rationale"><textarea required value={rationale} onChange={e => setRationale(e.target.value)} /></Field>
      <Field label="Revalidation time cap (seconds)"><input type="number" min="1" max="86400" required value={compute} onChange={e => setCompute(Number(e.target.value))} /></Field>
      <details><summary>Existing independent checks</summary><pre>{JSON.stringify(evaluator ? { numerical: prior.correctness_cases, contract: prior.contract_cases } : prior.behavior_checks, null, 2)}</pre></details>
      <ErrorNotice text={error} /><div className="modal-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button>
        <button className="button primary" disabled={busy || !rationale.trim()}>{busy ? 'Queuing checks…' : 'Queue independent revalidation'}</button></div>
    </form></Modal>;
}

function RuntimeControl({ version, state, refresh }: { version: Json; state: State; refresh: () => Promise<void> }) {
  const command = useCommand(state.campaign);
  const [runtime, setRuntime] = useState<Json | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const pending = !!runtime?.operations?.some((operation: Json) => operation.status === 'pending');
  async function read() {
    const result = await api<Json>(`/api/v1/implementations/${version.id}/runtime?campaign_id=${state.campaign?.id}`);
    setRuntime(result);
  }
  async function check(resolve = false) {
    setBusy(true); setError('');
    try {
      if (resolve) await command('implementation.resolve_runtime', { version_id: version.id });
      await read();
      if (resolve) await refresh();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  useEffect(() => { setRuntime(null); setError(''); }, [version.id, state.campaign?.id]);
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => { void read().catch(e => setError(errorText(e))); }, 2000);
    return () => window.clearInterval(timer);
  }, [pending, version.id, state.campaign?.id]);
  return <div className="runtime-control">
    <button className="text-button" disabled={busy || !state.campaign} onClick={() => void check()}>Check local runtime</button>
    {runtime && <>
      <p className="help-text"><Badge tone={runtime.status === 'available' ? 'green' : 'amber'}>
        {runtime.status === 'available' ? 'Runtime available' : 'Runtime unavailable'}</Badge> {runtime.reason}</p>
      {pending ? <p className="help-text">Runtime resolution is pending. Its receipt will appear here.</p>
        : runtime.status !== 'available' && runtime.can_resolve && <>
          <p className="help-text">Resolve this version using matching dependencies already installed on the implementation service.</p>
          <button className="button secondary" disabled={busy || !state.campaign} onClick={() => void check(true)}>
            {busy ? 'Resolving runtime…' : 'Resolve compatible runtime'}</button>
        </>}
      {!!runtime.receipts?.length && <details><summary>Runtime resolution receipts</summary><pre>{JSON.stringify(runtime.receipts, null, 2)}</pre></details>}
    </>}
    <ErrorNotice text={error} />
  </div>;
}

export function ImplementationLibrary({ state, refresh, discuss }: { state: State; refresh: () => Promise<void>; discuss: (message: string) => void }) {
  const [catalog, setCatalog] = useState<Json>(state.implementation_library || { versions: [] });
  const [error, setError] = useState(''), [busy, setBusy] = useState(''), [target, setTarget] = useState('');
  const [taskId, setTaskId] = useState(''), [rationale, setRationale] = useState('');
  const [revalidation, setRevalidation] = useState<Json | null>(null);
  const [search, setSearch] = useState('');
  const matches = (text: string) => text.toLowerCase().includes(search.trim().toLowerCase());
  const versions = (catalog.versions || []).filter((v: Json) => matches(`${v.name} ${v.id} ${v.spec.mechanism}`));
  const bundled = state.algorithms.filter(a => matches(`${a.name} ${a.id} ${a.description}`));
  const command = useCommand(state.campaign);
  const tasks = state.tasks.filter(task => task.evaluator_requirement_id);
  async function reload() { try { const query = new URLSearchParams();
    if (state.campaign) query.set('campaign_id', state.campaign.id);
    if (target) query.set('hypothesis_id', target); if (taskId) query.set('task_id', taskId);
    setCatalog(await api(`/api/implementations?${query}`)); setError(''); } catch (e) { setError(errorText(e)); } }
  useEffect(() => { void reload(); }, [state.campaign?.id, state.event_cursor, target, taskId]);
  async function decide(version: Json, decision: 'reuse' | 'decline') {
    setBusy(version.id); setError('');
    try {
      await command('implementation.reuse', { version_id: version.id, study_id: state.campaign?.active_study_id, decision, rationale,
        ...(version.kind === 'evaluator' ? { task_id: taskId } : { hypothesis_id: target }) });
      await refresh();
    } catch (e) { setError(errorText(e)); } finally { setBusy(''); }
  }
  async function control(id: string, action: string) {
    setBusy(id); setError('');
    try { await command('implementation.control', { grant_id: id, action }); await refresh(); }
    catch (e) { setError(errorText(e)); } finally { setBusy(''); }
  }
  return <>
    <div className="page-heading"><div><span className="eyebrow">Executable library</span><h1>Build, validate, and reuse.</h1><p>Inspect optimizer and evaluator versions and the evidence supporting their declared uses.</p></div>
      <button className="button secondary" onClick={() => void reload()}>Refresh library</button></div>
    <ErrorNotice text={error} />
    <Field label="Search installed implementations"><input aria-label="Search installed implementations" value={search} onChange={e => setSearch(e.target.value)} placeholder="Name, mechanism, or implementation ID" /></Field>
    <p className="help-text">Search covers bundled optimizers and this service's published packages. External source can be imported through Request implementation and passes the same correctness checks.</p>
    {!!bundled.length && <Panel title="Bundled optimizers"><div className="run-list">{bundled.map(method => <article key={method.id}><h3>{method.name}</h3><p>{method.description}</p><p className="help-text">Identifier: {method.id}. Check that the proposal's mechanism and parameters match before selecting it in a revised proposal.</p><details><summary>Supported parameters</summary><pre>{JSON.stringify((method as Json).parameter_schema || method.parameters || {}, null, 2)}</pre></details></article>)}</div></Panel>}
    {catalog.connection_error && <div className="callout amber"><p>{catalog.connection_error}</p><button className="text-button" onClick={() => discuss(catalog.connection_error)}>Discuss with campaign manager</button></div>}
    <Panel title="Implementation versions">
      <div className="form-grid"><Field label="Attach a version to an idea"><select value={target} onChange={e => setTarget(e.target.value)}>
        <option value="">Select an idea…</option>{state.hypotheses.filter(h => !['archived', 'finalist'].includes(h.status)).map(h => <option value={h.id} key={h.id}>{h.title}</option>)}</select></Field>
        {!!tasks.length && <><Field label="Attach an evaluator to a problem"><select value={taskId} onChange={e => setTaskId(e.target.value)}>
          <option value="">Select a problem…</option>{tasks.map(task => <option key={task.id} value={task.id}>{task.name}{task.evaluator_version_id ? ' · binding frozen' : ''}</option>)}</select></Field>
          </>}
        <Field label="Reason for reuse or decline" wide><textarea value={rationale} onChange={e => setRationale(e.target.value)} /></Field>
      </div>
      {versions.length ? <div className="run-list">{versions.map((version: Json) => {
        const evaluator = version.kind === 'evaluator', manifest = version.spec.manifest;
        const assessment = catalog.candidates?.[version.id];
        const selected = evaluator ? !!taskId : !!target;
        const compatible = selected && assessment?.eligible && !!rationale.trim();
        return <article key={version.id}><div className="row-between"><h3>{version.name}</h3><Status status={version.status} /></div>
          <Badge>{evaluator ? 'Problem evaluator' : 'Optimizer'}</Badge><p>{version.spec.mechanism}</p><p className="mono">{version.id}</p>
          <div className="meta-row"><span>{when(version.created_at)}</span>{evaluator ? <>
            <span>{manifest?.name} · {manifest?.candidate_schema?.representation}</span>
            <span>{manifest?.primary_objective?.direction} {manifest?.primary_objective?.name} ({manifest?.primary_objective?.units})</span></> : <>
            <span>{version.spec.n_cells_min}–{version.spec.n_cells_max} dimensions</span><span>{(version.spec.capabilities || []).join(', ')}</span></>}</div>
          <p className="help-text">{version.status === 'validation_failed' ? 'Revalidation found a disagreement. Further use is blocked; inspect the retained reports.' : evaluator ? version.status === 'contract_validated'
            ? 'Contract checks passed. Numerical correctness is unverified; exploratory use requires a study-scoped waiver.'
            : 'Numerical correctness evidence applies to this evaluator and its declared scope.' : 'Implementation checks support the declared behavior and scope.'} Optimizer performance is measured separately.</p>
          {version.validation_report?.scope?.evaluator_numerical_status === 'unverified' && <p className="callout amber">The evaluator used for these optimizer checks has no independent numerical evidence. Experimental use needs its own eligibility decision.</p>}
          <details><summary>Inspect validation and provenance</summary><pre>{JSON.stringify({ report: version.validation_report, history: version.validation_history, provenance: version.spec.provenance,
            dependencies: version.spec.dependencies, specification: version.spec }, null, 2)}</pre></details>
          <RuntimeControl version={version} state={state} refresh={refresh} />
          {assessment && <p className="help-text">{assessment.reason}</p>}
          <button className="button secondary" disabled={!compatible || !!busy || !(evaluator ? ['validated', 'contract_validated'] : ['validated']).includes(version.status)} onClick={() => void decide(version, 'reuse')}>
            {evaluator ? 'Use this evaluator for the selected problem' : 'Use this version for the selected idea'}</button>
          <button className="button secondary" disabled={!selected || !rationale.trim() || !!busy} onClick={() => void decide(version, 'decline')}>Record decision to decline reuse</button>
          <button className="button secondary" disabled={!state.campaign || !!busy || version.status === 'revoked'} onClick={() => setRevalidation(version)}>Revalidate this executable</button></article>;
      })}</div> : <Empty title="No published package versions">Commission an optimizer from an idea or an evaluator from the problem workbench. Supplied source uses the same validation workflow.</Empty>}
    </Panel>
    {!!state.executable_reuse_decisions?.length && <Panel title="Executable reuse decisions"><div className="run-list">{[...state.executable_reuse_decisions].reverse().map((decision: Json) => <article key={decision.id}>
      <div className="row-between"><h3>{catalog.versions?.find((version: Json) => version.id === decision.consequences.version_id)?.name || decision.consequences.version_id}</h3><Badge>{decision.decision === 'reuse' ? 'Reuse selected' : 'Reuse declined'}</Badge></div>
      <p>{decision.rationale}</p><p className="help-text">Study: {state.studies?.find((study: Json) => study.id === decision.study_id)?.goal || decision.study_id} · {decision.authority}</p>
      <details><summary>Decision scope and evidence</summary><pre>{JSON.stringify(decision, null, 2)}</pre></details>
    </article>)}</div></Panel>}
    <Panel title="Implementation jobs">{(state.implementation_jobs || []).length ? <div className="run-list">{[...state.implementation_jobs].reverse().map((job: Json) => <article key={job.id}>
      <div className="row-between"><h3>{job.request.spec?.name || `Revalidation · ${job.request.version_id}`}</h3><Status status={job.status} /></div><Badge>{(job.request.spec?.kind || job.request.checks?.kind) === 'evaluator' ? 'Problem evaluator' : 'Optimizer'}</Badge>
      <p>{job.error || `${(job.attempts || []).length} ${job.request.operation === 'revalidation' ? 'check' : 'candidate'} attempts`}</p><p className="help-text">{seconds(job.compute_seconds || 0)} used · {job.usage?.calls || 0} model calls</p>
      <div className="inline-actions">{!['completed', 'failed', 'cancelled', 'closed_uncertain'].includes(job.status) && <button className="button small secondary" disabled={!!busy} onClick={() => void control(job.id, 'cancel')}>Stop implementation</button>}
        {['interrupted', 'blocked'].includes(job.status) && <button className="button small secondary" disabled={!!busy} onClick={() => void control(job.id, 'resume')}>Resume within allocation</button>}
        {job.status === 'needs_reconciliation' && <button className="button small secondary" disabled={!!busy} onClick={() => void control(job.id, 'close_uncertain')}>Retain recorded usage and close</button>}
        <button className="text-button" onClick={() => discuss(`Review implementation job ${job.id}. ${job.error || 'Explain its current state and available next steps.'}`)}>Discuss with manager</button></div>
      <details><summary>{job.request.operation === 'revalidation' ? 'Independent revalidation reports' : 'Candidate validation reports'}</summary><pre>{JSON.stringify((job.attempts || []).map((a: Json) => ({ number: a.number, report: a.report, review: a.review })), null, 2)}</pre></details>
    </article>)}</div> : <Empty title="No implementation jobs yet">Implementations have their own allocation and validation workflow.</Empty>}</Panel>
    {revalidation && <RevalidationForm version={revalidation} state={state} onClose={() => setRevalidation(null)} onDone={async () => { await refresh(); await reload(); setRevalidation(null); }} />}
  </>;
}

export function CampaignMemoryView({ state, refresh }: { state: State; refresh: () => Promise<void> }) {
  const command = useCommand(state.campaign);
  const current = state.manager_context;
  const [draft, setDraft] = useState(current?.guidance || ''), [revision, setRevision] = useState(current?.revision || 0);
  const [dirty, setDirty] = useState(false), [history, setHistory] = useState<Json[]>([]), [selected, setSelected] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => { setDirty(false); setHistory([]); setSelected(''); setDraft(current?.guidance || ''); setRevision(current?.revision || 0); }, [state.campaign?.id]);
  useEffect(() => { if (!dirty) { setDraft(current?.guidance || ''); setRevision(current?.revision || 0); } }, [current?.id, dirty]);
  if (!state.campaign || !current) return <Empty title="Create a campaign to record its context">Campaign memory is stored as versioned structured text.</Empty>;
  async function save() { setBusy(true); setError(''); try { await command('context.edit', { content: draft, expected_revision: revision, reason: selected ? `Restore researcher guidance from ${selected}` : 'Researcher edited campaign guidance' }); setDirty(false); await refresh(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); } }
  async function revisions() { try { setHistory(await api<Json[]>(`/api/campaigns/${state.campaign!.id}/manager/context/history`)); } catch (e) { setError(errorText(e)); } }
  const previous = history.find(h => h.id === selected);
  return <><div className="page-heading"><div><span className="eyebrow">Durable campaign context</span><h1>What the manager remembers.</h1><p>Inspect the context, correct its guidance, and preserve the reasons behind changes of direction.</p></div><Badge>Revision {current.revision}</Badge></div><ErrorNotice text={error} />
    <Panel title="Researcher guidance"><p className="help-text">These notes guide future reasoning. Change executable budgets and physics through the charter; measurements retain their original records.</p><Field wide label="Campaign guidance (Markdown)"><textarea className="code-input memory-editor" rows={15} value={draft} onChange={e => { setDraft(e.target.value); setDirty(true); }} /></Field><div className="inline-actions"><button className="button primary" disabled={!dirty || busy} onClick={() => void save()}>Save new memory revision</button><button className="button secondary" onClick={() => { setDraft(current.guidance); setRevision(current.revision); setDirty(false); setError(''); }}>Reload current guidance</button><button className="text-button" onClick={() => void revisions()}>View revision history</button></div></Panel>
    {history.length > 0 && <Panel title="Revision history"><Field label="Earlier revision"><select value={selected} onChange={e => setSelected(e.target.value)}><option value="">Select a revision…</option>{[...history].reverse().map(h => <option value={h.id} key={h.id}>Revision {h.revision} · {when(h.updated_at)}</option>)}</select></Field>{previous && <><div className="dossier-columns"><section><h3>Earlier guidance</h3><TextContent text={previous.guidance} /></section><section><h3>Current guidance</h3><TextContent text={current.guidance} /></section></div><button className="button secondary" onClick={() => { setDraft(previous.guidance); setRevision(current.revision); setDirty(true); }}>Restore this guidance as a new revision</button></>}</Panel>}
    <Panel title="Current manager context"><TextContent text={current.document} /><details><summary>Context provenance</summary><pre>{JSON.stringify({ revision: current.id, event_cursor: current.event_cursor, sources: current.source_ids }, null, 2)}</pre></details></Panel>
  </>;
}

export function ManagerIssues({ state, refresh, discuss }: { state: State; refresh: () => Promise<void>; discuss: (message: string) => void }) {
  const command = useCommand(state.campaign);
  const [error, setError] = useState('');
  const issues = (state.manager_issues || []).filter((i: Json) => i.status === 'pending');
  async function resolve(issue: Json, choice: string) { try { await command('issue.resolve', { issue_id: issue.id, expected_revision: issue.revision, choice }); await refresh(); } catch (e) { setError(errorText(e)); } }
  return <><ErrorNotice text={error} />{issues.map((issue: Json) => <section className="manager-issue" key={issue.id}><Badge tone="amber">Needs attention</Badge><TextContent text={issue.message} /><div className="inline-actions"><button type="button" className="text-button" onClick={() => discuss(`Resolve issue ${issue.id}: ${issue.message}`)}>Discuss</button><button type="button" className="text-button" onClick={() => void resolve(issue, 'deferred')}>Defer</button><button type="button" className="text-button" onClick={() => void resolve(issue, 'resolved')}>Mark resolved</button></div></section>)}</>;
}
