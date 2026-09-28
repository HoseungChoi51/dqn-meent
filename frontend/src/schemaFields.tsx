import type { Json } from './api';
import { Field } from './ui';

function structured(field: Json) {
  return field.type === 'object' || field.type === 'array' && !['number', 'integer', 'string'].includes(field.items?.type);
}

export function SchemaFields({ schema, values, setValues }: { schema: Json; values: Json; setValues: (value: Json) => void }) {
  return <>{Object.entries(schema.properties || {}).map(([key, raw]) => {
    const property = raw as Json, value = values[key] ?? property.default ?? '';
    const change = (updated: unknown) => setValues({ ...values, [key]: updated });
    return <Field key={key} label={property.title || key} hint={property.description}>
      {property.enum ? <select value={String(value)} onChange={e => change(property.enum.find((entry: unknown) => String(entry) === e.target.value))}>
        {!property.enum.includes(value) && <option value="">Select a value</option>}{property.enum.map((option: unknown) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select>
        : property.type === 'boolean' ? <select value={String(value)} onChange={e => change(e.target.value === 'true')}>
          {value === '' && <option value="">Select a value</option>}<option value="true">Yes</option><option value="false">No</option></select>
        : structured(property) ? <textarea value={typeof value === 'object' ? JSON.stringify(value) : value} onChange={e => change(e.target.value)} required={schema.required?.includes(key)} placeholder={property.type === 'array' ? 'JSON array' : 'JSON object'} />
        : property.type === 'array' ? <input value={Array.isArray(value) ? value.join(', ') : value} onChange={e => change(e.target.value)} required={schema.required?.includes(key)} placeholder={property.items?.type === 'string' ? 'Comma-separated values' : 'Comma-separated numbers'} />
        : ['number', 'integer'].includes(property.type) ? <input type="number" step={property.type === 'integer' ? 1 : 'any'} min={property.minimum} max={property.maximum}
          value={value} onChange={e => change(e.target.value)} required={schema.required?.includes(key)} />
        : <input value={value} onChange={e => change(e.target.value)} required={schema.required?.includes(key)} />}
    </Field>;
  })}</>;
}

export function resolvedParameters(schema: Json, values: Json) {
  return Object.fromEntries(Object.entries(schema.properties || {}).flatMap(([key, raw]) => {
    const field = raw as Json, value = values[key] ?? field.default;
    if (value == null || value === '') return [];
    return [[key, structured(field) ? typeof value === 'object' ? value : JSON.parse(String(value))
      : field.type === 'array' ? Array.isArray(value) ? value : String(value).split(',').map(item => field.items?.type === 'string' ? item.trim() : Number(item))
      : ['number', 'integer'].includes(field.type) ? Number(value) : value]];
  }));
}
