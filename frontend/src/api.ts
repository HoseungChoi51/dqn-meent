export type Json = Record<string, any>;
export type Task = { id: string; name: string; physics: Json; split: string };
export type Algorithm = { id: string; name: string; description: string; parameters?: Json };
export type Trial = {
  id: string; campaign_id: string; task_id: string; hypothesis_id?: string; algorithm: string;
  status: string; seed: number; max_steps: number; wall_seconds: number; priority: number;
  created_at: string; updated_at?: string; progress: Json; result?: Json; validation?: Json;
  algorithm_config?: Json; error?: string; experiment_question?: string; question?: string; reason?: string; physics?: Json; task_name?: string; charter_version?: number; source_hash?: string; schedule_steps?: number; execution_seconds?: number; training?: Json;
};
export type Hypothesis = {
  id: string; title: string; mechanism: string; rationale: string; assumptions: (string | Json)[];
  risks: string[]; origin?: string; source?: string; executable?: boolean; sources: (string | Json)[]; parent_ids: string[]; algorithm: string;
  algorithm_config: Json; status: string; reviews: Json[]; created_at: string;
  predictions?: string[]; cheapest_test?: string; novelty?: string; startup_cost?: string;
  change_summary?: string; feedback_response?: string;
  revision_context?: { hypothesis_id: string; reviews: Json[] };
};
export type Decision = {
  id: string; title: string; context: string; options: (string | { id: string; label: string; description?: string })[];
  recommendation: string; status: string; choice?: string; comment?: string; created_at?: string;
};
export type Campaign = {
  id: string; name: string; objective: string; compute_budget_seconds: number;
  llm_budget_usd: number; autonomy: string; version?: number; compute_used_seconds?: number;
  llm_used_usd?: number; usage?: Json; created_at?: string; [key: string]: any;
};
export type ProviderStatus = {
  provider: string; model: string; billing_mode: 'subscription' | 'api';
  configured: boolean; enabled: boolean; status_reason?: string;
};
export type State = {
  campaigns: Campaign[]; campaign: Campaign | null; tasks: Task[]; hypotheses: Hypothesis[];
  trials: Trial[]; decisions: Decision[]; messages: Json[]; events: Json[]; algorithms: Algorithm[];
  settings: { llm_configured: boolean; model?: string; [key: string]: any }; research_runs: Json[];
  [key: string]: any;
};

export async function api<T = Json>(path: string, body?: unknown, method?: string): Promise<T> {
  const response = await fetch(path, {
    method: method || (body === undefined ? 'GET' : 'POST'),
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { const error = await response.json(); detail = typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail || error); } catch { /* Keep HTTP description. */ }
    throw new Error(detail);
  }
  return response.status === 204 ? undefined as T : response.json();
}

export const emptyState: State = { campaigns: [], campaign: null, tasks: [], hypotheses: [], trials: [], decisions: [], messages: [], events: [], algorithms: [], settings: { llm_configured: false }, research_runs: [] };
export const activeStatuses = ['queued', 'running', 'pausing', 'paused', 'resuming', 'stopping'];
export function percent(value: unknown, digits = 1): string { return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : '—'; }
export function seconds(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)}s`;
  if (value < 3600) return `${(value / 60).toFixed(1)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}
export function shortId(value: string): string { return value?.slice(0, 8) || '—'; }
export function when(value?: string): string { if (!value) return '—'; return new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
export function best(trial: Trial): number | undefined { return trial.progress?.best_efficiency ?? trial.result?.best_efficiency; }
export function errorText(error: unknown): string { return error instanceof Error ? error.message : String(error); }
export const physicsDefaults = { n_cells: 64, wavelength_nm: 1100, deflection_angle_deg: 50, thickness_nm: 325, n_incident: 1.45, n_exit: 1, material: 'constant', silicon_n: 3.551726470588235, silicon_k: 0, fourier_order: 15 };

export function providerStatus(state: State): ProviderStatus {
  const provider = state.settings.provider || {};
  return {
    provider: provider.provider || 'codex', model: provider.model || state.settings.model || 'gpt-6-sol',
    billing_mode: provider.billing_mode || 'subscription', configured: provider.configured ?? state.settings.llm_configured,
    enabled: provider.enabled ?? state.settings.llm_configured, status_reason: provider.status_reason,
  };
}

export function providerLabel(provider: ProviderStatus): string {
  const model = provider.model === 'gpt-6-sol' ? 'GPT6-sol' : provider.model;
  return `${provider.provider === 'codex' ? 'Codex' : provider.provider === 'openai_api' ? 'OpenAI API' : 'Model provider'} · ${model}`;
}
