"""Construction and mutation behavior for incomplete Config drafts."""

import difflib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, SupportsIndex, TypeVar, cast

from pydantic import BaseModel
from pydantic_core import PydanticUndefined
from typing_extensions import override

from .annotations import unwrap_annotation
from .errors import DraftError, UnsetError
from .interp import Interp
from .provenance import (
    record_delete,
    record_mutation,
    record_write,
    safe_exception_text,
    safe_repr,
)
from .semantic import is_known_immutable_atom
from .state import STATE_KEY, DraftState, FinalState, draft_state, is_draft, state_of

if TYPE_CHECKING:
    from .config import Config

__all__ = ["draft"]

C = TypeVar("C", bound="Config")

_INPLACE_ASSIGNMENT_ACK = "_nshconfig_inplace_assignment_ack"


def _acknowledge_inplace_assignment(value: Any) -> None:
    object.__setattr__(value, _INPLACE_ASSIGNMENT_ACK, True)


def _consume_inplace_assignment_ack(value: Any) -> bool:
    try:
        acknowledged = object.__getattribute__(value, _INPLACE_ASSIGNMENT_ACK)
    except AttributeError:
        return False
    assert acknowledged is True, "invalid in-place assignment acknowledgment"
    object.__delattr__(value, _INPLACE_ASSIGNMENT_ACK)
    return True


@dataclass
class _MutationTracker:
    owner: BaseModel
    field_name: str

    def _reachable_root(self, value: Any) -> Any:
        data = object.__getattribute__(self.owner, "__dict__")
        current = data.get(self.field_name, PydanticUndefined)
        if not _contains_identity(current, value, set()):
            return PydanticUndefined
        return current

    def prepare(self, value: Any) -> None:
        current = self._reachable_root(value)
        if current is PydanticUndefined:
            return
        assert_tracked_integrity(
            current,
            f"{type(self.owner).__name__}.{self.field_name}",
        )

    def record(self, value: Any, operation: str) -> None:
        current = self._reachable_root(value)
        if current is PydanticUndefined:
            return
        _sync_tracking_tree(current, set())
        record_mutation(self.owner, self.field_name, value, operation)


def _contains_identity(container: Any, target: Any, active: set[int]) -> bool:
    if container is target:
        return True
    identity = id(container)
    if identity in active:
        return False
    if type(container) is dict or isinstance(container, _DraftDict):
        active.add(identity)
        try:
            return any(
                _contains_identity(key, target, active)
                or _contains_identity(item, target, active)
                for key, item in container.items()
            )
        finally:
            active.remove(identity)
    if type(container) in {list, tuple, set, frozenset} or isinstance(
        container, (_DraftList, _DraftSet)
    ):
        active.add(identity)
        try:
            return any(_contains_identity(item, target, active) for item in container)
        finally:
            active.remove(identity)
    return False


def _tracking_token(value: Any, active: set[int]) -> Any:
    if value is None:
        return (type(None), None)
    if type(value) in {bool, int, str, bytes}:
        return (type(value), value)
    if type(value) is float:
        return (float, value.hex())
    if type(value) is complex:
        return (complex, value.real.hex(), value.imag.hex())
    identity = id(value)
    if identity in active:
        return ("cycle", identity)
    if type(value) is dict or isinstance(value, _DraftDict):
        active.add(identity)
        try:
            return (
                type(value),
                tuple(
                    (_tracking_token(key, active), _tracking_token(item, active))
                    for key, item in value.items()
                ),
            )
        finally:
            active.remove(identity)
    if type(value) in {list, tuple} or isinstance(value, _DraftList):
        active.add(identity)
        try:
            return (type(value), tuple(_tracking_token(item, active) for item in value))
        finally:
            active.remove(identity)
    if type(value) in {set, frozenset} or isinstance(value, _DraftSet):
        active.add(identity)
        try:
            return (
                type(value),
                frozenset(_tracking_token(item, active) for item in value),
            )
        finally:
            active.remove(identity)
    return (type(value), identity)


def _sync_tracking_tree(value: Any, active: set[int]) -> None:
    identity = id(value)
    if identity in active:
        return
    if not (
        type(value) in {dict, list, tuple, set, frozenset}
        or isinstance(value, (_DraftList, _DraftDict, _DraftSet))
    ):
        return
    active.add(identity)
    try:
        if type(value) is dict or isinstance(value, _DraftDict):
            for key, item in cast(dict[Any, Any], value).items():
                _sync_tracking_tree(key, active)
                _sync_tracking_tree(item, active)
        else:
            for _, item in enumerate(value):
                _sync_tracking_tree(item, active)
        if isinstance(value, (_DraftList, _DraftDict, _DraftSet)):
            value._nshconfig_shadow = _tracking_token(value, set())
    finally:
        active.remove(identity)


def assert_tracked_integrity(
    value: Any, path: str, active: set[int] | None = None
) -> None:
    """Reject built-in mutations that bypass the tracked Python methods."""
    if active is None:
        active = set()
    identity = id(value)
    traversable = type(value) in {dict, list, tuple, set, frozenset} or isinstance(
        value, (_DraftList, _DraftDict, _DraftSet)
    )
    if identity in active or not traversable:
        return
    active.add(identity)
    try:
        if isinstance(value, (_DraftList, _DraftDict, _DraftSet)) and (
            value._nshconfig_shadow != _tracking_token(value, set())
        ):
            raise DraftError(
                f"{path} was mutated through an untracked operation; use its normal "
                "list, dict, or set methods"
            )
        if type(value) is dict or isinstance(value, _DraftDict):
            for key, item in cast(dict[Any, Any], value).items():
                assert_tracked_integrity(key, f"{path}.<key>", active)
                assert_tracked_integrity(
                    item,
                    f"{path}[{safe_repr(key, limit=80)}]",
                    active,
                )
        else:
            for index, item in enumerate(value):
                assert_tracked_integrity(item, f"{path}[{index}]", active)
    finally:
        active.remove(identity)


class _DraftList(list[Any]):
    def __init__(self, tracker: _MutationTracker):
        super().__init__()
        self._nshconfig_tracker = tracker
        self._nshconfig_shadow: Any = None

    def _record(self, operation: str) -> None:
        self._nshconfig_tracker.record(self, operation)

    def _prepare(self) -> None:
        self._nshconfig_tracker.prepare(self)

    @override
    def append(self, value: Any) -> None:
        self._prepare()
        super().append(_track_value(value, self._nshconfig_tracker))
        self._record("append")

    @override
    def extend(self, values: Any) -> None:
        self._prepare()
        memo: dict[int, Any] = {}
        tracked = [
            _track_value(value, self._nshconfig_tracker, memo) for value in values
        ]
        super().extend(tracked)
        self._record("extend")

    @override
    def insert(self, index: SupportsIndex, value: Any) -> None:
        self._prepare()
        super().insert(index, _track_value(value, self._nshconfig_tracker))
        self._record("insert")

    @override
    def pop(self, index: SupportsIndex = -1) -> Any:
        self._prepare()
        value = super().pop(index)
        self._record("pop")
        return value

    @override
    def remove(self, value: Any) -> None:
        self._prepare()
        super().remove(value)
        self._record("remove")

    @override
    def clear(self) -> None:
        self._prepare()
        super().clear()
        self._record("clear")

    @override
    def reverse(self) -> None:
        self._prepare()
        super().reverse()
        self._record("reverse")

    @override
    def sort(self, *args: Any, **kwargs: Any) -> None:
        self._prepare()
        candidate = list(self)
        candidate.sort(*args, **kwargs)
        list.__setitem__(self, slice(None), candidate)
        self._record("sort")

    @override
    def __setitem__(self, key: Any, value: Any) -> None:
        self._prepare()
        if isinstance(key, slice):
            value = [_track_value(item, self._nshconfig_tracker) for item in value]
        else:
            value = _track_value(value, self._nshconfig_tracker)
        super().__setitem__(key, value)
        self._record("setitem")

    @override
    def __delitem__(self, key: Any) -> None:
        self._prepare()
        super().__delitem__(key)
        self._record("delitem")

    @override
    def __iadd__(self, values: Any) -> "_DraftList":
        self.extend(values)
        _acknowledge_inplace_assignment(self)
        return self

    @override
    def __imul__(self, count: SupportsIndex) -> "_DraftList":
        self._prepare()
        super().__imul__(count)
        self._record("repeat")
        _acknowledge_inplace_assignment(self)
        return self


class _DraftDict(dict[Any, Any]):
    def __init__(self, tracker: _MutationTracker):
        super().__init__()
        self._nshconfig_tracker = tracker
        self._nshconfig_shadow: Any = None

    def _record(self, operation: str) -> None:
        self._nshconfig_tracker.record(self, operation)

    def _prepare(self) -> None:
        self._nshconfig_tracker.prepare(self)

    @override
    def __setitem__(self, key: Any, value: Any) -> None:
        self._prepare()
        super().__setitem__(key, _track_value(value, self._nshconfig_tracker))
        self._record("setitem")

    @override
    def __delitem__(self, key: Any) -> None:
        self._prepare()
        super().__delitem__(key)
        self._record("delitem")

    @override
    def clear(self) -> None:
        self._prepare()
        super().clear()
        self._record("clear")

    @override
    def pop(self, key: Any, *default: Any) -> Any:
        self._prepare()
        value = super().pop(key, *default)
        self._record("pop")
        return value

    @override
    def popitem(self) -> tuple[Any, Any]:
        self._prepare()
        value = super().popitem()
        self._record("popitem")
        return value

    @override
    def setdefault(self, key: Any, default: Any = None) -> Any:
        self._prepare()
        if key in self:
            return super().__getitem__(key)
        value = _track_value(default, self._nshconfig_tracker)
        super().__setitem__(key, value)
        self._record("setdefault")
        return value

    @override
    def update(self, *args: Any, **kwargs: Any) -> None:
        self._prepare()
        values = dict(*args, **kwargs)
        memo: dict[int, Any] = {}
        tracked = {
            key: _track_value(value, self._nshconfig_tracker, memo)
            for key, value in values.items()
        }
        candidate = dict(self)
        candidate.update(tracked)
        dict.clear(self)
        dict.update(self, candidate)
        self._record("update")

    @override
    def __ior__(self, other: Any) -> "_DraftDict":
        self.update(other)
        _acknowledge_inplace_assignment(self)
        return self


class _DraftSet(set[Any]):
    def __init__(self, tracker: _MutationTracker):
        super().__init__()
        self._nshconfig_tracker = tracker
        self._nshconfig_shadow: Any = None

    def _record(self, operation: str) -> None:
        self._nshconfig_tracker.record(self, operation)

    def _prepare(self) -> None:
        self._nshconfig_tracker.prepare(self)

    def _transactional_update(self, operation: str, *others: Any) -> None:
        self._prepare()
        candidate = set(self)
        getattr(candidate, operation)(*others)
        set.clear(self)
        set.update(self, candidate)
        self._record(operation)

    @override
    def add(self, value: Any) -> None:
        self._prepare()
        super().add(value)
        self._record("add")

    @override
    def discard(self, value: Any) -> None:
        self._prepare()
        super().discard(value)
        self._record("discard")

    @override
    def remove(self, value: Any) -> None:
        self._prepare()
        super().remove(value)
        self._record("remove")

    @override
    def pop(self) -> Any:
        self._prepare()
        value = super().pop()
        self._record("pop")
        return value

    @override
    def clear(self) -> None:
        self._prepare()
        super().clear()
        self._record("clear")

    @override
    def update(self, *others: Any) -> None:
        self._transactional_update("update", *others)

    @override
    def intersection_update(self, *others: Any) -> None:
        self._transactional_update("intersection_update", *others)

    @override
    def difference_update(self, *others: Any) -> None:
        self._transactional_update("difference_update", *others)

    @override
    def symmetric_difference_update(self, other: Any) -> None:
        self._transactional_update("symmetric_difference_update", other)

    @override
    def __ior__(self, other: Any) -> "_DraftSet":
        self.update(other)
        _acknowledge_inplace_assignment(self)
        return self

    @override
    def __iand__(self, other: Any) -> "_DraftSet":
        self.intersection_update(other)
        _acknowledge_inplace_assignment(self)
        return self

    @override
    def __isub__(self, other: Any) -> "_DraftSet":
        self.difference_update(other)
        _acknowledge_inplace_assignment(self)
        return self

    @override
    def __ixor__(self, other: Any) -> "_DraftSet":
        self.symmetric_difference_update(other)
        _acknowledge_inplace_assignment(self)
        return self


def _track_value(
    value: Any,
    tracker: _MutationTracker,
    memo: dict[int, Any] | None = None,
) -> Any:
    if memo is None:
        memo = {}
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if type(value) is list or isinstance(value, _DraftList):
        output = _DraftList(tracker)
        memo[identity] = output
        list.extend(output, (_track_value(item, tracker, memo) for item in value))
        _sync_tracking_tree(output, set())
        return output
    if type(value) is dict or isinstance(value, _DraftDict):
        output = _DraftDict(tracker)
        memo[identity] = output
        dict.update(
            output,
            {key: _track_value(item, tracker, memo) for key, item in value.items()},
        )
        _sync_tracking_tree(output, set())
        return output
    if type(value) is set or isinstance(value, _DraftSet):
        output = _DraftSet(tracker)
        memo[identity] = output
        set.update(output, value)
        _sync_tracking_tree(output, set())
        return output
    if type(value) is tuple:
        output = tuple(_track_value(item, tracker, memo) for item in value)
        memo[identity] = output
        return output
    if type(value) is frozenset:
        output = frozenset(_track_value(item, tracker, memo) for item in value)
        memo[identity] = output
        return output
    return value


def _requires_pin_on_read(value: Any, active: set[int] | None = None) -> bool:
    """Return whether inner mutation cannot be tracked through built-in wrappers."""
    if is_known_immutable_atom(value):
        return False
    if is_draft(value):
        return False
    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        return True
    if type(value) is dict or isinstance(value, _DraftDict):
        active.add(identity)
        try:
            return any(
                _requires_pin_on_read(key, active)
                or _requires_pin_on_read(item, active)
                for key, item in value.items()
            )
        finally:
            active.remove(identity)
    if type(value) in {list, tuple, set, frozenset} or isinstance(
        value, (_DraftList, _DraftSet)
    ):
        active.add(identity)
        try:
            return any(_requires_pin_on_read(item, active) for item in value)
        finally:
            active.remove(identity)
    return True


def draft(cls: type[C]) -> C:
    """Create an incomplete mutable instance without running Pydantic hooks."""
    from .config import Config

    if not isinstance(cls, type) or not issubclass(cls, Config):
        raise TypeError("draft() expects a Config subclass")
    model = object.__new__(cls)
    object.__setattr__(model, "__dict__", {})
    object.__setattr__(model, "__pydantic_fields_set__", set())
    object.__setattr__(model, "__pydantic_extra__", None)

    private: dict[str, Any] = {STATE_KEY: DraftState()}
    object.__setattr__(model, "__pydantic_private__", private)
    return model


def set_field(obj: BaseModel, name: str, value: Any) -> None:
    """Implement one draft assignment after Config has identified draft state."""
    fields = type(obj).__pydantic_fields__
    if name not in fields:
        hint = difflib.get_close_matches(name, fields, n=1)
        suffix = f"; did you mean {hint[0]!r}?" if hint else ""
        raise AttributeError(f"{type(obj).__name__} has no field {name!r}{suffix}")
    state = draft_state(obj)
    data = object.__getattribute__(obj, "__dict__")
    try:
        tracker = object.__getattribute__(value, "_nshconfig_tracker")
    except AttributeError:
        tracker = None
    if (
        data.get(name, PydanticUndefined) is value
        and isinstance(tracker, _MutationTracker)
        and tracker.owner is obj
        and tracker.field_name == name
        and _consume_inplace_assignment_ack(value)
    ):
        # Python assigns an in-place operator result back to the attribute.
        # The tracked container already committed and recorded that mutation.
        return
    if isinstance(value, Interp):
        data.pop(name, None)
        state.pending[name] = value
        tracked = value
    else:
        assert_tracked_integrity(value, f"{type(obj).__name__}.{name}")
        tracked = _track_value(value, _MutationTracker(obj, name))
        data[name] = tracked
        state.pending.pop(name, None)
    obj.__pydantic_fields_set__.add(name)
    state.materialized.discard(name)
    record_write(obj, name, tracked)


def delete_field(obj: BaseModel, name: str) -> bool:
    """Delete one declared draft field and reactivate its schema default."""
    if name not in type(obj).__pydantic_fields__:
        return False
    data = object.__getattribute__(obj, "__dict__")
    state = draft_state(obj)
    existed = (
        name in data or name in state.pending or name in obj.__pydantic_fields_set__
    )
    data.pop(name, None)
    state.pending.pop(name, None)
    obj.__pydantic_fields_set__.discard(name)
    state.materialized.discard(name)
    if existed:
        record_delete(obj, name)
    return True


def read_field(obj: BaseModel, name: str) -> Any:
    """Materialize a readable draft default or an auto-vivified Config child."""
    model_field = type(obj).__pydantic_fields__[name]
    if name in draft_state(obj).pending:
        raise UnsetError(
            f"{type(obj).__name__}.{name} is pending interpolation; read it after finalize()"
        )
    if isinstance(model_field.default, Interp):
        raise UnsetError(
            f"{type(obj).__name__}.{name} is pending interpolation; read it after finalize()"
        )

    if model_field.is_required():
        child_type = _concrete_config_type(model_field.annotation)
        if child_type is None:
            raise UnsetError(f"{type(obj).__name__}.{name} is not set on this draft")
        value = draft(child_type)
    else:
        data = object.__getattribute__(obj, "__dict__")
        validated_data: dict[str, Any] = {}
        for field_name, earlier_field in type(obj).__pydantic_fields__.items():
            if field_name == name:
                break
            if field_name in data and not isinstance(data[field_name], Interp):
                validated_data[field_name] = data[field_name]
            elif not earlier_field.is_required() and not isinstance(
                earlier_field.default, Interp
            ):
                earlier_default = earlier_field.get_default(
                    call_default_factory=True,
                    validated_data=validated_data,
                )
                if earlier_default is not PydanticUndefined:
                    validated_data[field_name] = earlier_default
        try:
            value = model_field.get_default(
                call_default_factory=True,
                validated_data=validated_data,
            )
        except Exception as error:
            raise UnsetError(
                f"cannot materialize provisional default for {type(obj).__name__}.{name}: "
                f"{safe_exception_text(error)}"
            ) from error
        if value is PydanticUndefined:
            raise UnsetError(f"{type(obj).__name__}.{name} has no usable draft default")
        if isinstance(value, Interp):
            raise UnsetError(
                f"{type(obj).__name__}.{name} is pending interpolation from its default factory; "
                "read it after finalize()"
            )

    if _requires_pin_on_read(value):
        raise UnsetError(
            f"{type(obj).__name__}.{name} has an atomic provisional default that cannot "
            "be observed safely; assign an explicit value or read it after finalize()"
        )
    tracked = _track_value(value, _MutationTracker(obj, name))
    object.__getattribute__(obj, "__dict__")[name] = tracked
    state = draft_state(obj)
    state.materialized.add(name)
    return tracked


def _concrete_config_type(annotation: Any) -> type["Config"] | None:
    from .config import Config

    annotation, _ = unwrap_annotation(annotation, None)
    return (
        annotation
        if isinstance(annotation, type) and issubclass(annotation, Config)
        else None
    )


def has_user_input(value: Any, active: set[int] | None = None) -> bool:
    """Return whether a materialized draft subtree contains a user operation."""
    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        return False
    if is_draft(value):
        if value.__pydantic_fields_set__:
            return True
        active.add(identity)
        try:
            data = object.__getattribute__(value, "__dict__")
            return any(
                has_user_input(data[name], active)
                for name in type(value).__pydantic_fields__
                if name in data
            )
        finally:
            active.remove(identity)
    if type(value) is dict or isinstance(value, _DraftDict):
        active.add(identity)
        try:
            return any(has_user_input(item, active) for item in value.values())
        finally:
            active.remove(identity)
    if type(value) in {list, tuple} or isinstance(value, _DraftList):
        active.add(identity)
        try:
            return any(has_user_input(item, active) for item in value)
        finally:
            active.remove(identity)
    return False


def draft_repr(obj: BaseModel) -> str:
    """Render only meaningful composition state, pending rules, and missing fields."""
    return _draft_repr(obj, set())


def _draft_repr(obj: BaseModel, active: set[int]) -> str:
    identity = id(obj)
    if identity in active:
        return f"<recursive draft {type(obj).__name__}>"
    active.add(identity)
    bits: list[str] = []
    try:
        data = object.__getattribute__(obj, "__dict__")
        pending = draft_state(obj).pending
        for name, model_field in type(obj).__pydantic_fields__.items():
            if name in pending:
                bits.append(f"{name}=[pending {safe_repr(pending[name])}]")
            elif name in data:
                value = data[name]
                if name in obj.__pydantic_fields_set__ or is_draft(value):
                    bits.append(f"{name}={_draft_value_repr(value, active)}")
            elif isinstance(model_field.default, Interp):
                bits.append(f"{name}=[pending {safe_repr(model_field.default)}]")
            elif model_field.is_required():
                child_type = _concrete_config_type(model_field.annotation)
                label = (
                    f"<untouched {child_type.__name__}>" if child_type else "[UNSET]"
                )
                bits.append(f"{name}={label}")
        rendered = f"<draft {type(obj).__name__}({', '.join(bits)})>"
        return rendered if len(rendered) <= 1200 else f"{rendered[:1197]}..."
    finally:
        active.remove(identity)


def _draft_value_repr(value: Any, active: set[int]) -> str:
    if is_draft(value):
        return _draft_repr(value, active)
    value_type = type(value)
    container_types = {
        dict,
        list,
        tuple,
        set,
        frozenset,
        _DraftDict,
        _DraftList,
        _DraftSet,
    }
    if value_type not in container_types:
        return safe_repr(value)
    identity = id(value)
    if identity in active:
        return "..."
    active.add(identity)
    try:
        if isinstance(value, dict):
            body = ", ".join(
                f"{safe_repr(key)}: {_draft_value_repr(item, active)}"
                for key, item in value.items()
            )
            return f"{{{body}}}"
        rendered = [_draft_value_repr(item, active) for item in value]
        if isinstance(value, (set, frozenset)):
            rendered.sort()
        body = ", ".join(rendered)
        if isinstance(value, list):
            return f"[{body}]"
        if isinstance(value, tuple):
            return f"({body}{',' if len(value) == 1 else ''})"
        if isinstance(value, set):
            return f"{{{body}}}" if value else "set()"
        return f"frozenset({{{body}}})"
    except Exception:
        return safe_repr(value)
    finally:
        active.remove(identity)


def ensure_final(obj: BaseModel, operation: str = "serialization") -> None:
    """Require a completed final at a public value boundary."""
    if not isinstance(state_of(obj), FinalState):
        guidance = (
            "call finalize() first"
            if is_draft(obj)
            else "validation is still in progress"
        )
        raise DraftError(f"{operation} requires a completed Config final; {guidance}")
