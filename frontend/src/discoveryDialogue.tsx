import type { Json } from './api';
import { useState } from 'react';

const label = (value: string) => value.replaceAll('_', ' ');

function EvidenceText({ value, sources, depth = 0 }: { value: unknown; sources: Json[]; depth?: number }) {
  if (value == null || value === '') return null;
  if (typeof value !== 'object') {
    const source = sources.find(row => row.id === value);
    return source ? <a href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}</a> :
      <span className="discovery-prose">{String(value)}</span>;
  }
  if (Array.isArray(value)) return <ul>{value.map((item, index) => <li key={index}><EvidenceText value={item} sources={sources} depth={depth + 1} /></li>)}</ul>;
  return <dl className="discovery-evidence-fields">{Object.entries(value as Json)
    .filter(([key, item]) => key !== 'schema_version' && item != null && item !== '' && !(Array.isArray(item) && !item.length))
    .map(([key, item]) => <div key={key}><dt>{label(key)}</dt><dd><EvidenceText value={item} sources={sources} depth={depth + 1} /></dd></div>)}</dl>;
}

export function DiscoveryDialogue({ view, sessionId, sources }: { view: Json; sessionId: string; sources: Json[] }) {
  const [showEarlier, setShowEarlier] = useState(false);
  const tasks = new Map<string, Json>((view.tasks || []).map((task: Json) => [task.id, task]));
  const allSteps = (view.steps || []).filter((step: Json) => step.session_id === sessionId)
    .sort((a: Json, b: Json) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  const latest = new Map<string, string>(allSteps.map((step: Json) => [step.task_id, step.id]));
  const steps = showEarlier ? allSteps : allSteps.filter((step: Json) => latest.get(step.task_id) === step.id);
  const saved = (view.artifacts || []).filter((item: Json) => item.session_id === sessionId && !item.stale);
  const outputs = saved.filter((item: Json) => ['literature_map', 'candidate_batch', 'review', 'synthesis'].includes(item.kind));
  return <section className="discovery-dialogue" aria-label="Scientific dialogue">
    <h3>Scientific dialogue</h3>
    <p>Actual agent reports in completion order. Open the work products and tool evidence to follow the argument. Exact requests and ordinary responses are retained in Agent log.</p>
    {!!outputs.length && <details open><summary>Saved scientific outputs ({outputs.length})</summary>
      <p>These work products passed record validation. Their scientific claims still require review and experiments.</p>
      {outputs.map((item: Json) => <details key={item.id}><summary>{label(item.kind)}: {item.title}</summary>
        <EvidenceText value={item.content} sources={sources} />
        {!!item.limitations?.length && <><strong>Limitations</strong><EvidenceText value={item.limitations} sources={sources} /></>}
        <a href={`#${item.id.replace(/_artifact_\d+$/, '')}`}>Read the agent response</a>
      </details>)}
    </details>}
    <label><input type="checkbox" checked={showEarlier} onChange={event => setShowEarlier(event.target.checked)} /> Include earlier responses and corrections</label>
    {!showEarlier && allSteps.length > steps.length && <p>{allSteps.length - steps.length} earlier responses retained. Showing each task's latest report.</p>}
    {!steps.length && <p>No model response has returned yet. The agenda below shows the current assignments.</p>}
    {steps.map((step: Json, index: number) => {
      const task = tasks.get(step.task_id), result = step.result;
      const receipts = (view.tool_receipts || []).filter((receipt: Json) =>
        showEarlier ? (view.tools || []).some((tool: Json) => tool.step_id === step.id && tool.id === receipt.request_id) : receipt.task_id === step.task_id);
      const corrections = (view.feedback || []).filter((item: Json) => item.step_id === step.id);
      return <article key={step.id} className="discovery-message" id={step.id}>
        <div className="row-between"><strong>{index + 1}. {label(task?.brief.role || 'agent')} → {task?.brief.role === 'campaign_manager' ? 'research team / researcher' : 'campaign manager'}</strong>
          <time>{new Date(step.created_at).toLocaleTimeString()}</time></div>
        <p className="meta-row">{label(task?.brief.stage || 'research')} · {step.usage?.model || 'configured model'} · response {(Number(step.id.match(/_step_(\d+)$/)?.[1]) || 0) + 1}</p>
        <details><summary>Assignment and inputs</summary>
          <p className="discovery-prose">{task?.brief.objective}</p>
          {(task?.dependencies || []).map((id: string) => <p key={id}>After {label(tasks.get(id)?.brief.role || id)}: {tasks.get(id)?.brief.objective}</p>)}
          <EvidenceText value={task?.brief.evidence_ids} sources={sources} />
        </details>
        <p className="discovery-prose">{result.summary}</p>
        {result.rationale && <div><strong>Reported rationale</strong><p className="discovery-prose">{result.rationale}</p></div>}
        {!!corrections.length && <p className="callout">Part of this response required correction. Saved work products are labeled below; the original validation feedback remains in the task history.</p>}
        {(result.artifacts || []).map((artifact: Json, i: number) => <details key={i}>
          <summary>{label(artifact.kind)}: {artifact.title} · {saved.some((item: Json) => item.id === `${step.id}_artifact_${i}`) ? 'saved' : 'not accepted in current evidence'}</summary>
          <EvidenceText value={artifact.content} sources={sources} />
          {!!artifact.limitations?.length && <><strong>Limitations</strong><EvidenceText value={artifact.limitations} sources={sources} /></>}
        </details>)}
        {!!result.dissent?.length && <div><strong>Disagreement</strong><EvidenceText value={result.dissent} sources={sources} /></div>}
        {!!result.questions_for_manager?.length && <div><strong>Unresolved questions</strong><EvidenceText value={result.questions_for_manager} sources={sources} /></div>}
        {!!result.proposed_tasks?.length && <div><strong>Manager's next assignments</strong><ul>{result.proposed_tasks.map((brief: Json) =>
          <li key={brief.key}><strong>{label(brief.role)}</strong>: {brief.objective}</li>)}</ul></div>}
        {(result.tools || []).map((tool: Json) => <details key={tool.key}><summary>Requested tool: {tool.tool}</summary><EvidenceText value={tool.arguments} sources={sources} /></details>)}
        {receipts.map((receipt: Json) => <details key={receipt.id}><summary>Returned evidence: {receipt.tool} · {receipt.status}</summary>
          <EvidenceText value={receipt.error || receipt.result} sources={sources} /></details>)}
      </article>;
    })}
  </section>;
}
