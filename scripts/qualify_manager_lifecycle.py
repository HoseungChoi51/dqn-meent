"""Actual two-service restart qualification with explicit, deterministic model fixtures.

HTTP, commands, independent package checks, numerical workers, costs and recovery
are real. Manager answers and semantic implementation review are fixtures. No LLM
is contacted. All data and subprocesses belong to the specified new directory.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import httpx

from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.sqlite import Store


ROOT = Path(__file__).resolve().parents[1]


def specification():
    return {"name": "Qualified continuous coordinate package", "mechanism": "Sample one coordinate of the incumbent.",
        "acceptance_criteria": ["Respect continuous bounds", "Restore the next candidate"],
        "problem_id": "bounded_continuous", "capabilities": ["continuous", "scalar_objective"],
        "n_cells_min": 2, "n_cells_max": 4,
        "behavior_checks": [{"name": "Retain the incumbent after deterioration", "n_cells": 2,
            "assertion": "one_coordinate_from_incumbent", "efficiencies": [-10, -20, -5, -50, 100]}]}


def package(*, invalid=False):
    source = (ROOT / "examples/implementation-reference/continuous_optimizer.py").read_text()
    if invalid:
        source = source.replace('return [self.rng.uniform(lo, hi) for lo, hi in self.bounds]', 'return [999.0 for lo, hi in self.bounds]')
    return {"files": [{"path": "optimizer.py", "content": source}]}


def serve(args):
    from optimization_framework.api.app import create_app
    from optimization_framework.implementations.client import ImplementationClient
    from optimization_framework.research import coordinator, engine
    from optimization_framework.storage.sqlite import identifier
    os.environ["GRATING_LLM_DISABLED"] = "true"
    os.environ["GRATING_IMPLEMENTATIONS_TOKEN_FILE"] = str(args.directory / "library/service.token")
    app = create_app(args.directory / "workspace", implementation_client=ImplementationClient(args.library_url))
    workspace = app.state.workspace
    coordinator.provider_status = lambda: {"configured": True, "provider": "qualification_fixture", "billing_mode": "subscription"}

    def model(request, context, emit):
        message = request["message"]
        actions = []
        usage = {"calls": 0, "billing_mode": "subscription", "api_cost_usd": 0,
            "input_tokens": 0, "output_tokens": 0, "elapsed_seconds": 0}
        if message in {"fixture_wait_for_guidance", "fixture_uncertain_call"}:
            if message == "fixture_uncertain_call":
                usage.update(calls=1, pending_reservation={"id": "fixture_uncertain_external_call"})
                emit({"type": "provider_call_reserved", "role": "qualification_fixture", "usage": usage})
            atomic_json(args.directory / (message + ".json"), {"waiting": True})
            while not (args.directory / (message + ".release")).exists():
                time.sleep(.1)
            actions.append({"kind": "command", "command_operation": "trial.create", "command_payload": {
                "task_id": context["tasks"][0]["id"], "algorithm": "coordinate", "max_steps": 2, "wall_seconds": 5}})
        elif message == "fixture_independent_probe":
            actions.append({"kind": "command", "command_operation": "trial.create", "command_payload": {
                "task_id": context["tasks"][0]["id"], "algorithm": "coordinate", "max_steps": 2, "wall_seconds": 5}})
        else:
            for item in context["experiment_drafts"]:
                draft, readiness = item["draft"], item["readiness"]
                if draft["title"] != "Durable missing-code experiment":
                    continue
                launches = [row for row in workspace.store.list("draft_launch", request["campaign_id"]) if row["draft_id"] == draft["id"]]
                grants = workspace.store.list("implementation_grant", request["campaign_id"])
                if readiness["ready"] and not launches:
                    actions.append({"kind": "command", "command_operation": "draft.launch", "command_payload": {
                        "draft_id": draft["id"], "expected_draft_revision": draft["revision"],
                        "expected_readiness_hash": readiness["readiness_hash"]}})
                elif not grants:
                    actions.append({"kind": "command", "command_operation": "implementation.commission", "command_payload": {
                        "hypothesis_id": draft["procedure"]["hypothesis_id"], "spec": specification(), "package": package(),
                        "compute_seconds": 30, "max_calls": 1, "api_budget_usd": 0}})
        for action in actions:
            action.update(id=identifier("fixture_action"), title="Qualification fixture: bounded next step",
                rationale="Deterministic lifecycle qualification; no scientific model reasoning is claimed.", status="proposed")
        result = {"mode": "qualification_fixture", "status": "completed", "actions": actions[:1],
            "hypotheses": [], "decisions": [], "messages": [{"role": "assistant", "content": "Recorded qualification fixture output."}],
            "usage": usage, "trace": [{"role": "qualification_fixture", "status": "completed"}]}
        emit({"type": "research_completed", "result": result})
        return result
    engine.run_research = model

    @app.post("/__qualification/duplicate-completion")
    def duplicate():
        grant = next(row for row in workspace.store.list("implementation_grant") if row["status"] == "completed")
        workspace.store.event(grant["campaign_id"], "implementation.ready", {"record_id": grant["id"]})
        return {"grant_id": grant["id"]}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, timeout_graceful_shutdown=2)


def qualify(args):
    directory = args.directory
    directory.mkdir(parents=True, exist_ok=False)
    processes, logs, ports = {}, [], {}
    for role in ("library", "workspace"):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            ports[role] = listener.getsockname()[1]
    base = "http://127.0.0.1:" + str(ports["workspace"])
    library = "http://127.0.0.1:" + str(ports["library"])
    environment = {**os.environ, "GRATING_LLM_DISABLED": "true", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    starts = {"library": 0, "workspace": 0}

    def wait(check, label, seconds=60):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                value = check()
                if value:
                    return value
            except httpx.TransportError:
                pass
            time.sleep(.2)
        raise AssertionError("Timed out: " + label)

    def start(role):
        starts[role] += 1
        log = (directory / f"{role}-{starts[role]}.log").open("wb")
        logs.append(log)
        if role == "library":
            command = [sys.executable, str(ROOT / "scripts/serve_commissioning_fixture.py"), "library",
                "--directory", str(directory), "--port", str(ports[role])]
        else:
            command = [sys.executable, str(Path(__file__).resolve()), "--serve-workspace", "--directory", str(directory),
                "--port", str(ports[role]), "--library-url", library]
        processes[role] = subprocess.Popen(command, stdout=log, stderr=log, env=environment, cwd=ROOT)
        endpoint = library + "/health" if role == "library" else base + "/api/state"
        wait(lambda: httpx.get(endpoint).is_success, role + " startup", seconds=20)

    def stop(role):
        process = processes.get(role)
        if process and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=20)

    def state():
        response = httpx.get(base + "/api/state", params={"campaign_id": "campaign_manager_qualification"})
        response.raise_for_status()
        return response.json()

    def submit(operation, payload, identity, *, campaign_id="campaign_manager_qualification"):
        revision = 0 if operation == "campaign.create" else state()["campaign"]["version"]
        envelope = {"id": identity, "operation": operation, "campaign_id": campaign_id,
            "expected_revision": revision, "payload": payload}
        response = httpx.post(base + "/api/v1/commands", json=envelope, timeout=30)
        assert response.is_success, (operation, response.text)
        atomic_json(directory / (identity + ".json"), response.json())
        return response.json()["outcome"]

    def snapshot(name):
        value = state()
        atomic_json(directory / (name + ".json"), value)
        return value

    try:
        start("library")
        start("workspace")
        submit("campaign.create", {"name": "Durable manager HTTP qualification", "autonomy": "delegated",
            "compute_budget_seconds": 120, "validation_reserve_seconds": 0,
            "implementation_compute_budget_seconds": 120, "delegated_trial_seconds": 30,
            "tasks": [{"name": "Bounded quadratic", "problem_id": "bounded_continuous", "configuration": {"dimensions": 2}}]}, "create_campaign")
        task = state()["tasks"][0]
        hypothesis = submit("hypothesis.create", {"title": "Missing coordinate code", "mechanism": "Continuous coordinate proposals",
            "algorithm": "custom"}, "create_idea")["hypothesis_id"]
        draft = submit("draft.save", {"title": "Durable missing-code experiment", "follow_proposal_implementation": True,
            "procedure": {"task_id": task["id"], "hypothesis_id": hypothesis, "wall_seconds": 10, "max_steps": 3}}, "save_missing_draft")["draft_id"]
        readiness = httpx.get(base + f"/api/v1/drafts/{draft}")
        assert readiness.is_success and not readiness.json()["readiness"]["ready"], readiness.text
        submit("research.start", {"message": "Commission the missing-code draft within its bounds."}, "ask_manager")
        grant = wait(lambda: next((row for row in state()["implementation_jobs"] if row.get("job_id")), None), "commission admitted")
        stop("workspace")
        # The independently owned implementation job finishes while the campaign
        # service is offline. Its receipt is consumed after both services restart.
        library_store = Store(directory / "library")
        wait(lambda: next((row for row in library_store.list("implementation_job")
            if row["id"] == grant["job_id"] and row["status"] == "completed" and row.get("accounting_final")), None), "independent implementation completion")
        stop("library")
        start("library")
        start("workspace")
        wait(lambda: next((row for row in state()["trials"] if row["status"] == "completed" and row.get("evidence_committed_at")), None), "draft launch and numerical evidence")
        wait(lambda: all(row["status"] not in {"running", "stopping"} for row in state()["research_runs"]), "manager settled")
        before = snapshot("after_implementation_restart")
        assert len(before["trials"]) == 1 and len(before["implementation_jobs"]) == 1
        count = len(before["research_runs"])
        response = httpx.post(base + "/__qualification/duplicate-completion")
        response.raise_for_status()
        stop("workspace")
        start("workspace")
        time.sleep(3)
        assert len(state()["research_runs"]) == count
        assert len(state()["trials"]) == 1

        # A real independent package check fails. The manager reports one scoped
        # issue while a separate manager-authorized numerical action still runs.
        bad = submit("hypothesis.create", {"title": "Package with a failing bound check", "mechanism": "Negative qualification control",
            "algorithm": "custom"}, "bad_idea")["hypothesis_id"]
        bad_grant = submit("implementation.commission", {"hypothesis_id": bad, "spec": specification(), "package": package(invalid=True),
            "compute_seconds": 30, "max_calls": 1}, "bad_package")["grant_id"]
        issue = wait(lambda: next((row for row in state()["manager_issues"] if row.get("affected") == bad_grant and row["status"] == "pending"), None), "scoped failure")
        submit("research.start", {"message": "fixture_independent_probe"}, "independent_probe")
        independent = wait(lambda: next((row["id"] for row in state()["trials"] if row["algorithm"] == "coordinate"), None), "independent manager action")
        wait(lambda: next((row for row in state()["trials"] if row["id"] == independent and row["status"] == "completed"), None), "independent experiment")
        assert next(row for row in state()["manager_issues"] if row["id"] == issue["id"])["status"] == "pending"
        submit("issue.resolve", {"issue_id": issue["id"], "choice": "deferred", "comment": "Retain the counterexample; keep the validated package.",
            "expected_revision": issue.get("revision", 1)}, "resolve_scoped_issue")
        wait(lambda: all(row["status"] not in {"running", "stopping"} for row in state()["research_runs"]), "failure discussion settled")
        snapshot("after_scoped_issue")

        submit("research.start", {"message": "fixture_wait_for_guidance"}, "pending_guidance_turn")
        wait(lambda: (directory / "fixture_wait_for_guidance.json").exists(), "inference fixture entered")
        context = state()["manager_context"]
        submit("context.edit", {"content": "Keep the original comparison. Defer all additional fixture probes.",
            "expected_revision": context["revision"]}, "guidance_during_turn")
        (directory / "fixture_wait_for_guidance.release").touch()
        wait(lambda: any(row.get("result", {}).get("stale_charter") for row in state()["research_runs"]), "stale result retained")
        wait(lambda: all(row["status"] not in {"running", "stopping"} for row in state()["research_runs"]), "fresh-context reconsideration")
        assert len(state()["trials"]) == 2
        snapshot("after_guidance_change")

        submit("research.start", {"message": "fixture_uncertain_call"}, "uncertain_turn")
        wait(lambda: (directory / "fixture_uncertain_call.json").exists(), "durable uncertain reservation")
        stop("workspace")
        start("workspace")
        uncertain = next(row for row in state()["research_runs"] if row["status"] == "needs_reconciliation")
        decision = next(row for row in state()["decisions"] if row.get("research_run_id") == uncertain["id"])
        count = len(state()["research_runs"])
        submit("research.start", {"message": "Resume after reconciling the earlier attempt."}, "queued_after_restart")
        time.sleep(3)
        assert len(state()["research_runs"]) == count
        submit("decision.resolve", {"decision_id": decision["id"], "choice": "close_reserved", "expected_resolution_revision": 0,
            "comment": "Keep the uncertain fixture receipt and do not replay it."}, "close_uncertain")
        wait(lambda: len(state()["research_runs"]) == count + 1, "pending inbox dispatched after reconciliation")
        wait(lambda: all(row["status"] not in {"running", "stopping"} for row in state()["research_runs"]), "final settled turn")
        final = snapshot("final_state")
        assert len(final["trials"]) == 2
        assert len([row for row in final["manager_issues"] if row.get("affected") == bad_grant]) == 1
        stop("workspace")
        stop("library")
        workspace_store = Store(directory / "workspace")
        report = {"status": "passed", "service_starts": starts, "actual_model_calls": 0,
            "mocked": ["campaign model results and one uncertain provider reservation", "implementation semantic reviewer"],
            "real": ["HTTP commands and receipts", "independent package checks", "numerical workers", "service restarts", "cost ledger"],
            "counts": {kind: len(workspace_store.list(kind)) for kind in ("manager_input", "manager_command", "research_run", "research_result",
                "work_command", "implementation_grant", "trial", "execution_attempt", "cost_event", "manager_issue")},
            "scope": "D2 operational lifecycle; does not qualify model reasoning quality or scientific superiority.",
            "uncertain_run_id": uncertain["id"], "scoped_issue_id": issue["id"], "draft_id": draft}
        atomic_json(directory / "qualification.json", report)
        print(json.dumps(report, indent=2))
    finally:
        for role in ("workspace", "library"):
            stop(role)
        for log in logs:
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--serve-workspace", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--library-url")
    args = parser.parse_args()
    args.directory = args.directory.resolve()
    serve(args) if args.serve_workspace else qualify(args)


if __name__ == "__main__":
    main()
