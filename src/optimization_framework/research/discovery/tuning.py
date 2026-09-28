"""Reproducible small tuning batches; no model or evaluator runs here."""
from copy import deepcopy
import itertools
import math
import random
from typing import Any, Literal

from pydantic import Field, model_validator

from optimization_framework.contracts.base import Contract, content_hash


class Parameter(Contract):
    name: str = Field(pattern=r"^(algorithm_config|training)\.[a-zA-Z][a-zA-Z0-9_]*$")
    kind: Literal["integer", "real", "categorical"]
    low: float | None = None
    high: float | None = None
    scale: Literal["linear", "log"] = "linear"
    values: list[Any] = Field(default_factory=list, max_length=30)
    active_when: dict[str, Any] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def valid_range(self):
        if self.kind == "categorical":
            if not self.values or self.low is not None or self.high is not None:
                raise ValueError("Categorical parameters require explicit values and no numeric bounds")
        elif self.low is None or self.high is None or self.low > self.high:
            raise ValueError("Numeric parameters need ordered lower and upper bounds")
        elif self.scale == "log" and self.low <= 0:
            raise ValueError("Logarithmic parameters must have strictly positive bounds")
        elif self.kind == "integer" and (not self.low.is_integer() or not self.high.is_integer()):
            raise ValueError("Integer parameters need integral bounds")
        for value in self.values:
            self.check(value)
        return self

    def check(self, value):
        if self.kind == "categorical":
            if content_hash(value) not in {content_hash(option) for option in self.values}:
                raise ValueError("Configuration is outside the declared categorical space")
        elif type(value) not in {int, float} or not math.isfinite(value) or not self.low <= value <= self.high:
            raise ValueError("Configuration is outside the declared numeric space")
        elif self.kind == "integer" and type(value) is not int:
            raise ValueError("An integer tuning parameter must remain an integer")


class Constraint(Contract):
    left: str
    operator: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    right_parameter: str | None = None
    right_value: Any = None

    def accepts(self, values):
        if self.left not in values or self.right_parameter and self.right_parameter not in values:
            return False
        left = values[self.left]
        right = values[self.right_parameter] if self.right_parameter else self.right_value
        try:
            return {"eq": lambda: left == right, "ne": lambda: left != right, "lt": lambda: left < right,
                    "le": lambda: left <= right, "gt": lambda: left > right, "ge": lambda: left >= right}[self.operator]()
        except TypeError:
            return False


class Configuration(Contract):
    algorithm_config: dict[str, Any] = Field(default_factory=dict)
    training: dict[str, Any] = Field(default_factory=dict)

    def flat(self):
        return {section + "." + key: value for section in ("algorithm_config", "training")
                for key, value in getattr(self, section).items()}


class TuningSpace(Contract):
    strategy: Literal["explicit", "random", "grid"] = "explicit"
    seed: int = Field(default=0, ge=0, lt=2**32)
    count: int = Field(default=3, ge=1, le=30)
    base: Configuration = Field(default_factory=Configuration)
    initial: list[Configuration] = Field(default_factory=list, max_length=30)
    parameters: list[Parameter] = Field(default_factory=list, max_length=30)
    constraints: list[Constraint] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def space_contract(self):
        names = [parameter.name for parameter in self.parameters]
        if len(set(names)) != len(names):
            raise ValueError("Tuning parameter names must be unique")
        prior = set(self.base.flat()) - set(names)
        for parameter in self.parameters:
            if not set(parameter.active_when) <= prior:
                raise ValueError("Conditional parameters must follow the parameters they depend on")
            prior.add(parameter.name)
        if self.strategy == "explicit" and len(self.initial) != self.count:
            raise ValueError("An explicit tuning batch requires exactly the declared number of configurations")
        if len(self.initial) > self.count:
            raise ValueError("Initial configurations exceed the declared batch size")
        if self.strategy == "grid" and any(not row.values for row in self.parameters):
            raise ValueError("Grid tuning requires explicit grid values for every parameter")
        if self.strategy == "grid" and math.prod(len(row.values) for row in self.parameters) > 10000:
            raise ValueError("Grid exceeds the bounded 10,000-combination sampler")
        return self


def sample(space):
    space = TuningSpace.model_validate(space)
    rng, selected, seen = random.Random(space.seed), [], set()
    def add(flat, *, explicit=False):
        flat = deepcopy(flat)
        for parameter in space.parameters:
            active = all(key in flat and content_hash(flat[key]) == content_hash(value) for key, value in parameter.active_when.items())
            if not active:
                if explicit and parameter.name in flat:
                    raise ValueError("Explicit configuration supplies an inactive conditional parameter")
                flat.pop(parameter.name, None)
                continue
            if parameter.name not in flat:
                if explicit:
                    raise ValueError("Explicit configuration omits an active tuning parameter")
                return
            parameter.check(flat[parameter.name])
        if not all(rule.accepts(flat) for rule in space.constraints):
            if explicit:
                raise ValueError("Explicit configuration violates a declared parameter constraint")
            return
        result = {"algorithm_config": {}, "training": {}}
        for name, value in flat.items():
            section, key = name.split(".", 1)
            result[section][key] = value
        digest = content_hash(result)
        if digest in seen:
            if explicit:
                raise ValueError("Tuning configurations must be distinct; use experiment seeds for replications")
            return
        seen.add(digest)
        selected.append(result)
    for configuration in space.initial:
        add({**space.base.flat(), **configuration.flat()}, explicit=True)
    if space.strategy == "grid":
        for values in itertools.product(*(parameter.values for parameter in space.parameters)):
            if len(selected) >= space.count:
                break
            add({**space.base.flat(), **dict(zip([p.name for p in space.parameters], values))})
    elif space.strategy == "random":
        for _ in range(3000):
            if len(selected) >= space.count:
                break
            flat = space.base.flat()
            for parameter in space.parameters:
                if not all(key in flat and content_hash(flat[key]) == content_hash(value) for key, value in parameter.active_when.items()):
                    flat.pop(parameter.name, None)
                    continue
                if parameter.kind == "categorical":
                    value = rng.choice(parameter.values)
                elif parameter.kind == "integer" and parameter.scale == "linear":
                    value = rng.randint(int(parameter.low), int(parameter.high))
                else:
                    value = math.exp(rng.uniform(math.log(parameter.low), math.log(parameter.high))) if parameter.scale == "log" else rng.uniform(parameter.low, parameter.high)
                    if parameter.kind == "integer":
                        value = max(int(parameter.low), min(int(parameter.high), round(value)))
                flat[parameter.name] = value
            add(flat)
    if len(selected) != space.count:
        raise ValueError("The bounded sampler could not find enough distinct valid configurations; revise the declared space or count")
    return selected
