import { useEffect, useState } from 'react';
import type { Json } from './api';
import { Field } from './ui';
import { SchemaFields, resolvedParameters } from './schemaFields';

export const completionNames: Record<string, string> = { evaluation_requests: 'Evaluation requests', optimizer_decisions: 'Optimizer decisions' };

function MilestoneCounts({ values, onChange }: { values: number[]; onChange: (values: number[]) => void }) {
  const [text, setText] = useState(values.join(', ')), [focused, setFocused] = useState(false);
  const formatted = values.join(', ');
  useEffect(() => { if (!focused) setText(formatted); }, [formatted, focused]);
  return <Field label="Milestone counts" hint="Distinct increasing counts, separated by commas."><input value={text} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)} onChange={e => {
    setText(e.target.value); onChange(e.target.value.split(',').map(Number));
  }} /></Field>;
}

function recipeSchema(definition: Json | undefined, identity: string, problem: Json | undefined): Json {
  const schema = definition?.recipe_schemas?.[identity] || {};
  return { ...schema, properties: Object.fromEntries(Object.entries(schema.properties || {}).map(([key, raw]) => {
    const field = raw as Json;
    return [key, field.default_from_fidelity ? { ...field, default: problem?.fidelity?.[field.default_from_fidelity] } : field];
  })) };
}

function normalizeParameters(schema: Json, values: Json) {
  // Unknown keys stay visible to backend validation, including advanced edits.
  return { ...values, ...resolvedParameters(schema, values) };
}

export function normalizedDiagnostics(text: string, definition: Json | undefined, problem: Json | undefined, adapters: Json[]): Json[] {
  const schedules = JSON.parse(text || '[]');
  if (!Array.isArray(schedules)) throw new Error('Diagnostic schedules must be a JSON array.');
  const recipes = (items: Json[] = []) => items.map(recipe => ({ ...recipe,
    parameters: normalizeParameters(recipeSchema(definition, recipe.recipe_id, problem), recipe.parameters || {}) }));
  return schedules.map((schedule: Json) => ({ ...schedule, recipes: recipes(schedule.recipes),
    rollouts: (schedule.rollouts || []).map((rollout: Json) => {
      const adapter = adapters.find(item => item.id === rollout.adapter_id);
      return { ...rollout, ...(adapter ? { parameters: normalizeParameters(adapter.parameter_schema, rollout.parameters || {}) } : {}),
        recipes: recipes(rollout.recipes) };
    }) }));
}

function RecipeList({ values, onChange, definition, problem }: {
  values: Json[]; onChange: (values: Json[]) => void; definition?: Json; problem?: Json;
}) {
  const choices = definition?.validation_recipes || [];
  const available = choices.filter((id: string) => definition?.recipe_schemas?.[id]?.available !== false);
  const update = (index: number, value: Json) => onChange(values.map((item, i) => i === index ? value : item));
  return <div className="diagnostic-recipes">{values.map((recipe, index) => <fieldset key={index}>
    <legend>Validation {index + 1}</legend><div className="form-grid">
      <Field label="Validation recipe"><select value={recipe.recipe_id} onChange={e => update(index, { ...recipe, recipe_id: e.target.value, parameters: {} })}>
        {!choices.includes(recipe.recipe_id) && <option value={recipe.recipe_id}>{recipe.recipe_id} · unavailable</option>}
        {choices.map((id: string) => <option key={id} value={id} disabled={!available.includes(id)}>{definition?.recipe_schemas?.[id]?.title || id}{!available.includes(id) ? ' · unavailable' : ''}</option>)}</select></Field>
      <Field label="Validation time cap (seconds)"><input type="number" min="1" max="86400" value={recipe.wall_seconds ?? 120} onChange={e => update(index, { ...recipe, wall_seconds: Number(e.target.value) })} /></Field>
      <Field label="Maximum captured solutions"><input type="number" min="1" max="10" value={recipe.subject_limit ?? 1} onChange={e => update(index, { ...recipe, subject_limit: Number(e.target.value) })} /></Field>
      <SchemaFields schema={recipeSchema(definition, recipe.recipe_id, problem)} values={recipe.parameters || {}} setValues={parameters => update(index, { ...recipe, parameters })} />
    </div><button type="button" className="text-button" onClick={() => onChange(values.filter((_, i) => i !== index))}>Remove validation</button>
    {!choices.includes(recipe.recipe_id) && <p className="callout amber">This problem does not provide the saved validation recipe.</p>}
    {definition?.recipe_schemas?.[recipe.recipe_id]?.available === false && <p className="callout amber">{definition.recipe_schemas[recipe.recipe_id].unavailable_reason}</p>}
  </fieldset>)}
    <button type="button" className="button secondary" disabled={!available.length} onClick={() => onChange([...values, { recipe_id: available[0], parameters: {}, wall_seconds: 60, subject_limit: 1 }])}>Add validation</button>
  </div>;
}

export function DiagnosticEditor({ value, onChange, capabilities, adapters, definition, problem }: {
  value: string; onChange: (value: string) => void; capabilities: Json; adapters: Json[]; definition?: Json; problem?: Json;
}) {
  let schedules: Json[] = [];
  try {
    const record = (item: unknown) => !!item && typeof item === 'object' && !Array.isArray(item);
    const recipes = (items: unknown) => items == null || (Array.isArray(items) && items.every(item => record(item) && (item.parameters == null || record(item.parameters))));
    const parsed = JSON.parse(value || '[]');
    if (!Array.isArray(parsed) || !parsed.every(item => record(item) && (item.at_counts == null || Array.isArray(item.at_counts)) && recipes(item.recipes)
      && (item.rollouts == null || (Array.isArray(item.rollouts) && item.rollouts.every((rollout: Json) => record(rollout) && recipes(rollout.recipes)
        && (rollout.parameters == null || record(rollout.parameters))))))) throw new Error();
    schedules = parsed;
  }
  catch { return <p className="callout amber">Correct the advanced schedule JSON to use the diagnostic controls.</p>; }
  const units: string[] = capabilities.completion_units || ['evaluation_requests'];
  const supported = adapters.filter(adapter => adapter.representations.includes(problem?.candidate_schema?.representation)
    && (!problem?.candidate_schema?.constraints?.length || adapter.constraints)
    && (capabilities.exports || []).some((item: Json) => item.kind === adapter.artifact.kind && item.format === adapter.artifact.format
      && Object.entries(adapter.artifact.metadata || {}).every(([key, expected]) => item.metadata?.[key] === expected)));
  const change = (next: Json[]) => onChange(JSON.stringify(next, null, 2));
  const update = (index: number, next: Json) => change(schedules.map((item, i) => i === index ? next : item));
  const reserved = schedules.reduce((total, schedule) => total + (schedule.at_counts?.length || 0) * (
    (schedule.recipes || []).reduce((sum: number, recipe: Json) => sum + Number(recipe.wall_seconds ?? 120), 0)
    + (schedule.rollouts || []).reduce((sum: number, rollout: Json) => sum + Number(rollout.wall_seconds ?? 60)
      + (rollout.recipes || []).reduce((cost: number, recipe: Json) => cost + Number(recipe.wall_seconds ?? 120), 0), 0)), 0);
  return <div className="diagnostic-editor"><p className="help-text">Milestones capture immutable evidence. Independent diagnostics have their own seeds and reserved time.</p>
    {schedules.map((schedule, index) => {
      const rollouts: Json[] = schedule.rollouts || [];
      const updateRollout = (i: number, rollout: Json) => update(index, { ...schedule, rollouts: rollouts.map((item, j) => j === i ? rollout : item) });
      return <fieldset key={index}><legend>Diagnostic schedule {index + 1}</legend><div className="form-grid">
        <Field label="Milestone counter"><select value={schedule.unit || 'evaluation_requests'} onChange={e => update(index, { ...schedule, unit: e.target.value })}>
          {!units.includes(schedule.unit || 'evaluation_requests') && <option value={schedule.unit}>{completionNames[schedule.unit] || schedule.unit} · unsupported</option>}
          {units.map(unit => <option key={unit} value={unit}>{completionNames[unit]}</option>)}</select></Field>
        <MilestoneCounts values={schedule.at_counts || []} onChange={at_counts => update(index, { ...schedule, at_counts })} />
        <Field label="Capture optimizer artifacts"><input type="checkbox" checked={schedule.export_optimizer !== false} disabled={!!rollouts.length} onChange={e => update(index, { ...schedule, export_optimizer: e.target.checked })} /></Field>
      </div><RecipeList values={schedule.recipes || []} onChange={recipes => update(index, { ...schedule, recipes })} definition={definition} problem={problem} />
        {rollouts.map((rollout, i) => {
          const adapter = adapters.find(item => item.id === rollout.adapter_id);
          const fixed = typeof rollout.seed === 'number';
          return <fieldset key={i}><legend>Artifact inference {i + 1}</legend>
            {rollout.kind !== 'artifact_inference:v1' ? <p className="help-text">Saved legacy policy declaration. Its recorded behavior is preserved; inspect or edit it in the advanced schedule.</p> : <>
              <div className="form-grid"><Field label="Inference adapter"><select value={rollout.adapter_id} onChange={e => updateRollout(i, { ...rollout, adapter_id: e.target.value, parameters: {} })}>
                {!supported.some(item => item.id === rollout.adapter_id) && <option value={rollout.adapter_id}>{adapter?.title || rollout.adapter_id} · unavailable for this optimizer</option>}
                {supported.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></Field>
                <Field label="Inference time cap (seconds)"><input type="number" min="1" max="86400" value={rollout.wall_seconds ?? 60} onChange={e => updateRollout(i, { ...rollout, wall_seconds: Number(e.target.value) })} /></Field>
                <SchemaFields schema={adapter?.parameter_schema || {}} values={rollout.parameters || {}} setValues={parameters => updateRollout(i, { ...rollout, parameters })} />
                <Field label="Inference seed rule"><select value={fixed ? 'fixed' : 'affine'} onChange={e => updateRollout(i, { ...rollout,
                  seed: e.target.value === 'fixed' ? 0 : { kind: 'affine:v1', offset: 0, parent_seed_factor: 0, milestone_factor: 0, episode_factor: 1 } })}>
                  <option value="fixed">Fixed seed</option><option value="affine">Derived from parent and milestone</option></select></Field>
                {fixed ? <Field label="Inference seed"><input type="number" min="0" max="4294967295" value={rollout.seed} onChange={e => updateRollout(i, { ...rollout, seed: Number(e.target.value) })} /></Field>
                  : [['offset', 'Seed offset'], ['parent_seed_factor', 'Parent seed multiplier'], ['milestone_factor', 'Milestone multiplier'], ['episode_factor', 'Episode multiplier']].map(([key, label]) =>
                    <Field key={key} label={label}><input type="number" min="0" max="4294967295" value={rollout.seed?.[key] ?? 0} onChange={e => updateRollout(i, { ...rollout, seed: { ...rollout.seed, [key]: Number(e.target.value) } })} /></Field>)}
              </div>{!supported.some(item => item.id === rollout.adapter_id) && <p className="callout amber">The saved inference requires a compatible artifact export and installed adapter. Save this draft while that requirement is resolved.</p>}
            </>}
            <RecipeList values={rollout.recipes || []} onChange={recipes => updateRollout(i, { ...rollout, recipes })} definition={definition} problem={problem} />
            <button type="button" className="text-button" onClick={() => update(index, { ...schedule, rollouts: rollouts.filter((_, j) => j !== i) })}>Remove inference</button>
          </fieldset>;
        })}
        <div className="modal-actions"><button type="button" className="button secondary" disabled={!supported.length} onClick={() => update(index, { ...schedule, export_optimizer: true,
          rollouts: [...rollouts, { kind: 'artifact_inference:v1', adapter_id: supported[0].id, parameters: {}, seed: 0, wall_seconds: 60, recipes: [] }] })}>Add artifact inference</button>
          <button type="button" className="text-button" onClick={() => change(schedules.filter((_, i) => i !== index))}>Remove schedule</button></div>
        {!supported.length && <p className="help-text">No registered inference adapter matches this optimizer's declared exports. Snapshot validation remains available.</p>}
      </fieldset>;
    })}
    <button type="button" className="button secondary" onClick={() => change([...schedules, { unit: units[0], at_counts: [1], export_optimizer: true, recipes: [], rollouts: [] }])}>Add diagnostic schedule</button>
    {!!schedules.length && <p className="help-text">Reserved diagnostic time: {reserved} seconds, in addition to the parent experiment.</p>}
  </div>;
}
