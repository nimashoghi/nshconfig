"""Provenance: who set every value, where, and why.

Plain assignment is the API; the interpreter's frame is the source. Every draft
write flows through ``Config.__setattr__``, which records (file, line, function,
source text, optional ``source()`` label) per field as an append-only event chain.
Interp resolutions record their marker site plus the read-log of what they consulted
(the "because" chain). ``finalize`` merges draft chains into the final tree so
``explain(final, "optim.lr")`` answers "why did this run use that value" anywhere,
including cluster-side after a pickle round-trip.

Storage note: event chains live in instance ``__dict__`` under a dunder key; the
``Config`` base overrides ``__eq__``/``__hash__`` to compare declared fields only,
so two value-identical configs with different histories stay equal (see config.py).
"""

from __future__ import annotations

import linecache
import sys
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from typing_extensions import override

from .interp import Interp

if TYPE_CHECKING:
    from types import FrameType

__all__ = ["Event", "Explanation", "explain", "provenance", "source"]

PROV_KEY = "__nshconfig_prov__"

_LABEL: ContextVar[str | None] = ContextVar("nshconfig_label", default=None)


class source:
    """Optional semantic label for a block of assignments.

    Example::

        with C.source("sweep:lr"):
            cfg.optim.lr = 1e-4
    """

    def __init__(self, label: str):
        self.label = label

    def __enter__(self) -> source:
        self._token = _LABEL.set(self.label)
        return self

    def __exit__(self, *exc: object) -> None:
        _LABEL.reset(self._token)


@dataclass(frozen=True)
class Event:
    """One provenance event for one field. Plain data; survives pickling."""

    kind: Literal["set", "del", "seed", "interp"]
    value: str | None  # truncated repr; never the live object
    file: str | None = None
    line: int | None = None
    func: str | None = None
    code: str | None = None  # the source text of the assignment, when resolvable
    label: str | None = None  # from source(...)
    site: str | None = None  # interp marker construction site
    reads: tuple[tuple[str, str], ...] = ()  # (dotted path, value repr) the lambda read
    injected: bool = False  # interp came from the class default slot

    def describe(self) -> str:
        if self.kind == "interp":
            origin = "class default" if self.injected else "instance value"
            head = f"interpolated to {self.value} by {self.site} ({origin})"
            because = "".join(f"\n      because {p} = {v}" for p, v in self.reads)
            return head + because
        verb = {"set": f"set to {self.value}", "del": "deleted", "seed": f"seeded with {self.value}"}[self.kind]
        where = ""
        if self.file is not None:
            where = f" at {Path(self.file).name}:{self.line} in {self.func}"
        lab = f"  [{self.label}]" if self.label else ""
        src = f"   | {self.code}" if self.code else ""
        return f"{verb}{where}{lab}{src}"


def _capture(value: Any, frame: FrameType, kind: Literal["set", "del", "seed"]) -> Event:
    fn, line, func = frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name
    return Event(
        kind=kind,
        value=None if kind == "del" else repr(value)[:80],
        file=fn,
        line=line,
        func=func,
        code=linecache.getline(fn, line).strip() or None,
        label=_LABEL.get(),
    )


def _node_log(obj: BaseModel) -> dict[str, list[Event]]:
    d = object.__getattribute__(obj, "__dict__")
    log = d.get(PROV_KEY)
    if log is None:
        log = {}
        d[PROV_KEY] = log
    return log


def record_write(obj: BaseModel, name: str, value: Any, *, depth: int = 2) -> None:
    """Record a user write. ``depth`` frames above this call is the assignment site."""
    _node_log(obj).setdefault(name, []).append(_capture(value, sys._getframe(depth), "set"))


def record_del(obj: BaseModel, name: str, *, depth: int = 2) -> None:
    _node_log(obj).setdefault(name, []).append(_capture(None, sys._getframe(depth), "del"))


def record_seeds(obj: BaseModel, names: list[str], values: dict[str, Any], *, depth: int = 2) -> None:
    frame = sys._getframe(depth)
    log = _node_log(obj)
    for name in names:
        log.setdefault(name, []).append(_capture(values.get(name), frame, "seed"))


def record_interp_events(
    out: BaseModel,
    resolved: list[tuple[str, Interp, Any, list[tuple[str, str]]]],
    injected: list[str],
) -> None:
    """Called by the scope validator for every marker it resolved on this node."""
    log = _node_log(out)
    for name, marker, value, reads in resolved:
        log.setdefault(name, []).append(
            Event(
                kind="interp",
                value=repr(value)[:80],
                site=marker.site,
                reads=tuple(reads),
                injected=name in injected,
                label=_LABEL.get(),
            )
        )


def merge_draft_provenance(draft: BaseModel, final: BaseModel) -> None:
    """After finalize: prepend each draft node's user events onto the final tree.

    Interp events were already recorded on the final's nodes during validation, after
    the user events chronologically, so plain prepending keeps event order.
    """
    draft_log = object.__getattribute__(draft, "__dict__").get(PROV_KEY, {})
    if draft_log:
        final_log = _node_log(final)
        for name, events in draft_log.items():
            final_log[name] = [*events, *final_log.get(name, [])]
    for name in type(final).__pydantic_fields__:
        dv = object.__getattribute__(draft, "__dict__").get(name)
        fv = object.__getattribute__(final, "__dict__").get(name)
        if isinstance(dv, BaseModel) and isinstance(fv, BaseModel):
            merge_draft_provenance(dv, fv)


@dataclass
class Explanation:
    """The answer to "why does ``path`` have this value?". Newest event first."""

    path: str
    current: str
    events: list[Event] = field(default_factory=list)
    default_note: str | None = None

    @override
    def __str__(self) -> str:
        lines = [f"{self.path} = {self.current}"]
        lines.extend(f"  {e.describe()}" for e in reversed(self.events))
        if self.default_note:
            lines.append(f"  {self.default_note}")
        return "\n".join(lines)

    __repr__ = __str__


def explain(cfg: BaseModel, path: str) -> Explanation:
    """Trace why ``path`` has its value. Works on drafts and finals."""
    from .config import is_draft

    *hops, fname = path.split(".")
    node = cfg
    for hop in hops:
        node = getattr(node, hop)
    fields = type(node).__pydantic_fields__
    if fname not in fields:
        raise AttributeError(f"{type(node).__name__} has no field {fname!r}")
    f = fields[fname]
    d = object.__getattribute__(node, "__dict__")
    if fname in d and not isinstance(d[fname], Interp):
        current = repr(d[fname])[:80]
    elif is_draft(cfg) or is_draft(node):
        current = "<pending/unset>"
    else:
        current = repr(getattr(node, fname, "<unset>"))[:80]
    events = list(d.get(PROV_KEY, {}).get(fname, []))
    default_note: str | None = None
    dflt = f.default
    if isinstance(dflt, Interp):
        last = events[-1] if events else None
        shadowed = last is not None and not (
            last.kind == "del" or (last.kind == "interp" and last.injected)
        )
        state = "shadowed" if shadowed else "active"
        default_note = f"class-default rule: {dflt!r}   ({state})"
    elif not f.is_required():
        default_note = f"class default: {dflt!r} ({type(node).__name__})"
    return Explanation(path=path, current=current, events=events, default_note=default_note)


def provenance(cfg: BaseModel, *, _prefix: str = "") -> dict[str, list[Event]]:
    """The full provenance table: dotted path -> event chain (oldest first)."""
    out: dict[str, list[Event]] = {}
    d = object.__getattribute__(cfg, "__dict__")
    for name, events in d.get(PROV_KEY, {}).items():
        out[f"{_prefix}{name}"] = list(events)
    for name in type(cfg).__pydantic_fields__:
        v = d.get(name)
        if isinstance(v, BaseModel):
            out.update(provenance(v, _prefix=f"{_prefix}{name}."))
    return out
