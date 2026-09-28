"""Small ask/tell reference for the independently validated continuous lane."""
import pickle
import random


class Optimizer:
    def __init__(self, context):
        self.rng = random.Random(context["seed"])
        self.bounds = context["problem"]["candidate_schema"]["bounds"]
        self.incumbent = None
        self.score = None

    def ask(self):
        if self.incumbent is None:
            return [self.rng.uniform(lo, hi) for lo, hi in self.bounds]
        candidate = self.incumbent.copy()
        index = self.rng.randrange(len(candidate))
        lo, hi = self.bounds[index]
        candidate[index] = self.rng.uniform(lo, hi)
        return candidate

    def tell(self, candidate, utility):
        if self.score is None or utility > self.score:
            self.incumbent = list(candidate)
            self.score = utility

    def checkpoint(self):
        return pickle.dumps(self.__dict__)

    def restore(self, state):
        self.__dict__ = pickle.loads(state)


def create_optimizer(context):
    return Optimizer(context)
