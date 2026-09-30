import { useEffect, useMemo, useRef, useState } from 'react';
import { api, errorText, objectiveValue, seconds } from './api';
import type { Json, State } from './api';
import { useCommand } from './commands';
import { Empty, ErrorNotice, Field, Modal, Panel as BasePanel } from './ui';
import type { ComponentProps } from 'react';
import type { WorkspaceActions } from './views';
import './comparison.css';

type Props = { state: State; actions: WorkspaceActions };
function Panel({ children, ...props }: ComponentProps<typeof BasePanel>) {
  return <BasePanel {...props}><div className="evidence-panel-body">{children}</div></BasePanel>;
}
function useRead<T>(path: string | null, revision: unknown) {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState('');
  const [loading, setLoading] = useState(Boolean(path));
  const mounted = useRef(true), fetching = useRef(false), current = useRef<string | null>(null), pending = useRef<string | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; pending.current = null; }; }, []);
  useEffect(() => {
    if (current.current !== path) { current.current = path; setData(null); setError(''); }
    pending.current = path;
    setLoading(Boolean(path));
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
      } finally { fetching.current = false; if (mounted.current) setLoading(false); }
    }
    void refresh();
  }, [path, revision]);
  return { data, error, loading };
}
function cost(value: Json | undefined, axis: string) {
  if (!value || value.total == null) return `Unknown${value?.known ? ` (${axis === 'worker_seconds' ? seconds(value.known) : value.known} measured)` : ''}`;
  return axis === 'worker_seconds' ? seconds(value.total) : value.total.toLocaleString();
}
function canonical(value: unknown): string {
  if (value === undefined) return 'Not recorded';
  if (Array.isArray(value)) return `[${value.map(canonical).join(', ')}]`;
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${key}: ${canonical(item)}`).join(', ')}}`;
  return JSON.stringify(value);
}
function human(value: string) { return value.replaceAll('_', ' '); }
function short(value: string | undefined) { return value?.slice(0, 10) || 'Unrecorded'; }
export function methodParameterSummary(trial: Json): string {
  const parameters = trial.method?.parameters || trial.algorithm_config || trial.procedure?.algorithm_config || {};
  const entries = Object.entries(parameters);
  return entries.length ? entries.map(([key, value]) => `${human(key)}=${canonical(value)}`).join(' · ') : 'Default parameters';
}
function procedureSummary(trial: Json): string {
  const procedure = trial.resolved_procedure || trial.procedure || trial;
  return [procedure.max_steps != null ? `${procedure.max_steps.toLocaleString()} request cap` : '',
    procedure.wall_seconds != null ? `${seconds(procedure.wall_seconds)} time cap` : '',
    procedure.schedule_steps != null ? `schedule ${procedure.schedule_steps.toLocaleString()}` : ''].filter(Boolean).join(' · ');
}
function flattened(value: Json, prefix = '', result: Record<string, unknown> = {}): Record<string, unknown> {
  for (const [key, item] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === 'object' && !Array.isArray(item) && Object.keys(item).length) flattened(item, path, result);
    else result[path] = item;
  }
  return result;
}
function comparisonFields(trial: Json) {
  const method = trial.method || { algorithm: trial.algorithm, parameters: trial.algorithm_config || {}, training: trial.training || {} };
  const procedure = trial.procedure || {};
  const resolved = trial.resolved_procedure || procedure;
  // Retain the complete frozen procedure, even where fields repeat the method:
  // package version IDs can conceal source/runtime differences and comparison
  // identity intentionally omits some non-DQN training fields.
  return flattened({ method, procedure: resolved, ...(trial.resolved_procedure && canonical(procedure) !== canonical(resolved) ? { logical_procedure: procedure } : {}) });
}

export function MethodDetailsDialog({ trials, onClose }: { trials: Json[]; onClose: () => void }) {
  const [differencesOnly, setDifferencesOnly] = useState(trials.length > 1);
  const fields = useMemo(() => trials.map(comparisonFields), [trials]);
  const keys = useMemo(() => [...new Set(fields.flatMap(Object.keys))], [fields]);
  const changed = keys.filter(key => new Set(fields.map(value => canonical(value[key]))).size > 1);
  const visible = differencesOnly ? changed : keys;
  const compatible = new Set(trials.map(trial => trial.comparison_group_id).filter(Boolean)).size <= 1;
  return <Modal wide title={trials.length > 1 ? 'Compare method details' : 'Method and run details'}
    description="Inspect algorithm parameters, procedure budgets, implementation, runtime, and input assets. Seed varies between replicates and is excluded from the method identity." onClose={onClose}>
    {!compatible && <p className="comparison-caution">These runs belong to different problem, fidelity, or confirmation groups. Their recorded configurations can be inspected together; their objective values are not a matched comparison.</p>}
    {trials.length > 1 && <label className="checkbox-label"><input type="checkbox" checked={differencesOnly} onChange={e => setDifferencesOnly(e.target.checked)} />Show differing method and procedure fields only ({changed.length})</label>}
    <div className="table-scroll"><table className="data-table comparison-diff" style={{ minWidth: Math.max(620, 220 * (trials.length + 1)) }}><thead><tr><th>Recorded field</th>{trials.map(trial => <th key={trial.id}>
      {trial.hypothesis_title || trial.algorithm}<small>Method {short(trial.method_id)} · seed {trial.seed}</small><small>Run {trial.id}</small></th>)}</tr></thead>
      <tbody><tr><th>Observed endpoint</th>{trials.map(trial => <td key={trial.id}>{objectiveValue(trial.best_objective, trial.objective_definition)}<small>{trial.scientific_complete ? 'Scientifically complete' : 'Incomplete / censored'}</small></td>)}</tr>
        {visible.map(key => <tr key={key} className={changed.includes(key) ? 'comparison-difference' : ''} data-diff-field={key}>
          <th>{human(key)}<small>{key}</small></th>{fields.map((field, index) => <td key={trials[index].id}>{canonical(field[key])}</td>)}</tr>)}
      </tbody></table></div>
    {differencesOnly && !changed.length && <p className="help-text">No differences in the recorded method or procedure. These runs may differ in seed and observed outcome.</p>}
    <p className="help-text">Method IDs summarize parameters, schedule, implementation, runtime, and input assets. Compare procedure caps separately before choosing a prototype.</p>
    {trials.map(trial => <details key={trial.id} className="comparison-raw"><summary>Full recorded identity · {short(trial.method_id)} · seed {trial.seed}</summary>
      {trial.question && <p>{trial.question}</p>}<pre>{JSON.stringify({ trial_id: trial.id, seed: trial.seed, method_id: trial.method_id,
        procedure_id: trial.procedure_id, method: trial.method, procedure: trial.procedure, resolved_procedure: trial.resolved_procedure }, null, 2)}</pre></details>)}
  </Modal>;
}

type SortKey = 'method' | 'seed' | 'objective' | 'cost' | 'status';
type Sort = { key: SortKey; ascending: boolean };
function sortRows(rows: Json[], sort: Sort, axis: string) {
  const value = (row: Json): string | number | null => {
    if (sort.key === 'objective') return row.best_objective;
    if (sort.key === 'cost') return row.full_cost?.quantities?.[axis]?.total;
    if (sort.key === 'seed') return row.seed;
    if (sort.key === 'status') return row.scientific_complete ? 'Complete' : 'Incomplete / censored';
    return `${row.algorithm} ${methodParameterSummary(row)} ${row.method_id}`;
  };
  return [...rows].sort((a, b) => {
    const av = value(a), bv = value(b);
    const missing = (v: unknown) => v == null || (typeof v === 'number' && !Number.isFinite(v));
    if (missing(av) || missing(bv)) return Number(missing(av)) - Number(missing(bv)) || a.id.localeCompare(b.id);
    const delta = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true });
    return (sort.ascending ? delta : -delta) || a.id.localeCompare(b.id);
  });
}

function ComparisonGroup({ group, axis, selected, onSelect, finalists, saveFinalists, busy, inspect }: {
  group: Json; axis: string; selected: Set<string>; onSelect: (ids: string[], selected: boolean) => void;
  finalists: Set<string>; saveFinalists: (ids: string[]) => void; busy: boolean; inspect: (trials: Json[]) => void;
}) {
  const [view, setView] = useState('runs');
  const [sort, setSort] = useState<Sort>({ key: 'objective', ascending: group.problem.primary_objective.direction === 'minimize' });
  const rows = useMemo(() => sortRows(group.trials, sort, axis), [group.trials, sort, axis]);
  const methods = useMemo(() => {
    const groups = new Map<string, Json[]>();
    for (const trial of group.trials) groups.set(trial.method_id, [...(groups.get(trial.method_id) || []), trial]);
    const summaries = new Map<string, Json>((group.method_summaries || []).map((summary: Json) => [summary.method_id, summary]));
    return sortRows([...groups.entries()].map(([id, members]) => {
      const summary = summaries.get(id);
      return { ...members[0], id, members, summary, seed: members.length, best_objective: summary?.mean,
        // A method group's full cost is not a run endpoint; expose the total
        // only if every member has measured provenance.
        full_cost: { quantities: { [axis]: { total: members.every(item => item.full_cost?.quantities?.[axis]?.total != null)
          ? members.reduce((sum, item) => sum + item.full_cost.quantities[axis].total, 0) : null } } },
        scientific_complete: members.every(item => item.scientific_complete) };
    }), sort, axis);
  }, [group.trials, group.method_summaries, sort, axis]);
  function header(key: SortKey, label: string) {
    return <th aria-sort={sort.key === key ? sort.ascending ? 'ascending' : 'descending' : 'none'}><button className="comparison-sort" onClick={() => setSort(previous => ({ key,
      ascending: previous.key === key ? !previous.ascending : key === 'objective' ? group.problem.primary_objective.direction === 'minimize' : true }))}>
      {label}{sort.key === key ? sort.ascending ? ' ↑' : ' ↓' : ' ↕'}</button></th>;
  }
  function mark(ids: string[], enabled: boolean) { saveFinalists(enabled ? [...new Set([...finalists, ...ids])] : [...finalists].filter(id => !ids.includes(id))); }
  const decorate = (trial: Json) => ({ ...trial, objective_definition: group.problem.primary_objective, comparison_group_id: group.id });
  return <Panel title={`${group.problem.definition_id} · ${group.problem.primary_objective.direction} ${group.problem.primary_objective.name}`}>
    <p className="help-text">Objective units: {group.problem.primary_objective.units} · matched cost: {group.common_observed_cost == null ? 'No shared observed range' : axis === 'worker_seconds' ? seconds(group.common_observed_cost) : group.common_observed_cost}</p>
    <div className="comparison-table-options"><Field label="Results table"><select value={view} onChange={e => setView(e.target.value)}><option value="runs">Individual runs</option><option value="methods">Method groups (seed replicates)</option></select></Field>
      <span className="help-text">Click column headings to sort. Missing values stay last.</span></div>
    <div className="table-scroll"><table className="data-table comparison-results" aria-label={view === 'runs' ? 'Individual run results' : 'Method group results'}><thead><tr>
      <th>Inspect</th>{header('method', 'Method / configuration')}{header('seed', view === 'runs' ? 'Seed' : 'Seeds')}
      {header('objective', view === 'runs' ? 'Best observed endpoint' : 'Mean at matched cost')}{header('cost', view === 'runs' ? 'Full cost' : 'Sum of attributed costs')}
      {header('status', 'Procedure')}<th>Finalists</th></tr></thead><tbody>
      {(view === 'runs' ? rows : methods).map(row => {
        const members: Json[] = view === 'runs' ? [row] : row.members;
        const candidate = members[0];
        const eligible = members.filter(item => item.finalist_eligible === true);
        const marked = eligible.length ? eligible.every(item => finalists.has(item.id)) : members.some(item => finalists.has(item.id));
        const selectedMembers = members.filter(item => selected.has(item.id));
        const inspectIds = view === 'runs' ? [row.id] : [candidate.id];
        return <tr key={row.id} data-trial-id={view === 'runs' ? row.id : undefined} data-method-id={row.method_id} className={marked ? 'comparison-finalist' : ''}>
          <td><input type="checkbox" aria-label={`Compare ${view === 'runs' ? `run ${row.id}` : `method ${short(row.method_id)}`}`}
            checked={selectedMembers.length > 0} onChange={e => onSelect(e.target.checked ? inspectIds : members.map(item => item.id), e.target.checked)} /></td>
          <td><button className="table-link" onClick={() => inspect([decorate(candidate)])}>{row.hypothesis_title || row.algorithm}</button>
            <small>{row.hypothesis_title ? `${row.algorithm} · ` : ''}Method {short(row.method_id)}</small>
            <small className="comparison-parameters" title={methodParameterSummary(row)}>{methodParameterSummary(row)}</small>
            {view === 'runs' && <small>Run {row.id}</small>}{row.question && <details className="comparison-question"><summary>Experiment question</summary><p>{row.question}</p></details>}</td>
          <td>{view === 'runs' ? row.seed : `${members.length} runs`} {view === 'methods' && <small>{[...new Set(members.map(item => item.seed))].join(', ')}</small>}</td>
          <td>{objectiveValue(row.best_objective, group.problem.primary_objective)}{view === 'methods' && <small>{row.summary?.observed_count || 0} of {members.length} runs observed at shared cost</small>}</td>
          <td>{cost(row.full_cost?.quantities?.[axis], axis)}{members.some(item => item.unknown_cost) && <small>Incomplete cost provenance</small>}</td>
          <td>{row.scientific_complete ? 'Complete' : 'Incomplete / censored'}<small>{procedureSummary(candidate)}</small>
            {members.some(item => item.adaptive_extension) && <small>Allocation extended</small>}
            {view === 'methods' && new Set(members.map(item => item.procedure_id)).size > 1 && <small>Different procedure caps: inspect runs before confirmation</small>}</td>
          <td><button className="button small" disabled={busy || (!marked && !eligible.length)}
            title={!marked && !eligible.length ? candidate.finalist_ineligible_reason || 'This run is not an eligible development prototype.' : undefined}
            onClick={() => mark((marked ? members : eligible).map(item => item.id), !marked)}>
            {marked ? view === 'runs' ? 'Unmark finalist' : 'Unmark group' : view === 'runs' ? 'Mark finalist' : 'Mark method group'}</button>
            {view === 'methods' && !marked && members.some(item => finalists.has(item.id)) && <small>{members.filter(item => finalists.has(item.id)).length} runs marked</small>}
            {!marked && !eligible.length && <small>{candidate.finalist_ineligible_reason || 'Not eligible as a development prototype'}</small>}</td>
        </tr>;
      })}</tbody></table></div>
    <p className="help-text">{view === 'runs' ? 'Endpoints may have different budgets. Use method groups to compare means at the shared observed cost.' : 'Means use the shared observed cost, not each run’s endpoint. Seed replicates are grouped by method identity; procedure caps can still differ. Group cost sums attributed run costs and may count shared upstream work repeatedly.'} Descriptive observations do not establish superiority.</p>
  </Panel>;
}

export function GeneralComparison({ state, actions }: Props) {
  const [axis, setAxis] = useState('worker_seconds'), [study, setStudy] = useState('');
  const [showDetailedReport, setShowDetailedReport] = useState(false), [reportRevision, setReportRevision] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set()), [details, setDetails] = useState<Json[] | null>(null);
  const [failure, setFailure] = useState(''), [busy, setBusy] = useState(false);
  const studyId = study || state.campaign?.active_study_id;
  const path = showDetailedReport && state.campaign ? `/api/v1/campaigns/${state.campaign.id}/comparison?cost_axis=${axis}${studyId ? `&study_id=${studyId}` : ''}` : null;
  const { data: report, error, loading } = useRead<Json>(path, reportRevision);
  const saved = (state.finalist_selections || []).find((selection: Json) => selection.study_id === studyId);
  const finalists = useMemo(() => new Set<string>(saved?.trial_ids || []), [saved]);
  const command = useCommand(state.campaign);
  useEffect(() => { setStudy(''); setShowDetailedReport(false); }, [state.campaign?.id]);
  useEffect(() => { setSelected(new Set()); setDetails(null); setFailure(''); }, [state.campaign?.id, studyId]);
  const trials: Json[] = useMemo(() => (report?.groups || []).flatMap((group: Json) => group.trials.map((trial: Json) => ({
    ...trial, objective_definition: group.problem.primary_objective, comparison_group_id: group.id }))), [report]);
  const selectedTrials = trials.filter(trial => selected.has(trial.id));
  const eligibleSelected = selectedTrials.filter(trial => trial.finalist_eligible === true);
  async function saveFinalists(ids: string[]) {
    if (!studyId || busy) return;
    setBusy(true); setFailure('');
    try {
      const expectedProcedureIds = Object.fromEntries(trials.filter(trial => ids.includes(trial.id) && trial.procedure_id).map(trial => [trial.id, trial.procedure_id]));
      await command('finalist.set', { study_id: studyId, trial_ids: ids, expected_revision: saved?.revision || 0, expected_procedure_ids: expectedProcedureIds });
      await actions.refresh(); actions.notify('Finalist shortlist saved. Review it in Studies when defining confirmation.');
    } catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  function select(ids: string[], checked: boolean) { setSelected(previous => {
    const next = new Set(previous); for (const id of ids) if (checked) next.add(id); else next.delete(id); return next;
  }); }
  return <><div className="page-heading"><div><span className="eyebrow">Evidence comparison</span><h1>Compare results.</h1>
    <p>Explore optimization curves in TensorBoard, then inspect matched-cost results before shortlisting finalists.</p></div></div>
    <Panel title="Optimization curves · TensorBoard"><p className="help-text">Select runs from this campaign in TensorBoard’s Runs panel. Curves use direct worker time or solver executions; the detailed report below includes upstream implementation and asset costs.</p>
      <div className="inline-actions"><a className="button small secondary" href="/tensorboard/" target="_blank" rel="noreferrer">Open TensorBoard in a new tab</a></div>
      <iframe className="comparison-tensorboard" title="TensorBoard optimization curves" src="/tensorboard/" loading="lazy" /></Panel>
    <div className="form-grid"><Field label="Study"><select value={studyId || ''} onChange={e => setStudy(e.target.value)}>{(state.studies || []).map((s: Json) => <option key={s.id} value={s.id}>{s.goal}</option>)}</select></Field>
      <Field label="Comparison cost"><select value={axis} onChange={e => setAxis(e.target.value)}><option value="worker_seconds">Full upstream worker time</option><option value="evaluation_requests">Full upstream evaluation requests</option><option value="solver_executions">Full upstream solver executions</option></select></Field></div>
    <div className="inline-actions comparison-report-actions"><button className="button" onClick={() => showDetailedReport ? setReportRevision(n => n + 1) : setShowDetailedReport(true)}>{showDetailedReport ? 'Refresh matched-cost report' : 'Load matched-cost report'}</button>
      <span className="help-text">The detailed report reads full trial provenance and may take longer for large campaigns.</span></div>
    {showDetailedReport && <>
    <section className="comparison-shortlist" aria-label="Finalist shortlist"><strong>{finalists.size} finalist runs marked{saved?.prototype_trial_ids ? ` · ${saved.prototype_trial_ids.length} distinct confirmation procedures` : ''}</strong>
      <p>A saved shortlist records your choice for this study. It does not freeze a nomination or schedule experiments. Studies → Define a new study → Fixed confirmation procedure lets you use it.</p>
      <div className="inline-actions"><a className="button" href="#studies">Use finalists in a confirmation study</a>
        {!!finalists.size && <button className="button" disabled={busy} onClick={() => void saveFinalists([])}>Clear finalist shortlist</button>}</div>
    </section>
    <div className="comparison-selection-actions"><strong>{selectedTrials.length} runs selected for inspection</strong>
      <button className="button" disabled={!selectedTrials.length} onClick={() => setDetails(selectedTrials)}>Inspect / compare selected</button>
      <button className="button" disabled={busy || !eligibleSelected.length} onClick={() => void saveFinalists([...new Set([...finalists, ...eligibleSelected.map(trial => trial.id)])])}>Mark selected as finalists</button>
      <button className="text-button" disabled={!selected.size} onClick={() => setSelected(new Set())}>Clear inspection selection</button>
      {!!selectedTrials.length && eligibleSelected.length < selectedTrials.length && <span className="help-text">Only eligible development runs can be marked ({eligibleSelected.length} selected).</span>}
    </div>
    <ErrorNotice text={failure || error} />
    {busy && <p role="status" className="help-text">Saving finalist shortlist…</p>}
    {loading && <p className="help-text" role="status">{report ? 'Updating comparison results…' : 'Loading comparison results…'}</p>}
    {report && !report.groups.length && <Empty title="No comparable experiments yet">Create experiments in this study to compare their observed objectives.</Empty>}
    {report?.groups.map((group: Json) => <ComparisonGroup key={group.id} group={group} axis={axis} selected={selected} onSelect={select}
      finalists={finalists} saveFinalists={ids => void saveFinalists(ids)} busy={busy} inspect={setDetails} />)}
    {report && <Panel title="Actual campaign expenditure"><p>{cost(report.actual_campaign_costs.quantities.worker_seconds, 'worker_seconds')} worker time · {cost(report.actual_campaign_costs.quantities.evaluation_requests, 'evaluation_requests')} requests.</p>
      <p>{cost(report.actual_campaign_costs.quantities.model_calls, 'model_calls')} model calls · implementation service elapsed time: {cost(report.actual_campaign_costs.quantities.implementation_seconds, 'worker_seconds')}.</p>
      <p className="help-text">Shared computations are counted once here. Each dependent method receives its full upstream contribution in the comparison.</p></Panel>}
    {details && <MethodDetailsDialog trials={details} onClose={() => setDetails(null)} />}
    </>}
  </>;
}
