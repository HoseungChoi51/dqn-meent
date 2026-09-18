"""Command line entry points; all runs use real MEENT physics."""
import argparse
from dataclasses import replace
import json
from pathlib import Path

from .config import ExperimentConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train", help="Train Double DQN and save designs/checkpoints")
    train.add_argument("--config", required=True)
    train.add_argument("--output", required=True)
    train.add_argument("--steps", type=int)
    train.add_argument("--seed", type=int)
    train.add_argument("--resume", type=Path)
    baseline = sub.add_parser("baseline", help="Random or restart hill-climbing search")
    baseline.add_argument("--config", required=True)
    baseline.add_argument("--output", required=True)
    baseline.add_argument("--method", choices=["random", "hillclimb"], default="random")
    baseline.add_argument("--budget", type=int, default=1000)
    baseline.add_argument("--seed", type=int, default=0)
    evaluate = sub.add_parser("evaluate", help="Reevaluate best design and greedy policy at higher RCWA orders")
    evaluate.add_argument("--run", required=True)
    evaluate.add_argument("--orders", nargs="+", type=int, default=[15, 25, 40, 60])
    evaluate.add_argument("--tolerance", type=float, default=0.005)
    evaluate.add_argument("--skip-policy", action="store_true")
    plot = sub.add_parser("plot", help="Plot search traces with requested and actual physics budgets")
    plot.add_argument("--runs", nargs="+", required=True)
    plot.add_argument("--output", required=True)
    design = sub.add_parser("design", help="Render best binary geometry")
    design.add_argument("--run", required=True)
    design.add_argument("--output", required=True)
    args = parser.parse_args()
    # Small eigensystems/MLPs suffer badly from many BLAS worker threads.
    # This scoped setting affects NumPy/SciPy only; training sets PyTorch threads.
    # Load both BLAS libraries before entering threadpool_limits: its scan only
    # sees already-loaded libraries, not ones imported later by the dispatch.
    import numpy  # noqa: F401
    import scipy.linalg  # noqa: F401
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):
        result = _dispatch(args)
    if result is not None:
        print(json.dumps(result, indent=2, allow_nan=False))


def _dispatch(args):
    if args.command == "train":
        from .training import train
        config = ExperimentConfig.load(args.config)
        overrides = {key: value for key, value in (("total_steps", args.steps), ("seed", args.seed)) if value is not None}
        config = replace(config, training=replace(config.training, **overrides))
        return train(config, Path(args.output), resume=args.resume)
    from . import experiments
    if args.command == "baseline":
        return experiments.baseline(ExperimentConfig.load(args.config), args.output, args.method, args.budget, args.seed)
    if args.command == "evaluate":
        result = experiments.evaluate_run(args.run, args.orders, args.tolerance, not args.skip_policy)
        b = result["best_discovered"]
        return {"output": str(Path(args.run) / "evaluation.json"),
                "efficiencies_by_order": {r["fourier_order"]: r["efficiency"] for r in b["results"]},
                "last_two_within_tolerance": b["last_two_within_tolerance"]}
    if args.command == "plot":
        experiments.plot_runs(args.runs, args.output)
    if args.command == "design":
        experiments.plot_design(args.run, args.output)


if __name__ == "__main__":
    main()
