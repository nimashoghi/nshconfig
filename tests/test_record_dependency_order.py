"""Stored interpolation edges must be possible under declaration order."""

import copy

import pytest

import nshconfig as C


class _Ordered(C.Config):
    source: int
    copied: int = C.interp(lambda context: context.current().source)
    later: int


@pytest.mark.parametrize("impossible_read", ["copied", "later"])
def test_load_record_rejects_self_and_later_field_dependencies(
    impossible_read: str,
) -> None:
    final = _Ordered(source=7, later=7)
    payload = copy.deepcopy(C.record(final).to_dict())
    event = payload["provenance"]["copied"][-1]
    assert event["kind"] == "interpolate"
    event["reads"][0][0] = impossible_read

    with pytest.raises(C.RecordError, match="not observable|not published"):
        C.load_record(_Ordered, payload)
