"""The Config base: frozen finals, live drafts, one validation boundary.

Drafts are REAL pydantic instances made by ``model_construct`` plus three attribute
dunders. Draft writes go straight to ``__dict__`` (never through
``BaseModel.__setattr__``'s handler-memoization path, which is what made the v1
draft mechanism unsound), with ``__pydantic_fields_set__`` as native provenance.
The dunders are hidden from the type checker so basedpyright keeps pydantic's
declared attribute semantics on drafts: typo'd draft writes are STATIC errors.
"""

from collections.abc import Mapping
import difflib
from types import MethodType
from typing import TYPE_CHECKING, Any, Callable, cast

from pydantic import BaseModel, ConfigDict, model_validator
from typing_extensions import Self, Unpack, override

from . import transport as transport  # registers the pickle shim on import
from .errors import DraftError, UnsetError
from .interp import Interp
from .provenance import record_del, record_seeds, record_write
from .scope import interpolation_scope

if TYPE_CHECKING:
    from .provenance import Event, Explanation

__all__ = ["Config", "is_draft", "set_model_config_defaults"]

DRAFT_KEY = "__nshconfig_draft__"
_UNSET = object()
_CONFIG_DICT_KEYS = frozenset(ConfigDict.__annotations__)
_BUILTIN_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    frozen=True,
    strict=True,
    use_attribute_docstrings=True,
    validate_default=True,
)
_MODEL_CONFIG_DEFAULTS = _BUILTIN_MODEL_CONFIG


def _builtin_model_config_with(overrides: ConfigDict) -> ConfigDict:
    return cast("ConfigDict", {**_BUILTIN_MODEL_CONFIG, **overrides})


def _active_model_config_with(overrides: ConfigDict) -> ConfigDict:
    return cast("ConfigDict", {**_MODEL_CONFIG_DEFAULTS, **overrides})


def set_model_config_defaults(**overrides: Unpack[ConfigDict]) -> None:
    """Set default pydantic config for future ``Config`` subclasses.

    Call this before defining or importing project config classes. Existing
    classes keep the pydantic validators they were built with.
    """
    global _MODEL_CONFIG_DEFAULTS
    _MODEL_CONFIG_DEFAULTS = _builtin_model_config_with(overrides)
    Config.model_config = _MODEL_CONFIG_DEFAULTS


def _explicit_config_from(value: object) -> ConfigDict:
    if not isinstance(value, Mapping):
        raise TypeError("model_config must be a mapping")
    return cast("ConfigDict", dict(value))


# Pydantic compiles validators during class creation. Injecting the active
# defaults here makes global changes apply to future direct and indirect
# subclasses while preserving explicit class-level overrides.
class _ConfigMeta(type(BaseModel)):
    def __new__(
        mcls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> type:
        config_cls = globals().get("Config")
        if isinstance(config_cls, type) and any(issubclass(base, config_cls) for base in bases):
            inherited_explicit = ConfigDict()
            for base in bases:
                inherited_explicit.update(
                    getattr(base, "__nshconfig_explicit_model_config__", {})
                )

            raw_class_config = namespace.get("model_config", _UNSET)
            class_config = (
                ConfigDict()
                if raw_class_config is _UNSET
                else _explicit_config_from(raw_class_config)
            )
            class_kwargs = cast(
                "ConfigDict", {k: v for k, v in kwargs.items() if k in _CONFIG_DICT_KEYS}
            )

            namespace = dict(namespace)
            namespace["model_config"] = _active_model_config_with(
                cast("ConfigDict", {**inherited_explicit, **class_config})
            )
            cls = super().__new__(mcls, name, bases, namespace, **kwargs)
            cls.__nshconfig_explicit_model_config__ = cast(
                "ConfigDict", {**inherited_explicit, **class_config, **class_kwargs}
            )
            return cls

        return super().__new__(mcls, name, bases, namespace, **kwargs)


def is_draft(obj: Any) -> bool:
    """True if ``obj`` is a config draft (mutable, not yet validated)."""
    return isinstance(obj, BaseModel) and object.__getattribute__(obj, "__dict__").get(
        DRAFT_KEY, False
    )


def _verb_config_finalize(self: "Config") -> "Config":
    from .finalize import finalize

    return finalize(self)


def _verb_config_thaw(self: "Config") -> "Config":
    from .finalize import thaw

    return thaw(self)


def _verb_config_explain(self: "Config", path: str) -> "Explanation":
    from .provenance import explain

    return explain(self, path)


def _verb_config_provenance(self: "Config") -> "dict[str, list[Event]]":
    from .provenance import provenance

    return provenance(self)


# The config_* verb family, dispatched from Config.__getattr__ rather than living as
# class attributes. This is what makes the collision story exact: a user field named
# config_finalize leaves NO parent attribute to shadow, so it gets true field
# semantics (required stays required, no pydantic shadow warning), and the verb
# simply does not exist on that class. The module-level verbs (C.finalize etc.)
# remain the universal fallback. Deliberately NOT policed via protected_namespaces:
# fields like config_name / config_path stay warning-free.
_VERBS: dict[str, tuple[Callable[..., Any], bool]] = {  # name -> (impl, is_property)
    "config_finalize": (_verb_config_finalize, False),
    "config_thaw": (_verb_config_thaw, False),
    "config_explain": (_verb_config_explain, False),
    "config_provenance": (_verb_config_provenance, False),
    "config_is_draft": (is_draft, True),
}


class Config(BaseModel, metaclass=_ConfigMeta):
    model_config = _MODEL_CONFIG_DEFAULTS

    # The one resolution site. Module-level function (see scope.py) attached here so
    # cloudpickle of notebook-defined subclasses serializes it by reference.
    nshconfig_interpolation_scope = model_validator(mode="wrap")(
        classmethod(interpolation_scope)  # pyright: ignore[reportArgumentType]
    )

    @classmethod
    def config_draft(cls, **values: Any) -> Self:
        """A mutable draft of this config: plain assignment, validation deferred.

        Nested ``Config``-typed fields auto-vivify on access;
        ``draft.config_finalize()`` is the one validation boundary. Seed values
        count as explicit provenance.
        """
        m = cls.model_construct(**values)
        # Strip marker DEFAULTS materialized by model_construct; keep user-passed
        # markers (user-set provenance, exactly like any other value). pydantic
        # stubs type __dict__ as a mapping proxy; at runtime it is a plain dict.
        md = cast("dict[str, Any]", cast(object, m.__dict__))
        for n, v in list(md.items()):
            if isinstance(v, Interp) and n not in m.__pydantic_fields_set__:
                del md[n]
        object.__setattr__(m, DRAFT_KEY, True)
        if values:
            seeded = [n for n in m.__pydantic_fields_set__ if n in values]
            if seeded:
                record_seeds(m, seeded, values)
        return m

    # ---- equality and hashing over declared fields only ----
    # Provenance chains and the draft flag ride instance __dict__ under dunder keys;
    # pydantic's BaseModel.__eq__ (and its frozen __hash__) consume the whole
    # __dict__, which would make two value-identical configs with different
    # histories compare unequal. Field-based semantics restore the data-only view.

    @override
    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if type(other) is not type(self):
            return NotImplemented
        sd = object.__getattribute__(self, "__dict__")
        od = object.__getattribute__(other, "__dict__")
        for n in type(self).__pydantic_fields__:
            if sd.get(n, _UNSET) != od.get(n, _UNSET):
                return False
        return True

    @override
    def __hash__(self) -> int:
        d = object.__getattribute__(self, "__dict__")
        return hash(
            (type(self), tuple(id(_UNSET) if (v := d.get(n, _UNSET)) is _UNSET else v for n in type(self).__pydantic_fields__))
        )

    @override
    def __repr__(self) -> str:
        if not is_draft(self):
            return BaseModel.__repr__(self)
        bits: list[str] = []
        d = object.__getattribute__(self, "__dict__")
        for name, f in type(self).__pydantic_fields__.items():
            if name in d:
                v = d[name]
                if isinstance(v, Interp):
                    bits.append(f"{name}=[pending: instance {v!r}]")
                elif is_draft(v):
                    bits.append(f"{name}={v!r}")
                elif name in self.__pydantic_fields_set__:
                    bits.append(f"{name}={v!r}")
                # materialized-but-untouched static defaults: omitted as noise
            else:
                dflt = f.default
                ann = f.annotation
                if isinstance(dflt, Interp):
                    bits.append(f"{name}=[pending: class default {dflt!r}]")
                elif f.is_required():
                    if isinstance(ann, type) and issubclass(ann, Config):
                        bits.append(f"{name}=<untouched {ann.__name__}>")
                    else:
                        bits.append(f"{name}=[UNSET]")
        return f"<draft {type(self).__name__}({', '.join(bits)})>"

    def __treescope_repr__(self, path: str | None, subtree_renderer: Any) -> Any:
        # Optional rich notebook rendering; only invoked by treescope itself.
        from .treescope import render_config

        return render_config(self, path, subtree_renderer)

    # ---- the config_* verb family ----
    # Dispatched from __getattr__ at runtime (a declared field by the same name wins,
    # exactly, because the verb then never exists on that class); plain methods to the
    # type checker so calls and return types are fully checked. The module-level verbs
    # (C.finalize, C.thaw, C.explain, ...) remain the canonical functional layer
    # underneath; these are pure DX sugar over them.
    if TYPE_CHECKING:

        def config_finalize(self) -> Self:
            """Resolve interpolation, validate once, return the frozen final."""
            ...

        def config_thaw(self) -> Self:
            """A fresh draft seeded only from explicitly-set values."""
            ...

        def config_explain(self, path: str) -> Explanation:
            """Why does ``path`` have its value? Sites, chains, and because-lines."""
            ...

        def config_provenance(self) -> dict[str, list[Event]]:
            """The full provenance table: dotted path -> event chain."""
            ...

        @property
        def config_is_draft(self) -> bool:
            """True while this config is a mutable, unvalidated draft."""
            ...

    # The draft dunders are hidden from the type checker so basedpyright keeps
    # pydantic's declared attribute semantics (an ungated __getattr__ would turn
    # every unknown attribute into Any and silently disable checking on drafts).
    if not TYPE_CHECKING:

        def __setattr__(self, name, value):
            if is_draft(self):
                if name in type(self).__pydantic_fields__:
                    self.__dict__[name] = value
                    self.__pydantic_fields_set__.add(name)
                    record_write(self, name, value)
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
            # Field semantics FIRST: pydantic's __getattr__ can return raw class
            # attributes (e.g. a _ConfigVerb shadowed by a declared field), so an
            # unset declared field on a draft must be handled before delegating.
            fields = type(self).__pydantic_fields__
            if is_draft(self) and name in fields:
                f = fields[name]
                if isinstance(f.default, Interp):
                    # BEFORE auto-vivification, so an interpolated config-typed
                    # field cannot silently shadow its marker.
                    raise UnsetError(
                        f"{type(self).__name__}.{name} is interpolated "
                        f"({f.default!r}); read it after finalize(), or "
                        "assign a value first"
                    )
                ann = f.annotation
                if isinstance(ann, type) and issubclass(ann, Config):
                    child = ann.config_draft()
                    self.__dict__[name] = child  # present, but NOT user-set
                    return child
                raise UnsetError(
                    f"{type(self).__name__}.{name} is not set on this draft"
                )
            if name in _VERBS:
                impl, prop = _VERBS[name]
                return impl(self) if prop else MethodType(impl, self)
            return BaseModel.__getattr__(self, name)

        def __delattr__(self, name):
            if is_draft(self) and name in type(self).__pydantic_fields__:
                had = name in self.__dict__ or name in self.__pydantic_fields_set__
                self.__dict__.pop(name, None)
                self.__pydantic_fields_set__.discard(name)
                if had:
                    record_del(self, name)
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
