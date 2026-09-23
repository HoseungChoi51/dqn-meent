import { useEffect, useState } from 'react';
import { api, percent } from './api';
import type { Json, Trial } from './api';
import { Empty } from './ui';

export const colors = ['#23776d', '#cf874e', '#667daa', '#aa7391', '#7c9251', '#7e74af', '#639ba7', '#8c7963'];

export function useMetrics(trials: Trial[]) {
  const [records, setRecords] = useState<Record<string, Json[]>>({});
  const [error, setError] = useState('');
  const ids = trials.map(t => t.id).join(',');
  useEffect(() => {
    let canceled = false;
    async function load() {
      if (!ids) { setRecords({}); return; }
      const results = await Promise.allSettled(ids.split(',').map(async id => [id, await api<Json[]>(`/api/trials/${id}/metrics`)] as const));
      if (canceled) return;
      const valid = results.flatMap(r => r.status === 'fulfilled' ? [r.value] : []);
      setRecords(previous => ({ ...previous, ...Object.fromEntries(valid) }));
      setError(results.some(r => r.status === 'rejected') ? 'Some metric histories are unavailable. Previously loaded data is retained.' : '');
    }
    void load();
    const timer = window.setInterval(load, 4000);
    return () => { canceled = true; window.clearInterval(timer); };
  }, [ids]);
  return { records, error };
}

export function QualityChart({ trials, records, x = 'elapsed_seconds', compact = false, labels = {} }: { trials: Trial[]; records: Record<string, Json[]>; x?: string; compact?: boolean; labels?: Record<string, string> }) {
  const width = 820, height = compact ? 240 : 330;
  const margin = { top: 24, right: 25, bottom: 43, left: 53 };
  const w = width - margin.left - margin.right, h = height - margin.top - margin.bottom;
  const series = trials.map((trial, i) => ({ trial, color: colors[i % colors.length], points: (records[trial.id] || []).filter(row => Number.isFinite(row[x]) && Number.isFinite(row.best_efficiency)) }));
  const points = series.flatMap(s => s.points);
  if (!points.length) return <Empty icon="comparison" title="Evidence starts with a run">Evaluated results will appear here as trials report progress. No performance is estimated.</Empty>;
  const maxX = Math.max(x === 'elapsed_seconds' ? .001 : 1, ...points.map(p => p[x]));
  const maxY = Math.min(1, Math.max(0.1, Math.ceil(Math.max(...points.map(p => p.best_efficiency)) * 10) / 10));
  const sx = (n: number) => margin.left + n / maxX * w;
  const sy = (n: number) => margin.top + h - n / maxY * h;
  return <div className="quality-chart"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Best observed efficiency against ${x === 'elapsed_seconds' ? 'elapsed time' : x === 'solver_calls' ? 'actual solver calls' : 'evaluation requests'} for ${series.length} trials`}><title>Measured learning curves, preliminary solver fidelity</title>{[0, .25, .5, .75, 1].map(f => <g key={f}><line x1={margin.left} x2={width - margin.right} y1={sy(f * maxY)} y2={sy(f * maxY)} className="chart-grid" /><text x={margin.left - 12} y={sy(f * maxY) + 4} textAnchor="end" className="chart-label">{percent(f * maxY, 0)}</text><text x={sx(f * maxX)} y={height - 17} textAnchor="middle" className="chart-label">{(f * maxX).toFixed(x === 'elapsed_seconds' && maxX < 10 ? 1 : 0)}{x === 'elapsed_seconds' ? 's' : ''}</text></g>)}{series.map(({ trial, points: rows, color }) => rows.length ? <g key={trial.id}><path d={rows.filter((p, i) => i === 0 || i === rows.length - 1 || p.best_efficiency !== rows[i - 1].best_efficiency).map((p, i) => i ? `H${sx(p[x])}V${sy(p.best_efficiency)}` : `M${sx(p[x])},${sy(p.best_efficiency)}`).join(' ')} fill="none" stroke={color} strokeWidth="2.6" strokeLinejoin="round" strokeLinecap="round" /><circle cx={sx(rows.at(-1)![x])} cy={sy(rows.at(-1)!.best_efficiency)} r="3.5" fill={color}><title>{labels[trial.id] || trial.algorithm}: {percent(rows.at(-1)!.best_efficiency)}</title></circle></g> : null)}</svg><div className="chart-legend">{series.filter(s => s.points.length).map(s => <span key={s.trial.id}><i style={{ background: s.color }} />{labels[s.trial.id] || s.trial.algorithm.replaceAll('_', ' ')}<small>seed {s.trial.seed}</small></span>)}</div></div>;
}

export function CostScatter({ trials }: { trials: Trial[] }) {
  const rows = trials.filter(t => Number.isFinite(t.progress?.best_efficiency) && Number.isFinite(t.progress?.elapsed_seconds));
  if (!rows.length) return <p className="muted chart-no-data">No measured endpoints yet.</p>;
  const maxT = Math.max(.001, ...rows.map(t => t.progress.elapsed_seconds));
  const maxQ = Math.min(1, Math.max(.1, Math.ceil(Math.max(...rows.map(t => t.progress.best_efficiency)) * 10) / 10));
  return <svg className="scatter" viewBox="0 0 460 260" role="img" aria-label="Measured efficiency versus compute time for each trial"><title>Each dot is one trial endpoint; higher and farther left indicates better measured efficiency for less time.</title>{[0, .5, 1].map(f => <g key={f}><line x1="46" x2="434" y1={220 - f * 192} y2={220 - f * 192} className="chart-grid" /><text x="37" y={225 - f * 192} textAnchor="end" className="chart-label">{percent(maxQ * f, 0)}</text><text x={46 + f * 388} y="245" textAnchor="middle" className="chart-label">{(maxT * f).toFixed(maxT < 1 ? 3 : maxT < 10 ? 1 : 0)}s</text></g>)}{rows.map((t, i) => <circle key={t.id} cx={46 + t.progress.elapsed_seconds / maxT * 388} cy={220 - t.progress.best_efficiency / maxQ * 192} r="6" fill={colors[i % colors.length]} fillOpacity=".8" stroke="white" strokeWidth="1.5"><title>{t.algorithm} · seed {t.seed}: {percent(t.progress.best_efficiency)} in {t.progress.elapsed_seconds.toFixed(1)}s</title></circle>)}</svg>;
}
