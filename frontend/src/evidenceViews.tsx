import { useEffect, useRef, useState } from 'react';
import { api, errorText, objectiveValue, problemCatalogPath, problemDefinition, seconds } from './api';
import type { Json, State } from './api';
import { useCommand } from './commands';
import { SchemaFields as RecipeParameters, resolvedParameters } from './schemaFields';
import { Badge, Empty, ErrorNotice, Field, Modal, Panel as BasePanel, Status } from './ui';
import type { ComponentProps } from 'react';
import type { WorkspaceActions } from './views';
import { RevalidationForm } from './implementations';
import { BundleTransfers } from './bundles';
import { CostReceipts } from './costReceipts';
import { ReproductionPanel } from './reproduction';

type Props = { state: State; actions: WorkspaceActions };
function Panel({ children, ...props }: ComponentProps<typeof BasePanel>) {
  return <BasePanel {...props}><div className="evidence-panel-body">{children}</div></BasePanel>;
}
function useRead<T>(path: string | null, revision: unknown) {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState('');
  const mounted = useRef(true), fetching = useRef(false), current = useRef<string | null>(null), pending = useRef<string | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; pending.current = null; }; }, []);
  useEffect(() => {
    if (current.current !== path) { current.current = path; setData(null); setError(''); }
    pending.current = path;
    async function refresh() {
      if (fetching.current) return;
      fetching.current = true;
      try {
        // Coalesce SSE revisions during an expensive evidence read. Publish a
        // completed read for the same resource, then fetch the latest revision;
        // continuous events must not discard every response or stack requests.
        while (mounted.current && pending.current) {
          const requested = pending.current; pending.current = null;
          try {
            const value = await api<T>(requested);
            if (mounted.current && current.current === requested) { setData(value); setError(''); }
          } catch (e) { if (mounted.current && current.current === requested) setError(errorText(e)); }
        }
      } finally { fetching.current = false; }
    }
    void refresh();
  }, [path, revision]);
  return { data, error };
}
function eventRevision(state: State) { return state.event_cursor ?? Math.max(0, ...state.events.map(e => Number(e.id) || 0)); }
function cost(value: Json | undefined, axis: string) {
  if (!value || value.total == null) return `Unknown${value?.known ? ` (${axis === 'worker_seconds' ? seconds(value.known) : value.known} measured)` : ''}`;
  return axis === 'worker_seconds' ? seconds(value.total) : value.total.toLocaleString();
}

function ObjectivePlot({ group, axis }: { group: Json; axis: string }) {
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  for (const trial of group.trials) for (const point of trial.curve) {
    xmin = Math.min(xmin, point.cost); xmax = Math.max(xmax, point.cost);
    ymin = Math.min(ymin, point.objective); ymax = Math.max(ymax, point.objective);
  }
  if (!Number.isFinite(xmin)) return <p className="help-text">No complete cost observations are available for a curve yet.</p>;
  const x = (value: number) => 65 + (value - xmin) / (xmax - xmin || 1) * 650;
  const y = (value: number) => 230 - (value - ymin) / (ymax - ymin || 1) * 195;
  const colors = ['#215e72', '#bb7126', '#7454a1', '#1e8063', '#c05258'];
  return <svg viewBox="0 0 760 285" role="img" aria-label={`${group.problem.primary_objective.name} by ${axis}`} className="evidence-plot">
    <path d="M65 25V230H720" fill="none" stroke="#bac6cb" />
    <text x="60" y="245" textAnchor="middle">{xmin.toPrecision(3)}</text><text x="715" y="245" textAnchor="middle">{xmax.toPrecision(3)}</text>
    <text x="57" y="234" textAnchor="end">{ymin.toPrecision(3)}</text><text x="57" y="39" textAnchor="end">{ymax.toPrecision(3)}</text>
    <text x="390" y="274" textAnchor="middle">{axis.replaceAll('_', ' ')}</text>
    {group.trials.map((trial: Json, i: number) => <g key={trial.id}><polyline fill="none" stroke={colors[i % colors.length]} strokeWidth="2"
      points={trial.curve.map((point: Json) => `${x(point.cost)},${y(point.objective)}`).join(' ')}><title>{trial.algorithm} · seed {trial.seed}</title></polyline>
      {trial.curve.slice(-1).map((point: Json) => <circle key={point.cost} cx={x(point.cost)} cy={y(point.objective)} r="4" fill={colors[i % colors.length]}>
        <title>{trial.algorithm}, seed {trial.seed}: {point.objective} at {point.cost}</title></circle>)}</g>)}
  </svg>;
}

export function GeneralComparison({ state }: Props) {
  const [axis, setAxis] = useState('worker_seconds'), [study, setStudy] = useState('');
  const studyId = study || state.campaign?.active_study_id;
  const path = state.campaign ? `/api/v1/campaigns/${state.campaign.id}/comparison?cost_axis=${axis}${studyId ? `&study_id=${studyId}` : ''}` : null;
  const { data: report, error } = useRead<Json>(path, eventRevision(state));
  return <><div className="page-heading"><div><span className="eyebrow">Evidence comparison</span><h1>Compare at full upstream cost.</h1>
    <p>Each problem instance and fidelity has its own comparison. Incomplete work and unknown costs stay visible.</p></div></div>
    <div className="form-grid"><Field label="Study"><select value={studyId || ''} onChange={e => setStudy(e.target.value)}>{(state.studies || []).map((s: Json) => <option key={s.id} value={s.id}>{s.goal}</option>)}</select></Field>
      <Field label="Comparison cost"><select value={axis} onChange={e => setAxis(e.target.value)}><option value="worker_seconds">Full upstream worker time</option><option value="evaluation_requests">Full upstream evaluation requests</option><option value="solver_executions">Full upstream solver executions</option></select></Field></div>
    <ErrorNotice text={error} />
    {report && !report.groups.length && <Empty title="No comparable experiments yet">Create experiments in this study to compare their observed objectives.</Empty>}
    {report?.groups.map((group: Json) => <Panel key={group.id} title={`${group.problem.definition_id} · ${group.problem.primary_objective.direction} ${group.problem.primary_objective.name}`}>
      <p className="help-text">Objective units: {group.problem.primary_objective.units} · matched cost: {group.common_observed_cost == null ? 'No shared observed range' : axis === 'worker_seconds' ? seconds(group.common_observed_cost) : group.common_observed_cost}</p>
      <ObjectivePlot group={group} axis={axis} />
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Method / seed</th><th>Best observed</th><th>Full cost</th><th>Procedure</th></tr></thead><tbody>{group.trials.map((trial: Json) => <tr key={trial.id}>
        <td>{trial.algorithm} · {trial.seed}<small>{trial.method_id.slice(0, 10)}</small></td><td>{objectiveValue(trial.best_objective, group.problem.primary_objective)}</td>
        <td>{cost(trial.full_cost?.quantities?.[axis], axis)}{trial.unknown_cost && <small>Incomplete cost provenance</small>}</td>
        <td>{trial.scientific_complete ? 'Complete' : 'Incomplete / censored'}{trial.adaptive_extension && <small>Allocation extended</small>}</td></tr>)}</tbody></table></div>
      <p className="help-text">Descriptive observations; this view does not establish superiority or estimate uncertainty.</p></Panel>)}
    {report && <Panel title="Actual campaign expenditure"><p>{cost(report.actual_campaign_costs.quantities.worker_seconds, 'worker_seconds')} worker time · {cost(report.actual_campaign_costs.quantities.evaluation_requests, 'evaluation_requests')} requests.</p>
      <p>{cost(report.actual_campaign_costs.quantities.model_calls, 'model_calls')} model calls · implementation service elapsed time: {cost(report.actual_campaign_costs.quantities.implementation_seconds, 'worker_seconds')}.</p>
      <p className="help-text">Shared computations are counted once here. Each dependent method receives its full upstream contribution in the comparison.</p></Panel>}
  </>;
}

export function AssetLibrary({ state, actions }: Props) {
  const { data: assets, error } = useRead<Json[]>('/api/v1/assets', eventRevision(state));
  const [selected, setSelected] = useState<string | null>(null), [decision, setDecision] = useState('reference'), [use, setUse] = useState('manager_evidence');
  const [rationale, setRationale] = useState(''), [failure, setFailure] = useState(''), [busy, setBusy] = useState(false);
  const { data: detail, error: detailError } = useRead<Json>(selected ? `/api/v1/assets/${selected}` : null, eventRevision(state));
  const command = useCommand(state.campaign);
  async function decide(event: React.FormEvent) {
    event.preventDefault(); if (!selected) return; setBusy(true); setFailure('');
    try { await command('asset.reuse', { asset_id: selected, study_id: state.campaign?.active_study_id, decision, intended_use: use, rationale });
      await actions.refresh(); actions.notify('Asset decision recorded for this study.'); setSelected(null); setRationale(''); }
    catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  return <><div className="page-heading"><div><span className="eyebrow">Research assets</span><h1>Reuse when it helps this problem.</h1><p>Inspect applicability and production costs, then record whether to reuse, reference, or decline an asset.</p></div></div>
    <BundleTransfers state={state} assets={assets || []} refresh={actions.refresh} />
    <ReproductionPanel state={state} actions={actions} />
    <ErrorNotice text={error} />{assets && !assets.length && <Empty title="No assets published yet">Finished experiments publish solutions and supported diagnostics or policies here.</Empty>}
    {!!assets?.length && <Panel title="Available assets"><div className="table-scroll"><table className="data-table"><thead><tr><th>Asset</th><th>Kind</th><th>Problem</th><th>Cost provenance</th></tr></thead><tbody>{assets.map(asset => <tr key={asset.id}>
      <td><button className="table-link" onClick={() => setSelected(asset.id)}>{asset.title}</button></td><td>{asset.kind.replaceAll('_', ' ')}</td><td>{asset.applicability?.problem_id || 'Declared in record'}</td><td>{asset.cost_provenance}</td></tr>)}</tbody></table></div></Panel>}
    {selected && <Modal wide title={detail?.asset.title || 'Inspect asset'} description="A reference decision exposes evidence to the manager. An input reuse decision permits a later experiment to declare this asset." onClose={() => setSelected(null)}>
      <ErrorNotice text={detailError} />{detail && <><p>Full attributed worker cost: <strong>{cost(detail.full_attributed_cost.quantities.worker_seconds, 'worker_seconds')}</strong></p>
        {state.campaign && <CostReceipts key={detail.asset.id} detail={detail} campaign={state.campaign} refresh={actions.refresh} />}
        {detail.local_availability && <p>Local artifacts: {detail.local_availability.status}{detail.local_availability.reason ? ` · ${detail.local_availability.reason}` : ''}</p>}
        <p>{detail.asset.exposure_status === 'known' ? 'Exposure is recorded.' : 'Historical exposure is unknown; some confirmation claims may be ineligible.'}</p>
        <h3>Upstream assets</h3>{!detail.asset.dependency_ids.length ? <p className="muted">No declared asset dependencies.</p> : <ul>{detail.asset.dependency_ids.map((id: string) => <li key={id}><button className="text-button" onClick={() => setSelected(id)}>{id}</button></li>)}</ul>}
        <details><summary>Evidence, applicability, and artifact references</summary><pre>{JSON.stringify(detail.asset, null, 2)}</pre></details>
        {!!detail.historical_producers?.length && <details><summary>Historical producers and objective interpretation</summary><pre>{JSON.stringify(detail.historical_producers, null, 2)}</pre></details>}
        <form onSubmit={decide}><div className="form-grid"><Field label="Asset decision"><select value={decision} onChange={e => { setDecision(e.target.value); if (e.target.value === 'reference') setUse('manager_evidence'); }}><option value="reference">Read as reference evidence</option><option value="reuse">Reuse in this study</option><option value="decline">Decline reuse</option></select></Field>
          <Field label="Intended use"><select value={use} onChange={e => setUse(e.target.value)}><option value="manager_evidence">Manager evidence</option><option value="procedure">Study procedure</option>{decision !== 'reference' && <option value="optimizer_input">Declared optimizer input</option>}</select></Field>
          <Field wide label="Applicability rationale"><textarea required value={rationale} onChange={e => setRationale(e.target.value)} /></Field></div>
          <ErrorNotice text={failure} /><button className="button primary" disabled={busy}>Record asset decision</button></form></>}
    </Modal>}</>;
}

export function ValidationView({ state, actions }: Props) {
  const revision = eventRevision(state);
  const { data: requirements, error } = useRead<Json[]>(state.campaign ? `/api/v1/campaigns/${state.campaign.id}/validations` : null, revision);
  const [source, setSource] = useState(''), [recipeId, setRecipeId] = useState(''), [values, setValues] = useState<Json>({});
  const [wall, setWall] = useState(120), [mode, setMode] = useState('validation.run'), [failure, setFailure] = useState(''), [busy, setBusy] = useState(false);
  const [waiver, setWaiver] = useState<Json | null>(null), [rationale, setRationale] = useState(''), [evidence, setEvidence] = useState('');
  const [revoking, setRevoking] = useState<Json | null>(null);
  const [revalidation, setRevalidation] = useState<Json | null>(null);
  const command = useCommand(state.campaign);
  const trials = state.trials.filter(trial => !['recipe', 'validate'].includes(trial.algorithm));
  const trial = trials.find(item => item.id === source) || trials[0];
  const { data: captured, error: catalogError } = useRead<Json>(trial ? `/api/v1/trials/${trial.id}/problem` : null, 0);
  const definition = problemDefinition(captured?.definition ? [captured.definition] : [], trial?.problem);
  const recipe = definition?.validation_recipes.includes(recipeId) ? recipeId
    : definition?.validation_recipes.find((id: string) => definition.recipe_schemas?.[id]?.available !== false);
  const rawSchema = definition?.recipe_schemas?.[recipe] || {};
  const schema = { ...rawSchema, properties: Object.fromEntries(Object.entries(rawSchema.properties || {}).map(([key, raw]) => {
    const field = raw as Json;
    return [key, field.default_from_fidelity ? { ...field, default: trial?.problem?.fidelity?.[field.default_from_fidelity] } : field];
  })) };
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (!trial || !recipe) return; setBusy(true); setFailure('');
    try {
      const parameters = resolvedParameters(schema, values);
      const operation = schema.assertion_kind ? mode : 'validation.run';
      await command(operation, { trial_id: trial.id, recipe_id: recipe, parameters, wall_seconds: wall }); await actions.refresh(); actions.notify(operation === 'validation.require' ? 'Validation requirement recorded.' : 'Validation job queued.');
    } catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  async function execute(requirement: Json) {
    setBusy(true); setFailure('');
    try { await command('validation.execute', { requirement_id: requirement.id, wall_seconds: wall }); await actions.refresh(); actions.notify('The frozen validation procedure is queued.'); }
    catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  async function waive(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setFailure('');
    try { await command('validation.waive', { requirement_id: waiver?.requirement.id, rationale, evidence_ids: evidence.split(',').map(id => id.trim()).filter(Boolean) });
      await actions.refresh(); setWaiver(null); actions.notify('Waiver recorded separately from measured results.'); }
    catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  async function revoke(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setFailure('');
    try { await command('validation.revoke_waiver', { waiver_id: revoking?.id, rationale });
      await actions.refresh(); setRevoking(null); actions.notify('Waiver revoked. New attempts must satisfy the requirement again.'); }
    catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  return <><div className="page-heading"><div><span className="eyebrow">Scoped evidence</span><h1>Validation and waivers.</h1><p>Requirements bind a fixed subject and procedure. A waiver records authority and rationale without becoming a measured pass.</p></div></div>
    <ErrorNotice text={error} /><ErrorNotice text={catalogError} /><ErrorNotice text={failure} />
    {!!trials.length && <Panel title="Request a check or diagnostic"><form onSubmit={submit}><div className="form-grid">
      <Field label="Source experiment"><select value={trial?.id || ''} onChange={e => { setSource(e.target.value); setRecipeId(''); setValues({}); }}>{trials.map(item => <option key={item.id} value={item.id}>{item.algorithm} · seed {item.seed} · {item.id.slice(-8)}</option>)}</select></Field>
      <Field label="Validation recipe"><select value={recipe || ''} onChange={e => { setRecipeId(e.target.value); setValues({}); }}>{!recipe && <option value="">No available recipe</option>}{definition?.validation_recipes.map((id: string) => <option key={id} value={id} disabled={definition.recipe_schemas?.[id]?.available === false}>{definition.recipe_schemas?.[id]?.title || id}{definition.recipe_schemas?.[id]?.available === false ? ' · unavailable' : ''}</option>)}</select></Field>
      <RecipeParameters schema={schema} values={values} setValues={setValues} /><Field label="Validation time cap (seconds)"><input type="number" min="1" max="86400" value={wall} onChange={e => setWall(Number(e.target.value))} required /></Field>
      <Field label="Next action"><select value={!schema.assertion_kind ? 'validation.run' : mode} onChange={e => setMode(e.target.value)}><option value="validation.run">{schema.assertion_kind ? 'Record requirement and run check' : 'Run diagnostic measurements'}</option>{schema.assertion_kind && <option value="validation.require">Record requirement for later execution or waiver</option>}</select></Field>
      </div>{schema.description && <p className="help-text">{schema.description}</p>}
      {captured?.basis === 'compatible_current_definition' && <p className="help-text">This historical experiment uses a compatible current catalog for its form. Its recorded procedure still governs execution.</p>}
      {!definition && <p className="callout amber">The exact problem and evaluator definition is unavailable. Review its implementation evidence with the campaign manager.</p>}
      {definition && !definition.validation_recipes.length && <p className="help-text">This evaluator declares no analysis recipes. A new declaration can add supported recipes for future experiments.</p>}
      {definition?.validation_recipes.filter((id: string) => definition.recipe_schemas?.[id]?.available === false).map((id: string) => <p className="callout amber" key={id}>{definition.recipe_schemas[id].unavailable_reason}</p>)}
      <button className="button primary" disabled={busy || !recipe || schema.available === false}>Submit validation request</button></form></Panel>}
    {!requirements?.length ? <Empty title="No validation requirements recorded">Create a scoped check for an evaluator or observed candidate.</Empty> : [...requirements].sort((a, b) =>
      Number(b.requirement.study_id === state.campaign?.active_study_id) - Number(a.requirement.study_id === state.campaign?.active_study_id)).map(row => <Panel key={row.requirement.id}
        title={row.requirement.recipe_id === 'evaluator_numerical:v1' ? 'Evaluator numerical correctness'
          : row.requirement.scope?.recipe?.registered_recipe?.title || row.requirement.recipe_id} action={<Status status={row.status} />}>
      <p>{state.tasks.find(task => task.id === row.requirement.scope?.task_id)?.name || row.requirement.kind.replaceAll('_', ' ')}</p>
      <p className="help-text">{row.measured_pass ? 'Measured pass' : 'No passing measurement established'}</p>
      <p className="help-text">Study: {state.studies?.find((item: Json) => item.id === row.requirement.study_id)?.goal || row.requirement.study_id}
        {row.requirement.study_id === state.campaign?.active_study_id ? ' · Active study' : ' · Earlier study'}</p>
      {row.requirement.scope?.numerical_status === 'unverified' && !row.measured_pass && row.status !== 'failed' && <p className="callout amber">Evaluator contract checks passed. Numerical correctness is unverified. An allowed waiver authorizes exploratory use only.
        {!row.waiver_allowed && ' This study does not permit an evaluator waiver. Define a linked exploratory study with the appropriate policy, or obtain independent numerical evidence.'}</p>}
      <div className="inline-actions">{row.requirement.scope?.recipe && <button className="button secondary" disabled={busy} onClick={() => void execute(row.requirement)}>Run frozen check</button>}
        {row.requirement.recipe_id === 'evaluator_numerical:v1' && <button className="button secondary" disabled={busy || !state.implementation_library?.versions?.some((version: Json) => version.id === row.requirement.scope.version_id && version.status !== 'revoked')}
          onClick={() => setRevalidation(state.implementation_library?.versions.find((version: Json) => version.id === row.requirement.scope.version_id))}>Revalidate evaluator</button>}
        {row.waiver_allowed && <button className="button secondary" onClick={() => { setWaiver(row); setRationale(''); setFailure(''); setEvidence((row.requirement.scope?.contract_evidence_ids || []).join(', ')); }}>Record waiver</button>}
        <button className="text-button" onClick={() => void actions.research(`Assess validation requirement ${row.requirement.id} and propose the next authorized action.`, 'discuss')}>Discuss with manager</button></div>
      {!!row.results.length && <details><summary>Measured evidence ({row.results.length})</summary><pre>{JSON.stringify(row.results, null, 2)}</pre></details>}
      <details><summary>Requirement and subject records</summary><pre>{JSON.stringify(row.requirement, null, 2)}</pre></details>
      {row.waivers.map((item: Json) => <div key={item.id}><p className="help-text">Waiver by {item.authority}: {item.rationale}{row.revoked_waiver_ids.includes(item.id) ? ' · revoked' : ''}</p>
        {!row.revoked_waiver_ids.includes(item.id) && <button className="text-button danger" onClick={() => { setRevoking(item); setRationale(''); setFailure(''); }}>Revoke waiver</button>}</div>)}
    </Panel>)}
    {revalidation && <RevalidationForm version={revalidation} state={state} onClose={() => setRevalidation(null)} onDone={async () => { await actions.refresh(); setRevalidation(null); actions.notify('Independent revalidation queued in the implementation service.'); }} />}
    {waiver && <Modal title="Record a scoped waiver" description="Failed and missing measurements remain in the record." onClose={() => setWaiver(null)}><form onSubmit={waive}>
      {waiver.requirement.scope?.numerical_status === 'unverified' && <p className="callout amber">This authorizes exploratory use of this exact evaluator in the named study. It does not establish numerical correctness or permit confirmation.</p>}
      <Field label="Waiver rationale"><textarea required value={rationale} onChange={e => setRationale(e.target.value)} /></Field>
      <Field label="Supporting evidence record IDs" hint="Comma-separated IDs of recorded findings, experiments, or validation evidence."><input required value={evidence} onChange={e => setEvidence(e.target.value)} /></Field>
      <ErrorNotice text={failure} /><button className="button primary" disabled={busy}>Save waiver</button></form></Modal>}
    {revoking && <Modal title="Revoke a scoped waiver" description="Historical evidence retains its original authorization. New attempts will be checked again." onClose={() => setRevoking(null)}><form onSubmit={revoke}>
      <Field label="Revocation rationale"><textarea required value={rationale} onChange={e => setRationale(e.target.value)} /></Field>
      <ErrorNotice text={failure} /><button className="button primary" disabled={busy}>Revoke waiver</button></form></Modal>}
  </>;
}

function ConfirmationRoster({ state, actions, study }: Props & { study: Json }) {
  const { data: assessment, error } = useRead<Json>(`/api/v1/confirmations/${study.confirmation.id}`, eventRevision(state));
  const command = useCommand(state.campaign), [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  const [closing, setClosing] = useState(false), [rationale, setRationale] = useState('');
  async function execute(operation: string, extra: Json = {}) {
    setBusy(true); setFailure('');
    try { await command(operation, { protocol_id: study.confirmation.id, ...extra }); await actions.refresh(); setClosing(false);
      actions.notify(operation === 'confirmation.schedule' ? 'Missing confirmation cells queued.' : operation === 'confirmation.validate' ? 'Required checks queued for completed cells.' : 'Confirmation evidence released.'); }
    catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  if (!assessment) return <ErrorNotice text={error} />;
  const pendingStatuses = ['queued', 'running', 'pausing', 'paused', 'stopping', 'interrupted'];
  const live = assessment.cells.some((cell: Json) => pendingStatuses.includes(cell.status) || cell.diagnostics?.jobs?.some((job: Json) => pendingStatuses.includes(job.status)));
  return <div className="confirmation-roster"><ErrorNotice text={error} /><ErrorNotice text={failure} />
    <p>{assessment.cells.filter((cell: Json) => cell.evidence_complete).length} / {assessment.cells.length} cells have complete evidence · {assessment.kind.replaceAll('_', ' ')}</p>
    <div className="table-scroll"><table className="data-table"><thead><tr><th>Method / seed</th><th>Instance</th><th>Procedure</th><th>Best observed</th><th>Required checks</th></tr></thead>
      <tbody>{assessment.cells.map((cell: Json) => <tr key={`${cell.method_id}-${cell.instance_digest}-${cell.seed}`}>
        <td>{state.algorithms.find(method => method.id === assessment.methods[cell.method_id].algorithm)?.name || assessment.methods[cell.method_id].algorithm} · {cell.seed}<small>{cell.method_id.slice(0, 10)}</small></td><td>{cell.instance_name}</td>
        <td title={cell.trial_id}>{cell.status.replaceAll('_', ' ')}{cell.scientific_complete && <small>Scientific procedure complete</small>}{cell.diagnostics?.grants?.length > 0 && <small>Diagnostics {cell.diagnostics.complete ? 'complete' : 'pending or incomplete'}</small>}</td>
        <td>{objectiveValue(cell.best_objective, cell.objective)}</td><td>{cell.required_validation.length ? cell.required_validation.map((check: Json) => <div key={check.recipe_id}>{check.recipe_id}: {check.passed ? 'Measured pass' : 'Evidence required'}</div>) : 'None declared'}</td>
      </tr>)}</tbody></table></div>
    <p className="help-text">{assessment.interpretation}</p>
    {assessment.report && <details><summary>Evidence report · {assessment.report.claim_level.replaceAll('_', ' ')}{assessment.report.supersedes_report_id ? ' · reassessed' : ''}</summary>
      <p><strong>{assessment.report.outcome.replaceAll('_', ' ')}</strong></p><p>{assessment.report.interpretation}</p><pre>{JSON.stringify(assessment.report, null, 2)}</pre></details>}
    {assessment.release ? <p>Released as {assessment.release.outcome.replaceAll('_', ' ')}. {assessment.release.rationale}</p> : <div className="inline-actions">
      {!assessment.execution_id && <button className="button secondary" disabled={busy || assessment.cells.every((cell: Json) => cell.trial_id)} onClick={() => void execute('confirmation.schedule')}>Schedule missing cells</button>}
      {!!assessment.required_recipes.length && <button className="button secondary" disabled={busy || !assessment.cells.some((cell: Json) => cell.scientific_complete && !cell.evidence_complete)} onClick={() => void execute('confirmation.validate')}>Run required checks</button>}
      <button className="button primary" disabled={busy || !assessment.complete} onClick={() => void execute('confirmation.release')}>Release completed evidence</button>
      {!assessment.complete && <button className="button secondary" disabled={busy || live} onClick={() => setClosing(true)}>Close as inconclusive</button>}
    </div>}
    {!assessment.release && <p className="help-text">Stop or finish active cells and validation jobs before closing an incomplete roster. Declared input assets need a reuse decision for this study.</p>}
    {closing && <Modal title="Close incomplete confirmation" description="The frozen roster will close with an inconclusive outcome. Further work needs a linked study." onClose={() => setClosing(false)}>
      <form onSubmit={e => { e.preventDefault(); void execute('confirmation.release', { allow_incomplete: true, rationale }); }}>
        <Field label="Reason for closing"><textarea required value={rationale} onChange={e => setRationale(e.target.value)} /></Field>
        <ErrorNotice text={failure} /><button className="button primary" disabled={busy}>Close with an inconclusive result</button>
      </form></Modal>}
  </div>;
}

function RulePicker({ kind, problemIds, value, onChange }: { kind: string; problemIds: string[]; value: Json | null; onChange: (value: Json | null) => void }) {
  const { data, error } = useRead<Json>('/api/v1/study-rules', 0);
  const [draft, setDraft] = useState<Json>(value?.parameters || {});
  useEffect(() => { setDraft(value?.parameters || {}); }, [value?.provider, value?.rule_id]);
  const available = (data?.rules || []).filter((rule: Json) => rule.kind === kind && (rule.provider === 'framework' || problemIds.length && problemIds.every(id => id === rule.provider)));
  const selected = available.find((rule: Json) => rule.rule_id === value?.rule_id && rule.provider === value?.provider);
  return <><Field wide label={kind === 'selection' ? 'Development selection rule' : 'Confirmation analysis rule'}>
    <select value={selected ? `${selected.provider}|${selected.rule_id}` : ''} onChange={e => {
      const rule = available.find((item: Json) => `${item.provider}|${item.rule_id}` === e.target.value);
      onChange(rule ? { provider: rule.provider, rule_id: rule.rule_id, parameters: rule.defaults } : null);
    }}><option value="">{kind === 'selection' ? 'Exploration without automatic selection' : 'Descriptive report only'}</option>
      {available.map((rule: Json) => <option key={`${rule.provider}|${rule.rule_id}`} value={`${rule.provider}|${rule.rule_id}`}>{rule.title}</option>)}
    </select></Field>
    {selected && <RecipeParameters schema={selected.parameters_schema} values={draft} setValues={parameters => { setDraft(parameters); onChange({ ...value, parameters: resolvedParameters(selected.parameters_schema, parameters) }); }} />}
    <ErrorNotice text={error} /></>;
}

function DevelopmentSelection({ state, actions, study }: Props & { study: Json }) {
  const command = useCommand(state.campaign), [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  const nomination = (state.nominations || []).find((item: Json) => item.study_id === study.id);
  const { data: assessment, error: assessmentError } = useRead<Json>(nomination ? null : `/api/v1/studies/${study.id}/selection`, eventRevision(state));
  const [inspect, setInspect] = useState(false);
  const { data: evidence, error } = useRead<Json>(inspect && nomination ? `/api/v1/nominations/${nomination.id}` : null, nomination?.id);
  async function select() {
    setBusy(true); setFailure('');
    try { await command('study.nominate', { study_id: study.id, expected_evidence_hash: assessment?.evidence_hash }); await actions.refresh(); actions.notify('Method nomination and its development evidence are frozen.'); }
    catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  return <div><p>Selection: {study.selection.rule_id} · {Object.entries(study.selection.parameters).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : value}`).join(' · ')}</p>
    <ErrorNotice text={failure} /><ErrorNotice text={assessmentError} />{nomination ? <>
      <p>Frozen nomination: {nomination.selected_method_ids.map((id: string) => {
        const prototype = state.trials.find(trial => trial.id === nomination.prototypes[id]);
        return `${prototype?.algorithm || 'Method'} · ${id.slice(0, 10)}`;
      }).join(', ')}</p><p className="help-text">Further development cannot replace this selection. Use this nomination when defining confirmation.</p>
      <details onToggle={e => setInspect(e.currentTarget.open)}><summary>Nomination and fixed development evidence</summary><ErrorNotice text={error} /><pre>{JSON.stringify(evidence || nomination, null, 2)}</pre></details>
    </> : <><p className="help-text">{assessment?.ready ? 'The declared rule has eligible evidence. Freezing retains this selection and its exact inputs.' : assessment?.result.reason || 'Waiting for the declared seeds, required checks and evidence publication.'}</p>
      {!!assessment?.result.candidates?.length && <p>{assessment.result.candidates.length} eligible method{assessment.result.candidates.length === 1 ? '' : 's'}.</p>}
      <button className="button secondary" disabled={busy || !assessment?.ready} onClick={() => void select()}>Freeze method nomination</button></>}
  </div>;
}

function StudyExecutionCard({ state, actions, execution }: Props & { execution: Json }) {
  const { data, error } = useRead<Json>(`/api/v1/study-executions/${execution.id}`, eventRevision(state));
  const command = useCommand(state.campaign), [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  async function activate() {
    setBusy(true); setFailure('');
    try { await command('study.activate', { execution_id: execution.id }); await actions.refresh(); actions.notify('Study grant activated. Its deadlines are fixed.'); }
    catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  const study = data && (state.studies || []).find((row: Json) => row.id === data.study_ids.confirmation);
  return <Panel title={data?.name || 'Frozen study execution'} action={<Badge>{execution.status.replaceAll('_', ' ')}</Badge>}>
    <ErrorNotice text={error} /><ErrorNotice text={failure} />
    {data && <>
      {data.qualification_only && <p className="help-text">Software workflow qualification. This reduced study makes no production replication claim.</p>}
      <p>{seconds(data.template.worker_seconds)} worker envelope · up to {data.template.max_workers} workers.</p>
      {data.activation ? <p>Development cutoff: {new Date(data.activation.development_deadline * 1000).toLocaleString()}<br />Overall deadline: {new Date(data.activation.deadline_at * 1000).toLocaleString()}</p>
        : <><p>Activation fixes a {seconds(data.template.development_seconds)} development window and a {seconds(data.template.total_seconds)} total window.</p>
          <button className="button primary" disabled={busy} onClick={() => void activate()}>Activate study execution</button></>}
      <p>{data.nomination_id ? 'Development selection is frozen.' : 'Selected-method cells await the declared development evidence or cutoff.'}</p>
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Stage / method</th><th>Seed</th><th>State</th><th>Dependency or evidence</th></tr></thead>
        <tbody>{data.cells.map((cell: Json) => <tr key={cell.id}><td>{cell.scope} · {cell.slot_id}</td><td>{cell.seed}</td>
          <td>{cell.status.replaceAll('_', ' ')}</td><td>{cell.canonical_cell_id !== cell.id ? 'Shares the same declared method run' : cell.evidence_complete ? 'Complete evidence' : cell.waiting_for.map((item: string) => item.startsWith('cell_') ? 'Matching seed prefix' : item.replaceAll('_', ' ')).join(', ') || 'Awaiting measured completion'}</td></tr>)}</tbody></table></div>
      {!!Object.keys(data.input_bindings || {}).length && <p>Declared input assets: {Object.keys(data.input_bindings).map(slot => data.template.input_requirements[slot]?.title || slot).join(', ')}. Their recorded versions are fixed for this study.</p>}
      <details><summary>Frozen template and bindings</summary><pre>{JSON.stringify({ template: data.template, bindings: data.bindings, input_bindings: data.input_bindings }, null, 2)}</pre></details>
      {study && <ConfirmationRoster state={state} actions={actions} study={study} />}
    </>}
  </Panel>;
}

function TemplateStudies({ state, actions }: Props) {
  const [open, setOpen] = useState(false), [taskId, setTaskId] = useState(''), [templateId, setTemplateId] = useState('');
  const [assetBindings, setAssetBindings] = useState<Record<string, string>>({}), [catalogRevision, setCatalogRevision] = useState(0);
  const { data: catalog, error } = useRead<Json>(`/api/v1/study-templates${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''}`, `${open}:${catalogRevision}`);
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  const command = useCommand(state.campaign);
  const task = state.tasks.find(row => row.id === taskId);
  const choices = (catalog?.templates || []).filter((row: Json) => row.problem_id === task?.problem?.definition_id);
  const entry = choices.find((row: Json) => row.template.id === templateId), selected = entry?.template;
  const requirements = Object.entries(selected?.input_requirements || {}) as [string, Json][];
  const missingInputs = requirements.some(([slot]) => !(entry?.input_candidates?.[slot] || []).some((asset: Json) => asset.id === assetBindings[slot]));
  useEffect(() => { setOpen(false); setTaskId(''); setTemplateId(''); setAssetBindings({}); }, [state.campaign?.id]);
  async function freeze(event: React.FormEvent) {
    event.preventDefault(); if (!selected) return; setBusy(true); setFailure('');
    try { await command('study.freeze_template', { template: selected, task_ids: [taskId], asset_bindings: assetBindings }); await actions.refresh(); setOpen(false); actions.notify('Study cells and procedures are frozen. Activate its grant when ready.'); }
    catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  async function importReferences(referenceSet: Json) {
    setBusy(true); setFailure('');
    try {
      const imported = await command('asset.import_reference_set', { provider: referenceSet.provider,
        reference_set_id: referenceSet.manifest_preview.id, manifest_digest: referenceSet.manifest_digest });
      setAssetBindings(Object.fromEntries(requirements.map(([slot]) => [slot, imported.asset_bindings[slot] || assetBindings[slot] || ''])));
      setCatalogRevision(value => value + 1); await actions.refresh();
      actions.notify('Preserved references are in the library. Review the selected inputs before freezing.');
    } catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  return <><ErrorNotice text={error} />
    {!!catalog?.templates?.length && <p><button className="button secondary" onClick={() => { setOpen(true); setTaskId(state.tasks[0]?.id || ''); setTemplateId(''); setAssetBindings({}); setFailure(''); }}>Use a study template</button></p>}
    {(state.study_executions || []).map((execution: Json) => <StudyExecutionCard key={execution.id} state={state} actions={actions} execution={execution} />)}
    {open && <Modal wide title="Freeze a study template" description="Review the procedures, fixed cells and resource envelope before activation." onClose={() => setOpen(false)}>
      <form onSubmit={freeze}><div className="form-grid"><Field label="Template problem instance"><select required disabled={busy} value={taskId} onChange={e => { setTaskId(e.target.value); setTemplateId(''); setAssetBindings({}); }}>{state.tasks.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
        <Field label="Study template"><select required disabled={busy} value={templateId} onChange={e => { setTemplateId(e.target.value); setAssetBindings({}); }}><option value="">Choose a template</option>{choices.map((row: Json) => <option key={row.template.id} value={row.template.id}>{row.template.name}</option>)}</select></Field></div>
        {selected && <><p>{selected.goal}</p><p>{seconds(selected.worker_seconds)} worker envelope · {selected.max_workers} workers · development {seconds(selected.development_seconds)} · total {seconds(selected.total_seconds)}</p>
          {selected.qualification_only && <p className="help-text">Reduced software qualification; numerical superiority is not an acceptance requirement.</p>}
          {!!requirements.length && <><p>Select a versioned asset for each declared input. Historical production costs remain part of its provenance, including unknown costs.</p>
            <div className="form-grid">{requirements.map(([slot, requirement]) => {
              const candidates = entry.input_candidates?.[slot] || [];
              return <Field key={slot} label={`Input: ${requirement.title}`}><select required disabled={busy} value={assetBindings[slot] || ''}
                onChange={event => setAssetBindings(values => ({ ...values, [slot]: event.target.value }))}>
                <option value="">Choose an available asset</option>{candidates.map((asset: Json) => <option key={asset.id} value={asset.id}>{asset.title} · {asset.id.slice(-8)} · {asset.cost_provenance} cost provenance</option>)}</select>
                {!candidates.length && <span className="help-text">No available matching asset. Import the preserved references below, or publish a compatible solution in the research asset library and refresh.</span>}</Field>;
            })}</div>
            {(entry.reference_sets || []).map((referenceSet: Json) => <div key={referenceSet.manifest_preview.id}>
              <details><summary>{referenceSet.manifest_preview.title}</summary><p>{referenceSet.manifest_preview.description}</p>
                <ul>{referenceSet.manifest_preview.solutions.map((solution: Json) => <li key={solution.slot}>{solution.title}</li>)}</ul>
                <details><summary>Captured source identities</summary><pre>{JSON.stringify(referenceSet.manifest_preview, null, 2)}</pre></details></details>
              <button type="button" className="button secondary" disabled={busy} onClick={() => void importReferences(referenceSet)}>Import preserved references</button>
            </div>)}
            <button type="button" className="button secondary" disabled={busy} onClick={() => setCatalogRevision(value => value + 1)}>Refresh available inputs</button>
            {missingInputs && <p className="help-text">Choose an available asset for every required input before freezing this study.</p>}</>}
          <div className="table-scroll"><table className="data-table"><thead><tr><th>Stage</th><th>Methods</th><th>Seeds</th><th>Starts after</th></tr></thead><tbody>{selected.groups.map((group: Json) => <tr key={group.id}><td>{group.scope}</td><td>{group.slots.join(', ')}</td><td>{group.seeds.join(', ')}</td><td>{group.admission}</td></tr>)}</tbody></table></div>
          <details><summary>Full frozen procedures and validation</summary><pre>{JSON.stringify(selected, null, 2)}</pre></details></>}
        <ErrorNotice text={failure} /><button className="button primary" disabled={busy || !selected || missingInputs}>Freeze study template</button>
      </form></Modal>}
  </>;
}

export function StudyView({ state, actions }: Props) {
  const command = useCommand(state.campaign), [open, setOpen] = useState(false), [goal, setGoal] = useState(''), [scope, setScope] = useState('exploratory');
  const [kind, setKind] = useState('seed_replication'), [seeds, setSeeds] = useState('100, 101, 102'), [methods, setMethods] = useState<string[]>([]);
  const [selection, setSelection] = useState('Use every method/instance/seed cell in the frozen roster'), [policy, setPolicy] = useState(''), [waivers, setWaivers] = useState(false), [axis, setAxis] = useState('worker_seconds');
  const [evaluatorWaivers, setEvaluatorWaivers] = useState(false);
  const [failure, setFailure] = useState(''), [busy, setBusy] = useState(false);
  const [taskIds, setTaskIds] = useState<string[]>([]), [requiredChecks, setRequiredChecks] = useState<string[]>([]), [checkParameters, setCheckParameters] = useState<Json>({}), [checkWall, setCheckWall] = useState(120);
  const [selectionRule, setSelectionRule] = useState<Json | null>(null), [analysisRule, setAnalysisRule] = useState<Json | null>(null);
  const [nominationId, setNominationId] = useState(''), [references, setReferences] = useState<string[]>([]);
  const { data: assets } = useRead<Json[]>('/api/v1/assets', eventRevision(state));
  const { data: catalog } = useRead<Json>(problemCatalogPath(state.campaign?.id), eventRevision(state));
  const definitions = state.tasks.filter(task => taskIds.includes(task.id)).map(task => problemDefinition(catalog?.problems, task.problem));
  const assertionSchemas: Json = definitions[0]?.recipe_schemas || {};
  const checks = Object.keys(assertionSchemas).filter(id => definitions.length && definitions.every(definition => definition?.recipe_schemas?.[id]?.assertion_kind && definition.recipe_schemas[id].available !== false));
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setFailure('');
    try { await command('study.create', { goal, scope, task_ids: taskIds,
      validation_policy: { manager_may_waive: waivers, required_recipes: requiredChecks,
        waivable_kinds: ['solution_fidelity', 'learner_diagnostics', ...(scope === 'exploratory' && evaluatorWaivers ? ['evaluator_correctness'] : [])],
        required_recipe_parameters: Object.fromEntries(requiredChecks.map(id => [id, resolvedParameters(assertionSchemas[id], checkParameters[id] || {})])), validation_wall_seconds: checkWall },
      comparison: { cost_axis: axis, cost_view: 'full_attributed_cost' },
      ...(scope === 'exploratory' && selectionRule ? { selection: selectionRule } : {}),
      ...(scope === 'confirmation' ? { confirmation_kind: kind, prototype_trial_ids: methods, seeds: seeds.split(',').map(Number), selection_rule: selection,
        ...(analysisRule ? { analysis: analysisRule } : {}), ...(nominationId ? { nomination_id: nominationId } : {}), reference_trial_ids: references,
        ...(kind === 'policy_transfer' ? { policy_asset_id: policy, adaptation: 'forbidden' } : {}) } : {}) });
      await actions.refresh(); setOpen(false); actions.notify('A linked study with a frozen scientific scope is now active.');
    } catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  return <><div className="page-heading"><div><span className="eyebrow">Scientific scope</span><h1>Study history.</h1><p>Scientific changes create a linked study. Experiment procedures and prior evidence retain their original scope.</p></div>
    <button className="button primary" onClick={() => { setOpen(true); setGoal(state.campaign?.objective || ''); setTaskIds(state.tasks.map(task => task.id)); setRequiredChecks([]); setCheckParameters({}); }}>Define a new study</button></div>
    <TemplateStudies state={state} actions={actions} />
    {(state.studies || []).map((study: Json) => <Panel key={study.id} title={study.goal} action={<Badge>{study.id === state.campaign?.active_study_id ? 'Active' : 'Previous'} · {study.scope}</Badge>}>
      <p>{study.comparison?.cost_axis?.replaceAll('_', ' ')} · full upstream cost</p><p className="help-text">{study.id}{study.parent_study_id ? ` · follows ${study.parent_study_id}` : ''}</p>
      {!study.execution_id && study.selection?.rule_id && <DevelopmentSelection state={state} actions={actions} study={study} />}
      {!study.execution_id && study.confirmation?.id && <ConfirmationRoster state={state} actions={actions} study={study} />}
      <details><summary>Frozen scope and evidence policy</summary><pre>{JSON.stringify(study, null, 2)}</pre></details></Panel>)}
    {open && <Modal wide title="Define a linked study" description="The current campaign's problem instances will be frozen into this study." onClose={() => setOpen(false)}><form onSubmit={submit}><div className="form-grid">
      <Field wide label="Study question"><textarea required value={goal} onChange={e => setGoal(e.target.value)} /></Field>
      <Field wide label="Problem instances"><select multiple required value={taskIds} onChange={e => { setTaskIds(Array.from(e.target.selectedOptions, option => option.value)); setRequiredChecks([]); }}>{state.tasks.map(task => <option key={task.id} value={task.id}>{task.name} · {task.split}</option>)}</select></Field>
      <Field label="Study scope"><select value={scope} onChange={e => setScope(e.target.value)}><option value="exploratory">Exploratory optimizer development</option><option value="confirmation">Fixed confirmation procedure</option></select></Field>
      <Field label="Primary cost axis"><select value={axis} onChange={e => setAxis(e.target.value)}><option value="worker_seconds">Worker time</option><option value="evaluation_requests">Evaluation requests</option><option value="solver_executions">Solver executions</option></select></Field>
      <RulePicker kind={scope === 'exploratory' ? 'selection' : 'verdict'} problemIds={definitions.map(definition => definition?.id).filter(Boolean)}
        value={scope === 'exploratory' ? selectionRule : analysisRule} onChange={scope === 'exploratory' ? setSelectionRule : setAnalysisRule} />
      <label className="checkbox-label"><input type="checkbox" checked={waivers} onChange={e => setWaivers(e.target.checked)} />Delegate eligible validation waivers to the manager</label>
      {scope === 'exploratory' && <label className="checkbox-label"><input type="checkbox" checked={evaluatorWaivers} onChange={e => setEvaluatorWaivers(e.target.checked)} />Allow scoped evaluator waivers for exploratory use</label>}
      {checks.map(id => <label key={id} className="checkbox-label"><input type="checkbox" checked={requiredChecks.includes(id)} onChange={e => setRequiredChecks(e.target.checked ? [...requiredChecks, id] : requiredChecks.filter(item => item !== id))} />Require {assertionSchemas[id].title || id}</label>)}
      {requiredChecks.map(id => <div key={id}><p>{assertionSchemas[id].title}</p><RecipeParameters schema={assertionSchemas[id]} values={checkParameters[id] || {}} setValues={value => setCheckParameters({ ...checkParameters, [id]: value })} /></div>)}
      {!!requiredChecks.length && <Field label="Time cap per required check (seconds)"><input type="number" min="1" max="86400" required value={checkWall} onChange={e => setCheckWall(Number(e.target.value))} /></Field>}
      {scope === 'confirmation' && <><Field label="Confirmation protocol"><select value={kind} onChange={e => setKind(e.target.value)}><option value="seed_replication">Fresh seeds on a known instance</option><option value="unseen_instance">Unseen problem instances</option><option value="policy_transfer">Frozen policy transfer</option></select></Field>
        <Field label="Fresh seeds"><input required value={seeds} onChange={e => setSeeds(e.target.value)} /></Field>
        <Field wide label="Frozen development nomination" hint="A nomination includes its selected method automatically; choose any additional controls below."><select value={nominationId} onChange={e => setNominationId(e.target.value)}><option value="">Choose procedures directly</option>{(state.nominations || []).map((item: Json) => <option key={item.id} value={item.id}>{state.studies.find((study: Json) => study.id === item.study_id)?.goal || item.study_id} · {item.rule.rule_id}</option>)}</select></Field>
        <Field wide label="Prototype experiments" hint="Select procedures and budgets for confirmation, including controls for the nominated method."><select multiple required={!nominationId} value={methods} onChange={e => setMethods(Array.from(e.target.selectedOptions, item => item.value))}>{state.trials.filter(trial => !['recipe', 'validate'].includes(trial.algorithm) && !trial.diagnostic_grant_id).map(trial => <option key={trial.id} value={trial.id}>{trial.algorithm} · seed {trial.seed} · {trial.id.slice(-8)}</option>)}</select></Field>
        {analysisRule && <Field wide label="Frozen comparison references" hint="Optional prior completed experiments; the selected analysis rule determines whether references are required."><select multiple value={references} onChange={e => setReferences(Array.from(e.target.selectedOptions, item => item.value))}>{state.trials.filter(trial => trial.status === 'completed' && !trial.recipe && !trial.diagnostic_grant_id).map(trial => <option key={trial.id} value={trial.id}>{trial.algorithm} · seed {trial.seed} · {trial.id.slice(-8)}</option>)}</select></Field>}
        <Field wide label="Selection and comparison rule"><textarea required value={selection} onChange={e => setSelection(e.target.value)} /></Field>
        {kind === 'policy_transfer' && <Field label="Frozen policy asset"><select required value={policy} onChange={e => setPolicy(e.target.value)}><option value="">Select a declared policy</option>{assets?.filter(asset => asset.kind === 'policy').map(asset => <option key={asset.id} value={asset.id}>{asset.title}</option>)}</select></Field>}</>}
      </div><ErrorNotice text={failure} /><button className="button primary" disabled={busy}>Freeze and activate study</button></form></Modal>}
  </>;
}
