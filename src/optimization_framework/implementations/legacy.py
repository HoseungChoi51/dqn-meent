"""Import historical single-file optimizers without upgrading their evidence."""
from optimization_framework.implementations.models import ImplementationSpec, Package, SourceFile


ADAPTER = '''import json
import legacy
class Optimizer:
    def __init__(self, context):
        self.state = legacy.initialize(context['n_cells'], context['seed'], context['parameters'])
    def ask(self):
        answer = legacy.propose(self.state)
        self.state = answer['state']
        return answer['design']
    def tell(self, design, efficiency):
        self.state = legacy.observe(self.state, design, efficiency)
    def checkpoint(self):
        return json.dumps(self.state, allow_nan=False).encode()
    def restore(self, raw):
        self.state = json.loads(raw)
def create_optimizer(context):
    return Optimizer(context)
'''


def import_legacy(hypothesis):
    parameters = hypothesis.get("algorithm_config", {}).get("parameters", {})
    properties = {}
    for key, value in parameters.items():
        kind = "boolean" if type(value) is bool else "integer" if type(value) is int else "number" if type(value) is float else "string" if isinstance(value, str) else "array" if isinstance(value, list) else "object"
        properties[key] = {"type": kind, "enum": [value]}
    spec = ImplementationSpec(name=hypothesis["title"], mechanism=hypothesis.get("mechanism") or hypothesis["title"],
        acceptance_criteria=["Implements the declared mechanism", "Reproduces seeded proposals and resumes exactly from a checkpoint"],
        parameters=parameters, parameter_schema={"type": "object", "properties": properties, "additionalProperties": False},
        provenance=[{"hypothesis_id": hypothesis["id"], "campaign_id": hypothesis["campaign_id"],
                     "previous_validation_level": "protocol_only", "verification": hypothesis.get("verification")}])
    package = Package(entrypoint="adapter:create_optimizer", files=[SourceFile(path="adapter.py", content=ADAPTER),
        SourceFile(path="legacy.py", content=hypothesis["source"])])
    return spec, package
