"""Conservative confirmation cohorts and immutable finalist protocols.

Exposure starts when a confirmation job is accepted, before its first result.
Physical conditions retain exposure across renamed tasks and charter revisions.
Nominated members of the original cohort may collect additional seeds; later
ideas need fresh physical conditions. This is a software provenance rule, not a
claim that the researcher has never seen a similar problem outside this service.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import sys

from optimization_framework.evaluation.registry import problems
from optimization_framework.execution.source import scientific_hash, runtime_identity, snapshot
from optimization_framework.storage.sqlite import now


SCIENTIFIC_FILES = ("config.py", "physics.py", "dqn.py", "training.py", "workspace/optimizers.py",
                    "workspace/custom_optimizer.py", "workspace/worker.py")


def _json(value):
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def scientific_source_hash(source_root=None):
    return scientific_hash(source_root)


def scientific_environment():
    return runtime_identity()


def physical_condition_key(physics):
    return problems.resolve("meent_grating", physics).scientific_identity


def _nomination_identity(hypothesis):
    return _json({"algorithm": hypothesis.get("algorithm"),
                  **({"implementation_version_id": hypothesis["implementation_version_id"]} if hypothesis.get("implementation_version_id") else {}),
                  "algorithm_config": hypothesis.get("algorithm_config", {}),
                  "candidate_source_hash": hashlib.sha256(hypothesis.get("source", "").encode()).hexdigest()
                  if hypothesis.get("source") else None})


def nominate_finalist(hypothesis):
    """Freeze scientific implementation identity once; re-nomination cannot reset it."""
    result = copy.deepcopy(hypothesis)
    identity = _nomination_identity(hypothesis)
    if result.get("frozen_identity"):
        if result["frozen_identity"] != identity:
            raise ValueError("This finalist identity changed after nomination; fork a new hypothesis")
    else:
        result.update(frozen_identity=identity, frozen_at=now(),
                      frozen_config=identity["algorithm_config"],
                      frozen_scientific_source_hash=scientific_source_hash(),
                      frozen_environment=scientific_environment())
    result["status"] = "finalist"
    return result


def _condition_id(campaign_id, condition_key):
    return "confirmation_" + campaign_id + "_" + condition_key


def condition_exposure(store, campaign_id, physics):
    key = physical_condition_key(physics)
    record_id = _condition_id(campaign_id, key)
    try:
        return store.get(record_id, "confirmation_condition")
    except KeyError:
        pass
    inherited = [item for item in store.list("implementation_exposure", campaign_id) if item["condition_key"] == key]
    if inherited:
        return {"id": record_id, "campaign_id": campaign_id, "physical_condition_key": key,
                "exposed": True, "exposed_at": min(item["created_at"] for item in inherited), "cohort": {},
                "reason": "Reused implementation was developed or validated on this physical condition",
                "implementation_version_ids": sorted({item["implementation_version_id"] for item in inherited})}
    previous = [trial for trial in store.list("trial", campaign_id)
                if trial.get("physics") is not None and trial.get("algorithm") != "validate" and
                physical_condition_key(trial["physics"]) == key]
    if previous:
        return {"id": record_id, "campaign_id": campaign_id, "physical_condition_key": key,
                "exposed": True, "exposed_at": min(t.get("created_at", now()) for t in previous),
                "cohort": {}, "reason": "Existing numerical trials predate this confirmation protocol",
                "prior_trial_ids": [t["id"] for t in previous]}
    return None


def task_exposure_fields(store, campaign_id, physics):
    exposed = condition_exposure(store, campaign_id, physics)
    return {"physical_condition_key": physical_condition_key(physics), "exposed": bool(exposed),
            "exposed_at": exposed.get("exposed_at") if exposed else None,
            "confirmation_cohort_ids": list(exposed.get("cohort", {})) if exposed else []}


def _protocol(trial):
    return _json({"algorithm": trial["algorithm"], "algorithm_config": trial["algorithm_config"],
                  "training": {k: v for k, v in trial["training"].items() if k != "seed"},
                  "schedule_steps": trial["schedule_steps"], "max_steps": trial["max_steps"],
                  "wall_seconds": trial["wall_seconds"]})


def prepare_confirmation(store, campaign, task, hypothesis, trial):
    """Validate without mutating records; caller commits before queuing a worker."""
    if not hypothesis or hypothesis.get("status") != "finalist" or not hypothesis.get("frozen_identity"):
        raise ValueError("Nominate this finalist with a frozen implementation before confirmation")
    if hypothesis["frozen_identity"] != _nomination_identity(hypothesis):
        raise ValueError("Finalist implementation changed after nomination; use a new hypothesis and fresh conditions")
    if scientific_source_hash() != hypothesis.get("frozen_scientific_source_hash"):
        raise ValueError("Scientific source changed after finalist nomination; nominate a new version before confirmation")
    if scientific_environment() != hypothesis.get("frozen_environment"):
        raise ValueError("Numerical dependencies changed after finalist nomination; nominate a new version")
    expected = dict(hypothesis["frozen_config"])
    if hypothesis["algorithm"] == "custom":
        expected["source"] = hypothesis.get("source")
    if trial["algorithm"] != hypothesis["algorithm"] or _json(trial["algorithm_config"]) != _json(expected):
        raise ValueError("Confirmatory runs must match the frozen finalist algorithm and parameters")
    condition = condition_exposure(store, campaign["id"], task["physics"])
    if condition is None:
        cohort = {}
        for candidate in store.list("hypothesis", campaign["id"]):
            if candidate.get("status") == "finalist" and candidate.get("frozen_identity"):
                cohort[candidate["id"]] = {"identity": candidate["frozen_identity"],
                                            "scientific_source_hash": candidate.get("frozen_scientific_source_hash"),
                                            "nominated_at": candidate.get("frozen_at")}
        key = physical_condition_key(task["physics"])
        condition = {"id": _condition_id(campaign["id"], key), "campaign_id": campaign["id"],
                     "physical_condition_key": key, "exposed": True, "exposed_at": now(), "cohort": cohort,
                     "reason": "Conservatively exposed at first confirmation launch", "first_trial_id": trial["id"]}
    member = condition.get("cohort", {}).get(hypothesis["id"])
    if member is None:
        raise ValueError("This physical condition was already exposed before this finalist joined its cohort; choose fresh test conditions")
    if (member["identity"] != hypothesis["frozen_identity"] or
            member["scientific_source_hash"] != hypothesis["frozen_scientific_source_hash"]):
        raise ValueError("Finalist no longer matches the cohort frozen before exposure")
    protocol = _protocol(trial)
    frozen_protocol = hypothesis.get("confirmation_protocol")
    if frozen_protocol is not None and frozen_protocol != protocol:
        raise ValueError("Confirmation training, schedule, request budget, and time allocation are frozen; fork a new strategy and use fresh conditions")
    updated = copy.deepcopy(hypothesis)
    updated.setdefault("confirmation_protocol", protocol)
    updated.setdefault("confirmation_protocol_frozen_at", now())
    return {"condition": condition, "hypothesis": updated,
            "trial_fields": {"confirmation_condition_id": condition["id"],
                             "confirmation_protocol_hash": _hash(protocol),
                             "scientific_source_hash": hypothesis["frozen_scientific_source_hash"],
                             "scientific_environment": hypothesis["frozen_environment"],
                             "confirmation_exposure_at": condition["exposed_at"]}}


def commit_confirmation(store, plan):
    condition = plan["condition"]
    store.put("confirmation_condition", condition, "confirmation.condition_exposed")
    store.put("hypothesis", plan["hypothesis"], "hypothesis.confirmation_frozen")
    for task in store.list("task", condition["campaign_id"]):
        if physical_condition_key(task["physics"]) == condition["physical_condition_key"]:
            task.update(exposed=True, exposed_at=condition["exposed_at"],
                        physical_condition_key=condition["physical_condition_key"],
                        confirmation_cohort_ids=list(condition["cohort"]))
            store.put("task", task, "task.exposed")


def snapshot_confirmation_code(directory, expected_scientific_hash):
    """Pin source when the protocol is accepted, closing the queued-code-change gap."""
    destination = Path(directory) / "code" / "dqn_meent"
    source_digest = snapshot(directory)
    if scientific_source_hash(destination) != expected_scientific_hash:
        raise ValueError("Scientific source changed while snapshotting the confirmation run; no work was queued")
    return source_digest
