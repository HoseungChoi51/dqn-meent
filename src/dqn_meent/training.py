"""Historical training entry point backed by the common optimizer lifecycle."""
from optimization_framework.optimizers.schedules import epsilon_at_step
from optimization_framework.storage.artifacts import atomic_json as _atomic_json

from .lifecycle import execute, LOG_FIELDS


def train(config, output_dir, resume=None):
    return execute(config, output_dir, method="dqn", resume=resume)
