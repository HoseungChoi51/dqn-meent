"""Per-attempt deadline enforcement independent of a numerical call's progress.

This process owns no jobs and cannot allocate work. It watches one process
identity, requests cooperative stop, then enforces its frozen grace period.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from optimization_framework.storage.artifacts import atomic_json


def identity(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        fields = raw[raw.rfind(")")+2:].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, ValueError, IndexError, TypeError):
        return None


def arm(directory, spec, lease):
    return subprocess.Popen([sys.executable, "-m", __name__, "--directory", str(directory),
        "--lease-json", json.dumps(lease), "--deadline", str(spec["absolute_deadline"]),
        "--grace", str(spec.get("stop_grace_seconds", 5))],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def enforce(directory, lease, deadline, grace):
    pid, owner_identity = lease["pid"], lease["process_identity"]
    cutoff = time.monotonic()+max(0, deadline-time.time())
    while identity(pid) == owner_identity:
        if time.monotonic() >= cutoff or time.time() >= deadline:
            break
        time.sleep(min(.1, max(0, cutoff-time.monotonic())))
    if identity(pid) != owner_identity:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    grace_end = time.monotonic()+grace
    while identity(pid) == owner_identity and time.monotonic() < grace_end:
        time.sleep(min(.05, max(0, grace_end-time.monotonic())))
    if identity(pid) != owner_identity:
        return
    # This is evidence of a termination request, not a fabricated final worker
    # result. Reconciliation verifies the identity and observes that it exited.
    atomic_json(Path(directory)/"deadline-enforcement.json", {**lease,
        "absolute_deadline": deadline, "grace_seconds": grace, "enforced_at": time.time(),
        "elapsed_seconds": lease["elapsed_before"] + max(0, time.monotonic() - lease["started_monotonic"]),
        "reason": "study_deadline_reached", "signal": int(signal.SIGKILL), "uncertain_final_call": True})
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--lease-json", required=True)
    parser.add_argument("--deadline", type=float, required=True)
    parser.add_argument("--grace", type=float, required=True)
    args = parser.parse_args()
    enforce(args.directory, json.loads(args.lease_json), args.deadline, args.grace)


if __name__ == "__main__":
    main()
