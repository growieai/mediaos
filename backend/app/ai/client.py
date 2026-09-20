"""Typed execution boundary. No unconstrained model prose enters application logic."""

from collections.abc import Callable
from typing import TypeVar

from app.models.schemas import StrictModel

T = TypeVar("T", bound=StrictModel)


class MockAdapter:
    provider = "mock"
    model = "deterministic-v1"
    version = "mock-v1"
    is_mock = True

    def generate(self, schema: type[T], operation: Callable[[], T]) -> T:
        # JSON validation is strict, while permitting JSON UUID/date strings.
        return schema.model_validate_json(operation().model_dump_json(), strict=True)


class DeterministicAdapter(MockAdapter):
    provider = "deterministic"
    model = "rules-v1"
    version = "deterministic-v1"
    is_mock = False
