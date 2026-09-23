# Grating Lab dashboard

The local React/TypeScript workspace for researcher-guided algorithm discovery. It uses the Python service's HTTP API and server-sent events; all charts and numerical summaries come from recorded experiments. Empty workspaces contain no demonstration measurements.

## Run

From this directory:

```sh
npm ci
npm run build
```

Start `uv run grating-lab` from the repository root and open `http://127.0.0.1:8765`. The Python service serves the production build. For frontend development, leave the Python service running and run `npm run dev`; Vite serves port 5173 and proxies `/api` to port 8765.

The workspace includes charter editing and history, strategy dossiers and lineage, custom optimizer source and verification, concurrent experiment controls, matched comparisons, researcher decisions, research conversation, source retrieval, and Markdown report export. Model credentials are configured in the server environment and never entered into the browser.

## Checks

```sh
npm run build
npm run test:e2e
```

The interaction tests use explicit API fixtures to verify browser behavior. They cover charter creation and strict revision payloads, trial lifecycle controls, decision rationale, and mobile navigation. Playwright uses `/usr/bin/google-chrome` when present; otherwise install its Chromium browser with `npx playwright install chromium`, or set `CHROME_PATH`.

For a real-service smoke check, start an isolated service from the repository root:

```sh
GRATING_LLM_DISABLED=true uv run grating-lab --directory /tmp/grating-browser-check --port 8765
```

Then run `npm run test:live` in this directory. This creates a test campaign, runs two tiny RCWA baseline experiments, requests a two-order convergence check, and opens comparative analysis and charter history. These small physics settings test integration; they do not establish physical accuracy or algorithm superiority. Model calls are disabled in this test service.

## Interpretation

Observed best-efficiency curves are step functions with no extrapolation. Overview curves share a configuration. Comparisons distinguish method configurations, code versions, charter versions, and search fidelity. Server-computed paired comparisons exclude ambiguous duplicate seeds and retain censored trials. The dashboard keeps preliminary search values separate from physical-convergence records and shows researcher interventions in the notebook.
