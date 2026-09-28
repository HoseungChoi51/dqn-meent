"""Load historical trusted MEENT policy artifacts with their original profile."""
from pathlib import Path
import json
import numpy as np
import torch
from optimization_framework.optimizers.dqn_network import QNetwork


def make_network(config):
    tc = config.training
    return QNetwork(config.observation_size, config.physics.n_cells, tc.hidden_sizes, tc.activation, tc.initialization)

def load_policy(checkpoint: str | Path, device: str = "cpu"):
    """Load a trusted local training checkpoint and its experiment configuration.

    Training checkpoints contain Python/NumPy RNG and replay state and therefore
    use pickle. Do not load checkpoints from an untrusted source.
    """
    from dqn_meent.config import ExperimentConfig

    checkpoint = Path(checkpoint)
    with checkpoint.open("rb") as stream:
        is_reference = stream.read(1) == b"{"
    if is_reference:
        from optimization_framework.contracts.experiments import ArtifactReference
        from optimization_framework.contracts.base import content_hash
        from optimization_framework.storage.artifacts import LocalArtifactStore
        pointer = json.loads(checkpoint.read_text())
        if pointer.get("format") != "framework-checkpoint-reference-v1":
            raise ValueError("Unsupported policy reference")
        outputs = json.loads((checkpoint.parent / "outputs.json").read_text())
        if outputs["id"] != "outputs_" + content_hash({key: value for key, value in outputs.items() if key != "id"}):
            raise ValueError("Policy output manifest failed integrity verification")
        if outputs["checkpoint_id"] != pointer["checkpoint_id"]:
            raise ValueError("Exported policy does not match the requested checkpoint")
        policy = next(item for item in outputs["outputs"] if item["kind"] == "policy")
        if policy["format"] != "numpy-state-dict-v1":
            raise ValueError("Unsupported policy artifact format")
        config = ExperimentConfig.load(checkpoint.parent / "config.json")
        store = LocalArtifactStore(checkpoint.parent / "artifacts")
        with store.open(ArtifactReference(**policy["reference"])) as stream, np.load(stream, allow_pickle=False) as arrays:
            weights = {key: torch.from_numpy(arrays[key].copy()) for key in arrays.files}
        if any(not torch.isfinite(value).all() for value in weights.values()):
            raise ValueError("Policy artifact contains nonfinite weights")
    else:
        state = torch.load(checkpoint, map_location=device, weights_only=False)
        config = ExperimentConfig.from_dict(state["config"])
        weights = state["online"]
    # Merely inspecting an exported policy must not advance training randomness.
    with torch.random.fork_rng(devices=[]):
        model = make_network(config).to(device)
    model.load_state_dict(weights)
    model.eval()
    return model, config
