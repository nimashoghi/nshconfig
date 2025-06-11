from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import logging
import typing
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast, get_origin, overload

from pydantic import (
    BaseModel,
    PrivateAttr,
    ValidationInfo,
    model_serializer,
    model_validator,
)
from pydantic import ConfigDict as _ConfigDict
from typing_extensions import Self, TypedDict, TypeVar, Unpack, override

from .utils import PYDANTIC_SETTINGS_VERSION, PYDANTIC_VERSION, IncEx

try:
    from pydantic import with_config as _pydantic_with_config  # type: ignore
except ImportError:
    _WithConfigTypeT = TypeVar("_WithConfigTypeT", bound=type)

    def _pydantic_with_config(
        config: ConfigDict,
    ) -> Callable[[_WithConfigTypeT], _WithConfigTypeT]:
        def inner(TypedDictClass: _WithConfigTypeT, /) -> _WithConfigTypeT:
            TypedDictClass.__pydantic_config__ = config
            return TypedDictClass

        return inner


if TYPE_CHECKING:
    from ruamel.yaml import YAML


log = logging.getLogger(__name__)

_MutableMappingBase = MutableMapping[str, Any]
if TYPE_CHECKING:
    _MutableMappingBase = object


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

    set_default_hash: bool
    """
    Whether to set the default hash function for the config class. By default,
    Pydantic adds a `__hash__` method for frozen models, but Config classes are mutable,
    so we don't get a hash function by default. Setting this to `True` will enable the hash function
    for the config class, which is useful for using the config class as a key in a dictionary.
    Defaults to `True`.
    """

    disable_typed_dict_generation: bool
    """
    Whether to disable the generation of TypedDict classes for the config class.
    If `True`, the `nshconfig-export` will not generate TypedDict classes for the config class.
    Defaults to `False`.
    """


class DumpKwargs(TypedDict, total=False):
    include: IncEx | None
    """Field(s) to include in the JSON output."""
    exclude: IncEx | None
    """Field(s) to exclude from the JSON output."""
    context: Any | None
    """Additional context to pass to the serializer."""
    by_alias: bool
    """Whether to serialize using field aliases."""
    exclude_unset: bool
    """Whether to exclude fields that have not been explicitly set."""
    exclude_defaults: bool
    """Whether to exclude fields that are set to their default value."""
    exclude_none: bool
    """Whether to exclude fields that have a value of `None`."""
    round_trip: bool
    """If True, dumped values should be valid as input for non-idempotent types such as Json[T]."""
    warnings: bool | Literal["none", "warn", "error"]
    """How to handle serialization errors. False/"none" ignores them, True/"warn" logs errors, "error" raises a [`PydanticSerializationError`][pydantic_core.PydanticSerializationError]."""
    serialize_as_any: bool
    """Whether to serialize fields with duck-typing serialization behavior."""


class YamlDumpKwargs(DumpKwargs, total=False):
    default_flow_style: bool | None
    """Whether to use "flow style" (more human-readable).
    https://yaml.readthedocs.io/en/latest/detail.html?highlight=default_flow_style#indentation-of-block-sequences
    """
    map_indent: int | None
    """Indent value for mappings."""
    sequence_indent: int | None
    """Indent value for sequences."""
    sequence_dash_offset: int | None
    """Indent value for the dash in sequences."""
    custom_yaml_writer: "YAML | None"
    """An instance of ruamel.yaml.YAML (or a subclass) to use as the writer.
    The above options will be set on it, if given."""


_DEFAULT_JSON_DUMP_KWARGS: DumpKwargs = {}
_DEFAULT_YAML_DUMP_KWARGS: YamlDumpKwargs = {
    "sequence_dash_offset": 0,
    # For some reason, ^ is necessary to get a properly parsable YAML output
}


_JsonLoadContextSentinel = object()

_model_config: ConfigDict = {
    # Pydantic's default config options
    **BaseModel.model_config,
    # Our overrides
    "validate_assignment": True,
    "validate_return": True,
    "validate_default": True,
    "strict": True,
    "revalidate_instances": "always",
    "arbitrary_types_allowed": True,
    "extra": "forbid",
    # Our custom config options
    "repr_diff_only": False,
    "no_validate_assignment_for_draft": True,
    "set_default_hash": True,
    "disable_typed_dict_generation": False,
}


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

    model_config: ClassVar[ConfigDict] = {  # pyright: ignore[reportIncompatibleVariableOverride]
        # Pydantic's default config options
        **BaseModel.model_config,
        # Our overrides
        "validate_assignment": True,
        "validate_return": True,
        "validate_default": True,
        "strict": True,
        "revalidate_instances": "always",
        "arbitrary_types_allowed": True,
        "extra": "forbid",
        # The settings below are > 2.0.0; to prevent type-checking errors on
        # older versions, we use `cast` to ensure compatibility.
        **cast(
            ConfigDict,
            {
                "validation_error_cause": True,
                "use_attribute_docstrings": True,
            },
        ),
        # Our custom config options
        "repr_diff_only": False,
        "no_validate_assignment_for_draft": True,
        "set_default_hash": True,
        "disable_typed_dict_generation": False,
    }

    if TYPE_CHECKING:

        @override
        def __init_subclass__(cls, **kwargs: Unpack[ConfigDict]) -> None:
            super().__init_subclass__(**kwargs)

    @override
    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        if cls.model_config.get("set_default_hash", True):
            _try_set_hash(cls)

        from .registry import Registry, extract_registries_from_field_info

        log.debug(f"__pydantic_init_subclass__: {cls}")

        # Look through fields for registry annotations including nested types
        registries: list[Registry] = []
        registry_ids: set[int] = set()
        for name, field in cls.model_fields.items():
            if not (found_registries := extract_registries_from_field_info(field)):
                continue

            for registry in found_registries:
                if (id_ := id(registry)) in registry_ids:
                    continue

                log.debug(f"__pydantic_init_subclass__@{cls} - {name}: {registry}")
                registries.append(registry)
                registry_ids.add(id_)

        # Register rebuild callback with each registry found
        for registry in registries:
            registry._rebuild_on_registers_if_auto_rebuild(cls)

    def __draft_pre_init__(self):
        """Called right before a draft config is finalized."""
        pass

    def __post_init__(self):
        """Called after the final config is validated."""
        pass

    def __after_post_init__(self):
        """Called after __post_init__ is successfully called."""
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

    _patched_post_init: ClassVar[bool] = False

    @classmethod
    @contextlib.contextmanager
    def _nshconfig_patch_model_post_init(cls):
        cls._patched_post_init = True
        try:
            yield
        finally:
            cls._patched_post_init = False

    @override
    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)

        # Call the `__post_init__` method if this is not a draft config
        if self._patched_post_init:
            return

        self.__post_init__()

        self.__after_post_init__()

    def model_validate_no_missing(self):
        from .missing import validate_no_missing

        validate_no_missing(self)

    @classmethod
    def model_construct_draft(cls, _fields_set: set[str] | None = None, **values: Any):
        """
        This is a copy of the `model_construct` method from Pydantic's `Model` class,
            with the following changes:
            - The `model_post_init` method is called with the `_DraftConfigContext` context.
            - The `_is_draft_config` attribute is set to `True` in the `values` dict.
        """

        values = copy.deepcopy(values)
        values["_is_draft_config"] = True
        with cls._nshconfig_patch_model_post_init():
            return cls.model_construct(_fields_set, **values)

    @contextlib.contextmanager
    def _nshconfig_patch_validator_validate_assignment(self):
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
                    stack.enter_context(
                        self._nshconfig_patch_validator_validate_assignment()
                    )
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
    @model_serializer(mode="wrap")
    def include_literals(self, next_serializer):
        """Include fields with the `Literal` annotation in the dumped data,
        even if `exclude_defaults` is `True`.

        See https://github.com/pydantic/pydantic/discussions/9108#discussioncomment-8926452
        for the original implementation."""
        dumped = next_serializer(self)
        for name, field_info in type(self).model_fields.items():
            if get_origin(field_info.annotation) == Literal:
                dumped[name] = getattr(self, name)
        return dumped

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

    @classmethod
    def from_dict_or_instance(cls, data: Mapping[str, Any] | Self):
        """Create configuration from a dictionary or an instance.

        Args:
            data: Dictionary containing configuration values or an instance of the config

        Returns:
            A validated configuration instance
        """
        if isinstance(data, cls):
            return data
        elif isinstance(data, Mapping):
            return cls.from_dict(dict(data))
        else:
            raise TypeError(
                "data must be a dictionary or an instance of the config class"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to a dictionary.

        Returns:
            Dictionary representation of the configuration
        """
        return self.model_dump()

    # JSON

    @model_validator(mode="before")
    @classmethod
    def _pop_schema(cls, values: dict[str, Any], info: ValidationInfo):
        if (
            not info.context
            or not isinstance(info.context, dict)
            or (json_load_context := info.context.get("_nshconfig_json_load")) is None
            or json_load_context is not _JsonLoadContextSentinel
        ):
            return values

        # If the loaded config contains a `$schema` key (which was added by the
        # `to_json_str` method with `with_schema=True`), we remove it.
        values.pop("$schema", None)
        return values

    def to_json_str(
        self,
        /,
        with_schema: bool = True,
        indent: int | None = 4,
        **kwargs: Unpack[DumpKwargs],
    ) -> str:
        """Dump configuration to a JSON string.

        Args:
            with_schema: Whether to include the schema reference in the JSON file
            indent: Number of spaces to use for indentation
            kwargs: Additional keyword arguments to pass to the YAML dumper,
                these are parameters directly passed to cls.model_dump_json().
        """
        kwargs_with_defaults = _DEFAULT_JSON_DUMP_KWARGS.copy()
        kwargs_with_defaults.update(kwargs)
        json_str = self.model_dump_json(
            indent=indent, **cast(Any, kwargs_with_defaults)
        )

        # Add schema reference if available and with_schema is True
        if with_schema and (schema_uri := _get_schema_uri(type(self))):
            try:
                # A bit hacky here, but we will re-load the JSON,
                # add the $schema key, and then dump it again.
                json_obj = json.loads(json_str)
                if isinstance(json_obj, MutableMapping):
                    json_obj["$schema"] = schema_uri
                    json_str = json.dumps(json_obj, indent=indent)
                else:
                    log.warning(
                        "Could not add schema reference to JSON string. "
                        "The JSON object is not a dictionary. "
                        "The schema reference will not be added."
                    )
            except json.JSONDecodeError:
                log.warning(
                    "Could not add schema reference to JSON string. "
                    "The JSON string is not valid JSON. "
                    "The schema reference will not be added."
                )

        return json_str

    def to_json_file(
        self,
        path: str | Path,
        /,
        with_schema: bool = True,
        indent: int | None = 4,
        **kwargs: Unpack[DumpKwargs],
    ) -> None:
        """Save configuration to a JSON file.

        Args:
            path: Path where the JSON file will be saved
            with_schema: Whether to include the schema reference in the JSON file
            indent: Number of spaces to use for indentation
            kwargs: Additional keyword arguments to pass to the YAML dumper,
                these are parameters directly passed to cls.model_dump_json().
        """
        json_str = self.to_json_str(with_schema=with_schema, indent=indent, **kwargs)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json_str)

    @classmethod
    def from_json_str(cls, json_str: str | bytes, /):
        """Create configuration from a JSON string.

        Args:
            json_str: JSON string

        Returns:
            A validated configuration instance
        """
        return cls.model_validate_json(
            json_str, context={"_nshconfig_json_load": _JsonLoadContextSentinel}
        )

    @classmethod
    def from_json_file(cls, path: str | Path, /):
        """Create configuration from a JSON file.

        Args:
            path: Path to the JSON file

        Returns:
            A validated configuration instance
        """
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json_str(f.read())

    def to_yaml_str(
        self,
        /,
        with_schema: bool = True,
        indent: int | None = 4,
        **kwargs: Unpack[YamlDumpKwargs],
    ) -> str:
        """Dump configuration to a YAML string.

        Args:
            with_schema: Whether to include the schema reference in the YAML string
            indent: Number of spaces to use for indentation
            kwargs: Additional keyword arguments to pass to the YAML dumper,
                these are parameters directly passed to cls.model_dump_json().

        Raises:
            ImportError: If pydantic-yaml is not installed
        """
        try:
            from pydantic_yaml import to_yaml_str
        except ImportError:
            raise ImportError(
                "Pydantic-yaml is required for YAML support. "
                "You can either install nshconfig with "
                "all extras using 'pip install nshconfig[extra]'"
                ", install the yaml extra via 'pip install nshconfig[yaml]'"
                ", or install with 'pip install pydantic-yaml'"
            )

        kwargs_with_defaults = _DEFAULT_YAML_DUMP_KWARGS.copy()
        kwargs_with_defaults.update(kwargs)
        data_str = to_yaml_str(self, indent=indent, **kwargs_with_defaults)

        # Add YAML language server schema directive if with_schema is True
        if with_schema and (schema_uri := _get_schema_uri(type(self))):
            data_str = f"# yaml-language-server: $schema={schema_uri}\n\n" + data_str

        return data_str

    def to_yaml_file(
        self,
        path: str | Path,
        /,
        with_schema: bool = True,
        indent: int | None = 4,
        **kwargs: Unpack[YamlDumpKwargs],
    ) -> None:
        """Save configuration to a YAML file.

        Args:
            path: Path where the YAML file will be saved
            with_schema: Whether to include the schema reference in the YAML file
            indent: Number of spaces to use for indentation
            kwargs: Additional keyword arguments to pass to the YAML dumper,
                these are parameters directly passed to cls.model_dump_json().

        Raises:
            ImportError: If pydantic-yaml is not installed
        """
        yaml_str = self.to_yaml_str(with_schema=with_schema, indent=indent, **kwargs)

        # Write the file
        with open(path, "w", encoding="utf-8") as f:
            f.write(yaml_str)

    @classmethod
    def from_yaml_str(cls, yaml_str: str, /):
        """Create configuration from a YAML string.

        Args:
            yaml_str: YAML string

        Returns:
            A validated configuration instance

        Raises:
            ImportError: If pydantic-yaml is not installed
        """
        try:
            from pydantic_yaml import parse_yaml_raw_as
        except ImportError:
            raise ImportError(
                "Pydantic-yaml is required for YAML support. "
                "You can either install nshconfig with "
                "all extras using 'pip install nshconfig[extra]'"
                ", install the yaml extra via 'pip install nshconfig[yaml]'"
                ", or install with 'pip install pydantic-yaml'"
            )

        return parse_yaml_raw_as(cls, yaml_str)

    @classmethod
    def from_yaml(cls, path: str | Path, /):
        """Create configuration from a YAML file.

        Args:
            path: Path to the YAML file

        Returns:
            A validated configuration instance

        Raises:
            ImportError: If pydantic-yaml is not installed
        """
        try:
            from pydantic_yaml import parse_yaml_file_as
        except ImportError:
            raise ImportError(
                "Pydantic-yaml is required for YAML support. "
                "You can either install nshconfig with "
                "all extras using 'pip install nshconfig[extra]'"
                ", install the yaml extra via 'pip install nshconfig[yaml]'"
                ", or install with 'pip install pydantic-yaml'"
            )

        return parse_yaml_file_as(cls, path)

    @classmethod
    def from_python_file(cls, path: str | Path, /):
        """Create configuration from a Python file.

        The Python file should export a `__config__` variable that contains the configuration,
        or a `__create_config__` function that returns a configuration.

        Args:
            path: Path to the Python file

        Returns:
            A validated configuration instance

        Raises:
            FileNotFoundError: If the Python file does not exist
            ImportError: If the Python file cannot be imported
            ValueError: If the Python file does not export a valid `__config__` variable
        """
        from .utils import import_and_parse_python_file

        return import_and_parse_python_file(path, cls)

    @classmethod
    def from_python_module(cls, module_name: str, /):
        """Create configuration from a Python module.

        The Python module should export a `__config__` variable that contains the configuration,
        or a `__create_config__` function that returns a configuration.

        Args:
            module_name: Name of the Python module (e.g. "myapp.config")

        Returns:
            A validated configuration instance

        Raises:
            ImportError: If the Python module cannot be imported
            ValueError: If the Python module does not export a valid `__config__` variable
        """
        from .utils import import_and_parse_python_module

        return import_and_parse_python_module(module_name, cls)

    # endregion

    def to_toml_str(
        self,
        /,
        indent: int = 4,
        multiline_strings: bool = False,
        **kwargs: Unpack[DumpKwargs],
    ) -> str:
        """Dump configuration to a TOML string.

        Args:
            indent: Number of spaces to use for indentation
            multiline_strings: Whether to use multiline strings
            kwargs: Additional keyword arguments to pass to the TOML dumper,
                these are parameters directly passed to cls.model_dump_json().

        Raises:
            ImportError: If pydantic-yaml is not installed
        """

        try:
            from tomli_w import dumps
        except ImportError:
            raise ImportError(
                "Tomli-w is required for TOML support. "
                "You can either install nshconfig with "
                "all extras using 'pip install nshconfig[extra]'"
                ", install the w extra via 'pip install nshconfig[toml]'"
                ", or install with 'pip install tomli-w'"
            )

        # We need to convert the config to a dict first
        config_dict = self.model_dump(**cast(Any, kwargs))

        # Then we can use tomli_w to dump the dict to a TOML string
        toml_str = dumps(
            config_dict, indent=indent, multiline_strings=multiline_strings
        )
        return toml_str

    def to_toml_file(
        self,
        /,
        path: str | Path,
        indent: int = 4,
        multiline_strings: bool = False,
        **kwargs: Unpack[DumpKwargs],
    ) -> None:
        """Dump configuration to a TOML file.

        Args:
            path: Path to the TOML file
            indent: Number of spaces to use for indentation
            multiline_strings: Whether to use multiline strings
            kwargs: Additional keyword arguments to pass to the TOML dumper,
                these are parameters directly passed to cls.model_dump_json().
        """
        toml_str = self.to_toml_str(
            indent=indent, multiline_strings=multiline_strings, **kwargs
        )

        with open(path, "w") as f:
            f.write(toml_str)

    @classmethod
    def from_toml_str(cls, toml_str: str, /):
        """Create configuration from a TOML string.

        Args:
            toml_str: TOML string

        Returns:
            A validated configuration instance

        Raises:
            ImportError: If tomli is not installed
        """
        try:
            from tomli import loads
        except ImportError:
            raise ImportError(
                "Tomli is required for TOML support. "
                "You can either install nshconfig with "
                "all extras using 'pip install nshconfig[extra]'"
                ", install the tomli extra via 'pip install nshconfig[toml]'"
                ", or install with 'pip install tomli'"
            )

        config_dict = loads(toml_str)
        return cls.from_dict(config_dict)

    @classmethod
    def from_toml_file(cls, path: str | Path, /):
        """Create configuration from a TOML file.

        Args:
            path: Path to the TOML file
        """
        toml_str = Path(path).read_text()
        return cls.from_toml_str(toml_str)

    # region CLI
    # We're just adding this so `cli_cmd` override is visible in the IDE.
    if TYPE_CHECKING:

        def cli_cmd(self) -> None | Awaitable[None]:
            """
            The command to run the CLI. If this is implemented, the CLI will be run with this command.
            """
            raise NotImplementedError

    def cli_run_subcommand(self) -> Config:
        if PYDANTIC_SETTINGS_VERSION < "2.3.0":
            raise RuntimeError(
                "The `cli_run_subcommand` method is only available with Pydantic Settings >= 2.3.0. "
                f"Current version: {PYDANTIC_SETTINGS_VERSION}. "
                "Please upgrade Pydantic Settings to use this feature."
            )

        from .root import CLI

        return CLI.run_subcommand(self)

    @classmethod
    def cli_available_subcommands(cls) -> list[str]:
        if PYDANTIC_SETTINGS_VERSION < "2.3.0":
            raise RuntimeError(
                "The `cli_run_subcommand` method is only available with Pydantic Settings >= 2.3.0. "
                f"Current version: {PYDANTIC_SETTINGS_VERSION}. "
                "Please upgrade Pydantic Settings to use this feature."
            )

        from pydantic_settings import (
            CliSubCommand,  # pyright: ignore[reportAttributeAccessIssue]
        )

        _CliSubCommand = typing.get_args(CliSubCommand)[-1]

        return [
            field_name
            for field_name, field_info in cls.model_fields.items()
            if _CliSubCommand in field_info.metadata
        ]

    def cli_active_subcommand(self) -> str | None:
        """
        The active subcommand. This is set by the CLI when the config is created.
        """
        if PYDANTIC_SETTINGS_VERSION < "2.3.0":
            raise RuntimeError(
                "The `cli_run_subcommand` method is only available with Pydantic Settings >= 2.3.0. "
                f"Current version: {PYDANTIC_SETTINGS_VERSION}. "
                "Please upgrade Pydantic Settings to use this feature."
            )

        from pydantic_settings import (
            CliSubCommand,  # pyright: ignore[reportAttributeAccessIssue]
        )

        _CliSubCommand = typing.get_args(CliSubCommand)[-1]

        return next(
            (
                field_name
                for field_name, field_info in type(self).model_fields.items()
                if _CliSubCommand in field_info.metadata
                and getattr(self, field_name) is not None
            ),
            None,
        )

    # endregion


def _get_schema_uri(cls: type[Config]) -> str | None:
    """Helper to get the absolute schema path for a config class."""
    # First check for __nshconfig_json_schema_uri__ directly on the class (not inherited)
    if "__nshconfig_json_schema_uri__" in cls.__dict__:
        return getattr(cls, "__nshconfig_json_schema_uri__")

    from .export import find_config_metadata

    if not (metadata := find_config_metadata(cls)):
        return None

    _, schema_path = metadata
    if schema_path is None:
        return None

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
            file_path = str((current_dir / schema_path).resolve().absolute())
            return f"file://{file_path}"
        current_dir = current_dir.parent

    return None


def _try_set_hash_legacy(cls: type[Config], bases: tuple[type, ...]):
    if "__hash__" in cls.__dict__:
        return

    from pydantic_core import PydanticUndefined

    base_hash_func = None
    for base in bases:
        base_hash_func = getattr(base, "__hash__", PydanticUndefined)
        if base_hash_func is not PydanticUndefined:
            break

    if base_hash_func is None:
        # This will be the case for `BaseModel` since it defines `__eq__` but not `__hash__`.
        # In this case, we generate a standard hash function, generally for use with frozen models.

        def hash_func(self: Any) -> int:
            return hash(self.__class__) + hash(tuple(self.__dict__.values()))

        cls.__hash__ = hash_func  # type: ignore[assignment]


def _try_set_hash(cls: type[Config]):
    # On earlier versions of Pydantic, `set_default_hash_func` has a different signature,
    # so we need to handle that gracefully.
    if PYDANTIC_VERSION < "2.6":
        return _try_set_hash_legacy(cls, cls.__bases__)

    # This is a bit of a hack as we're relying on Pydantic's internal API,
    # but we can fail gracefully if it doesn't work.

    try:
        from pydantic._internal._model_construction import (
            set_default_hash_func,  # type: ignore
        )
    except ImportError:
        log.warning(
            "Could not set default hash function for config class. "
            "This should not happen. Please report this issue."
        )
        return

    set_default_hash_func(cast(Any, cls), cls.__bases__)
    log.debug(f"Default hash function set for class: {cls.__name__}")


_TypeT = TypeVar("_TypeT", bound=type[Config], infer_variance=True)


@overload
def with_config(config: ConfigDict, /) -> Callable[[_TypeT], _TypeT]: ...


@overload
def with_config(**config: Unpack[ConfigDict]) -> Callable[[_TypeT], _TypeT]: ...


def with_config(
    config: ConfigDict | None = None, /, **kwargs: Any
) -> Callable[[_TypeT], _TypeT]:
    return _pydantic_with_config(config, **kwargs)  # pyright: ignore[reportArgumentType]
