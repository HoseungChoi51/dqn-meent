"""Small reference package for infrastructure acceptance, not a new research claim."""
import pickle
import numpy as np


class Optimizer:
    def __init__(self, context):
        self.n = context["n_cells"]
        self.rng = np.random.default_rng(context["seed"])
        self.best = None
        self.score = float("-inf")

    def ask(self):
        if self.best is None:
            return self.rng.integers(0, 2, self.n).tolist()
        design = self.best.copy()
        index = int(self.rng.integers(self.n))
        design[index] = 1 - design[index]
        return design

    def tell(self, design, efficiency):
        if efficiency > self.score:
            self.best, self.score = list(design), efficiency

    def checkpoint(self):
        return pickle.dumps((self.best, self.score, self.rng.bit_generator.state))

    def restore(self, data):
        self.best, self.score, self.rng.bit_generator.state = pickle.loads(data)


def create_optimizer(context):
    return Optimizer(context)
