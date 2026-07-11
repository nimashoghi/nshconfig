"""Transformed interpolation containers preserve canonical source paths."""

import pytest
from pydantic import ValidationError

import nshconfig as C


class _ContainerOps(C.Config):
    xs: list[int]
    ys: list[int]
    left: dict[str, int]
    right: dict[str, int]
    sliced: int = C.interp(lambda context: context.current().xs[1:][0])
    reverse_sliced: int = C.interp(lambda context: context.current().xs[::-1][0])
    last: int = C.interp(lambda context: context.current().xs[-1])
    first: int = C.interp(
        lambda context: context.current().xs[-len(context.current().xs)]
    )
    appended: list[int] = C.interp(lambda context: context.current().xs + [99])
    repeated: list[int] = C.interp(lambda context: context.current().xs * 2)
    joined: list[int] = C.interp(
        lambda context: context.current().xs + context.current().ys
    )
    equal: bool = C.interp(lambda context: context.current().xs == context.current().ys)
    merged: dict[str, int] = C.interp(
        lambda context: context.current().left | context.current().right
    )


def test_transformed_containers_round_trip_with_truthful_dependencies() -> None:
    final = _ContainerOps(
        xs=[10, 20, 30],
        ys=[40],
        left={"a": 1, "shared": 2},
        right={"shared": 3, "b": 4},
    )

    assert final.sliced == 20
    assert final.reverse_sliced == 30
    assert final.last == 30
    assert final.first == 10
    assert final.appended == [10, 20, 30, 99]
    assert final.repeated == [10, 20, 30, 10, 20, 30]
    assert final.joined == [10, 20, 30, 40]
    assert final.equal is False
    assert final.merged == {"a": 1, "shared": 3, "b": 4}

    history = C.provenance(final)
    assert history["sliced"][-1].reads == (("xs", "[10, 20, 30]"),)
    assert history["reverse_sliced"][-1].reads == (("xs", "[10, 20, 30]"),)
    assert ("xs[2]", "30") in history["last"][-1].reads
    assert ("xs[0]", "10") in history["first"][-1].reads
    assert {path for path, _ in history["equal"][-1].reads} == {"xs", "ys"}
    assert {"xs", "ys"} <= {path for path, _ in history["joined"][-1].reads}
    assert {"left", "right"} <= {path for path, _ in history["merged"][-1].reads}

    run_record = C.record(final)
    restored = C.load_record(_ContainerOps, run_record)
    assert restored == final
    assert C.record(restored) == run_record


def test_out_of_range_negative_index_remains_a_structured_validation_error() -> None:
    class OutOfRange(C.Config):
        xs: list[int]
        copied: int = C.interp(lambda context: context.current().xs[-4])

    with pytest.raises(ValidationError, match="list index out of range"):
        OutOfRange(xs=[1, 2, 3])


def test_sequence_iteration_records_shape_as_well_as_elements() -> None:
    class Iterated(C.Config):
        xs: list[int]
        copied: list[int] = C.interp(
            lambda context: [item for item in context.current().xs]
        )

    final = Iterated(xs=[1, 2])
    assert C.provenance(final)["copied"][-1].reads == (
        ("xs", "[1, 2]"),
        ("xs[0]", "1"),
        ("xs[1]", "2"),
    )

    final.xs.append(3)
    with pytest.raises(C.FingerprintError, match="provenance is not self-contained"):
        C.record(final)


def test_negative_index_records_the_sequence_shape() -> None:
    class LastValue(C.Config):
        xs: list[int]
        last: int = C.interp(lambda context: context.current().xs[-1])

    final = LastValue(xs=[1])
    assert C.provenance(final)["last"][-1].reads == (
        ("xs", "[1]"),
        ("xs[0]", "1"),
    )

    final.xs.append(2)
    with pytest.raises(C.FingerprintError, match="provenance is not self-contained"):
        C.record(final)


def test_negative_index_search_bounds_record_the_sequence_shape() -> None:
    class Position(C.Config):
        xs: list[int]
        position: int = C.interp(lambda context: context.current().xs.index(1, -2))

    final = Position(xs=[1, 1])
    assert final.position == 0
    assert C.provenance(final)["position"][-1].reads[0] == ("xs", "[1, 1]")

    final.xs.append(2)
    with pytest.raises(C.FingerprintError, match="provenance is not self-contained"):
        C.record(final)
