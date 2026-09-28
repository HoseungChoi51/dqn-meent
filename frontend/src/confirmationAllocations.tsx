import type { Json, State } from './api';
import { seconds } from './api';
import { Badge, Field } from './ui';
import { prototypeProcedure } from './prototypePicker';
import './confirmationAllocations.css';

export type PrototypeAllocation = {
  expected_control_revision: number;
  max_steps: number;
  wall_seconds: number;
  schedule_steps: number;
  completion_count: number;
};
export type PrototypeAllocations = Record<string, PrototypeAllocation>;

function stable(value: any): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).filter(key => value[key] !== undefined).sort()
    .map(key => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

function defaults(trial: Json, locked = false): PrototypeAllocation {
  const completion = trial.completion || { unit: 'evaluation_requests', count: trial.max_steps };
  return { expected_control_revision: trial.control_revision ?? 0, max_steps: trial.max_steps,
    wall_seconds: trial.wall_seconds, schedule_steps: trial.schedule_steps,
    completion_count: completion.unit === 'evaluation_requests' && !locked ? trial.max_steps : completion.count };
}

function rowsFor(state: State, sourceIds: string[], nominationId: string) {
  const nomination: Json | undefined = (state.nominations || []).find((item: Json) => item.id === nominationId);
  const nominatedIds = new Set<string>(Object.values(nomination?.prototypes || {}));
  const nominatedMethods = new Set(Object.values(nomination?.methods || {}).map(stable));
  const seen = new Set<string>();
  return [...new Set([...nominatedIds, ...sourceIds])].flatMap(id => {
    const trial: Json | undefined = state.trials.find(item => item.id === id);
    if (!trial) return [];
    const key = stable(prototypeProcedure(trial));
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ trial, locked: nominatedIds.has(id) || nominatedMethods.has(key) }];
  });
}

export function confirmationAllocationPayload(state: State, sourceIds: string[], nominationId: string,
  edits: PrototypeAllocations): PrototypeAllocations {
  return Object.fromEntries(rowsFor(state, sourceIds, nominationId).filter(row => !row.locked && sourceIds.includes(row.trial.id))
    .map(({ trial }) => [trial.id, edits[trial.id] || defaults(trial)]));
}

type Props = {
  state: State; sourceIds: string[]; nominationId: string; taskIds: string[]; seeds: string;
  value: PrototypeAllocations; onChange: (value: PrototypeAllocations) => void; disabled?: boolean;
};

function count(value: number) { return Number.isFinite(value) ? value.toLocaleString() : '—'; }

export function ConfirmationAllocations({ state, sourceIds, nominationId, taskIds, seeds, value, onChange, disabled = false }: Props) {
  const rows = rowsFor(state, sourceIds, nominationId);
  const seedParts = seeds.split(',').map(part => part.trim());
  const seedValues = seedParts.map(Number);
  const validSeeds = seedParts.every(part => /^\d+$/.test(part)) && seedValues.every(seed => Number.isSafeInteger(seed) && seed <= 2 ** 32 - 1)
    && new Set(seedValues).size === seedValues.length;
  const instanceCount = new Set(taskIds).size;
  const allocations = rows.map(({ trial, locked }) => locked ? defaults(trial, true) : value[trial.id] || defaults(trial));
  const uniqueMethods = new Map<string, PrototypeAllocation>();
  rows.forEach(({ trial }, index) => {
    const source = prototypeProcedure(trial), allocation = allocations[index];
    uniqueMethods.set(stable({ ...source, max_steps: allocation.max_steps, wall_seconds: allocation.wall_seconds,
      schedule_steps: allocation.schedule_steps, completion: { ...source.completion, count: allocation.completion_count } }), allocation);
  });
  const rosterAllocations = [...uniqueMethods.values()];
  const perInstanceSeed = rosterAllocations.reduce((total, item) => total + item.wall_seconds, 0);
  const perSeed = perInstanceSeed * instanceCount;
  const total = validSeeds ? perSeed * seedValues.length : null;
  const requestTotal = validSeeds ? rosterAllocations.reduce((sum, item) => sum + item.max_steps, 0) * instanceCount * seedValues.length : null;
  const budget: Json = state.budget || {}, cap = state.campaign?.compute_budget_seconds;
  const allocated = budget.allocated_seconds, reserve = state.campaign?.validation_reserve_seconds;
  const available = typeof cap === 'number' && typeof allocated === 'number' && typeof reserve === 'number'
    ? Math.max(0, cap - allocated - reserve) : null;
  const invalid = allocations.some(item => ![item.max_steps, item.wall_seconds, item.schedule_steps, item.completion_count]
    .every(number => Number.isFinite(number) && number > 0));
  function edit(trial: Json, field: keyof PrototypeAllocation, number: number) {
    const next = { ...(value[trial.id] || defaults(trial)), [field]: number };
    if (field === 'max_steps' && (trial.completion?.unit || 'evaluation_requests') === 'evaluation_requests') next.completion_count = number;
    onChange({ ...value, [trial.id]: next });
  }
  return <section className="confirmation-allocations" aria-label="Main-run allocations">
    <h3>Main-run allocations</h3>
    <p className="help-text">Set the budget for each finalist and control before freezing confirmation. Each cell starts with its fresh seed using the selected code and parameters. The source run's checkpoint is not resumed automatically; declared initialization assets are preserved.</p>
    {!rows.length ? <p className="help-text">Select prototypes or a frozen nomination to set their main-run allocations.</p> : <>
      <div className="table-scroll"><table className="data-table confirmation-allocation-table"><thead><tr>
        <th>Method / source</th><th>Request cap</th><th>Time cap</th><th>Optimizer schedule</th><th>Completion target</th>
      </tr></thead><tbody>{rows.map(({ trial, locked }, index) => {
        const allocation = allocations[index], sourceCompletion = trial.completion || { unit: 'evaluation_requests', count: trial.max_steps };
        const title = state.hypotheses.find(item => item.id === trial.hypothesis_id)?.title;
        const label = `${trial.algorithm} ${trial.id}`;
        return <tr key={trial.id} data-source-trial-id={trial.id}>
          <td><strong>{trial.algorithm}</strong>{title && <small>{title}</small>}<small>{trial.id}</small>
            <small>Source seed {trial.seed} · revision {trial.control_revision ?? 0}</small>
            {locked && <Badge>Frozen nomination</Badge>}</td>
          <td><Field label="Evaluation requests"><input aria-label={`Request cap · ${label}`} type="number" required min="1" max="10000000" step="1"
            disabled={disabled || locked} value={allocation.max_steps} onChange={event => edit(trial, 'max_steps', Number(event.target.value))} /></Field>
            <small>Source: {count(trial.max_steps)} evaluation requests</small></td>
          <td><Field label="Seconds per run"><input aria-label={`Time cap (seconds) · ${label}`} type="number" required min={Number.MIN_VALUE} max="86400" step="any"
            disabled={disabled || locked} value={allocation.wall_seconds} onChange={event => edit(trial, 'wall_seconds', Number(event.target.value))} /></Field>
            <small>Source: {count(trial.wall_seconds)} seconds per run</small></td>
          <td><Field label={trial.algorithm === 'dqn' ? 'DQN exploration schedule horizon' : 'Search schedule horizon'}><input aria-label={`${trial.algorithm === 'dqn' ? 'DQN exploration schedule horizon' : 'Search schedule horizon'} · ${label}`} type="number" required min="1" max="10000000" step="1"
            disabled={disabled || locked} value={allocation.schedule_steps} onChange={event => edit(trial, 'schedule_steps', Number(event.target.value))} /></Field>
            <small>Source: {count(trial.schedule_steps)} steps</small>
            {trial.algorithm === 'dqn' && <small>The request cap and exploration schedule are independent. Increase this horizon when you want exploration spread over a longer run.</small>}</td>
          <td>{sourceCompletion.unit === 'optimizer_decisions' ? <Field label="Required optimizer decisions"><input aria-label={`Required optimizer decisions · ${label}`} type="number" required min="1" max="10000000" step="1"
            disabled={disabled || locked} value={allocation.completion_count} onChange={event => edit(trial, 'completion_count', Number(event.target.value))} /></Field>
            : <><strong>{count(allocation.completion_count)} evaluation requests</strong><small>{locked ? 'Fixed by the nomination.' : 'Main completion follows the request cap.'}</small></>}
            <small>Source target: {count(sourceCompletion.count)} {String(sourceCompletion.unit).replaceAll('_', ' ')}</small>
            {sourceCompletion.unit === 'optimizer_decisions' && <small>Allow evaluation requests for initialization and episode resets in addition to optimizer decisions.</small>}
            {sourceCompletion.unit === 'evaluation_requests' && allocation.completion_count !== sourceCompletion.count
              && <small className="allocation-change">Completion target changes from {count(sourceCompletion.count)} to {count(allocation.completion_count)}.</small>}</td>
        </tr>;
      })}</tbody></table></div>
      {rows.some(row => row.locked) && <p className="help-text">A rule-based nomination freezes its methods and allocations. To compare a revised budget, choose procedures directly; additional controls can still be edited here.</p>}
      <div className="allocation-estimate" role="status">
        <strong>Numerical run allocation</strong>
        {validSeeds && !invalid ? <><p>{uniqueMethods.size} method{uniqueMethods.size === 1 ? '' : 's'} × {instanceCount} instance{instanceCount === 1 ? '' : 's'} × {seedValues.length} fresh seed{seedValues.length === 1 ? '' : 's'} = {uniqueMethods.size * instanceCount * seedValues.length} runs.</p>
          <p>{count(perSeed)} worker-seconds per seed across selected instances · <strong>{count(total!)} worker-seconds ({seconds(total)}) total time cap</strong>.</p>
          <p>Up to {count(requestTotal!)} evaluation requests across the roster.</p></>
          : <p>Enter positive allocations and distinct nonnegative fresh seeds to estimate the roster.</p>}
        {uniqueMethods.size < rows.length && <p>Some source rows now resolve to the same final procedure; the frozen roster includes that procedure once.</p>}
        {available !== null ? <p>Campaign room for numerical work: <strong>{count(available)} worker-seconds ({seconds(available)})</strong>. Cap {count(cap!)} − already allocated or spent {count(allocated)} − validation reserve {count(reserve!)} seconds.</p>
          : <p>Campaign cap: {seconds(cap)}; validation reserve: {seconds(reserve)}. Current available allocation is not supplied in this view.</p>}
        {total !== null && available !== null && total > available && <p className="allocation-over-budget">These run caps exceed current campaign room by {count(total - available)} worker-seconds. Reduce the allocations or revise the campaign cap before scheduling.</p>}
        <p>Aggregate worker time sums concurrent runs. These caps cover optimizer runs; required validation and diagnostic jobs need additional budget. Freezing records the design and does not start runs or reserve this estimate.</p>
      </div>
    </>}
  </section>;
}

export function FrozenConfirmationAllocations({ study }: { study: Json }) {
  const methods: [string, Json][] = Object.entries(study.confirmation?.methods || {});
  if (!methods.length) return null;
  const seeds = study.confirmation?.seeds || [], instances = study.confirmation?.instances || [];
  return <details className="frozen-confirmation-allocations"><summary>Frozen main-run allocations · {methods.length} method{methods.length === 1 ? '' : 's'}</summary>
    <div className="table-scroll"><table className="data-table confirmation-allocation-table"><thead><tr><th>Method</th><th>Requests per run</th><th>Time per run</th><th>Schedule horizon</th><th>Completion target</th></tr></thead>
      <tbody>{methods.map(([id, method]) => <tr key={id}><td>{method.algorithm}<small>{id.slice(0, 10)}</small><small>Source: {study.confirmation?.prototypes?.[id] || 'Pinned by the study'}</small></td>
        <td>{count(method.max_steps)}</td><td>{count(method.wall_seconds)} seconds</td><td>{count(method.schedule_steps)} steps</td>
        <td>{count(method.completion?.count ?? method.max_steps)} {String(method.completion?.unit || 'evaluation_requests').replaceAll('_', ' ')}</td></tr>)}</tbody></table></div>
    <p className="help-text">{instances.length} instance{instances.length === 1 ? '' : 's'} × {seeds.length} fresh seed{seeds.length === 1 ? '' : 's'} per method. Scheduling executes these frozen allocations.</p>
  </details>;
}
