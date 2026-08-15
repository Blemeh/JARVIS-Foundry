"""Injectable deterministic ID generators."""

from __future__ import annotations


class IdGenerator:
    """Port: unique deterministic identifier factory."""

    def new_id(self, prefix: str) -> str:
        raise NotImplementedError


class DeterministicIdGenerator(IdGenerator):
    def __init__(self, seed: int = 1) -> None:
        self._counter = seed

    def new_id(self, prefix: str) -> str:
        value = f"{prefix}-{self._counter:08d}"
        self._counter += 1
        return value
