import { useState } from 'react';
import type { Campaign, Json } from './api';
import { errorText } from './api';
import { useCommand } from './commands';
import { ErrorNotice, Field } from './ui';

export function CostReceipts({ detail, campaign, refresh }: { detail: Json; campaign: Campaign; refresh: () => Promise<void> }) {
  const command = useCommand(campaign);
  const sources: Json[] = detail.accounting_sources || [];
  const [sourceId, setSourceId] = useState(''), [stop, setStop] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [evidence, setEvidence] = useState(''), [rationale, setRationale] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [receipt, setReceipt] = useState('');
  const source = sources.find(row => row.source_id === sourceId) || sources[0];
  const boundary = stop || String(source?.observed_stop || 0);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (!source) return;
    setBusy(true); setError(''); setReceipt('');
    try {
      const result = await command('cost.reconcile', { asset_id: detail.asset.id, source_id: source.source_id,
        stop: Number(boundary), quantities: Object.fromEntries(Object.entries(values).filter(([, value]) => value !== '').map(([key, value]) => [key, Number(value)])),
        evidence_ids: evidence.split(/[\s,]+/).filter(Boolean), rationale });
      setReceipt(result.receipt_id); setValues({});
      await refresh();
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }
  const quantities: Json = detail.full_attributed_cost.quantities;
  return <>
    <p>Accounting basis: <strong>{detail.full_attributed_cost.accounting_basis?.status || 'unknown'}</strong></p>
    <details><summary>Recorded costs and later receipts</summary>
      <table className="data-table"><thead><tr><th>Quantity</th><th>Attributed total</th></tr></thead><tbody>
        {Object.entries(quantities).map(([axis, raw]) => { const value = raw as Json; return <tr key={axis}>
          <td>{axis.replaceAll('_', ' ')}</td><td>{value.total == null ? `Unknown (${value.known} measured)` : value.total}</td></tr>; })}
      </tbody></table>
      {!!sources.length && <>
        <p className="help-text">A later receipt can resolve unknown costs. Original events stay unchanged, and a cumulative total does not estimate each experiment's share.</p>
        <form onSubmit={submit}>
          <Field label="Cost source"><select value={source?.source_id || ''} onChange={e => { setSourceId(e.target.value); setStop(''); setValues({}); setReceipt(''); }}>
            {sources.map(row => <option key={row.source_id} value={row.source_id}>{row.source_id}</option>)}
          </select></Field>
          <Field label="Receipt covers first cost entries"><input type="number" min="1" max={source?.observed_stop} required value={boundary} onChange={e => setStop(e.target.value)} /></Field>
          <p className="help-text">Use the cost boundary covered by the receipt. Leave unreported quantities blank.</p>
          <div className="form-grid">{source?.axes.map((axis: string) => <Field key={axis} label={`Measured ${axis.replaceAll('_', ' ')}`}>
            <input type="number" min="0" step="any" value={values[axis] || ''} onChange={e => setValues(previous => ({ ...previous, [axis]: e.target.value }))} />
          </Field>)}</div>
          <Field label="Supporting receipt record IDs"><input required value={evidence} onChange={e => setEvidence(e.target.value)} /></Field>
          <p className="help-text">Reference saved findings, receipt snapshots or imported evidence that support these quantities.</p>
          <Field label="Cost reconciliation rationale"><textarea required value={rationale} onChange={e => setRationale(e.target.value)} /></Field>
          <ErrorNotice text={error} />
          <button className="button secondary" disabled={busy || !Object.values(values).some(value => value !== '')}>Record cost receipt</button>
          {receipt && <p role="status">Cost receipt recorded: {receipt}</p>}
        </form>
        <details><summary>Cost boundaries and receipt history</summary><pre>{JSON.stringify(sources, null, 2)}</pre></details>
      </>}
    </details>
  </>;
}
