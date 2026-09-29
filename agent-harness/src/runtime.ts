import { createAgentSession, createExtensionRuntime, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import fs from 'node:fs';
import path from 'node:path';

// Explicit resources: neither host extensions nor repository instructions are
// discovered. All tools are scoped by the application gateway, not a prompt.
export async function createRuntime(options: any) {
  const modelRuntime = await ModelRuntime.create({authPath: options.authPath, refreshOnCreate: false});
  await modelRuntime.refresh({providers: ['openai-codex'], allowNetwork: false});
  const model = modelRuntime.getModel('openai-codex', options.model);
  if (!model) throw new Error(`Model unavailable: openai-codex/${options.model}`);
  const loader: any = {
    getExtensions: () => ({extensions: [], errors: [], runtime: createExtensionRuntime()}),
    getSkills: () => ({skills: [], diagnostics: []}), getPrompts: () => ({prompts: [], diagnostics: []}),
    getThemes: () => ({themes: [], diagnostics: []}), getAgentsFiles: () => ({agentsFiles: []}),
    getSystemPrompt: () => options.instructions, getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [], getAppendSystemPromptSources: () => [],
    extendResources: () => {}, reload: async () => {},
  };
  const sessionDir = path.join(options.directory, 'sessions');
  fs.mkdirSync(sessionDir, {recursive: true, mode: 0o700});
  const manager = options.sessionFile ? SessionManager.open(options.sessionFile)
    : SessionManager.create(options.directory, sessionDir);
  const {session, modelFallbackMessage} = await createAgentSession({
    cwd: options.directory, modelRuntime, model, thinkingLevel: options.effort,
    resourceLoader: loader, tools: options.tools.map(t => t.name), customTools: options.tools,
    sessionManager: manager,
    settingsManager: SettingsManager.inMemory({compaction: {enabled: true, reserveTokens: 16384, keepRecentTokens: 20000},
      retry: {enabled: true, maxRetries: 3, baseDelayMs: 2000}}),
  });
  if (modelFallbackMessage) { session.dispose(); throw new Error(modelFallbackMessage); }
  return session;
}

export async function authStatus(authPath: string) {
  const runtime = await ModelRuntime.create({authPath, refreshOnCreate: false});
  // hasConfiguredAuth() is a snapshot populated by refresh(); construction
  // deliberately skips that refresh. Read persisted credentials instead, also
  // picking up sign-in through the browser or a separate CLI process.
  const auth = await runtime.checkAuth('openai-codex');
  return {configured: Boolean(auth), provider: 'openai-codex',
    models: runtime.getModels('openai-codex').map(m => m.id)};
}
