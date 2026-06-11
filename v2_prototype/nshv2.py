"""nshconfig v2 synthesis: ``derive()`` markers as first-class field values.

The design-panel synthesis, merged from the two convergent winning implementations
(designA.py, semU.py) plus the judges' verified graftings:

- ONE concept: ``derive(lambda c: ...)`` returns a ``Derive`` marker, typed as the
  lambda's return type (the contained ``Field()``-style lie), legal anywhere a value
  sits: a draft assignment, a ``model_validate`` input dict, or a class default.
- ONE resolution rule (semU): per field, ``v = value.get(name, field.default)``; if
  ``v`` is a marker, resolve it against the ancestor stack, in declaration order,
  each scope resolving BEFORE descending (so ancestor reads are always concrete).
- Precedence falls out of dict presence: input slot beats default slot, last write
  wins, ``del`` re-arms. Markers may satisfy required fields.
- Marker hygiene (from the proxy design's verified silent-leak probes): ``__slots__``
  plus raising ``__bool__``/``__format__``; ``==`` stays identity and is documented.
- Loud failures: dotted instance paths, owning ``Cls.field``, the lambda's source
  site (plain string, survives cloudpickle), ancestor chains on orphan errors, and
  a root-level sweep so nothing symbolic survives into finals (even via Any fields).
- Re-entrancy guard (the one attack that beat all five panel designs): a nested
  ``model_validate`` *inside* a resolver gets a fresh stack and its own root sweep.
- Class-default markers injected during resolution are scrubbed from
  ``__pydantic_fields_set__`` so ``exclude_unset`` dumps keep default provenance.
- Draft dunders are gated behind ``if not TYPE_CHECKING`` so basedpyright keeps
  static attribute checking on drafts; the draft repr labels pending state.

Known, deliberate limits: one validation pass (a marker reading a still-pending
value fails loudly with both ends named; that IS the cycle detector), and dotted
descent from ``c.root`` sees raw input + class defaults (use ``c.nearest`` for
resolved ancestor values).
"""

from __future__ import annotations

import copyreg
import difflib
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, TypeVar, cast

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_core import SchemaSerializer, SchemaValidator

__all__ = [
    "Config",
    "Ctx",
    "Derive",
    "DraftError",
    "UnsetError",
    "derive",
    "finalize",
    "is_draft",
]

T = TypeVar("T")
C = TypeVar("C", bound="Config")

# stack entry: (model class, in-progress input dict, key label in parent or None)
_Stack = tuple[tuple[type, "dict[str, Any]", "str | None"], ...]
_STACK: ContextVar[_Stack] = ContextVar("nsh_stack", default=())
_IN_RESOLVER: ContextVar[bool] = ContextVar("nsh_in_resolver", default=False)


class UnsetError(AttributeError):
    pass


class DraftError(TypeError):
    pass


class Derive:
    """Runtime marker: this value is computed from the config tree at validation.

    Stateless after construction (fn + captured source site), so one instance can
    occupy a class default slot and any number of instance slots concurrently.
    ``==`` is identity (raising would break container membership); ``bool()`` and
    f-string formatting raise so a pending marker cannot silently leak into data.
    """

    __slots__ = ("fn", "site")

    def __init__(self, fn: Callable[[Ctx], Any]):
        self.fn = fn
        code = getattr(fn, "__code__", None)
        name = getattr(fn, "__name__", type(fn).__name__)
        # Plain string captured NOW: survives pickling, citable on a cluster.
        self.site = f"{name} @ {code.co_filename}:{code.co_firstlineno}" if code else name

    def __repr__(self) -> str:
        name, _, loc = self.site.partition(" @ ")
        if loc:
            file, _, line = loc.rpartition(":")
            return f"derive(<{name} @ {Path(file).name}:{line}>)"
        return f"derive(<{name}>)"

    def __bool__(self) -> bool:
        raise DraftError(f"refusing to use pending {self!r} in a boolean context")

    def __format__(self, spec: str) -> str:
        raise DraftError(f"refusing to format pending {self!r} into a string")


def derive(fn: Callable[[Ctx], T]) -> T:
    """Mark a field value as derived from the surrounding config tree.

    Returns a ``Derive`` marker at runtime; typed as ``T`` so basedpyright checks
    the lambda's return type against the field at every use site.
    """
    return cast(T, Derive(fn))


class _View:
    """Read access to one stack level: resolved/provided input first, class defaults second."""

    def __init__(self, cls: type[BaseModel], data: dict[str, Any]):
        object.__setattr__(self, "_cls", cls)
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        f = self._cls.__pydantic_fields__.get(name)
        if f is None:
            hint = difflib.get_close_matches(name, self._cls.__pydantic_fields__, n=1)
            raise AttributeError(
                f"{self._cls.__name__} has no field {name!r}"
                + (f"; did you mean {hint[0]!r}?" if hint else "")
            )
        if name in self._data:
            v = self._data[name]
            if isinstance(v, Derive):
                raise AttributeError(
                    f"{self._cls.__name__}.{name} is itself pending derivation ({v!r}) "
                    "(possible cycle; set a concrete value, or point both at the same "
                    "concrete source)"
                )
        elif isinstance(f.default, Derive):
            raise AttributeError(
                f"{self._cls.__name__}.{name} is itself derived and not filled yet"
            )
        elif not f.is_required():
            data = {k: v for k, v in self._data.items() if not isinstance(v, Derive)}
            v = f.get_default(call_default_factory=True, validated_data=data)
        else:
            raise AttributeError(
                f"{self._cls.__name__}.{name}: not provided and no usable default"
            )
        ann = f.annotation
        if isinstance(v, dict) and isinstance(ann, type) and issubclass(ann, BaseModel):
            return _View(ann, v)
        return v

    def __repr__(self) -> str:
        return f"<view {self._cls.__name__} {self._data!r}>"


class Ctx:
    """What a derive lambda sees."""

    def __init__(self, stack: _Stack):
        self._stack = stack

    @property
    def data(self) -> Any:
        cls, data, _ = self._stack[-1]
        return _View(cls, data)

    @property
    def parent(self) -> Any:
        if len(self._stack) < 2:
            raise AttributeError("no parent: this model is the validation root")
        cls, data, _ = self._stack[-2]
        return _View(cls, data)

    @property
    def root(self) -> Any:
        cls, data, _ = self._stack[0]
        return _View(cls, data)

    def nearest(self, cls: type) -> Any:
        for c, d, _ in reversed(self._stack[:-1]):
            if issubclass(c, cls):
                return _View(c, d)
        chain = " > ".join(c.__name__ for c, _, _ in self._stack)
        raise AttributeError(f"no enclosing {cls.__name__} (ancestors here: {chain})")


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


def _dotted(stack: _Stack, field: str) -> str:
    labels = [lbl for _, _, lbl in stack if lbl is not None]
    return ".".join([*labels, field])


def _assert_no_pending(v: Any, path: str) -> None:
    """Nothing symbolic may survive into a final or its dumps (even via Any fields)."""
    if isinstance(v, Derive):
        raise ValueError(
            f"unresolved {v!r} leaked into the final at {path}; derive() markers "
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


class _LazyValSer:
    """Pickle stand-in for pydantic-core SchemaValidator/SchemaSerializer.

    Notebook-defined Config subclasses are cloudpickled BY VALUE, and a derive
    lambda's ``__globals__`` typically forward-references sibling model classes.
    pydantic-core's ``__reduce__`` passes the LIVE, cross-class-shared core schema
    dict as a constructor argument; with such reference cycles, pickle can invoke
    that constructor while a shared sub-dict is still partially materialized
    (observed: ``SchemaError ... KeyError: 'function'``). Fix: never construct
    during unpickling; build the real object lazily on first attribute access.
    Properly an upstream pydantic fix; shipped here as a contained shim.
    """

    def __init__(self, factory: Any, args: tuple[Any, ...]):
        self._factory = factory
        self._args = args
        self._obj: Any = None

    def __getattr__(self, name: str) -> Any:  # only fires for missing attrs
        if self.__dict__.get("_obj") is None:
            self.__dict__["_obj"] = self._factory(*self._args)
        return getattr(self.__dict__["_obj"], name)

    def __reduce__(self):
        return (_LazyValSer, (self._factory, self._args))


def _reduce_valser(obj: Any) -> tuple[Any, tuple[Any, ...]]:
    factory, args, *_ = obj.__reduce__()
    return (_LazyValSer, (factory, args))


copyreg.pickle(SchemaValidator, _reduce_valser)
copyreg.pickle(SchemaSerializer, _reduce_valser)


def is_draft(obj: Any) -> bool:
    return isinstance(obj, BaseModel) and object.__getattribute__(obj, "__dict__").get(
        "__nsh_draft__", False
    )


def _interpolation_scope(cls: type[BaseModel], value: Any, handler: Any) -> Any:
    """The one wrap validator. Module-level so cloudpickle of notebook-defined
    Config subclasses serializes it BY REFERENCE (a class-body def is pickled by
    value and drags in the unpicklable ContextVar)."""
    if not isinstance(value, dict):
        return handler(value)
    # Re-entrancy guard: a model_validate entered from INSIDE a resolver is a
    # fresh validation session, not a child of the outer tree.
    parent_stack = () if _IN_RESOLVER.get() else _STACK.get()
    label = _key_in_parent(parent_stack[-1][1], value) if parent_stack else None
    value = dict(value)  # never mutate the caller's dict
    fields = cls.__pydantic_fields__
    stack = parent_stack + ((cls, value, label),)
    ctx = Ctx(stack)
    injected: list[str] = []
    # The one rule (semU): a marker resolves wherever it sits -- instance slot,
    # input-dict value, or class default slot -- in field declaration order.
    for name, f in fields.items():
        v = value.get(name, f.default)
        if isinstance(v, Derive):
            if name not in value:
                injected.append(name)  # came from the default slot
            tok = _IN_RESOLVER.set(True)
            try:
                value[name] = v.fn(ctx)
            except Exception as e:
                raise ValueError(
                    f"cannot derive {_dotted(stack, name)} "
                    f"[{cls.__name__}.{name} = {v!r}]: {e}"
                ) from e
            finally:
                _IN_RESOLVER.reset(tok)
    token = _STACK.set(stack)
    try:
        out = handler(value)
    finally:
        _STACK.reset(token)
    # Class-default markers we injected are defaults, not user data: scrub them
    # from fields_set so exclude_unset dumps keep default provenance.
    for n in injected:
        out.__pydantic_fields_set__.discard(n)
    if not parent_stack:
        _assert_no_pending(out, cls.__name__)
    return out


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    nsh_interpolation_scope = model_validator(mode="wrap")(
        classmethod(_interpolation_scope)  # pyright: ignore[reportArgumentType]
    )

    # ---- draft layer ----

    @classmethod
    def draft(cls, **values: Any):
        m = cls.model_construct(**values)
        # Strip marker DEFAULTS injected by model_construct; keep user-passed
        # markers (user-set provenance, exactly like any other value). pydantic
        # stubs type __dict__ as a mapping proxy; at runtime it is a plain dict.
        md = cast("dict[str, Any]", cast(object, m.__dict__))
        for n, v in list(md.items()):
            if isinstance(v, Derive) and n not in m.__pydantic_fields_set__:
                del md[n]
        object.__setattr__(m, "__nsh_draft__", True)
        return m

    def __repr__(self) -> str:
        if not is_draft(self):
            return BaseModel.__repr__(self)
        bits: list[str] = []
        for name, f in type(self).__pydantic_fields__.items():
            if name in self.__dict__:
                v = self.__dict__[name]
                if isinstance(v, Derive):
                    bits.append(f"{name}=[pending: instance {v!r}]")
                elif is_draft(v):
                    bits.append(f"{name}={v!r}")
                elif name in self.__pydantic_fields_set__:
                    bits.append(f"{name}={v!r}")
                # materialized-but-untouched static defaults: omitted as noise
            else:
                d = f.default
                ann = f.annotation
                if isinstance(d, Derive):
                    bits.append(f"{name}=[pending: class default {d!r}]")
                elif f.is_required():
                    if isinstance(ann, type) and issubclass(ann, Config):
                        bits.append(f"{name}=<untouched {ann.__name__}>")
                    else:
                        bits.append(f"{name}=[UNSET]")
        return f"<draft {type(self).__name__}({', '.join(bits)})>"

    # The draft dunders are hidden from the type checker so basedpyright keeps
    # pydantic's declared attribute semantics on drafts (typo'd reads/writes are
    # STATIC errors; the ungated baseline silently made everything Any).
    if not TYPE_CHECKING:

        def __setattr__(self, name, value):
            if is_draft(self):
                if name in type(self).__pydantic_fields__:
                    self.__dict__[name] = value
                    self.__pydantic_fields_set__.add(name)
                    return
                if not name.startswith("_"):
                    hint = difflib.get_close_matches(
                        name, type(self).__pydantic_fields__, n=1
                    )
                    raise AttributeError(
                        f"{type(self).__name__} has no field {name!r}"
                        + (f"; did you mean {hint[0]!r}?" if hint else "")
                    )
            BaseModel.__setattr__(self, name, value)

        def __getattr__(self, name):
            try:
                return BaseModel.__getattr__(self, name)
            except AttributeError:
                fields = type(self).__pydantic_fields__
                if is_draft(self) and name in fields:
                    f = fields[name]
                    if isinstance(f.default, Derive):
                        # BEFORE auto-vivification, so a derived config-typed
                        # field cannot silently shadow its marker.
                        raise UnsetError(
                            f"{type(self).__name__}.{name} is derived "
                            f"({f.default!r}); read it after finalize(), or "
                            "assign a value first"
                        )
                    ann = f.annotation
                    if isinstance(ann, type) and issubclass(ann, Config):
                        child = ann.draft()
                        self.__dict__[name] = child
                        return child
                    raise UnsetError(
                        f"{type(self).__name__}.{name} is not set on this draft"
                    )
                raise

        def __delattr__(self, name):
            if is_draft(self) and name in type(self).__pydantic_fields__:
                self.__dict__.pop(name, None)
                self.__pydantic_fields_set__.discard(name)
                return
            BaseModel.__delattr__(self, name)

        def model_dump(self, *a, **kw):
            if is_draft(self):
                raise DraftError("drafts are not serializable; finalize() first")
            return BaseModel.model_dump(self, *a, **kw)

        def model_dump_json(self, *a, **kw):
            if is_draft(self):
                raise DraftError("drafts are not serializable; finalize() first")
            return BaseModel.model_dump_json(self, *a, **kw)


def _emit_required_spine(cls: type) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n, f in cls.__pydantic_fields__.items():
        ann = f.annotation
        if f.is_required() and isinstance(ann, type) and issubclass(ann, Config):
            out[n] = _emit_required_spine(ann)
    return out


def _collect(node: Config) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, f in type(node).__pydantic_fields__.items():
        if name in node.__dict__:
            v = node.__dict__[name]
            out[name] = _collect(v) if is_draft(v) else v
        else:
            ann = f.annotation
            if f.is_required() and isinstance(ann, type) and issubclass(ann, Config):
                out[name] = _emit_required_spine(ann)
    return out


def finalize(draft: C) -> C:
    if not is_draft(draft):
        return draft
    return type(draft).model_validate(_collect(draft))
