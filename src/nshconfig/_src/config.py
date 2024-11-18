from __future__ import annotations

import contextlib
import importlib.util
import json
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pydantic import BaseModel, PrivateAttr
from pydantic import ConfigDict as _ConfigDict
from typing_extensions import Unpack, override

from .missing import MISSING as _MISSING
from .missing import validate_no_missing_values

_MutableMappingBase = MutableMapping[str, Any]
if TYPE_CHECKING:
    _MutableMappingBase = object


_DraftConfigContextSentinel = object()


class ConfigDict(_ConfigDict, total=False):
    repr_diff_only: bool
    """
    If `True`, the repr methods will only show values for fields that are different from the default.
    Defaults to `False`.
    """

    no_validate_assignment_for_draft: bool
    """
    Whether to disable the validation of assignments for draft configs.
    Defaults to `True`.
    """


class Config(BaseModel, _MutableMappingBase):
    """
    A base configuration class that provides validation and serialization capabilities.

    This class extends Pydantic's BaseModel and implements a mutable mapping interface. It supports draft configurations
    for flexible initialization and provides various serialization methods.
    """

    _is_draft_config: bool = PrivateAttr(default=False)
    """
    Whether this config is a draft config or not.

    Draft configs are configs that are not yet fully validated.
    They allow for a nicer API when creating configs, e.g.:

        ```python
        config = MyConfig.draft()

        # Set some values
        config.a = 10
        config.b = "hello"

        # Finalize the config
        config = config.finalize()
        ```
    """

    MISSING: ClassVar[Any] = _MISSING
    """
    Alias for the `MISSING` constant.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(  # type: ignore
        # By default, Pydantic will throw a warning if a field starts with "model_",
        # so we need to disable that warning (beacuse "model_" is a popular prefix for ML).
        protected_namespaces=(),
        validate_assignment=True,
        validate_return=True,
        validate_default=True,
        strict=True,
        revalidate_instances="always",
        arbitrary_types_allowed=True,
        extra="ignore",
        validation_error_cause=True,
        use_attribute_docstrings=True,
    )

    if TYPE_CHECKING:

        def __init_subclass__(cls, **kwargs: Unpack[ConfigDict]):
            super().__init_subclass__(**kwargs)

    def __draft_pre_init__(self):
        """Called right before a draft config is finalized."""
        pass

    def __post_init__(self):
        """Called after the final config is validated."""
        pass

    def model_deep_validate(self, strict: bool = True):
        """
        Validate the config and all of its sub-configs.

        Args:
            config: The config to validate.
            strict: Whether to validate the config strictly.
        """
        config_dict = self.model_dump(round_trip=True)
        config = self.model_validate(config_dict, strict=strict)

        # Make sure that this is not a draft config
        if config._is_draft_config:
            raise ValueError("Draft configs are not valid. Call `finalize` first.")

        return config

    @classmethod
    def draft(cls, **kwargs):
        config = cls.model_construct_draft(**kwargs)
        return config

    def finalize(self, strict: bool = True):
        # This must be a draft config, otherwise we raise an error
        if not self._is_draft_config:
            raise ValueError("Finalize can only be called on drafts.")

        # First, we call `__draft_pre_init__` to allow the config to modify itself a final time
        self.__draft_pre_init__()

        # Then, we dump the config to a dict and then re-validate it
        return self.model_deep_validate(strict=strict)

    @override
    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)

        # Call the `__post_init__` method if this is not a draft config
        if __context is _DraftConfigContextSentinel:
            return

        self.__post_init__()

        # After `_post_init__` is called, we perform the final round of validation
        self.model_post_init_validate()

    def model_post_init_validate(self):
        validate_no_missing_values(self)

    @classmethod
    def model_construct_draft(cls, _fields_set: set[str] | None = None, **values: Any):
        """
        NOTE: This is a copy of the `model_construct` method from Pydantic's `Model` class,
            with the following changes:
            - The `model_post_init` method is called with the `_DraftConfigContext` context.
            - The `_is_draft_config` attribute is set to `True` in the `values` dict.

        Creates a new instance of the `Model` class with validated data.

        Creates a new model setting `__dict__` and `__pydantic_fields_set__` from trusted or pre-validated data.
        Default values are respected, but no other validation is performed.

        !!! note
            `model_construct()` generally respects the `model_config.extra` setting on the provided model.
            That is, if `model_config.extra == 'allow'`, then all extra passed values are added to the model instance's `__dict__`
            and `__pydantic_extra__` fields. If `model_config.extra == 'ignore'` (the default), then all extra passed values are ignored.
            Because no validation is performed with a call to `model_construct()`, having `model_config.extra == 'forbid'` does not result in
            an error if extra values are passed, but they will be ignored.

        Args:
            _fields_set: The set of field names accepted for the Model instance.
            values: Trusted or pre-validated data dictionary.

        Returns:
            A new instance of the `Model` class with validated data.
        """

        values["_is_draft_config"] = True

        m = cls.__new__(cls)
        fields_values: dict[str, Any] = {}
        fields_set = set()

        for name, field in cls.model_fields.items():
            if field.alias and field.alias in values:
                fields_values[name] = values.pop(field.alias)
                fields_set.add(name)
            elif name in values:
                fields_values[name] = values.pop(name)
                fields_set.add(name)
            elif not field.is_required():
                fields_values[name] = field.get_default(call_default_factory=True)
        if _fields_set is None:
            _fields_set = fields_set

        _extra: dict[str, Any] | None = None
        if cls.model_config.get("extra") == "allow":
            _extra = {}
            for k, v in values.items():
                _extra[k] = v
        object.__setattr__(m, "__dict__", fields_values)
        object.__setattr__(m, "__pydantic_fields_set__", _fields_set)
        if not cls.__pydantic_root_model__:
            object.__setattr__(m, "__pydantic_extra__", _extra)

        if cls.__pydantic_post_init__:
            m.model_post_init(_DraftConfigContextSentinel)
            # update private attributes with values set
            if (
                hasattr(m, "__pydantic_private__")
                and m.__pydantic_private__ is not None
            ):
                for k, v in values.items():
                    if k in m.__private_attributes__:
                        m.__pydantic_private__[k] = v

        elif not cls.__pydantic_root_model__:
            # Note: if there are any private attributes, cls.__pydantic_post_init__ would exist
            # Since it doesn't, that means that `__pydantic_private__` should be set to None
            object.__setattr__(m, "__pydantic_private__", None)

        return m

    @contextlib.contextmanager
    def __patch_validator_validate_assignment(self):
        prev_value = self.model_config.get("validate_assignment", _notset := object())
        try:
            # We temporarily disable the validation of assignments
            self.model_config["validate_assignment"] = False
            yield
        finally:
            # We re-enable the validation of assignments
            if prev_value is _notset:
                del self.model_config["validate_assignment"]
            else:
                self.model_config["validate_assignment"] = cast(Any, prev_value)

    if not TYPE_CHECKING:

        @override
        def __setattr__(self, name: str, value: Any) -> None:
            __tracebackhide__ = True

            with contextlib.ExitStack() as stack:
                if self._is_draft_config and self.model_config.get(
                    "no_validate_assignment_for_draft", True
                ):
                    stack.enter_context(self.__patch_validator_validate_assignment())
                return super().__setattr__(name, value)

    @override
    def __repr_args__(self):
        # If `repr_diff_only` is `True`, we only show the fields that are different from the default.
        if not self.model_config.get("repr_diff_only", False):
            yield from super().__repr_args__()
            return

        # First, we get the default values for all fields.
        default_values = self.model_construct_draft()

        # Then, we compare the default values with the current values.
        for k, v in super().__repr_args__():
            if k is None:
                yield k, v
                continue

            # If there is no default value or the value is different from the default, we yield it.
            if not hasattr(default_values, k) or getattr(default_values, k) != v:
                yield k, v
                continue

            # Otherwise, we can skip this field.

    # region MutableMapping implementation
    if not TYPE_CHECKING:
        # This is mainly so the config can be used with lightning's hparams
        #   transparently and without any issues.

        @property
        def _nshconfig_dict(self):
            return self.model_dump()

        # We need to make sure every config class
        #   is a MutableMapping[str, Any] so that it can be used
        #   with lightning's hparams.
        @override
        def __getitem__(self, key: str):
            # Key can be of the format "a.b.c"
            #   so we need to split it into a list of keys.
            [first_key, *rest_keys] = key.split(".")
            value = self._nshconfig_dict[first_key]

            for key in rest_keys:
                if isinstance(value, Mapping):
                    value = value[key]
                else:
                    value = getattr(value, key)

            return value

        @override
        def __setitem__(self, key: str, value: Any):
            # Key can be of the format "a.b.c"
            #   so we need to split it into a list of keys.
            [first_key, *rest_keys] = key.split(".")
            if len(rest_keys) == 0:
                self._nshconfig_dict[first_key] = value
                return

            # We need to traverse the keys until we reach the last key
            #   and then set the value
            current_value = self._nshconfig_dict[first_key]
            for key in rest_keys[:-1]:
                if isinstance(current_value, Mapping):
                    current_value = current_value[key]
                else:
                    current_value = getattr(current_value, key)

            # Set the value
            if isinstance(current_value, MutableMapping):
                current_value[rest_keys[-1]] = value
            else:
                setattr(current_value, rest_keys[-1], value)

        @override
        def __delitem__(self, key: str):
            # This is unsupported for this class
            raise NotImplementedError

        @override
        def __iter__(self):
            return iter(self._nshconfig_dict)

        @override
        def __len__(self):
            return len(self._nshconfig_dict)

    # endregion

    # region `treescope` integration
    def __treescope_repr__(self, path, subtree_renderer):
        if importlib.util.find_spec("treescope") is None:
            raise ImportError("The `treescope` package is required for this feature.")

        from .treescope_util import render_object_constructor

        # For the attributes, let's first output the fields that are different from the default.
        attributes = {}

        # First, we get the default values for all fields.
        default_values = self.model_construct_draft()

        # Then, we compare the default values with the current values.
        for k, v in super().__repr_args__():
            if k is None:
                continue

            is_default = hasattr(default_values, k) and getattr(default_values, k) == v
            attributes[k] = (v, is_default)

        return render_object_constructor(
            object_type=type(self),
            attributes=attributes,
            path=path,
            subtree_renderer=subtree_renderer,
            roundtrippable=True,
        )

    # endregion

    # region Construction methods
    # Dict
    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]):
        """Create configuration from a dictionary.

        Args:
            config_dict: Dictionary containing configuration values

        Returns:
            A validated configuration instance
        """
        return cls.model_validate(config_dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to a dictionary.

        Returns:
            Dictionary representation of the configuration
        """
        return self.model_dump()

    # JSON
    @classmethod
    def from_json(cls, path: str | Path):
        """Create configuration from a JSON file.

        Args:
            path: Path to the JSON file

        Returns:
            A validated configuration instance
        """
        with open(path, "r", encoding="utf-8") as f:
            config_dict = json.load(f)
        return cls.model_validate(config_dict)

    def to_json(self, path: str | Path) -> None:
        """Save configuration to a JSON file.

        Args:
            path: Path where the JSON file will be saved
        """
        data = self.model_dump()

        # Add schema reference if available
        if schema_path := _get_schema_path(type(self)):
            data["$schema"] = f"file://{schema_path}"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # YAML
    @classmethod
    def from_yaml(cls, path: str | Path):
        """Create configuration from a YAML file.

        Args:
            path: Path to the YAML file

        Returns:
            A validated configuration instance

        Raises:
            ImportError: If PyYAML is not installed
        """
        try:
            import yaml
        except ImportError:
            raise ImportError(
                "PyYAML is required for YAML support. "
                "You can either install nshconfig with "
                "all extras using 'pip install nshconfig[extra]"
                ", or install with 'pip install PyYAML'"
            )

        with open(path, "r", encoding="utf-8") as f:
            config_dict = yaml.safe_load(f)
        return cls.model_validate(config_dict)

    def to_yaml(self, path: str | Path) -> None:
        """Save configuration to a YAML file.

        Args:
            path: Path where the YAML file will be saved

        Raises:
            ImportError: If PyYAML is not installed
        """
        try:
            import yaml
        except ImportError:
            raise ImportError(
                "PyYAML is required for YAML support. "
                "You can either install nshconfig with "
                "all extras using 'pip install nshconfig[extra]"
                ", or install with 'pip install PyYAML'"
            )

        data = self.model_dump()

        # Write the file with schema reference if available
        with open(path, "w", encoding="utf-8") as f:
            if schema_path := _get_schema_path(type(self)):
                # Add YAML language server schema directive
                f.write(f"# yaml-language-server: $schema=file://{schema_path}\n")
            yaml.dump(data, f)

    # TOML
    @classmethod
    def from_toml(cls, path: str | Path):
        """Create configuration from a TOML file.

        Args:
            path: Path to the TOML file

        Returns:
            A validated configuration instance

        Raises:
            ImportError: If toml is not installed
        """
        try:
            import toml
        except ImportError:
            raise ImportError(
                "toml is required for TOML support. "
                "You can either install nshconfig with "
                "all extras using 'pip install nshconfig[extra]"
                ", or install toml with 'pip install toml'"
            )

        with open(path, "r", encoding="utf-8") as f:
            config_dict = toml.load(f)
        return cls.model_validate(config_dict)

    def to_toml(self, path: str | Path) -> None:
        """Save configuration to a TOML file.

        Args:
            path: Path where the TOML file will be saved

        Raises:
            ImportError: If toml is not installed
        """
        try:
            import toml
        except ImportError:
            raise ImportError(
                "toml is required for TOML support. "
                "You can either install nshconfig with "
                "all extras using 'pip install nshconfig[extra]"
                ", or install with 'pip install toml'"
            )

        data = self.model_dump()

        # Add schema reference if available
        if schema_path := _get_schema_path(type(self)):
            # TOML uses comments for schema references
            comment = f"#:schema {schema_path}\n"
            with open(path, "w", encoding="utf-8") as f:
                f.write(comment)
                toml.dump(data, f)
        else:
            with open(path, "w", encoding="utf-8") as f:
                toml.dump(data, f)

    # endregion


def _get_schema_path(cls: type[Config]) -> str | None:
    """Helper to get the absolute schema path for a config class."""
    from .export import find_config_metadata

    if metadata := find_config_metadata(cls):
        _, schema_path = metadata
        if schema_path is not None:
            # Get the absolute path by looking up from the metadata file location
            if (
                spec := importlib.util.find_spec(cls.__module__)
            ) is None or spec.origin is None:
                return None
            module_dir = Path(spec.origin).parent
            # Look up until we find the .nshconfig.generated.json file
            current_dir = module_dir
            while current_dir.parent != current_dir:
                if (current_dir / ".nshconfig.generated.json").exists():
                    return str((current_dir / schema_path).resolve().absolute())
                current_dir = current_dir.parent
    return None
