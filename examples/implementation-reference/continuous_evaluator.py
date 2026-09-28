"""Small evaluator example. Independent expected results live outside this file."""
import json


class QuadraticEvaluator:
    def __init__(self, context):
        self.problem = context["problem"]
        self.count = 0

    def evaluate(self, candidate):
        self.count += 1
        configuration = self.problem["configuration"]
        value = sum((x - configuration["center"]) ** 2 for x in candidate) + configuration["offset"]
        value = round(value, self.problem["fidelity"]["digits"])
        return {"objectives": {"energy": value}, "metadata": {"evaluations": self.count}}

    def checkpoint(self):
        return json.dumps({"problem": self.problem, "count": self.count}).encode()

    def restore(self, payload):
        state = json.loads(payload)
        if state["problem"] != self.problem:
            raise ValueError("Problem changed")
        self.count = state["count"]


def create_evaluator(context):
    return QuadraticEvaluator(context)
