"""A Config under validation is neither a draft nor a final."""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import model_validator

import nshconfig as C


def _assert_final_apis_reject(value: C.Config) -> None:
    operations: tuple[Callable[[], Any], ...] = (
        lambda: C.fingerprint(value),
        lambda: C.record(value),
        lambda: C.finalize(value),
        lambda: C.provenance(value),
        lambda: C.explain(value, "number"),
        value.model_dump,
        value.model_dump_json,
        value.model_copy,
        lambda: copy.copy(value),
        lambda: copy.deepcopy(value),
        lambda: list(value),
    )
    for operation in operations:
        with pytest.raises(C.DraftError):
            operation()


def test_model_post_init_cannot_cross_a_final_only_boundary() -> None:
    class PostInit(C.Config):
        number: int

        def model_post_init(self, context: Any) -> None:
            del context
            _assert_final_apis_reject(self)

    final = PostInit(number=7)
    assert C.fingerprint(final) == C.record(final).fingerprint
    assert C.finalize(final) == final


def test_model_after_cannot_cross_a_final_only_boundary() -> None:
    class ModelAfter(C.Config):
        number: int

        @model_validator(mode="after")
        def check_lifecycle(self) -> "ModelAfter":
            _assert_final_apis_reject(self)
            return self

    final = ModelAfter(number=7)
    assert final.model_dump() == {"number": 7}
    assert C.provenance(final) == {}
