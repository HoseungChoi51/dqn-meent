import { useEffect, useState } from 'react';
import { api, errorText } from './api';
import type { Json, State } from './api';
import { useCommand } from './commands';
import { Badge, ErrorNotice, Field, Panel } from './ui';

export function BundleTransfers({ state, assets, refresh }: { state: State; assets: Json[]; refresh: () => Promise<void> }) {
  const command = useCommand(state.campaign);
  const [selected, setSelected] = useState(''), [file, setFile] = useState<File | null>(null);
  const [operations, setOperations] = useState<Json[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const campaign = state.campaign?.id;
  useEffect(() => {
    let active = true, pending = false;
    setOperations([]); setSelected(''); setError('');
    async function read() {
      if (!campaign || pending) return;
      pending = true;
      try {
        const value = await api<Json[]>(`/api/v1/bundles/operations?campaign_id=${campaign}`);
        if (active) setOperations(value);
      } catch (e) { if (active) setError(errorText(e)); }
      finally { pending = false; }
    }
    void read();
    const timer = window.setInterval(() => void read(), 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [campaign]);
  async function perform(operation: string, payload: Json) {
    setBusy(true); setError('');
    try {
      const result = await command(operation, payload);
      const completed = await api<Json>(`/api/v1/bundles/operations/${result.operation_id}`);
      setOperations(previous => [...previous.filter(item => item.id !== completed.id), completed]);
      await refresh();
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }
  async function inspect() {
    if (!file) return;
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/v1/bundle-uploads', { method: 'POST', body: file,
        headers: { 'Content-Type': 'application/vnd.optimization.evidence+zip' } });
      const uploaded = await response.json();
      if (!response.ok) throw new Error(uploaded.detail || 'Bundle upload failed');
      await perform('bundle.inspect', { upload_id: uploaded.upload_id });
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }
  return <Panel title="Portable evidence">
    <p className="help-text">Export an asset with its declared dependencies, captured source, exposure and costs. Inspect an incoming bundle before adding its history to this campaign.</p>
    <div className="form-grid">
      <Field label="Asset to export"><select value={selected} onChange={e => setSelected(e.target.value)}>
        <option value="">Select an asset…</option>{assets.map(asset => <option key={asset.id} value={asset.id}>{asset.title} · {asset.id.slice(-8)}</option>)}
      </select></Field>
      <div className="field"><button className="button secondary" disabled={busy || !selected || !campaign}
        onClick={() => void perform('bundle.export', { asset_ids: [selected] })}>Export asset and dependencies</button></div>
      <Field label="Evidence bundle"><input type="file" accept=".zip,application/vnd.optimization.evidence+zip"
        onChange={e => setFile(e.target.files?.[0] || null)} /></Field>
      <div className="field"><button className="button secondary" disabled={busy || !file || !campaign} onClick={() => void inspect()}>Inspect bundle</button></div>
    </div>
    <ErrorNotice text={error} />
    <div className="run-list">{[...operations].reverse().slice(0, 12).map(operation => <article key={operation.id}>
      <div className="row-between"><h3>{operation.action === 'export' ? 'Evidence export' : operation.action === 'inspect' ? 'Bundle inspection' : 'Historical import'}</h3><Badge>{operation.status}</Badge></div>
      {operation.error && <p className="callout amber">{operation.error}</p>}
      {operation.summary && <>
        <p>{operation.summary.record_count} records · {operation.summary.blob_count} captured files · provenance {operation.summary.provenance} · accounting {operation.summary.accounting}</p>
        <p className="help-text">{operation.summary.execution}</p>
        {!!operation.summary.missing?.length && <details><summary>Missing historical evidence</summary><pre>{JSON.stringify(operation.summary.missing, null, 2)}</pre></details>}
        <details><summary>Bundle identity and scope</summary><pre>{JSON.stringify(operation.summary, null, 2)}</pre></details>
      </>}
      {operation.status === 'completed' && operation.action === 'export' && <a className="button secondary"
        href={`/api/v1/bundles/operations/${operation.id}/download`} download>Download evidence bundle</a>}
      {operation.status === 'completed' && operation.action === 'inspect' && <button className="button secondary" disabled={busy}
        onClick={() => void perform('bundle.publish', { inspection_id: operation.inspection_id })}>Import inspected history</button>}
    </article>)}</div>
  </Panel>;
}
