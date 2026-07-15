"""Instance-local lifecycle state and small structural value helpers."""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .semantic import is_known_immutable_atom

STATE_KEY = "_nshconfig_state"
RESERVED_PRIVATE_KEYS = frozenset({STATE_KEY})


def is_path_part(value: Any) -> bool:
    """Return whether a value has one unambiguous structural path spelling."""

    return type(value) in {str, int}


@dataclass(frozen=True)
class Recipe:
    """Replayable raw constructor input for a validated Config final."""

    inputs: dict[str, Any]
    input_token: Any


@dataclass
class DraftState:
    """Mutable composition metadata that is never part of the model schema."""

    base: Recipe | None = None
    deleted: set[str] = field(default_factory=set)
    materialized: dict[str, Any] = field(default_factory=dict)
    pending: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FinalState:
    """Private final-state marker plus an optional constructor recipe."""

    recipe: Recipe | None = None
    value_token: Any = None


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


def set_final_state(obj: BaseModel, recipe: Recipe | None = None) -> FinalState:
    """Install final lifecycle state and capture the declared value graph."""

    private = object.__getattribute__(obj, "__pydantic_private__")
    if private is None:
        private = {}
        object.__setattr__(obj, "__pydantic_private__", private)
    state = FinalState(recipe=recipe, value_token=value_token(obj))
    private[STATE_KEY] = state
    return state


def is_draft(obj: Any) -> bool:
    """Return whether an object is an nshconfig draft."""

    return isinstance(obj, BaseModel) and isinstance(state_of(obj), DraftState)


def copy_builtin_graph(value: Any, memo: dict[int, Any] | None = None) -> Any:
    """Copy built-in containers while retaining opaque values by identity."""

    if memo is None:
        memo = {}
    if type(value) not in {dict, list, tuple, set, frozenset}:
        return value
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if type(value) is dict:
        output: dict[Any, Any] = {}
        memo[identity] = output
        output.update(
            (copy_builtin_graph(key, memo), copy_builtin_graph(item, memo))
            for key, item in value.items()
        )
        return output
    if type(value) is list:
        output_list: list[Any] = []
        memo[identity] = output_list
        output_list.extend(copy_builtin_graph(item, memo) for item in value)
        return output_list
    if type(value) is tuple:
        output_tuple = tuple(copy_builtin_graph(item, memo) for item in value)
        memo[identity] = output_tuple
        return output_tuple
    if type(value) is set:
        output_set: set[Any] = set()
        memo[identity] = output_set
        output_set.update(copy_builtin_graph(item, memo) for item in value)
        return output_set
    output_frozen = frozenset(copy_builtin_graph(item, memo) for item in value)
    memo[identity] = output_frozen
    return output_frozen


def make_recipe(values: dict[str, Any]) -> Recipe:
    """Capture raw keyword input before Pydantic validation."""

    inputs = copy_builtin_graph(values)
    assert isinstance(inputs, dict)
    return Recipe(inputs=inputs, input_token=value_token(inputs))


def valid_recipe(obj: BaseModel) -> Recipe | None:
    """Return an intact constructor recipe, or None when replay is unsafe."""

    state = state_of(obj)
    if not isinstance(state, FinalState) or state.recipe is None:
        return None
    if value_token(obj) != state.value_token:
        return None
    if value_token(state.recipe.inputs) != state.recipe.input_token:
        return None
    return state.recipe


def value_token(value: Any, active: set[int] | None = None) -> Any:
    """Return a compact token for the declared graph and built-in containers."""

    if active is None:
        active = set()
    value_type = type(value)
    if value is None or value_type in {bool, int, str, bytes}:
        return value_type, value
    if value_type is float:
        return float, value.hex()
    if value_type is complex:
        return complex, value.real.hex(), value.imag.hex()
    if is_known_immutable_atom(value):
        return value_type, value

    identity = id(value)
    if identity in active:
        return "cycle", identity
    if isinstance(value, BaseModel):
        active.add(identity)
        try:
            data = object.__getattribute__(value, "__dict__")
            return (
                "model",
                type(value),
                tuple(
                    (
                        name,
                        value_token(data[name], active)
                        if name in data
                        else ("missing",),
                    )
                    for name in type(value).__pydantic_fields__
                ),
            )
        finally:
            active.remove(identity)
    if value_type is dict:
        active.add(identity)
        try:
            return (
                dict,
                tuple(
                    (value_token(key, active), value_token(item, active))
                    for key, item in value.items()
                ),
            )
        finally:
            active.remove(identity)
    if value_type in {list, tuple}:
        active.add(identity)
        try:
            return value_type, tuple(value_token(item, active) for item in value)
        finally:
            active.remove(identity)
    if value_type in {set, frozenset}:
        active.add(identity)
        try:
            tokens = [value_token(item, active) for item in value]
            return value_type, tuple(sorted(tokens, key=repr))
        finally:
            active.remove(identity)
    return "opaque", value_type
