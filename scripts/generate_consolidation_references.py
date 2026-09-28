"""Generate bounded scientific references from the captured sibling source tree.

This utility never imports the developing framework into the reference process.
The source digest, runtime, exact configuration, measured trace and weights are
saved together. Do not regenerate fixtures to hide an unexplained mismatch.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


REFERENCE = r'''
import csv,json,sys
from pathlib import Path
from dataclasses import asdict
from importlib.metadata import version
import torch
from dqn_meent.config import ExperimentConfig,PhysicsConfig,TrainConfig
from dqn_meent.training import train
from dqn_meent.experiments import baseline
from refine_design import refine
root=Path(sys.argv[1])
records={"runtime":{name:version(name) for name in ('numpy','torch','meent')}}
profiles={
 'finite':{},
 'continuing_public':dict(episode_mode='continuing',activation='leaky_relu',initialization='orthogonal',gradient_clip_norm=None,target_update_tau=.1,random_streams='python_numpy',epsilon_schedule='paper',greedy_tie_break='first',learning_starts_strict=True,double_dqn=False),
 'continuing_default_init':dict(episode_mode='continuing',activation='leaky_relu',initialization='default',gradient_clip_norm=None,target_update_tau=.1,random_streams='python_numpy',epsilon_schedule='paper',greedy_tie_break='first',learning_starts_strict=True,double_dqn=False),
}
records['learners']={}
for name,changes in profiles.items():
 settings=dict(total_steps=24,horizon=3,seed=123,batch_size=4,buffer_size=64,learning_starts=4,hidden_sizes=(8,8),target_update_interval=4,checkpoint_interval=8)
 settings.update(changes)
 config=ExperimentConfig(PhysicsConfig(n_cells=8,fourier_order=1),TrainConfig(**settings))
 out=root/name
 summary=train(config,out)
 state=torch.load(out/'checkpoint.pt',weights_only=False)
 with (out/'metrics.csv').open() as stream:
  rows=[{k:v for k,v in row.items() if k!='elapsed_seconds'} for row in csv.DictReader(stream)]
 records['learners'][name]={'config':config.to_dict(),'trace':rows,'summary':{k:summary[k] for k in ('steps','updates','evaluations','solver_calls','cache_hits','best_efficiency','last_efficiency','last_loss')},'weights':{k:v.tolist() for k,v in state['online'].items()},'target':{k:v.tolist() for k,v in state['target'].items()},'replay':{k:v[:state['replay']['size']].tolist() for k,v in state['replay'].items() if hasattr(v,'shape')}}
config=ExperimentConfig(PhysicsConfig(n_cells=8,fourier_order=1),TrainConfig(seed=9))
hc=root/'hillclimb'
baseline(config,hc,'hillclimb',budget=40,seed=9)
refined=root/'refined'
refine(hc,refined,budget=90,seed=11)
records['controls']={}
for name,path in [('hillclimb',hc),('refinement',refined)]:
 with (path/'metrics.csv').open() as stream:
  rows=[{k:v for k,v in row.items() if k!='elapsed_seconds'} for row in csv.DictReader(stream)]
 records['controls'][name]={'config':json.loads((path/'config.json').read_text()),'trace':rows}
records['controls']['refinement']['initial_design']=json.loads((hc/'best_design.json').read_text())['design']
print(json.dumps(records,allow_nan=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    hashes = {}
    for root in (source / "src", source / "scripts"):
        for path in sorted(root.rglob("*.py")):
            hashes[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(source / "src"), str(source / "scripts")]),
           "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    with tempfile.TemporaryDirectory(prefix="consolidation-reference-") as work:
        result = subprocess.run([sys.executable, "-c", REFERENCE, work], cwd=work, env=env, capture_output=True, text=True, check=True)
    record = {"schema_version": 1, "source_hashes": hashes, "evidence_kind": "bounded_numerical_regression", **json.loads(result.stdout)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")
    print(f"Wrote {args.output}: three learner profiles, hill climbing, refinement")


if __name__ == "__main__":
    main()
