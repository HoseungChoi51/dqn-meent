import { useEffect, useState } from 'react';
import type { Json, State, Task } from './api';
import { api, errorText, seconds } from './api';
import { useCommand } from './commands';
import { ErrorNotice, Field, Modal } from './ui';
import { SchemaFields, resolvedParameters } from './schemaFields';

const emptySchema = { type: 'object', properties: {}, additionalProperties: false };
export function newProblemManifest(index: number): Json {
  return { id: `custom_problem_${index + 1}`, name: 'Custom optimization problem',
    candidate_schema: { representation: 'continuous', dimensions: 2, bounds: [[-1, 1], [-1, 1]], values: [], constraints: [] },
    primary_objective: { name: 'value', direction: 'minimize', units: 'dimensionless' }, extra_metrics: [],
    configuration_schema: emptySchema, configuration: {}, fidelity_schema: emptySchema, fidelity: {},
    public_configuration_keys: [], deterministic: true };
}

export function ProblemManifestFields({ value, onChange }: { value: Json; onChange: (value: Json) => void }) {
  const [advanced, setAdvanced] = useState(() => JSON.stringify(value, null, 2)), [error, setError] = useState('');
  const [recipes, setRecipes] = useState<Json[]>([]), [catalogError, setCatalogError] = useState('');
  useEffect(() => { let current = true;
    api('/api/v1/registered-recipes').then(result => { if (current) setRecipes(result.recipes); })
      .catch(e => { if (current) setCatalogError(errorText(e)); });
    return () => { current = false; };
  }, []);
  useEffect(() => { setAdvanced(JSON.stringify(value, null, 2)); }, [value]);
  const domain = value.candidate_schema, objective = value.primary_objective;
  const [valuesText, setValuesText] = useState<string>((domain.values || []).join(', '));
  useEffect(() => { setValuesText((domain.values || []).join(', ')); }, [domain.values]);
  function candidate(changes: Json) { onChange({ ...value, candidate_schema: { ...domain, ...changes } }); }
  function resize(dimensions: number) {
    if (!Number.isInteger(dimensions) || dimensions < 1 || dimensions > 1000) return;
    candidate({ dimensions, bounds: domain.representation === 'continuous' ? Array.from({ length: dimensions }, (_, i) => domain.bounds[i] || [-1, 1]) : [] });
  }
  return <fieldset className="diagnostic-editor"><legend>Problem declaration</legend><p className="help-text">Declare what a candidate and a measurement mean. An evaluator can be commissioned after saving the campaign.</p>
    <div className="form-grid">
      <Field label="Problem identifier"><input required pattern="[a-z][a-z0-9_]{1,99}" value={value.id} onChange={e => onChange({ ...value, id: e.target.value })} /></Field>
      <Field label="Problem name"><input required value={value.name} onChange={e => onChange({ ...value, name: e.target.value })} /></Field>
      <Field label="Candidate representation"><select value={domain.representation} onChange={e => candidate({ representation: e.target.value,
        bounds: e.target.value === 'continuous' ? Array.from({ length: domain.dimensions }, () => [-1, 1]) : [], values: e.target.value === 'discrete' ? [0, 1, 2] : [] })}>
        <option value="continuous">Bounded continuous</option><option value="binary">Binary</option><option value="discrete">Discrete values</option></select></Field>
      <Field label="Candidate dimensions"><input type="number" min="1" max="1000" value={domain.dimensions} onChange={e => resize(Number(e.target.value))} /></Field>
      {domain.representation === 'continuous' && <><Field label="Common lower bound"><input type="number" step="any" required value={domain.bounds[0][0]}
        onChange={e => candidate({ bounds: domain.bounds.map((b: number[]) => [Number(e.target.value), b[1]]) })} /></Field>
        <Field label="Common upper bound"><input type="number" step="any" required value={domain.bounds[0][1]}
          onChange={e => candidate({ bounds: domain.bounds.map((b: number[]) => [b[0], Number(e.target.value)]) })} /></Field></>}
      {domain.representation === 'discrete' && <Field label="Permitted values (comma separated)"><input required value={valuesText}
        onChange={e => { setValuesText(e.target.value); e.target.setCustomValidity(''); }}
        onBlur={e => { const parts = valuesText.split(',').map(s => s.trim()), values = parts.map(Number);
          if (parts.some(s => !s) || values.some(v => !Number.isFinite(v))) e.target.setCustomValidity('Enter finite numbers separated by commas.');
          else candidate({ values }); }} /></Field>}
      <Field label="Objective name"><input required value={objective.name} onChange={e => onChange({ ...value, primary_objective: { ...objective, name: e.target.value } })} /></Field>
      <Field label="Optimization direction"><select value={objective.direction} onChange={e => onChange({ ...value, primary_objective: { ...objective, direction: e.target.value } })}>
        <option value="minimize">Minimize</option><option value="maximize">Maximize</option></select></Field>
      <Field label="Objective units"><input required value={objective.units} onChange={e => onChange({ ...value, primary_objective: { ...objective, units: e.target.value } })} /></Field>
      <Field label="Repeated evaluation"><select value={String(value.deterministic)} onChange={e => onChange({ ...value, deterministic: e.target.value === 'true' })}>
        <option value="true">Deterministic</option><option value="false">Stateful or stochastic; checkpoint continuation required</option></select></Field>
    </div>
    <fieldset><legend>Supported analysis recipes</legend><p className="help-text">Choose reviewed operations that future experiments can run with this evaluator. Recipe results keep their stated evidence limits.</p>
      {recipes.map(recipe => <Field key={recipe.id} label={recipe.title} hint={recipe.description}><input type="checkbox" checked={(value.recipe_ids || []).includes(recipe.id)}
        onChange={e => onChange({ ...value, recipe_ids: e.target.checked ? [...(value.recipe_ids || []), recipe.id] : (value.recipe_ids || []).filter((id: string) => id !== recipe.id) })} /></Field>)}
      {(value.recipe_ids || []).filter((id: string) => !recipes.some(recipe => recipe.id === id)).map((id: string) => <p className="callout amber" key={id}>{id} is declared but unavailable in the installed recipe catalog.</p>)}
      <ErrorNotice text={catalogError} /></fieldset>
    <details><summary>Advanced declaration: configuration, fidelity, constraints and metrics</summary>
      <Field wide label="Problem manifest JSON"><textarea rows={12} className="code-input" value={advanced} onChange={e => setAdvanced(e.target.value)} /></Field>
      <button className="button secondary" type="button" onClick={() => { try { const parsed = JSON.parse(advanced);
        const domain = parsed?.candidate_schema;
        if (!['continuous', 'binary', 'discrete'].includes(domain?.representation) || !Number.isInteger(domain?.dimensions)
          || domain.dimensions < 1 || domain.dimensions > 1000 || !parsed?.primary_objective?.name) throw new Error('Declare a supported candidate domain, dimensions and objective.');
        if (domain.representation === 'continuous' && (!Array.isArray(domain.bounds) || domain.bounds.length !== domain.dimensions
          || domain.bounds.some((b: unknown) => !Array.isArray(b) || b.length !== 2 || b.some(v => typeof v !== 'number' || !Number.isFinite(v)) || b[0] >= b[1]))) throw new Error('Provide one finite increasing bound pair per dimension.');
        if (domain.representation === 'discrete' && (!Array.isArray(domain.values) || !domain.values.length
          || domain.values.some((v: unknown) => typeof v !== 'number' || !Number.isFinite(v)))) throw new Error('Provide finite permitted discrete values.');
        onChange({ ...parsed, candidate_schema: { bounds: [], values: [], constraints: [], ...domain } }); setError('');
      } catch (e) { setError(errorText(e)); } }}>Apply declaration</button><ErrorNotice text={error} />
    </details></fieldset>;
}

type Fixture = { name: string; basis: string; candidate: string; objectives: Json; configuration: Json; fidelity: Json };
export function EvaluatorRequest({ task, state, onClose, onDone }: { task: Task; state: State; onClose: () => void; onDone: () => void }) {
  const manifest = task.evaluator_manifest!;
  const objectives = [manifest.primary_objective, ...(manifest.extra_metrics || [])];
  const [validationMode, setValidationMode] = useState('numerical');
  const contractOnly = validationMode === 'contract_only', caseLabel = contractOnly ? 'Probe' : 'Reference';
  const numericalCriteria = 'Return the declared raw objective and units\nMatch independent numerical references\nRestore complete evaluator state';
  const contractCriteria = 'Return finite values in the declared output format\nPreserve declared determinism\nRestore complete evaluator state';
  const fresh = (index: number): Fixture => ({ name: `${caseLabel} ${index + 1}`, basis: '',
    candidate: Array(manifest.candidate_schema.dimensions).fill(0).join(', '), objectives: {},
    configuration: { ...manifest.configuration }, fidelity: { ...manifest.fidelity } });
  const [fixtures, setFixtures] = useState<Fixture[]>([fresh(0)]);
  const [mechanism, setMechanism] = useState(''), [criteria, setCriteria] = useState(numericalCriteria);
  const [dependencies, setDependencies] = useState('{}'), [source, setSource] = useState('');
  const [compute, setCompute] = useState('120'), [calls, setCalls] = useState('12');
  const [apiCap, setApiCap] = useState('0'), [timeout, setTimeoutValue] = useState('10');
  const [catalog, setCatalog] = useState<Json[]>([]), [version, setVersion] = useState(''), [rationale, setRationale] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const command = useCommand(state.campaign);
  useEffect(() => { let active = true; api(`/api/implementations?campaign_id=${state.campaign?.id || ''}&task_id=${task.id}`).then(result => { if (active) {
    setCatalog((result.versions || []).filter((v: Json) => v.kind === 'evaluator' && v.spec.manifest.id === manifest.id)
      .map((v: Json) => ({ ...v, candidate_assessment: result.candidates?.[v.id] })));
    if (result.connection_error) setError(result.connection_error);
  } }).catch(e => { if (active) setError(errorText(e)); }); return () => { active = false; }; }, [manifest.id, task.id, state.campaign?.id]);
  function change(index: number, values: Partial<Fixture>) { setFixtures(items => items.map((item, i) => i === index ? { ...item, ...values } : item)); }
  async function commission(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const cases = fixtures.map(item => ({ name: item.name,
        candidate: item.candidate.split(',').map(s => { if (!s.trim() || !Number.isFinite(Number(s))) throw new Error('Each candidate coordinate needs a finite number.'); return Number(s); }),
        configuration: resolvedParameters(manifest.configuration_schema || emptySchema, item.configuration),
        fidelity: resolvedParameters(manifest.fidelity_schema || emptySchema, item.fidelity),
        ...(!contractOnly ? { basis: item.basis, objectives: Object.fromEntries(objectives.map(o => [o.name, Number(item.objectives[o.name])])) } : {}) }));
      await command('evaluator.commission', { task_id: task.id, compute_seconds: Number(compute), max_calls: Number(calls), api_budget_usd: Number(apiCap),
        spec: { kind: 'evaluator', name: `${manifest.name} evaluator`, mechanism, acceptance_criteria: criteria.split('\n').filter(s => s.trim()),
          manifest, dependencies: JSON.parse(dependencies), operation_timeout_seconds: Number(timeout),
          ...(contractOnly ? { validation_mode: 'contract_only', contract_cases: cases, correctness_cases: [] } : { correctness_cases: cases }) },
        package: source.trim() ? JSON.parse(source) : null });
      onDone(); onClose();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function decide(decision: 'reuse' | 'decline') {
    setBusy(true); setError('');
    try { await command('implementation.reuse', { task_id: task.id, study_id: state.campaign?.active_study_id, version_id: version, decision, rationale });
      onDone(); if (decision === 'reuse') onClose(); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <Modal wide title="Commission an evaluator" description="Define the measurement implementation and independent checks. Unresolved findings return through the campaign manager." onClose={onClose}>
    <p><strong>{manifest.name}</strong> · {manifest.primary_objective.direction} {manifest.primary_objective.name} ({manifest.primary_objective.units})</p>
    <p className="help-text">Implementation allocation: {seconds(state.campaign?.implementation_compute_budget_seconds || 0)}. Publication does not establish optimizer performance.</p>
    {!!catalog.length && <fieldset><legend>Reuse an existing evaluator</legend><Field label="Published evaluator version"><select value={version} onChange={e => setVersion(e.target.value)}>
      <option value="">Select a version</option>{catalog.map(v => <option key={v.id} value={v.id}>{v.name} · {v.status.replaceAll('_', ' ')} · {v.id}</option>)}</select></Field>
      {version && <p className="help-text">{catalog.find(v => v.id === version)?.candidate_assessment?.reason}</p>}
      <Field label="Reason this version fits"><textarea value={rationale} onChange={e => setRationale(e.target.value)} /></Field>
      <button className="button secondary" disabled={busy || !version || !rationale.trim() || !catalog.find(v => v.id === version)?.candidate_assessment?.eligible} onClick={() => void decide('reuse')}>Use evaluator version</button>
      <button className="button secondary" disabled={busy || !version || !rationale.trim()} onClick={() => void decide('decline')}>Record decision to decline reuse</button></fieldset>}
    <form onSubmit={commission}><div className="form-grid">
      <Field label="Evaluator specification" wide><textarea required rows={4} value={mechanism} onChange={e => setMechanism(e.target.value)} /></Field>
      <Field label="Evaluator acceptance criteria" wide><textarea required rows={3} value={criteria} onChange={e => setCriteria(e.target.value)} /></Field>
      <Field label="Evaluator time allocation (seconds)"><input type="number" required min="1" max="86400" value={compute} onChange={e => setCompute(e.target.value)} /></Field>
      <Field label="Evaluator maximum model calls"><input type="number" required min="1" max="20" value={calls} onChange={e => setCalls(e.target.value)} /></Field>
      <Field label="Evaluator API allocation (USD)"><input type="number" required min="0" step="0.01" value={apiCap} onChange={e => setApiCap(e.target.value)} /></Field>
      <Field label="Maximum time per evaluation (seconds)"><input type="number" required min="0.01" step="any" max="3600" value={timeout} onChange={e => setTimeoutValue(e.target.value)} /></Field>
      <Field label="Evaluator pinned dependencies (JSON)" wide><textarea value={dependencies} onChange={e => setDependencies(e.target.value)} /></Field>
      <Field label="Evaluator validation mode" wide><select value={validationMode} onChange={e => { setValidationMode(e.target.value);
        if ([numericalCriteria, contractCriteria].includes(criteria)) setCriteria(e.target.value === 'contract_only' ? contractCriteria : numericalCriteria);
      }}><option value="numerical">Independent numerical references</option><option value="contract_only">Contract checks only · exploratory use</option></select></Field>
    </div><h3>{contractOnly ? 'Contract probes' : 'Independent numerical references'}</h3><p className={contractOnly ? 'callout amber' : 'help-text'}>{contractOnly
      ? 'These probes check the output format, declared determinism and recovery. Numerical correctness remains unverified. Experiments require a separate study-scoped exploratory waiver; confirmation is unavailable.'
      : 'Supply known values with their analytical or trusted source basis. Candidate code does not receive these fixtures.'}</p>
      {fixtures.map((item, index) => <fieldset className="diagnostic-editor" key={index}><legend>{caseLabel} {index + 1}</legend><div className="form-grid">
        <Field label={`${caseLabel} ${index + 1} name`}><input required value={item.name} onChange={e => change(index, { name: e.target.value })} /></Field>
        <Field label={`${caseLabel} ${index + 1} candidate`}><input required value={item.candidate} onChange={e => change(index, { candidate: e.target.value })} /></Field>
        {!contractOnly && objectives.map(o => <Field key={o.name} label={`Reference ${index + 1} expected ${o.name}`} hint={o.units}><input type="number" required step="any" value={item.objectives[o.name] ?? ''}
          onChange={e => change(index, { objectives: { ...item.objectives, [o.name]: e.target.value } })} /></Field>)}
        {!contractOnly && <Field label={`Reference ${index + 1} independent basis`} wide><textarea required value={item.basis} onChange={e => change(index, { basis: e.target.value })} /></Field>}
        <SchemaFields schema={manifest.configuration_schema || emptySchema} values={item.configuration} setValues={configuration => change(index, { configuration })} />
        <SchemaFields schema={manifest.fidelity_schema || emptySchema} values={item.fidelity} setValues={fidelity => change(index, { fidelity })} />
      </div>{fixtures.length > 1 && <button type="button" className="text-button danger" onClick={() => setFixtures(fixtures.filter((_, i) => i !== index))}>Remove {caseLabel.toLowerCase()} {index + 1}</button>}</fieldset>)}
      <button type="button" className="button secondary" onClick={() => setFixtures([...fixtures, fresh(fixtures.length)])}>{contractOnly ? 'Add contract probe' : 'Add independent reference'}</button>
      <details><summary>Supply an existing evaluator package</summary><Field label="Evaluator package JSON" wide><textarea className="code-input" rows={7} value={source} onChange={e => setSource(e.target.value)} /></Field></details>
      <ErrorNotice text={error} /><div className="modal-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button>
        <button className="button primary" disabled={busy}>{busy ? 'Commissioning…' : 'Commission evaluator'}</button></div>
    </form></Modal>;
}
