"""Assignment and interpolation provenance for drafts and validated finals."""

import ast
import inspect
import keyword
import linecache
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from pydantic_core import PydanticUndefined
from typing_extensions import override

from .errors import DraftError
from .interp import _Read
from .semantic import stable_semantic_digest
from .state import (
    CANONICAL_PATH_KEY,
    DraftState,
    FinalState,
    ORIGIN_PATH_KEY,
    draft_state,
    final_state,
    is_draft,
    is_path_part,
    set_final_state,
    state_of,
)

if TYPE_CHECKING:
    from .config import Config

__all__ = ["Event", "Explanation", "explain", "provenance", "source"]

EventKind = Literal["set", "delete", "mutate", "interpolate"]
PathPart = str | int
ConfigPath = tuple[PathPart, ...]

_LABEL: ContextVar[str | None] = ContextVar("nshconfig_source_label", default=None)


def safe_repr(value: Any, *, limit: int | None = 120) -> str:
    """Render provenance data without retaining it or trusting its repr."""
    try:
        rendered = _stable_builtin_repr(value, set())
    except Exception as error:
        rendered = f"<repr unavailable: {type(error).__name__}>"
    if limit is None or len(rendered) <= limit:
        return rendered
    return f"{rendered[: limit - 3]}..."


def safe_exception_text(error: BaseException, *, limit: int = 240) -> str:
    """Render a user exception without trusting ``str`` or allowing huge errors."""

    try:
        rendered = str(error)
    except Exception:
        rendered = safe_repr(error, limit=limit)
    if not rendered:
        rendered = type(error).__qualname__
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: limit - 3]}..."


def _stable_builtin_repr(value: Any, active: set[int]) -> str:
    value_type = type(value)
    if value_type not in {dict, list, tuple, set, frozenset}:
        return repr(value)
    identity = id(value)
    if identity in active:
        return "..."
    active.add(identity)
    try:
        if value_type is dict:
            return (
                "{"
                + ", ".join(
                    f"{_stable_builtin_repr(key, active)}: "
                    f"{_stable_builtin_repr(item, active)}"
                    for key, item in value.items()
                )
                + "}"
            )
        rendered = [_stable_builtin_repr(item, active) for item in value]
        if value_type in {set, frozenset}:
            rendered.sort()
        body = ", ".join(rendered)
        if value_type is list:
            return f"[{body}]"
        if value_type is tuple:
            return f"({body}{',' if len(value) == 1 else ''})"
        if value_type is set:
            return f"{{{body}}}" if value else "set()"
        return f"frozenset({{{body}}})"
    finally:
        active.remove(identity)


def provenance_token(value: Any) -> str | None:
    """Return a durable integrity token, or ``None`` for opaque values."""
    return stable_semantic_digest(value)


def stored_path(path: str, name: Any) -> str:
    """Render one physically tagged instance-storage location."""
    if isinstance(name, tuple) and name:
        if name[0] in {"dict", "field"} and len(name) == 2:
            return stored_path(path, name[1])
        if name[0] == "slot" and len(name) == 4:
            return f"{path}[stored slot {name[2]}.{name[3]}]"
    if isinstance(name, str) and name.isidentifier() and not keyword.iskeyword(name):
        return f"{path}.{name}"
    return f"{path}[stored {safe_repr(name, limit=80)}]"


@contextmanager
def source(label: str) -> Iterator[None]:
    """Attach a semantic label to draft operations in this context."""
    if not isinstance(label, str):
        raise TypeError("source() label must be a string")
    if not label:
        raise ValueError("source() label must not be empty")
    token = _LABEL.set(label)
    try:
        yield
    finally:
        _LABEL.reset(token)


@dataclass(frozen=True)
class Event:
    """One immutable provenance event for one declared field."""

    kind: EventKind
    value: str | None = None
    file: str | None = None
    line: int | None = None
    function: str | None = None
    code: str | None = None
    label: str | None = None
    operation: str | None = None
    site: str | None = None
    reads: tuple[tuple[str, str], ...] = ()
    value_token: str | None = None
    read_tokens: tuple[str | None, ...] = ()
    from_default: bool = False
    _read_lineage: tuple[tuple[ConfigPath, ConfigPath], ...] = field(
        default=(), init=False, repr=False, compare=False
    )

    def describe(self) -> str:
        """Return one compact human-readable event description."""
        if self.kind == "interpolate":
            origin = "class default" if self.from_default else "input value"
            text = f"interpolated to {self.value} by {self.site} ({origin})"
            if self.reads:
                text += "".join(
                    f"\n    read {path} = {value}" for path, value in self.reads
                )
            return text

        if self.kind == "delete":
            text = "deleted"
        elif self.kind == "mutate":
            text = f"mutated via {self.operation}; now {self.value}"
        else:
            text = f"set to {self.value}"
        if self.file is not None:
            text += f" at {Path(self.file).name}:{self.line} in {self.function}"
        if self.label:
            text += f" [{self.label}]"
        if self.code:
            text += f" | {self.code}"
        return text


def _user_frame() -> FrameType | None:
    frame = inspect.currentframe()
    if frame is not None:
        frame = frame.f_back
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if not (module == "nshconfig" or module.startswith("nshconfig.")):
            return frame
        frame = frame.f_back
    return None


def _operation_event(
    kind: Literal["set", "delete", "mutate"],
    value: Any,
    *,
    operation: str | None = None,
) -> Event:
    frame = _user_frame()
    if frame is None:
        return Event(
            kind=kind,
            value=None if kind == "delete" else safe_repr(value),
            label=_LABEL.get(),
            operation=operation,
        )
    filename = frame.f_code.co_filename
    line = frame.f_lineno
    return Event(
        kind=kind,
        value=None if kind == "delete" else safe_repr(value),
        file=filename,
        line=line,
        function=frame.f_code.co_name,
        code=linecache.getline(filename, line).strip() or None,
        label=_LABEL.get(),
        operation=operation,
    )


def _append(obj: BaseModel, name: str, event: Event) -> None:
    draft_state(obj).events.setdefault(name, []).append(event)


def record_write(obj: BaseModel, name: str, value: Any) -> None:
    """Record a successful draft assignment."""
    _append(obj, name, _operation_event("set", value))


def record_delete(obj: BaseModel, name: str) -> None:
    """Record a successful draft deletion."""
    _append(obj, name, _operation_event("delete", None))


def record_mutation(obj: BaseModel, name: str, value: Any, operation: str) -> None:
    """Record an in-place mutation and promote the field to explicit input."""
    obj.__pydantic_fields_set__.add(name)
    _append(obj, name, _operation_event("mutate", value, operation=operation))


def interpolation_event(
    marker: Any,
    value: Any,
    reads: Sequence[_Read],
    *,
    from_default: bool,
) -> Event:
    """Build an event after an interpolated value has validated successfully."""
    normalized: list[_Read] = []
    seen: set[tuple[ConfigPath, ConfigPath]] = set()
    for read in reads:
        if read.provisional and any(
            other.anchor == read.anchor
            and (
                (
                    len(other.relative) > len(read.relative)
                    and other.relative[: len(read.relative)] == read.relative
                )
                or (other.relative == read.relative and not other.provisional)
            )
            for other in reads
        ):
            continue
        key = (read.anchor, read.relative)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(read)
    visible_reads = tuple((_render_path(read.path), read.value) for read in normalized)
    lineage = tuple((read.anchor, read.relative) for read in normalized)
    event = Event(
        kind="interpolate",
        value=safe_repr(value),
        label=_LABEL.get(),
        site=marker.site,
        reads=visible_reads,
        value_token=provenance_token(value),
        read_tokens=tuple(read.token for read in normalized),
        from_default=from_default,
    )
    object.__setattr__(event, "_read_lineage", lineage)
    return event


def attach_interpolations(
    obj: BaseModel,
    events: Mapping[str, Event],
    validation_path: ConfigPath,
) -> None:
    """Attach field events created by the validation frame to its final model."""
    private = object.__getattribute__(obj, "__pydantic_private__")
    if private is None:
        private = {}
        object.__setattr__(obj, "__pydantic_private__", private)
    private[ORIGIN_PATH_KEY] = tuple(validation_path)
    current = dict(final_state(obj).events)
    for name, event in events.items():
        current[name] = (*current.get(name, ()), event)
    set_final_state(obj, current)
    if not validation_path:
        _canonicalize_interpolation_reads(obj)


def merge_draft_provenance(draft: Any, final: Any) -> None:
    """Merge draft events into the matching validated graph, including containers."""
    _canonicalize_interpolation_reads(final)
    targets: dict[ConfigPath, tuple[BaseModel, ConfigPath]] = {}
    _index_final_nodes(final, (), targets, set())
    _merge_by_lineage(draft, (), targets, set(), include_final=False)


def copy_provenance(source: Any, target: Any) -> None:
    """Copy final provenance onto an equal revalidated graph."""
    _canonicalize_interpolation_reads(source)
    _canonicalize_interpolation_reads(target)
    targets: dict[ConfigPath, tuple[BaseModel, ConfigPath]] = {}
    _index_final_nodes(target, (), targets, set())
    _merge_by_lineage(source, (), targets, set(), include_final=True)


def final_reuse_error(value: BaseModel) -> str | None:
    """Reject reuse when a shallow-frozen final contains an evaluated recipe.

    A final can still contain mutable containers.  Without retaining immutable
    dependency and target snapshots, an old interpolation event cannot prove
    that its concrete result remains fresh.  Reusing such a branch would make
    provenance look authoritative while silently carrying stale values.
    """

    return _interpolation_history_error(value, (), set())


def _interpolation_history_error(
    value: Any, path: ConfigPath, active: set[int]
) -> str | None:
    identity = id(value)
    if identity in active:
        return None
    if not isinstance(value, (BaseModel, Mapping, list, tuple)):
        return None
    active.add(identity)
    try:
        if isinstance(value, BaseModel):
            state = state_of(value)
            if isinstance(state, FinalState):
                for name, events in state.events.items():
                    if any(event.kind == "interpolate" for event in events):
                        event_path = _render_path((*path, name))
                        return (
                            f"interpolation history at {event_path!r} cannot be proven fresh; "
                            "reuse the original draft recipe instead"
                        )
            data = object.__getattribute__(value, "__dict__")
            for name in type(value).__pydantic_fields__:
                if name in data:
                    reason = _interpolation_history_error(
                        data[name], (*path, name), active
                    )
                    if reason is not None:
                        return reason
            return None
        if isinstance(value, Mapping):
            for key, item in value.items():
                part: PathPart = key if is_path_part(key) else safe_repr(key)
                reason = _interpolation_history_error(item, (*path, part), active)
                if reason is not None:
                    return reason
            return None
        for index, item in enumerate(value):
            reason = _interpolation_history_error(item, (*path, index), active)
            if reason is not None:
                return reason
        return None
    finally:
        active.remove(identity)


def merge_reused_final_provenance(
    source: BaseModel,
    target_root: BaseModel,
    validation_path: ConfigPath,
) -> None:
    """Merge one portable final branch after its new root graph is complete."""
    _canonicalize_interpolation_reads(source)
    _canonicalize_interpolation_reads(target_root)
    targets: dict[ConfigPath, tuple[BaseModel, ConfigPath]] = {}
    _index_final_nodes(target_root, (), targets, set())
    _merge_by_lineage(source, validation_path, targets, set(), include_final=True)


def _origin_path(value: BaseModel) -> ConfigPath | None:
    return _private_path(value, ORIGIN_PATH_KEY)


def _canonical_path(value: BaseModel) -> ConfigPath | None:
    return _private_path(value, CANONICAL_PATH_KEY)


def _private_path(value: BaseModel, key: str) -> ConfigPath | None:
    private = object.__getattribute__(value, "__pydantic_private__")
    if private is None:
        return None
    path = private.get(key)
    if not isinstance(path, tuple) or not all(is_path_part(part) for part in path):
        return None
    return path


def _index_final_nodes(
    value: Any,
    path: ConfigPath,
    out: dict[ConfigPath, tuple[BaseModel, ConfigPath]],
    active: set[int],
) -> None:
    identity = id(value)
    if identity in active:
        return
    if isinstance(value, (BaseModel, Mapping, list, tuple)):
        active.add(identity)
    else:
        return
    try:
        if isinstance(value, BaseModel):
            origin = _origin_path(value)
            if origin is not None:
                out.setdefault(origin, (value, path))
            data = object.__getattribute__(value, "__dict__")
            for name in type(value).__pydantic_fields__:
                if name in data:
                    _index_final_nodes(data[name], (*path, name), out, active)
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                part: PathPart = key if is_path_part(key) else safe_repr(key)
                _index_final_nodes(item, (*path, part), out, active)
            return
        for index, item in enumerate(value):
            _index_final_nodes(item, (*path, index), out, active)
    finally:
        active.remove(identity)


def _merge_by_lineage(
    source: Any,
    path: ConfigPath,
    targets: Mapping[ConfigPath, tuple[BaseModel, ConfigPath]],
    active: set[int],
    *,
    include_final: bool,
) -> None:
    identity = id(source)
    if identity in active:
        return
    if isinstance(source, (BaseModel, Mapping, list, tuple)):
        active.add(identity)
    else:
        return
    try:
        if isinstance(source, BaseModel):
            target_entry = targets.get(path)
            state = state_of(source)
            if target_entry is not None and (
                isinstance(state, DraftState)
                or (include_final and isinstance(state, FinalState))
            ):
                target, target_path = target_entry
                object.__setattr__(
                    target,
                    "__pydantic_fields_set__",
                    set(source.__pydantic_fields_set__),
                )
                if isinstance(state, DraftState):
                    source_events = {
                        name: tuple(events) for name, events in state.events.items()
                    }
                else:
                    source_path = _canonical_path(source)
                    source_events = {
                        name: _rebase_events(events, source_path, target_path)
                        for name, events in state.events.items()
                    }
                target_events = dict(final_state(target).events)
                for name, events in source_events.items():
                    target_events[name] = (*events, *target_events.get(name, ()))
                set_final_state(target, target_events)
            data = object.__getattribute__(source, "__dict__")
            for name in type(source).__pydantic_fields__:
                if name in data:
                    _merge_by_lineage(
                        data[name],
                        (*path, name),
                        targets,
                        active,
                        include_final=include_final,
                    )
            return
        if isinstance(source, Mapping):
            for key, item in source.items():
                part: PathPart = key if is_path_part(key) else safe_repr(key)
                _merge_by_lineage(
                    item,
                    (*path, part),
                    targets,
                    active,
                    include_final=include_final,
                )
            return
        for index, item in enumerate(source):
            _merge_by_lineage(
                item,
                (*path, index),
                targets,
                active,
                include_final=include_final,
            )
    finally:
        active.remove(identity)


def _rebase_events(
    events: Sequence[Event],
    source_path: ConfigPath | None,
    target_path: ConfigPath,
) -> tuple[Event, ...]:
    """Move dependency paths that are local to a reused final branch."""
    if source_path is None or source_path == target_path:
        return tuple(events)
    output: list[Event] = []
    prefix_length = len(source_path)
    for event in events:
        reads: list[tuple[str, str]] = []
        changed = False
        for path, rendered in event.reads:
            try:
                parts = _parse_path(path)
            except ValueError:
                reads.append((path, rendered))
                continue
            if parts[:prefix_length] == source_path:
                path = _render_path((*target_path, *parts[prefix_length:]))
                changed = True
            reads.append((path, rendered))
        output.append(replace(event, reads=tuple(reads)) if changed else event)
    return tuple(output)


def _canonicalize_interpolation_reads(root: Any) -> None:
    """Resolve temporary validation paths against the completed final graph."""
    nodes: list[tuple[BaseModel, ConfigPath]] = []
    _collect_final_nodes(root, (), nodes, set())
    relocated = {
        origin: current
        for node, current in nodes
        if (origin := _origin_path(node)) is not None
    }
    for node, current_path in nodes:
        private = object.__getattribute__(node, "__pydantic_private__")
        if private is not None and _origin_path(node) is not None:
            private[CANONICAL_PATH_KEY] = current_path
        state = state_of(node)
        if not isinstance(state, FinalState):
            continue
        changed = False
        local: dict[str, tuple[Event, ...]] = {}
        for name, events in state.events.items():
            updated: list[Event] = []
            for event in events:
                if not event._read_lineage:
                    updated.append(event)
                    continue
                reads = tuple(
                    (
                        _render_path((*relocated.get(anchor, anchor), *relative)),
                        rendered,
                    )
                    for (_, rendered), (anchor, relative) in zip(
                        event.reads, event._read_lineage
                    )
                )
                updated.append(replace(event, reads=reads))
                changed = True
            local[name] = tuple(updated)
        if changed:
            set_final_state(node, local)


def _collect_final_nodes(
    value: Any,
    path: ConfigPath,
    out: list[tuple[BaseModel, ConfigPath]],
    active: set[int],
) -> None:
    identity = id(value)
    if identity in active:
        return
    if isinstance(value, (BaseModel, Mapping, list, tuple)):
        active.add(identity)
    else:
        return
    try:
        if isinstance(value, BaseModel):
            out.append((value, path))
            data = object.__getattribute__(value, "__dict__")
            for name in type(value).__pydantic_fields__:
                if name in data:
                    _collect_final_nodes(data[name], (*path, name), out, active)
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                part: PathPart = key if is_path_part(key) else safe_repr(key)
                _collect_final_nodes(item, (*path, part), out, active)
            return
        for index, item in enumerate(value):
            _collect_final_nodes(item, (*path, index), out, active)
    finally:
        active.remove(identity)


def _render_path(parts: Sequence[PathPart]) -> str:
    rendered = ""
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif part.isidentifier() and not keyword.iskeyword(part):
            rendered += ("." if rendered else "") + part
        else:
            rendered += f"[{part!r}]"
    return rendered


def _parse_path(path: str | Sequence[PathPart]) -> ConfigPath:
    if not isinstance(path, str):
        parts = tuple(path)
        if not all(is_path_part(part) for part in parts):
            raise ValueError("path components must be exact strings or integers")
        return parts
    if not path:
        raise ValueError("path must name a field")
    try:
        expression = f"root{path}" if path.startswith("[") else f"root.{path}"
        node = ast.parse(expression, mode="eval").body
    except SyntaxError as error:
        raise ValueError(f"invalid config path {path!r}") from error

    def collect(expr: ast.expr) -> list[PathPart]:
        if isinstance(expr, ast.Name) and expr.id == "root":
            return []
        if isinstance(expr, ast.Attribute):
            return [*collect(expr.value), expr.attr]
        if isinstance(expr, ast.Subscript):
            parts = collect(expr.value)
            index = expr.slice
            if (
                isinstance(index, ast.UnaryOp)
                and isinstance(index.op, ast.USub)
                and isinstance(index.operand, ast.Constant)
                and isinstance(index.operand.value, int)
                and not isinstance(index.operand.value, bool)
            ):
                return [*parts, -index.operand.value]
            if not isinstance(index, ast.Constant) or not is_path_part(index.value):
                raise ValueError(f"path indices must be strings or integers: {path!r}")
            value = index.value
            if isinstance(value, str):
                return [*parts, value]
            if type(value) is int:
                return [*parts, value]
            raise ValueError(f"path indices must be strings or integers: {path!r}")
        raise ValueError(f"invalid config path {path!r}")

    return tuple(collect(node))


def _read_part(value: Any, part: PathPart) -> Any:
    if isinstance(part, str) and isinstance(value, BaseModel):
        return getattr(value, part)
    return value[part]


def _field_owner(cfg: BaseModel, parts: ConfigPath) -> tuple[BaseModel, str]:
    if not parts or not isinstance(parts[-1], str):
        raise ValueError("explain() paths must end at a declared Config field")
    owner: Any = cfg
    for part in parts[:-1]:
        owner = _read_part(owner, part)
    name = parts[-1]
    from .config import Config

    if not isinstance(owner, Config) or name not in type(owner).__pydantic_fields__:
        raise AttributeError(
            f"{_render_path(parts)!r} does not name a declared Config field"
        )
    return owner, name


def _explanation_target(
    cfg: BaseModel, parts: ConfigPath
) -> tuple[BaseModel, str, Any]:
    """Resolve a path and its nearest provenance-bearing declared field."""
    if not parts:
        raise ValueError(
            "explain() paths must name a declared Config field or its value"
        )
    value: Any = cfg
    nearest: tuple[BaseModel, str] | None = None
    from .config import Config

    for part in parts:
        if isinstance(value, BaseModel):
            fields = type(value).__pydantic_fields__
            if not isinstance(part, str) or part not in fields:
                raise AttributeError(
                    f"{_render_path(parts)!r} does not follow declared Config fields"
                )
            if isinstance(value, Config):
                nearest = (value, part)
            data = object.__getattribute__(value, "__dict__")
            value = data.get(part, PydanticUndefined)
        else:
            if value is PydanticUndefined:
                raise KeyError(part)
            value = value[part]
    if nearest is None:
        raise AttributeError(
            f"{_render_path(parts)!r} does not name a declared Config field or its value"
        )
    return (*nearest, value)


@dataclass(frozen=True)
class Explanation:
    """A field value, its direct events, its origin, and interpolation causes."""

    path: str
    current: str
    events: tuple[Event, ...] = ()
    origin: str | None = None
    causes: tuple["Explanation", ...] = ()

    @override
    def __str__(self) -> str:
        lines = [f"{self.path} = {self.current}"]
        lines.extend(f"  {event.describe()}" for event in self.events)
        if self.origin:
            lines.append(f"  {self.origin}")
        for cause in self.causes:
            rendered = str(cause).replace("\n", "\n    ")
            lines.append(f"  because {rendered}")
        return "\n".join(lines)

    __repr__ = __str__


def explain(cfg: "Config", path: str | Sequence[PathPart]) -> Explanation:
    """Explain why a declared field has its current or pending value."""
    _require_config(cfg, "explain")
    return _explain(cfg, path, frozenset())


def _explain(
    cfg: "Config",
    path: str | Sequence[PathPart],
    seen_paths: frozenset[str],
) -> Explanation:
    _canonicalize_interpolation_reads(cfg)
    parts = _parse_path(path)
    rendered_path = _render_path(parts)
    owner, name, value = _explanation_target(cfg, parts)
    field = type(owner).__pydantic_fields__[name]
    if value is PydanticUndefined or _is_interp(value):
        current = "<pending/unset>" if is_draft(owner) else "<unset>"
    else:
        current = safe_repr(value)

    state = state_of(owner)
    if isinstance(state, DraftState):
        events = tuple(state.events.get(name, ()))
    elif isinstance(state, FinalState):
        events = state.events.get(name, ())
    else:
        events = ()

    if _is_interp(field.default):
        origin = f"interpolated class default: {field.default!r}"
    elif field.default_factory is not None:
        factory = field.default_factory
        try:
            factory_name = getattr(factory, "__qualname__")
        except Exception:
            factory_name = None
        if not isinstance(factory_name, str):
            factory_name = safe_repr(factory)
        origin = f"default factory: {factory_name}"
    elif field.default is not PydanticUndefined:
        origin = f"class default: {safe_repr(field.default)}"
    else:
        origin = None

    causes: list[Explanation] = []
    if rendered_path not in seen_paths:
        seen = seen_paths | {rendered_path}
        read_paths = dict.fromkeys(
            read_path
            for event in events
            if event.kind == "interpolate"
            for read_path, _ in event.reads
        )
        for read_path in read_paths:
            try:
                causes.append(_explain(cfg, read_path, seen))
            except (AttributeError, KeyError, IndexError, TypeError, ValueError):
                continue
    return Explanation(rendered_path, current, events, origin, tuple(causes))


def provenance(cfg: "Config") -> dict[str, tuple[Event, ...]]:
    """Return all local field event chains under a Config graph."""
    _require_config(cfg, "provenance")
    _canonicalize_interpolation_reads(cfg)
    out: dict[str, tuple[Event, ...]] = {}
    _collect_provenance(cfg, (), out, set())
    return out


def _require_config(value: Any, operation: str) -> None:
    from .config import Config

    if not isinstance(value, Config):
        raise TypeError(f"{operation}() expects a Config instance")
    if not isinstance(state_of(value), (DraftState, FinalState)):
        raise DraftError(
            f"{operation}() is unavailable until Config lifecycle state is established"
        )


def _collect_provenance(
    value: Any,
    path: ConfigPath,
    out: dict[str, tuple[Event, ...]],
    active: set[int],
) -> None:
    identity = id(value)
    if identity in active:
        return
    active.add(identity)
    try:
        if isinstance(value, BaseModel):
            state = state_of(value)
            if isinstance(state, DraftState):
                local = {name: tuple(events) for name, events in state.events.items()}
            elif isinstance(state, FinalState):
                local = state.events
            else:
                local = {}
            for name, events in local.items():
                out[_render_path((*path, name))] = tuple(events)
            data = object.__getattribute__(value, "__dict__")
            for name in type(value).__pydantic_fields__:
                if name in data:
                    _collect_provenance(data[name], (*path, name), out, active)
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                part: PathPart = key if is_path_part(key) else safe_repr(key)
                _collect_provenance(item, (*path, part), out, active)
            return
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                _collect_provenance(item, (*path, index), out, active)
    finally:
        active.remove(identity)


def event_to_dict(event: Event) -> dict[str, Any]:
    """Convert an event to JSON-compatible record data."""
    value = asdict(event)
    value.pop("_read_lineage", None)
    value["reads"] = [list(read) for read in event.reads]
    value["read_tokens"] = list(event.read_tokens)
    return value


def event_from_dict(value: Mapping[str, Any]) -> Event:
    """Validate and rebuild an event from record data."""
    fields = {
        event_field.name
        for event_field in Event.__dataclass_fields__.values()
        if not event_field.name.startswith("_")
    }
    unknown = set(value) - fields
    if unknown:
        raise ValueError(f"unknown provenance event keys: {sorted(unknown)!r}")
    payload = dict(value)
    payload["reads"] = tuple(tuple(read) for read in payload.get("reads", ()))
    payload["read_tokens"] = tuple(payload.get("read_tokens", ()))
    return Event(**payload)


def provenance_value(cfg: BaseModel, path: str | Sequence[PathPart]) -> Any:
    """Resolve one structural provenance path without invoking custom access hooks."""
    current: Any = cfg
    for part in _parse_path(path):
        if type(current) in {list, tuple} and (type(part) is not int or part < 0):
            raise ValueError(
                "sequence provenance indices must be non-negative integers"
            )
        current = _read_part(current, part)
    return current


def canonicalize_path(path: str | Sequence[PathPart]) -> str:
    """Return the one durable spelling of a structural Config path."""
    return _render_path(_parse_path(path))


def restore_provenance(cfg: BaseModel, values: Mapping[str, Sequence[Event]]) -> None:
    """Restore record provenance onto matching Config nodes."""
    grouped: dict[int, tuple[BaseModel, dict[str, tuple[Event, ...]]]] = {}
    for path, events in values.items():
        parts = _parse_path(path)
        owner, name = _field_owner(cfg, parts)
        entry = grouped.setdefault(id(owner), (owner, dict(final_state(owner).events)))
        entry[1][name] = tuple(events)
    for owner, local in grouped.values():
        set_final_state(owner, local)


def _is_interp(value: Any) -> bool:
    from .interp import Interp

    return isinstance(value, Interp)
