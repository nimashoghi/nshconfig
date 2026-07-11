"""Run-record provenance events have one canonical shape per event kind."""

from collections.abc import Callable
from typing import Any

import pytest

import nshconfig as C


class _Trace(C.Config):
    source: int
    values: list[int] = []
    derived: int = C.interp(lambda context: context.current().source)


def _trace_record() -> C.RunRecord:
    work = C.draft(_Trace)
    work.source = 1
    del work.source
    work.source = 2
    work.values.append(3)
    return C.record(C.finalize(work))


def _event(
    envelope: dict[str, Any],
    owner: str,
    index: int,
) -> dict[str, Any]:
    provenance = envelope["provenance"]
    assert isinstance(provenance, dict)
    events = provenance[owner]
    assert isinstance(events, list)
    event = events[index]
    assert isinstance(event, dict)
    return event


@pytest.mark.parametrize(
    ("owner", "index", "mutate", "message"),
    [
        ("source", 0, lambda event: event.update(operation="append"), "set provenance"),
        (
            "source",
            0,
            lambda event: event.update(from_default=True),
            "only interpolate",
        ),
        ("source", 1, lambda event: event.update(value="deleted"), "delete provenance"),
        ("values", 0, lambda event: event.update(operation=None), "no operation"),
        ("derived", 0, lambda event: event.update(site=None), "no resolver site"),
        (
            "derived",
            0,
            lambda event: event.update(file="resolver.py"),
            "source metadata",
        ),
    ],
)
def test_load_record_rejects_kind_inconsistent_event_fields(
    owner: str,
    index: int,
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    envelope = _trace_record().to_dict()
    mutate(_event(envelope, owner, index))

    with pytest.raises(C.RecordError, match=message):
        C.load_record(_Trace, envelope)


def test_load_record_rejects_duplicate_interpolation_reads() -> None:
    envelope = _trace_record().to_dict()
    event = _event(envelope, "derived", 0)
    reads = event["reads"]
    tokens = event["read_tokens"]
    assert isinstance(reads, list)
    assert isinstance(tokens, list)
    reads.append(list(reads[0]))
    tokens.append(tokens[0])

    with pytest.raises(C.RecordError, match="duplicate reads"):
        C.load_record(_Trace, envelope)
