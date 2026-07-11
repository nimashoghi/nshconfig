"""Executable transport is explicit, local, and separate from run records."""

from importlib.metadata import version
import os
import pickle
import subprocess
import sys
from pathlib import Path

import cloudpickle
from packaging.version import Version
from pydantic import field_validator

import nshconfig as C


class ImportableLeaf(C.Config):
    derived: int = C.interp(lambda context: context.root(ImportableRun).width * 2)


class ImportableRun(C.Config):
    width: int
    leaf: ImportableLeaf

    @field_validator("width")
    @classmethod
    def normalize_width(cls, value: int) -> int:
        return abs(value)


def _run_python(script: str, *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"child process failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_supported_cloudpickle_version_is_installed():
    assert Version(version("cloudpickle")) >= Version("3.1.2")


def test_importable_drafts_and_finals_use_normal_pickle_by_reference():
    work = C.draft(ImportableRun)
    with C.source("importable sender"):
        work.width = -3

    restored_work = pickle.loads(pickle.dumps(work))
    assert C.is_draft(restored_work)

    final = C.finalize(restored_work)
    assert final.width == 3
    assert final.leaf.derived == 6
    assert C.provenance(final)["width"][0].label == "importable sender"

    restored_final = pickle.loads(pickle.dumps(final))
    assert restored_final == final
    assert C.provenance(restored_final) == C.provenance(final)


def test_cloudpickle_round_trips_drafts_finals_and_provenance():
    work = C.draft(ImportableRun)
    with C.source("cloudpickle sender"):
        work.width = 4
        work.leaf.derived = C.interp(
            lambda context: context.root(ImportableRun).width * 3
        )
        final = C.finalize(work)

    restored_work, restored_final = pickle.loads(cloudpickle.dumps((work, final)))
    assert C.is_draft(restored_work)
    assert not C.is_draft(restored_final)
    assert restored_final == final
    assert C.provenance(restored_final) == C.provenance(final)

    with C.source("cloudpickle receiver"):
        restored_work.width = -5
        receiver_final = C.finalize(restored_work)
    assert receiver_final.width == 5
    assert receiver_final.leaf.derived == 15
    assert [event.label for event in C.provenance(receiver_final)["width"]] == [
        "cloudpickle sender",
        "cloudpickle receiver",
    ]


def test_import_does_not_install_global_pydantic_core_reducers():
    # Run in a fresh interpreter because this test module has already imported
    # nshconfig while pytest was collecting it.
    script = """
import cloudpickle
import copyreg
import pickle
from pydantic import BaseModel
from pydantic_core import SchemaSerializer, SchemaValidator

missing = object()
types = (SchemaValidator, SchemaSerializer)
before = [
    (
        copyreg.dispatch_table.get(value_type, missing),
        cloudpickle.CloudPickler.dispatch_table.get(value_type, missing),
    )
    for value_type in types
]

import nshconfig

after = [
    (
        copyreg.dispatch_table.get(value_type, missing),
        cloudpickle.CloudPickler.dispatch_table.get(value_type, missing),
    )
    for value_type in types
]
assert all(
    old_copyreg is new_copyreg and old_cloudpickle is new_cloudpickle
    for (old_copyreg, old_cloudpickle), (new_copyreg, new_cloudpickle)
    in zip(before, after)
)

class UnrelatedModel(BaseModel):
    value: int

model = UnrelatedModel(value=1)
assert pickle.loads(pickle.dumps(model)) == model
assert cloudpickle.loads(cloudpickle.dumps(model)) == model
"""
    _run_python(script)


_LOCAL_SENDER = """
# Deliberately use eager annotations. Notebook classes intended for by-value
# transport follow nshconfig's no-PEP-563 contract.
import os
from pathlib import Path

import cloudpickle
from pydantic import field_validator

import nshconfig as C


class LocalLeaf(C.Config):
    derived: int = C.interp(lambda context: context.root(LocalRun).width * 2)


class LocalRun(C.Config):
    width: int
    leaf: LocalLeaf

    @field_validator("width")
    @classmethod
    def normalize_width(cls, value: int) -> int:
        return abs(value)


draft = C.draft(LocalRun)
with C.source("sender draft"):
    draft.width = -5
    draft.leaf.derived = C.interp(
        lambda context: context.root(LocalRun).width * 3
    )

final_recipe = C.draft(LocalRun)
with C.source("sender final"):
    final_recipe.width = -7
    final = C.finalize(final_recipe)

payload = cloudpickle.dumps((draft, final))
Path(os.environ["NSHCONFIG_TRANSPORT_PAYLOAD"]).write_bytes(payload)
"""


_LOCAL_RECEIVER = """
import os
from pathlib import Path

import cloudpickle

import nshconfig as C


payload = Path(os.environ["NSHCONFIG_TRANSPORT_PAYLOAD"]).read_bytes()
draft, sender_final = cloudpickle.loads(payload)

assert C.is_draft(draft)
with C.source("receiver"):
    draft.width = -11
    receiver_final = C.finalize(draft)

assert receiver_final.width == 11
assert receiver_final.leaf.derived == 33
receiver_events = C.provenance(receiver_final)
assert [event.label for event in receiver_events["width"]] == [
    "sender draft",
    "receiver",
]
assert receiver_events["leaf.derived"][0].kind == "set"
assert receiver_events["leaf.derived"][0].label == "sender draft"
assert receiver_events["leaf.derived"][-1].kind == "interpolate"
assert receiver_events["leaf.derived"][-1].label == "receiver"
assert receiver_events["leaf.derived"][-1].reads == (("width", "11"),)

assert not C.is_draft(sender_final)
assert sender_final.width == 7
assert sender_final.leaf.derived == 14
sender_events = C.provenance(sender_final)
assert sender_events["width"][0].label == "sender final"
assert sender_events["leaf.derived"][-1].kind == "interpolate"
assert sender_events["leaf.derived"][-1].label == "sender final"

# Revalidation proves that the by-value class's validator and compiled schema
# remain executable in the receiving interpreter.
assert C.finalize(sender_final) == sender_final
"""


def test_notebook_local_classes_cross_a_process_with_cloudpickle(tmp_path: Path):
    payload = tmp_path / "transport.pkl"
    env = os.environ.copy()
    env["NSHCONFIG_TRANSPORT_PAYLOAD"] = str(payload)

    _run_python(_LOCAL_SENDER, env=env)
    _run_python(_LOCAL_RECEIVER, env=env)
