"""Common v1 protocol; sees raw objectives and explicit proposal identities."""
import pickle
import random


class Optimizer:
    def initialize(self, problem_descriptor, parameters, seed, declared_assets):
        self.rng = random.Random(seed)
        self.bounds = problem_descriptor["candidate_schema"]["bounds"]
        self.objective = problem_descriptor["primary_objective"]
        self.incumbent = self.score = None
        self.proposals = self.observations = 0

    def propose(self, max_candidates):
        self.proposals += 1
        if self.incumbent is None:
            candidate = [self.rng.uniform(lo, hi) for lo, hi in self.bounds]
        else:
            candidate = self.incumbent.copy()
            index = self.rng.randrange(len(candidate))
            candidate[index] = self.rng.uniform(*self.bounds[index])
        return [{"id": f"candidate_{self.proposals}", "candidate": candidate}]

    def observe(self, observations):
        for observation in observations:
            if observation["status"] != "ok":
                raise ValueError("This reference requires feasible successful observations")
            value = observation["objectives"][self.objective["name"]]
            improvement = self.score is None or (value < self.score if self.objective["direction"] == "minimize" else value > self.score)
            if improvement:
                self.incumbent = list(observation["candidate"])
                self.score = value
            self.observations += 1

    def checkpoint(self):
        return pickle.dumps(self.__dict__)

    def restore(self, state):
        self.__dict__ = pickle.loads(state)

    def inspect(self):
        return {"decisions": self.proposals, "observations": self.observations}

    def export_artifacts(self):
        return []


def create_optimizer(context):
    return Optimizer()
