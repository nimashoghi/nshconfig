"""Executable transport is explicit, trusted, and local."""

from importlib.metadata import version
import os
import pickle
import subprocess
import sys
from pathlib import Path

import cloudpickle
from packaging.version import Version

import nshconfig as C


class ImportableLeaf(C.Config):
    derived: int = C.interp(lambda context: context.root(ImportableRun).width * 2)


class ImportableRun(C.Config):
    width: int
    leaf: ImportableLeaf

    @C.field_validator("width")
    @classmethod
    def normalize_width(cls, value: int) -> int:
        return abs(value)


def _copy_importable_width(context: C.Context) -> int:
    return context.parent(ImportableTemplateParent).width


class ImportableTemplateChild(C.Config):
    copied: int = C.interp(_copy_importable_width)


class ImportableTemplateParent(C.Config):
    width: int = 3
    child: ImportableTemplateChild = ImportableTemplateChild()


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
    work = ImportableRun.config_draft()
    work.width = -3

    restored_work = pickle.loads(pickle.dumps(work))
    assert C.is_draft(restored_work)

    final = restored_work.config_finalize()
    assert final.width == 3
    assert final.leaf.derived == 6

    restored_final = pickle.loads(pickle.dumps(final))
    assert restored_final == final


def test_cloudpickle_round_trips_drafts_and_finals():
    work = ImportableRun.config_draft()
    work.width = 4
    work.leaf.derived = C.interp(lambda context: context.root(ImportableRun).width * 3)
    final = work.config_finalize()

    restored_work, restored_final = pickle.loads(cloudpickle.dumps((work, final)))
    assert C.is_draft(restored_work)
    assert not C.is_draft(restored_final)
    assert restored_final == final

    restored_work.width = -5
    receiver_final = restored_work.config_finalize()
    assert receiver_final.width == 5
    assert receiver_final.leaf.derived == 15


def test_pickle_and_cloudpickle_preserve_unbound_template_recipes() -> None:
    template = ImportableTemplateParent.model_fields["child"].default
    assert C.is_template(template)

    for module in (pickle, cloudpickle):
        restored = module.loads(module.dumps(template))
        assert C.is_template(restored)
        assert ImportableTemplateParent(child=restored).child.copied == 3


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
from __future__ import annotations

import os
from pathlib import Path

import cloudpickle

import nshconfig as C


class LocalLeaf(C.Config):
    derived: int = C.interp(lambda context: context.root(LocalRun).width * 2)


class LocalRun(C.Config):
    width: int
    leaf: LocalLeaf = LocalLeaf()

    @C.field_validator("width")
    @classmethod
    def normalize_width(cls, value: int) -> int:
        return abs(value)


draft = LocalRun.config_draft()
draft.width = -5
draft.leaf.derived = C.interp(
    lambda context: context.root(LocalRun).width * 3
)

final_recipe = LocalRun.config_draft()
final_recipe.width = -7
final = final_recipe.config_finalize()

template = LocalRun.model_fields["leaf"].default
assert C.is_template(template)
payload = cloudpickle.dumps((draft, final, template))
Path(os.environ["NSHCONFIG_TRANSPORT_PAYLOAD"]).write_bytes(payload)
"""


_LOCAL_RECEIVER = """
import os
from pathlib import Path

import cloudpickle

import nshconfig as C


payload = Path(os.environ["NSHCONFIG_TRANSPORT_PAYLOAD"]).read_bytes()
draft, sender_final, template = cloudpickle.loads(payload)

assert C.is_draft(draft)
assert C.is_template(template)
draft.width = -11
receiver_final = draft.config_finalize()

assert receiver_final.width == 11
assert receiver_final.leaf.derived == 33

assert not C.is_draft(sender_final)
assert sender_final.width == 7
assert sender_final.leaf.derived == 14
assert sender_final.model_dump() == {"width": 7, "leaf": {"derived": 14}}

bound_template = type(sender_final)(width=13, leaf=template)
assert bound_template.leaf.derived == 26
"""


def test_notebook_local_classes_cross_a_process_with_cloudpickle(tmp_path: Path):
    payload = tmp_path / "transport.pkl"
    env = os.environ.copy()
    env["NSHCONFIG_TRANSPORT_PAYLOAD"] = str(payload)

    _run_python(_LOCAL_SENDER, env=env)
    _run_python(_LOCAL_RECEIVER, env=env)


def test_late_reference_and_forced_schema_rebuild_survive_transport() -> None:
    script = """
from __future__ import annotations

import cloudpickle

import nshconfig as C


class Parent(C.Config):
    child: Child


class Child(C.Config):
    value: int = 1


assert Parent.model_rebuild() is True
Parent.model_rebuild(force=True)
restored = cloudpickle.loads(cloudpickle.dumps(Parent.config_draft()))
assert restored.config_finalize() == Parent(child=Child())
"""
    _run_python(script)
