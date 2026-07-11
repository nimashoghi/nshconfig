"""Interpolation contexts and views are resolver-local capabilities."""

from threading import Thread
from typing import Any

import pytest
from pydantic import ValidationError

import nshconfig as C


def test_context_and_views_expire_when_the_resolver_returns() -> None:
    saved: dict[str, Any] = {}

    def resolve(context: C.Context) -> int:
        saved["context"] = context
        saved["view"] = context.current().values
        return context.current().values[0]

    class Values(C.Config):
        values: list[int]
        first: int = C.interp(resolve)

    final = Values(values=[3])
    assert final.first == 3

    with pytest.raises(RuntimeError, match="expire"):
        saved["context"].current()
    with pytest.raises(RuntimeError, match="expire"):
        len(saved["view"])


def test_context_cannot_cross_a_thread_boundary() -> None:
    def resolve(context: C.Context) -> int:
        values: list[int] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                values.append(context.current().source[0])
            except BaseException as error:
                errors.append(error)

        thread = Thread(target=worker)
        thread.start()
        thread.join()
        if errors:
            raise errors[0]
        return values[0]

    class ThreadedRead(C.Config):
        source: list[int]
        derived: int = C.interp(resolve)

    with pytest.raises(ValidationError) as caught:
        ThreadedRead(source=[5])

    assert caught.value.errors()[0]["type"] == "nshconfig_interpolation"
    assert "thread boundaries" in caught.value.errors()[0]["msg"]


def test_stale_context_cannot_attach_reads_to_a_later_resolver() -> None:
    saved: dict[str, C.Context] = {}

    def remember(context: C.Context) -> int:
        saved["context"] = context
        return context.current().source

    class First(C.Config):
        source: int
        copied: int = C.interp(remember)

    assert First(source=7).copied == 7

    class Second(C.Config):
        source: int
        copied: int = C.interp(lambda context: saved["context"].current().source)

    with pytest.raises(ValidationError) as caught:
        Second(source=7)

    assert caught.value.errors()[0]["type"] == "nshconfig_interpolation"
    assert "expire" in caught.value.errors()[0]["msg"]
