import { useMemo, useState } from 'react';
import type { Json, State } from './api';
import { best, objectiveValue, seconds } from './api';
import { Badge, Field } from './ui';
import './prototypePicker.css';

function canonical(value: any): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).filter(key => value[key] !== undefined).sort()
    .map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

// This groups the visible source procedures, including their resource limits.
// The service still resolves and validates the authoritative procedure at freeze.
function procedure(trial: Json): Json {
  return {
    ...(trial.diagnostics?.length ? { diagnostics: trial.diagnostics } : {}),
    ...([2, 3].includes(trial.method_contract) ? { method_contract: trial.method_contract, recovery: {
      schema_version: 1, mode: 'latest_checkpoint', every_observations: 100, every_seconds: 30,
      chunk_bytes: 8388608, max_checkpoint_bytes: 4294967296, ...trial.recovery,
    } } : {}),
    algorithm: trial.algorithm, algorithm_config: trial.algorithm_config || {},
    training: Object.fromEntries(Object.entries(trial.training || {}).filter(([key]) => key !== 'seed')),
    schedule_steps: trial.schedule_steps, max_steps: trial.max_steps, wall_seconds: trial.wall_seconds,
    completion: trial.completion || { unit: 'evaluation_requests', count: trial.max_steps },
    implementation_version_id: trial.implementation_version_id ?? null,
    implementation_digest: trial.implementation_artifact_digest ?? null,
    scientific_source_hash: trial.scientific_source_hash, runtime: trial.scientific_environment,
    initial_assets: trial.initial_assets || [], contribution_asset_ids: trial.contribution_asset_ids || [],
    asset_digests: Object.keys(trial.experiment_spec?.asset_digests || {}).length ? trial.experiment_spec.asset_digests
      : Object.fromEntries((trial.declared_assets || []).map((asset: Json) => [asset.id, asset.content_hash])),
  };
}

function unavailable(trial: Json): string {
  if (trial.method_contract === 3) return 'Use the frozen template execution for this procedure with cell-specific bindings.';
  if (trial.execution_contract !== 1 || !trial.scientific_source_hash || !trial.scientific_environment)
    return 'Verified source and runtime identities are required before confirmation.';
  return '';
}

function parameters(trial: Json): string {
  const entries = Object.entries(trial.algorithm_config || {});
  return entries.length ? entries.map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`).join(' · ')
    : 'No algorithm-specific parameters';
}

type Props = { state: State; value: string[]; onChange: (ids: string[]) => void; onUseSaved: (selection: Json) => void; disabled?: boolean };

export function PrototypePicker({ state, value, onChange, onUseSaved, disabled = false }: Props) {
  const trials: Json[] = state.trials.filter(trial => !['recipe', 'validate'].includes(trial.algorithm) && !trial.recipe && !trial.diagnostic_grant_id);
  const studies: Json[] = (state.studies || []).filter((study: Json) => trials.some(trial => trial.study_id === study.id));
  const selections: Json[] = state.finalist_selections || [];
  const active = studies.find(study => study.id === state.campaign?.active_study_id && study.scope !== 'confirmation');
  const latestSaved = [...selections].filter(row => row.prototype_trial_ids?.length && studies.some(study => study.id === row.study_id))
    .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))[0];
  const [source, setSource] = useState(active?.id || latestSaved?.study_id || studies.at(-1)?.id || '');
  const [query, setQuery] = useState(''), [finalistsOnly, setFinalistsOnly] = useState(false);
  const [representatives, setRepresentatives] = useState<Record<string, string>>({});
  const saved = selections.find(row => row.study_id === source);
  const savedIds: string[] = saved?.prototype_trial_ids || [];
  const savedTrialIds = new Set<string>(saved?.trial_ids || []);
  const byId = new Map(trials.map(trial => [trial.id, trial]));
  const identities = useMemo(() => new Map(state.trials.map(trial => [trial.id, canonical(procedure(trial))])), [state.trials]);
  const groups = new Map<string, Json[]>();
  for (const trial of trials) {
    if (source && trial.study_id !== source) continue;
    const key = identities.get(trial.id)!;
    groups.set(key, [...(groups.get(key) || []), trial]);
  }
  const missingSaved = savedIds.filter(id => !byId.has(id) || unavailable(byId.get(id)!));
  const changedSaved = savedIds.filter(id => {
    const entry = (saved?.entries || []).find((item: Json) => item.trial_id === id);
    const snapshot = entry?.resolved_procedure || entry?.procedure;
    return snapshot && byId.has(id) && canonical(snapshot) !== identities.get(id);
  });
  const visible = [...groups.entries()].filter(([, group]) => {
    const isFinalist = group.some(trial => savedTrialIds.has(trial.id) || savedIds.includes(trial.id));
    const text = group.map(trial => [trial.algorithm, trial.id, parameters(trial),
      state.hypotheses.find(item => item.id === trial.hypothesis_id)?.title || ''].join(' ')).join(' ').toLowerCase();
    return (!finalistsOnly || isFinalist) && (!query.trim() || text.includes(query.trim().toLowerCase()));
  }).sort(([, a], [, b]) => Number(b.some(trial => savedTrialIds.has(trial.id))) - Number(a.some(trial => savedTrialIds.has(trial.id)))
    || String(a[0].algorithm).localeCompare(String(b[0].algorithm)) || parameters(a[0]).localeCompare(parameters(b[0])));
  const outside = value.filter(id => !visible.some(([, group]) => group.some(trial => trial.id === id)));

  function choose(key: string, id: string, checked: boolean) {
    const remaining = value.filter(current => identities.get(current) !== key);
    onChange(checked ? [...remaining, id] : remaining);
  }

  return <section aria-label="Prototype experiments" style={{ gridColumn: '1 / -1', minWidth: 0 }}>
    <h3>Prototype experiments</h3>
    <p className="help-text">Choose one source experiment per procedure. Repeated seeds share a row when their parameters, budgets, source, runtime and inputs match. Add baseline controls alongside your finalists.</p>
    <div className="form-grid">
      <Field label="Prototype source study"><select disabled={disabled} value={source} onChange={event => { setSource(event.target.value); setQuery(''); setFinalistsOnly(false); }}>
        {!studies.length && <option value="">No source studies yet</option>}
        {studies.map(study => <option key={study.id} value={study.id}>{study.goal} · {study.id}</option>)}
      </select></Field>
      <Field label="Find prototype"><input disabled={disabled} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Method, proposal, parameter or experiment ID" /></Field>
    </div>
    <p><button type="button" className="button secondary" disabled={disabled || !savedIds.length || !!missingSaved.length || !!changedSaved.length}
      onClick={() => saved && onUseSaved(saved)}>Use saved finalists</button>{' '}
      {savedIds.length ? <span>{saved?.label || 'Saved finalist group'} · {savedIds.length} procedure{savedIds.length === 1 ? '' : 's'}</span>
        : <span className="help-text">Mark finalists in Compare results to save a group for this study.</span>}</p>
    {!!savedIds.length && <p className="help-text">This button replaces the current prototype choices with the saved group. You can then add controls below. The editable shortlist is separate from a frozen development nomination.</p>}
    {!!missingSaved.length && <p className="help-text">Some saved source experiments are unavailable for direct confirmation. Review the saved group in Compare results.</p>}
    {!!changedSaved.length && <p className="help-text">A saved procedure has changed. Review and save the group again in Compare results before importing it.</p>}
    <label className="checkbox-label"><input type="checkbox" disabled={disabled} checked={finalistsOnly} onChange={event => setFinalistsOnly(event.target.checked)} />Show saved finalists only</label>
    <p role="status">{value.length} prototype{value.length === 1 ? '' : 's'} selected{outside.length ? ` · ${outside.length} outside the current filter` : ''}.</p>
    {!!outside.length && <ul>{outside.map(id => <li key={id}>{byId.get(id)?.algorithm || 'Unavailable experiment'} · {id}{' '}
      <button type="button" className="text-button" disabled={disabled} onClick={() => onChange(value.filter(current => current !== id))}>Remove</button></li>)}</ul>}
    <div className="table-scroll"><table className="data-table prototype-table"><thead><tr><th>Use</th><th>Method / proposal</th><th>Parameters and budget</th><th>Source experiment</th></tr></thead><tbody>
      {visible.map(([key, group]) => {
        const selectedId = value.find(id => identities.get(id) === key);
        const trial = group.find(item => item.id === selectedId) || group.find(item => item.id === representatives[key])
          || group.find(item => savedIds.includes(item.id)) || group[0];
        const title = state.hypotheses.find(item => item.id === trial.hypothesis_id)?.title;
        const entry = (saved?.entries || []).find((item: Json) => (item.source_trial_ids || [item.trial_id]).some((id: string) => group.some(row => row.id === id)));
        const isFinalist = group.some(item => savedTrialIds.has(item.id) || savedIds.includes(item.id));
        const reason = unavailable(trial), completion = trial.completion || { unit: 'evaluation_requests', count: trial.max_steps };
        return <tr key={key}>
          <td><input type="checkbox" aria-label={`Select ${trial.algorithm} prototype ${trial.id}`} disabled={disabled || !!reason} checked={!!selectedId}
            onChange={event => choose(key, trial.id, event.target.checked)} /></td>
          <td><strong>{trial.algorithm}</strong>{title && <small>{title}</small>}{isFinalist && <Badge>Saved finalist</Badge>}
            {entry?.method_id && <small>Method {entry.method_id.slice(0, 10)}</small>}
            <small>{group.length} source seed{group.length === 1 ? '' : 's'}: {group.map(item => item.seed).join(', ')}</small></td>
          <td>{parameters(trial)}<small>Completion: {Number(completion.count).toLocaleString()} {String(completion.unit).replaceAll('_', ' ')}</small>
            <small>Allocation: {Number(trial.max_steps).toLocaleString()} steps · {seconds(trial.wall_seconds)} wall limit</small>
            <small>Optimizer schedule: {Number(trial.schedule_steps).toLocaleString()} steps</small>
            <details><summary>Exact procedure, implementation and inputs</summary><pre>{JSON.stringify(procedure(trial), null, 2)}</pre></details></td>
          <td><select aria-label={`Source experiment for ${trial.algorithm} ${group[0].id}`} disabled={disabled} value={trial.id} onChange={event => {
            setRepresentatives(current => ({ ...current, [key]: event.target.value }));
            if (selectedId) choose(key, event.target.value, true);
          }}>{group.map(item => <option key={item.id} value={item.id}>Seed {item.seed} · {item.id}</option>)}</select>
            <small>{trial.id}</small><small>{trial.status} · best observed {objectiveValue(best(trial as any), trial.problem?.primary_objective)}</small>
            <small>{trial.task_name || state.tasks.find(task => task.id === trial.task_id)?.name || trial.task_id}</small>
            {reason && <small>{reason}</small>}</td>
        </tr>;
      })}
    </tbody></table></div>
    {!visible.length && <p className="help-text">No prototype experiments match this study and filter.</p>}
    <p className="help-text">Confirmation copies each selected procedure's exact parameters, completion target, optimizer schedule and time limit. To use a larger budget, first create a source experiment with that budget. Freezing the study defines its roster; scheduling starts its runs.</p>
  </section>;
}
