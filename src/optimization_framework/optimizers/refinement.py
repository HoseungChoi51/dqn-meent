"""Resumable one/two-coordinate best improvement from an explicitly supplied asset."""
import itertools

from .binary import BinaryOptimizer


class Refinement(BinaryOptimizer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.initial_design is None:
            raise ValueError("Refinement requires a declared initial design asset and upstream cost provenance")
        self.current = self.initial_design.copy()
        self.current_score = None
        self.winner = self.current.copy()
        self.winner_score = None
        self.phase = "initial"
        self.moves = []
        self.cursor = 0
        self.restarts = 0
        self.depth = 1
        self.tolerance = float(self.config.get("improvement_tolerance", 1e-12))
        if not 0 <= self.tolerance < 1:
            raise ValueError("Improvement tolerance must be nonnegative and less than one")

    def _neighborhood(self, depth):
        self.phase = "scan"
        self.depth = depth
        self.winner, self.winner_score = self.current.copy(), self.current_score
        indices = self.rng.permutation(self.n_cells)
        self.moves = list(itertools.combinations(indices.tolist(), depth))
        self.cursor = 0

    def ask(self):
        if self.phase == "initial":
            return self.current.copy()
        if self.phase == "restart":
            self.restarts += 1
            candidate = self.best_design.copy()
            # The historical method uses 3..8 flips on 64 cells. Bound this
            # explicitly for smaller schemas; the original domain is unchanged.
            count = min(self.n_cells, int(self.rng.integers(3, 9)))
            candidate[self.rng.choice(self.n_cells, count, replace=False)] ^= 1
            return candidate
        if self.phase == "prepare":
            self._neighborhood(self.depth)
        candidate = self.current.copy()
        candidate[list(self.moves[self.cursor])] ^= 1
        return candidate

    def tell(self, design, efficiency):
        super().tell(design, efficiency)
        if self.phase in {"initial", "restart"}:
            self.current, self.current_score = design.copy(), efficiency
            self.phase, self.depth = "prepare", 1
            return
        if efficiency > self.winner_score + self.tolerance:
            self.winner, self.winner_score = design.copy(), efficiency
        self.cursor += 1
        if self.cursor == len(self.moves):
            if self.winner_score > self.current_score + self.tolerance:
                self.current, self.current_score = self.winner.copy(), self.winner_score
                self.phase, self.depth = "prepare", 1
            elif self.depth == 1:
                self.phase, self.depth = "prepare", 2
            else:
                self.phase = "restart"

    def diagnostics(self):
        return {"decisions": self.count, "phase": self.phase, "neighborhood_depth": self.depth,
                "neighborhood_cursor": self.cursor, "restarts": self.restarts}
