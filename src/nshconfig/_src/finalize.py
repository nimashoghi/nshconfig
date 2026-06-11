"""The one boundary: collect a draft to a plain dict, validate once, return frozen.

Also: ``thaw`` (final -> fresh draft seeded only from explicit provenance, so
bump-and-refinalize re-derives interpolated values instead of pinning them).
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from .config import Config, is_draft
from .provenance import merge_draft_provenance

__all__ = ["finalize", "thaw"]

C = TypeVar("C", bound=Config)


def _emit_required_spine(cls: type[BaseModel]) -> dict[str, Any]:
    """Untouched REQUIRED sub-config subtrees must exist (recursively) so their leaf
    markers participate in resolution and missing-field errors are per-leaf."""
    out: dict[str, Any] = {}
    for n, f in cls.__pydantic_fields__.items():
        ann = f.annotation
        if f.is_required() and isinstance(ann, type) and issubclass(ann, Config):
            out[n] = _emit_required_spine(ann)
    return out


def _collect(node: Config) -> dict[str, Any]:
    out: dict[str, Any] = {}
    d = object.__getattribute__(node, "__dict__")
    for name, f in type(node).__pydantic_fields__.items():
        if name in d:
            v = d[name]
            out[name] = _collect(v) if is_draft(v) else v
        else:
            ann = f.annotation
            if f.is_required() and isinstance(ann, type) and issubclass(ann, Config):
                out[name] = _emit_required_spine(ann)
    return out


def _restore_fields_set(draft: Config, final: Config) -> bool:
    """Make the final's ``__pydantic_fields_set__`` mean "explicitly set by a user".

    collect feeds model_validate a fully materialized dict, so pydantic would record
    every field as set. Restore per node from draft provenance, marking a parent
    field as set iff the user assigned it or its subtree retains explicit content
    (so ``exclude_unset`` dumps and ``thaw`` keep working through nesting).
    """
    explicit: set[str] = set()
    ddict = object.__getattribute__(draft, "__dict__")
    fdict = object.__getattribute__(final, "__dict__")
    user_set = draft.__pydantic_fields_set__
    for name in type(final).__pydantic_fields__:
        dv = ddict.get(name)
        fv = fdict.get(name)
        if is_draft(dv) and isinstance(fv, Config):
            child_has = _restore_fields_set(dv, fv)
            if child_has or name in user_set:
                explicit.add(name)
        elif name in user_set:
            explicit.add(name)
    object.__setattr__(final, "__pydantic_fields_set__", explicit)
    return bool(explicit)


def finalize(draft: C) -> C:
    """Resolve interpolation, validate once, freeze. Idempotent on finals.

    Non-destructive: the draft stays live, so tweak-and-refinalize is the sweep loop.
    """
    if not is_draft(draft):
        return draft
    final = type(draft).model_validate(_collect(draft))
    _restore_fields_set(draft, final)
    merge_draft_provenance(draft, final)
    return final


def thaw(final: C) -> C:
    """A fresh draft seeded only from the final's explicit provenance.

    Interpolated and defaulted values are NOT seeded, so after
    ``thaw -> tweak -> finalize`` they re-derive against the new values.
    Invariant: ``finalize(thaw(x)) == x`` when nothing changed.
    """
    if is_draft(final):
        return final
    cls = type(final)
    fdict = object.__getattribute__(final, "__dict__")
    seeds: dict[str, Any] = {}
    for name in final.__pydantic_fields_set__:
        if name not in cls.__pydantic_fields__:
            continue
        v = fdict.get(name)
        seeds[name] = thaw(v) if isinstance(v, Config) and not is_draft(v) else v
    return cls.draft(**seeds)
