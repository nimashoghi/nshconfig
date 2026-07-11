"""Small instance-local state objects shared by the lifecycle modules."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from .provenance import Event

STATE_KEY = "_nshconfig_state"
ORIGIN_PATH_KEY = "_nshconfig_origin_path"
CANONICAL_PATH_KEY = "_nshconfig_canonical_path"
RESERVED_PRIVATE_KEYS = frozenset({STATE_KEY, ORIGIN_PATH_KEY, CANONICAL_PATH_KEY})


def is_path_part(value: Any) -> bool:
    """Return whether a value has one unambiguous structural path spelling."""
    return type(value) in {str, int}


@dataclass
class DraftState:
    """Mutable composition metadata that is never part of the model schema."""

    events: dict[str, list["Event"]] = field(default_factory=dict)
    materialized: set[str] = field(default_factory=set)
    pending: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FinalState:
    """Immutable-by-convention provenance local to one final Config node."""

    events: dict[str, tuple["Event", ...]] = field(default_factory=dict)


def state_of(obj: BaseModel) -> DraftState | FinalState | None:
    """Return nshconfig state without invoking user attribute hooks."""
    private = object.__getattribute__(obj, "__pydantic_private__")
    state = private.get(STATE_KEY) if private is not None else None
    return state if isinstance(state, (DraftState, FinalState)) else None


def draft_state(obj: BaseModel) -> DraftState:
    """Return draft state or fail on an internal lifecycle invariant."""
    state = state_of(obj)
    assert isinstance(state, DraftState), "expected an nshconfig draft"
    return state


def final_state(obj: BaseModel) -> FinalState:
    """Create or return the final state attached to a validated node."""
    state = state_of(obj)
    if isinstance(state, FinalState):
        return state
    assert not isinstance(state, DraftState), "a draft cannot become final in place"
    state = FinalState()
    private = object.__getattribute__(obj, "__pydantic_private__")
    if private is None:
        private = {}
        object.__setattr__(obj, "__pydantic_private__", private)
    private[STATE_KEY] = state
    return state


def set_final_state(obj: BaseModel, events: dict[str, tuple["Event", ...]]) -> None:
    """Replace a final node's local provenance after validation or record loading."""
    private = object.__getattribute__(obj, "__pydantic_private__")
    if private is None:
        private = {}
        object.__setattr__(obj, "__pydantic_private__", private)
    private[STATE_KEY] = FinalState(events)


def is_draft(obj: Any) -> bool:
    """Return whether an object is an nshconfig draft."""
    return isinstance(obj, BaseModel) and isinstance(state_of(obj), DraftState)
