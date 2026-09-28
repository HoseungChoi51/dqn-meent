"""Capture selection/verdict fixtures from preserved sibling code in isolation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


REFERENCE = r'''
import copy, json
from dqn_meent.replication import select_profile, decide
def development(profile, values, seconds=10):
 return [dict(profile=profile, seed=seed, phase='development', status='completed', converged=True,
              efficiency_F320=value, elapsed_seconds=seconds) for seed,value in zip((10,11,12),values)]
base=development('P',[.8,.81,.82])+development('C',[.82,.82,.83])
selections=[('median',base),('missing_seed',base[:-1]),
 ('tie_maximum',development('P',[.8,.82,.84])+development('C',[.81,.8204,.83])),
 ('tie_elapsed',development('P',[.8,.82,.84],10)+development('C',[.8,.82,.84],9)),
 ('tie_identifier',development('P',[.8,.82,.84])+development('C',[.8,.82,.84])),
 ('incomplete',development('P',[.8,.82,.84])[:2])]
unconverged=copy.deepcopy(base)
unconverged[-1]['converged']=False
selections.append(('failed_validation',unconverged))
controls=[dict(kind=kind,seed=seed,phase='control',status='completed',converged=True,efficiency_F480=.8)
          for kind in ('hc','refine') for seed in range(100,105)]
refs=[dict(method='paper',converged=True,efficiency_F480=.95),dict(method='refine',converged=True,efficiency_F480=.82)]
verdicts=[]
for wins in range(6):
 rows=controls+[dict(kind='train',profile='C',seed=seed,phase='confirmation',status='completed',converged=True,
                   efficiency_F480=.83 if seed-100 < wins else .81) for seed in range(100,105)]
 verdicts.append((str(wins)+'_wins',rows,'C',refs))
verdicts.append(('missing_control',verdicts[-1][1][1:],'C',refs))
verdicts.append(('missing_reference',verdicts[-2][1],'C',[]))
failed=copy.deepcopy(refs)
failed[0]['converged']=False
verdicts.append(('failed_reference',verdicts[3][1],'C',failed))
verdicts.append(('no_selection',verdicts[3][1],None,refs))
boundary=copy.deepcopy(verdicts[5][1])
for row in boundary:
 if row.get('profile')=='C': row['efficiency_F480']=.82+.005
verdicts.append(('strict_gain_boundary',boundary,'C',refs))
print(json.dumps(dict(selection=[dict(name=name,rows=rows,expected=select_profile(rows)) for name,rows in selections],
 verdict=[dict(name=name,rows=rows,selected=selected,references=refs,expected=decide(rows,selected,refs))
          for name,rows,selected,refs in verdicts]),allow_nan=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    hashes = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted((source / "src/dqn_meent").glob("*.py"))}
    with tempfile.TemporaryDirectory(prefix="rule-reference-") as directory:
        result = subprocess.run([sys.executable, "-c", REFERENCE], cwd=directory,
            env={**os.environ, "PYTHONPATH": str(source / "src")}, capture_output=True, text=True, check=True)
    record = {"schema_version": 1, "source_hashes": hashes, "evidence_kind": "preserved_sibling_rule_fixtures", **json.loads(result.stdout)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")
    print(f"Wrote {len(record['selection'])} selection and {len(record['verdict'])} verdict cases")


if __name__ == "__main__":
    main()
