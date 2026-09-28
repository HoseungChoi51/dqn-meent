import { useEffect, useRef, useState } from 'react';
import type { Json } from './api';
import { errorText } from './api';
import { ErrorNotice } from './ui';

type Line = { event: Json; raw: string; bytes: number };
const MAX_EVENTS = 2000, MAX_BYTES = 2 * 1024 * 1024;
const encoder = new TextEncoder();
function line(event: Json, raw = JSON.stringify(event)) { return { event, raw: raw.trimEnd(), bytes: encoder.encode(raw).length }; }
function bounded(rows: Line[], fromEnd = true): Line[] {
  const chosen: Line[] = []; let bytes = 0;
  for (const row of fromEnd ? [...rows].reverse() : rows) {
    if (chosen.length >= MAX_EVENTS || bytes + row.bytes > MAX_BYTES) break;
    chosen.push(row); bytes += row.bytes;
  }
  return fromEnd ? chosen.reverse() : chosen;
}

export function AgentLog({ campaignId }: { campaignId: string }) {
  const [rows, setRows] = useState<Line[]>([]), [status, setStatus] = useState<Json>({});
  const [connection, setConnection] = useState('Connecting'), [error, setError] = useState('');
  const [follow, setFollow] = useState(true), [wrap, setWrap] = useState(false);
  const [search, setSearch] = useState(''), [actor, setActor] = useState(''), [task, setTask] = useState(''), [type, setType] = useState('');
  const [selected, setSelected] = useState<Json | null>(null), [pending, setPending] = useState(0);
  const [retry, setRetry] = useState(0);
  const cursor = useRef(0), following = useRef(true), buffer = useRef<Line[]>([]), stream = useRef<EventSource | null>(null);
  const bottom = useRef<HTMLDivElement>(null), generation = useRef(0);
  const base = `/api/campaigns/${encodeURIComponent(campaignId)}/agent-log`;

  useEffect(() => {
    const run = ++generation.current;
    let active = true, initialized = false;
    cursor.current = 0; buffer.current = []; following.current = true;
    setRows([]); setStatus({}); setSelected(null); setFollow(true); setPending(0); setError('');
    setConnection('Connecting');
    async function start() {
      try {
        const response = await fetch(base);
        if (!response.ok) throw new Error(`Log request failed (${response.status})`);
        const page = await response.json();
        if (!active) return;
        cursor.current = page.next_cursor;
        setRows(page.events.map((event: Json, index: number) => line(event, page.lines[index])));
        setStatus(page);
        initialized = true;
        connect(cursor.current);
      } catch (e) { if (active) { setError(errorText(e)); setConnection('Unavailable'); } }
    }
    function connect(after: number) {
      const source = new EventSource(`${base}/stream?after=${after}`);
      stream.current = source;
      source.onopen = () => active && setConnection('Live');
      source.onerror = () => active && setConnection('Reconnecting');
      source.addEventListener('line', message => {
        if (!active || !following.current) return;
        try {
          const event = JSON.parse((message as MessageEvent).data);
          if (event.seq <= cursor.current) return;
          if (event.seq !== cursor.current + 1) {
            setError(`Log gap after ${cursor.current}; reconnecting from that cursor.`);
            source.close(); connect(cursor.current); return;
          }
          cursor.current = event.seq;
          buffer.current.push(line(event, (message as MessageEvent).data));
          // Native SSE backpressure is bounded by flushing to the capped view.
          if (buffer.current.length >= 200 || buffer.current.reduce((sum, row) => sum + row.bytes, 0) >= MAX_BYTES) flush();
        } catch (e) { setError(errorText(e)); }
      });
      source.addEventListener('status', message => {
        if (active) setStatus(JSON.parse((message as MessageEvent).data));
      });
    }
    function flush() {
      if (!active || !buffer.current.length) return;
      const batch = buffer.current.splice(0);
      setRows(old => bounded([...old, ...batch]));
    }
    const timer = window.setInterval(flush, 150);
    // Paused views keep no unbounded line buffer. Poll only metadata/tail and
    // reconnect at the saved cursor when following resumes.
    const poll = window.setInterval(async () => {
      if (following.current || !active) return;
      try {
        const response = await fetch(`${base}?limit=1`);
        if (!response.ok) return;
        const page = await response.json();
        if (active) { setStatus(page); setPending(Math.max(0, page.latest_seq - cursor.current)); }
      } catch { /* Connection state remains paused; the next poll can recover. */ }
    }, 2000);
    void start();
    // Switching follow mode changes only the subscription, never scientific work.
    const controls = window.setInterval(() => {
      if (!active || !initialized || generation.current !== run) return;
      if (!following.current && stream.current) { flush(); stream.current.close(); stream.current = null; setConnection('Paused view'); }
      if (following.current && !stream.current && cursor.current >= 0) { setPending(0); connect(cursor.current); }
    }, 200);
    return () => { active = false; stream.current?.close(); stream.current = null; clearInterval(timer); clearInterval(poll); clearInterval(controls); };
  }, [base, retry]);

  useEffect(() => { if (follow) bottom.current?.scrollIntoView({ block: 'nearest' }); }, [rows, follow]);
  function toggleFollow() {
    if (!follow && rows.length) cursor.current = rows[rows.length - 1].event.seq;
    following.current = !follow; setFollow(!follow);
  }
  async function older() {
    following.current = false; setFollow(false);
    const first = rows[0]?.event.seq;
    if (!first || first <= 1) return;
    try {
      const response = await fetch(`${base}?before=${first}&limit=200`);
      if (!response.ok) throw new Error(`Log request failed (${response.status})`);
      const page = await response.json();
      setRows(old => bounded([...page.events.map((event: Json, index: number) => line(event, page.lines[index])), ...old], false));
    } catch (e) { setError(errorText(e)); }
  }
  const visible = rows.filter(row => (!actor || row.event.agent_id === actor) && (!task || row.event.task_id === task)
    && (!type || row.event.event_type === type) && (!search || row.raw.toLowerCase().includes(search.toLowerCase())));
  const options = (key: string) => [...new Set(rows.map(row => row.event[key]).filter(Boolean))].sort();
  return <section className="panel agent-log" aria-label="Agent work log">
    <div className="row-between"><strong>Agent work log</strong><span role="status">{connection} · sequence {status.projected_seq ?? 0}{pending ? ` · ${pending} new events` : ''}</span></div>
    <p><code>{status.path || 'Loading log path…'}</code></p>
    <p>Requests, reported rationale, ordinary outputs, tools and handoffs. Provider-private reasoning is unavailable.</p>
    <ErrorNotice text={error || status.error || ''} />
    {status.lag > 0 && <p role="status">{status.lag} committed events awaiting file projection.</p>}
    <div className="agent-log-controls">
      <button className="button secondary" onClick={toggleFollow}>{follow ? 'Pause scrolling' : 'Resume live'}</button>
      {connection === 'Unavailable' && <button className="button secondary" onClick={() => setRetry(n => n + 1)}>Reconnect</button>}
      <button className="button secondary" onClick={() => void older()} disabled={!rows.length || rows[0].event.seq <= 1}>Load older</button>
      <button className="button secondary" onClick={() => void navigator.clipboard.writeText(visible.map(row => row.raw).join('\n')).catch(e => setError(errorText(e)))}>Copy loaded lines</button>
      <a className="button secondary" href={`${base}/download`} download>Download log</a>
      <label><input type="checkbox" checked={wrap} onChange={e => setWrap(e.target.checked)} /> Wrap lines</label>
      <label>Agent <select aria-label="Agent" value={actor} onChange={e => setActor(e.target.value)}><option value="">All</option>{options('agent_id').map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Task <select aria-label="Task" value={task} onChange={e => setTask(e.target.value)}><option value="">All</option>{options('task_id').map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Event <select aria-label="Event" value={type} onChange={e => setType(e.target.value)}><option value="">All</option>{options('event_type').map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Search loaded lines <input value={search} onChange={e => setSearch(e.target.value)} /></label>
    </div>
    <p>{visible.length} matching / {rows.length} loaded events. Older events remain in the file.</p>
    <div className="agent-log-lines" style={{ whiteSpace: wrap ? 'pre-wrap' : 'pre' }} aria-label="Live JSONL content">
      {visible.map(row => <div key={row.event.event_id}><button className="agent-log-inspect" title="Inspect event" onClick={() => setSelected(row.event)}>+</button>{row.raw}</div>)}
      {!rows.length && <div>No agent activity recorded yet.</div>}<div ref={bottom} />
    </div>
    {selected && <details open><summary>Event {selected.seq} · {selected.event_type}</summary><pre>{JSON.stringify(selected, null, 2)}</pre>
      {selected.payload_ref && <a href={`${base}/payloads/${selected.payload_ref}`} target="_blank" rel="noreferrer">Open complete redacted payload ({selected.payload_bytes} bytes)</a>}
    </details>}
  </section>;
}
