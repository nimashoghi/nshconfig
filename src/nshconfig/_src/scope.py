"""The one resolution site: a wrap validator maintaining the ancestor stack.

Defined at module level (not in the class body) so cloudpickle of notebook-defined
``Config`` subclasses serializes it BY REFERENCE; a class-body def would be pickled
by value and drag in the unpicklable ``ContextVar``. See V2_CORE.md sections 4 and 8.
"""

from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel

from .interp import _READS, Ctx, Interp, Stack

__all__ = ["interpolation_scope"]

_STACK: "ContextVar[Stack]" = ContextVar("nshconfig_stack", default=())
_IN_RESOLVER: ContextVar[bool] = ContextVar("nshconfig_in_resolver", default=False)


def _key_in_parent(parent_data: dict[str, Any], child: Any) -> str | None:
    """Best-effort label of ``child`` (an input dict) inside its parent's input."""
    for k, v in parent_data.items():
        if v is child:
            return k
        if isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if item is child:
                    return f"{k}[{i}]"
        elif isinstance(v, dict):
            for kk, item in v.items():
                if item is child:
                    return f"{k}[{kk!r}]"
    return None


def _dotted(stack: "Stack", field: str) -> str:
    labels = [lbl for _, _, lbl in stack if lbl is not None]
    return ".".join([*labels, field])


def _assert_no_pending(v: Any, path: str) -> None:
    """Nothing symbolic may survive into a final or its dumps (even via Any fields)."""
    if isinstance(v, Interp):
        raise ValueError(
            f"unresolved {v!r} leaked into the final at {path}; interp() markers "
            "resolve only as direct values of declared config fields"
        )
    if isinstance(v, BaseModel):
        for n in type(v).__pydantic_fields__:
            _assert_no_pending(getattr(v, n), f"{path}.{n}")
    elif isinstance(v, dict):
        for k, item in v.items():
            _assert_no_pending(item, f"{path}[{k!r}]")
    elif isinstance(v, (list, tuple, set, frozenset)):
        for i, item in enumerate(v):
            _assert_no_pending(item, f"{path}[{i}]")


def interpolation_scope(cls: type[BaseModel], value: Any, handler: Any) -> Any:
    """The one wrap validator: stack maintenance plus the one-rule resolution.

    Per field, in declaration order: ``v = value.get(name, field.default)``; if ``v``
    is a marker, resolve it against the ancestor stack. An instance marker is found AT
    the key; a class-level marker is the same kind of object found in the default slot
    when the key is absent. There is no second mechanism (V2_CORE.md section 4).
    """
    if not isinstance(value, dict):
        return handler(value)
    # Re-entrancy guard: a model_validate entered from INSIDE a resolver is a fresh
    # validation session, not a child of the outer tree.
    parent_stack = () if _IN_RESOLVER.get() else _STACK.get()
    label = _key_in_parent(parent_stack[-1][1], value) if parent_stack else None
    value = dict(value)  # never mutate the caller's dict
    fields = cls.__pydantic_fields__
    stack = parent_stack + ((cls, value, label),)
    ctx = Ctx(stack)
    injected: list[str] = []
    resolved: list[tuple[str, Interp, Any, list[tuple[str, str]]]] = []
    for name, f in fields.items():
        v = value.get(name, f.default)
        if isinstance(v, Interp):
            if name not in value:
                injected.append(name)  # came from the default slot
            reads: list[tuple[str, str]] = []
            tok = _IN_RESOLVER.set(True)
            rtok = _READS.set(reads)
            try:
                value[name] = v.fn(ctx)
            except Exception as e:
                raise ValueError(
                    f"cannot interpolate {_dotted(stack, name)} "
                    f"[{cls.__name__}.{name} = {v!r}]: {e}"
                ) from e
            finally:
                _READS.reset(rtok)
                _IN_RESOLVER.reset(tok)
            resolved.append((name, v, value[name], reads))
    token = _STACK.set(stack)
    try:
        out = handler(value)
    finally:
        _STACK.reset(token)
    # Class-default markers we injected are defaults, not user data: scrub them from
    # fields_set so exclude_unset dumps keep default provenance.
    for n in injected:
        out.__pydantic_fields_set__.discard(n)
    if resolved:
        from .provenance import record_interp_events

        record_interp_events(out, resolved, injected)
    if not parent_stack:
        _assert_no_pending(out, cls.__name__)
    return out
