"""Run one isolated qualification service with an explicitly mocked model reviewer.

Only supplied packages can be commissioned. HTTP, authentication, persistence,
independent correctness checks, package isolation and workers are the real product.
This fixture never contacts an LLM and is not live-model acceptance evidence.
"""
import argparse
import os
from pathlib import Path

from optimization_framework.implementations.models import ReviewResult


class FixtureReviewer:
    def __init__(self, **kwargs):
        self.usage = {"calls": 0, "api_cost_usd": 0}
        self.callback = kwargs["reservation_callback"]

    def call(self, role, payload, *, result_type, instructions):
        if role != "implementation_validator":
            raise ValueError("Qualification fixture requires a supplied source package; no model builder is configured")
        self.usage["calls"] += 1
        self.callback({"type": "provider_call_reserved", "usage": self.usage})
        return ReviewResult(passed=True, criteria=payload["spec"]["acceptance_criteria"],
                            findings=["Mocked semantic reviewer for supplied-code qualification"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["workspace", "library"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--library-url", default="http://127.0.0.1:8768")
    parser.add_argument("--frontend-directory", type=Path)
    args = parser.parse_args()
    os.environ["GRATING_LLM_DISABLED"] = "true"
    if args.role == "library":
        from optimization_framework.implementations.api import create_app
        app = create_app(args.directory / "library", adapter_factory=FixtureReviewer)
    else:
        from optimization_framework.api.app import create_app
        from optimization_framework.implementations.client import ImplementationClient
        os.environ["GRATING_IMPLEMENTATIONS_TOKEN_FILE"] = str(args.directory.resolve() / "library/service.token")
        app = create_app(args.directory / "workspace", implementation_client=ImplementationClient(args.library_url),
            frontend_directory=args.frontend_directory)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
