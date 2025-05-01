from __future__ import annotations

import asyncio
import inspect
import logging
import sys
import threading
from argparse import Namespace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic._internal._signature import _field_name_for_signature
from pydantic_settings import (
    BaseSettings,
    CliSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
    get_subcommand,
)
from pydantic_settings.sources import ENV_FILE_SENTINEL, DotenvType, PathType
from pydantic_settings.sources.types import PydanticModel
from typing_extensions import Self, TypeVar, override

from .config import Config, ConfigDict

log = logging.getLogger(__name__)


class RootConfigDict(ConfigDict, SettingsConfigDict, total=False):
    """RootConfigDict is a subclass of ConfigDict and SettingsConfigDict.

    It is used to define the configuration for the RootConfig class.
    """

    unset_magic_init_method: bool
    """Pydantic-settings overrides the `__init__` method to set default values for fields
    based on environment variables, CLI arguments, pyproject.toml, etc.

    However, I find this behavior to be confusing and unnecessary, and I instead
    prefer to have a dedicated classmethod for this magic initialization. Moreover,
    this behavior really messes with IDEs and type checkers, as they expect the
    `__init__` method to be the constructor of the class, and not a method that
    sets default values for fields. This option allows you to revert the initialization
    behavior to the default Pydantic behavior.

    Default: True
    """

    respect_pydantic_settings_callers: bool
    """If True, allow BaseSettings.__init__ to run when the call originates inside
    pydantic_settings.

    This settings is only relevant when `unset_magic_init_method` is set to True.
    When set to True, this setting allows pydantic-settings' magic initialization
    to still work when the call originates inside pydantic_settings. This is so
    that we don't break any existing code that relies on this behavior. I don't
    think this is a good idea. I'm leaving it in for backwards compatibility, but
    it is disabled by default.

    Default: False
    """

    auto_set_sources_from_model_config: bool
    """If True, analyzes the current RootConfig class' model config and adds
    additional SettingsSources depending on the model config. Specifically, it:

    - Adds `JsonConfigSettingsSource` if `json_file` is set in the model config.
    - Adds `YamlConfigSettingsSource` if `yaml_file` is set in the model config.
    - Adds `TomlConfigSettingsSource` if `toml_file` is set in the model config.
    - Adds `PyprojectTomlConfigSettingsSource` if `pyproject_toml_table_header`
        is set in the model config.

    Default: True
    """


class RootConfig(BaseSettings, Config):
    model_config: ClassVar[RootConfigDict] = RootConfigDict(  # pyright: ignore[reportIncompatibleVariableOverride]
        validate_assignment=True,
        validate_return=True,
        validate_default=True,
        strict=True,
        revalidate_instances="always",
        arbitrary_types_allowed=True,
        extra="forbid",
        validation_error_cause=True,
        use_attribute_docstrings=True,
        # BaseSettings default options
        case_sensitive=False,
        env_prefix="",
        nested_model_default_partial_update=False,
        env_file=None,
        env_file_encoding=None,
        env_ignore_empty=False,
        env_nested_delimiter=None,
        env_nested_max_split=None,
        env_parse_none_str=None,
        env_parse_enums=None,
        cli_prog_name=None,
        cli_parse_args=None,
        cli_parse_none_str=None,
        cli_hide_none_type=False,
        cli_avoid_json=False,
        cli_enforce_required=False,
        cli_use_class_docs_for_groups=False,
        cli_exit_on_error=True,
        cli_prefix="",
        cli_flag_prefix_char="-",
        cli_implicit_flags=False,
        cli_ignore_unknown_args=False,
        cli_kebab_case=False,
        json_file=None,
        json_file_encoding=None,
        yaml_file=None,
        yaml_file_encoding=None,
        toml_file=None,
        secrets_dir=None,
        protected_namespaces=(
            "model_validate",
            "model_dump",
            "settings_customise_sources",
        ),
        enable_decoding=True,
        # Custom Config options
        repr_diff_only=False,
        no_validate_assignment_for_draft=True,
        set_default_hash=True,
        disable_typed_dict_generation=False,
        # Custom RootConfig options
        unset_magic_init_method=True,
        respect_pydantic_settings_callers=False,
        auto_set_sources_from_model_config=True,
    )

    if not TYPE_CHECKING:

        @override
        def __init__(
            self,
            /,
            _nshconfig_rootconfig_magic_init: bool = False,
            **data: Any,
        ) -> None:
            __tracebackhide__ = True

            # If unset_magic_init_method is not set, just continue with the
            # default pydantic-settings behavior.
            # Or if we explicitly set `_nshconfig_rootconfig_magic_init` to True,
            # we want to continue with the default pydantic-settings behavior.
            if (
                not self.model_config.get("unset_magic_init_method", True)
                or _nshconfig_rootconfig_magic_init
            ):
                # full Pydantic-settings initialization
                super().__init__(**data)
                return

            if self.model_config.get("respect_pydantic_settings_callers", False):
                # look up the stack to see if any caller is in pydantic_settings
                frame = sys._getframe(1)
                came_from_pydantic_settings = False
                while frame:
                    mod = frame.f_globals.get("__name__", "")
                    if mod.startswith("pydantic_settings"):
                        came_from_pydantic_settings = True
                        break
                    frame = frame.f_back
                del frame  # avoid reference cycles

                if came_from_pydantic_settings:
                    log.info(
                        "BaseSettings.__init__ called from pydantic_settings, "
                        "enabling BaseSettings magic initialization"
                    )
                    # let BaseSettings handle it
                    super().__init__(**data)
                    return

            # either respect=False, or no pydantic caller found:
            # skip BaseSettings and go straight to Config.__init__
            log.info(
                "BaseSettings.__init__ called from outside pydantic_settings, "
                "skipping BaseSettings magic initialization"
            )
            super(BaseSettings, self).__init__(**data)

    @override
    @classmethod
    def settings_customise_sources(  # pyright: ignore[reportIncompatibleMethodOverride]
        cls,
        settings_cls: type[Self],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        t = super().settings_customise_sources(
            settings_cls,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

        # If the model config has `auto_set_sources_from_model_config` set to True,
        # we want to add the additional settings sources based on the model config.
        if not settings_cls.model_config.get(
            "auto_set_sources_from_model_config", True
        ):
            return t

        if settings_cls.model_config.get("json_file"):
            from pydantic_settings import JsonConfigSettingsSource

            t = (JsonConfigSettingsSource(settings_cls),) + t

        if settings_cls.model_config.get("yaml_file"):
            from pydantic_settings import YamlConfigSettingsSource

            t = (YamlConfigSettingsSource(settings_cls),) + t

        if settings_cls.model_config.get("toml_file"):
            from pydantic_settings import TomlConfigSettingsSource

            t = (TomlConfigSettingsSource(settings_cls),) + t

        if settings_cls.model_config.get("pyproject_toml_table_header"):
            from pydantic_settings import PyprojectTomlConfigSettingsSource

            t = (PyprojectTomlConfigSettingsSource(settings_cls),) + t

        return t

    @classmethod
    def auto_init(
        cls,
        _case_sensitive: bool | None = None,
        _nested_model_default_partial_update: bool | None = None,
        _env_prefix: str | None = None,
        _env_file: DotenvType | None = ENV_FILE_SENTINEL,
        _env_file_encoding: str | None = None,
        _env_ignore_empty: bool | None = None,
        _env_nested_delimiter: str | None = None,
        _env_nested_max_split: int | None = None,
        _env_parse_none_str: str | None = None,
        _env_parse_enums: bool | None = None,
        _cli_prog_name: str | None = None,
        _cli_parse_args: bool | list[str] | tuple[str, ...] | None = None,
        _cli_settings_source: CliSettingsSource[Any] | None = None,
        _cli_parse_none_str: str | None = None,
        _cli_hide_none_type: bool | None = None,
        _cli_avoid_json: bool | None = None,
        _cli_enforce_required: bool | None = None,
        _cli_use_class_docs_for_groups: bool | None = None,
        _cli_exit_on_error: bool | None = None,
        _cli_prefix: str | None = None,
        _cli_flag_prefix_char: str | None = None,
        _cli_implicit_flags: bool | None = None,
        _cli_ignore_unknown_args: bool | None = None,
        _cli_kebab_case: bool | None = None,
        _secrets_dir: PathType | None = None,
        **values: Any,
    ):
        """
        Instantiate and initialize a new `RootConfig` by automatically discovering,
        loading, and validating configuration values from all supported sources.

        This method aggregates settings from:
          - Environment variables
          - CLI arguments (via `CliSettingsSource`)
          - JSON, TOML, and YAML files (`env_file`, `json_file`, `toml_file`, `yaml_file`)
          - `pyproject.toml` and other project configuration
          - Secrets directory (`secrets_dir`)

        The method also supports custom settings sources via `settings_sources`.
        """
        return cls(
            **values,
            _nshconfig_rootconfig_magic_init=True,  # pyright: ignore[reportCallIssue]
            _case_sensitive=_case_sensitive,  # pyright: ignore[reportCallIssue]
            _nested_model_default_partial_update=_nested_model_default_partial_update,  # pyright: ignore[reportCallIssue]
            _env_prefix=_env_prefix,  # pyright: ignore[reportCallIssue]
            _env_file=_env_file,  # pyright: ignore[reportCallIssue]
            _env_file_encoding=_env_file_encoding,  # pyright: ignore[reportCallIssue]
            _env_ignore_empty=_env_ignore_empty,  # pyright: ignore[reportCallIssue]
            _env_nested_delimiter=_env_nested_delimiter,  # pyright: ignore[reportCallIssue]
            _env_nested_max_split=_env_nested_max_split,  # pyright: ignore[reportCallIssue]
            _env_parse_none_str=_env_parse_none_str,  # pyright: ignore[reportCallIssue]
            _env_parse_enums=_env_parse_enums,  # pyright: ignore[reportCallIssue]
            _cli_prog_name=_cli_prog_name,  # pyright: ignore[reportCallIssue]
            _cli_parse_args=_cli_parse_args,  # pyright: ignore[reportCallIssue]
            _cli_settings_source=_cli_settings_source,  # pyright: ignore[reportCallIssue]
            _cli_parse_none_str=_cli_parse_none_str,  # pyright: ignore[reportCallIssue]
            _cli_hide_none_type=_cli_hide_none_type,  # pyright: ignore[reportCallIssue]
            _cli_avoid_json=_cli_avoid_json,  # pyright: ignore[reportCallIssue]
            _cli_enforce_required=_cli_enforce_required,  # pyright: ignore[reportCallIssue]
            _cli_use_class_docs_for_groups=_cli_use_class_docs_for_groups,  # pyright: ignore[reportCallIssue]
            _cli_exit_on_error=_cli_exit_on_error,  # pyright: ignore[reportCallIssue]
            _cli_prefix=_cli_prefix,  # pyright: ignore[reportCallIssue]
            _cli_flag_prefix_char=_cli_flag_prefix_char,  # pyright: ignore[reportCallIssue]
            _cli_implicit_flags=_cli_implicit_flags,  # pyright: ignore[reportCallIssue]
            _cli_ignore_unknown_args=_cli_ignore_unknown_args,  # pyright: ignore[reportCallIssue]
            _cli_kebab_case=_cli_kebab_case,  # pyright: ignore[reportCallIssue]
            _secrets_dir=_secrets_dir,  # pyright: ignore[reportCallIssue]
        )


T = TypeVar("T", bound=Config, infer_variance=True)


class CLI:
    """
    A utility class for running `nshconfig.RootConfig` or `nshconfig.Config`
    instances as CLI applications.
    """

    @staticmethod
    def _run_cli_cmd(model: Any, cli_cmd_method_name: str, is_required: bool) -> Any:
        if (command := getattr(type(model), cli_cmd_method_name, None)) is None:
            if is_required:
                raise SettingsError(
                    f"Error: {type(model).__name__} class is missing {cli_cmd_method_name} entrypoint"
                )
            return model

        # If the method is asynchronous, we handle its execution based on the current event loop status.
        if inspect.iscoroutinefunction(command):
            # For asynchronous methods, we have two execution scenarios:
            # 1. If no event loop is running in the current thread, run the coroutine directly with asyncio.run().
            # 2. If an event loop is already running in the current thread, run the coroutine in a separate thread to avoid conflicts.
            try:
                # Check if an event loop is currently running in this thread.
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # We're in a context with an active event loop (e.g., Jupyter Notebook).
                # Running asyncio.run() here would cause conflicts, so we use a separate thread.
                exception_container = []

                def run_coro() -> None:
                    try:
                        # Execute the coroutine in a new event loop in this separate thread.
                        asyncio.run(command(model))
                    except Exception as e:
                        exception_container.append(e)

                thread = threading.Thread(target=run_coro)
                thread.start()
                thread.join()
                if exception_container:
                    # Propagate exceptions from the separate thread.
                    raise exception_container[0]
            else:
                # No event loop is running; safe to run the coroutine directly.
                asyncio.run(command(model))
        else:
            # For synchronous methods, call them directly.
            command(model)

        return model

    @staticmethod
    def run(
        model_cls: type[T],
        cli_args: list[str]
        | Namespace
        | SimpleNamespace
        | dict[str, Any]
        | None = None,
        cli_settings_source: CliSettingsSource[Any] | None = None,
        cli_exit_on_error: bool | None = None,
        cli_cmd_method_name: str = "cli_cmd",
        **model_init_data: Any,
    ) -> T:
        """
        Runs a Pydantic `nshconfig.RootConfig` or `nshconfig.Config` as a CLI application.
        Running a model as a CLI application requires the `cli_cmd` method to be defined in the model class.

        Args:
            model_cls: The model class to run as a CLI application.
            cli_args: The list of CLI arguments to parse. If `cli_settings_source` is specified, this may
                also be a namespace or dictionary of pre-parsed CLI arguments. Defaults to `sys.argv[1:]`.
            cli_settings_source: Override the default CLI settings source with a user defined instance.
                Defaults to `None`.
            cli_exit_on_error: Determines whether this function exits on error. If model is subclass of
                `nshconfig.RootConfig`, defaults to nshconfig.RootConfig `cli_exit_on_error` value. Otherwise, defaults to
                `True`.
            cli_cmd_method_name: The CLI command method name to run. Defaults to "cli_cmd".
            model_init_data: The model init data.

        Returns:
            The ran instance of model.

        Raises:
            SettingsError: If model_cls is not subclass of `BaseModel` or `pydantic.dataclasses.dataclass`.
            SettingsError: If model_cls does not have a `cli_cmd` entrypoint defined.
        """

        if not issubclass(model_cls, (Config, RootConfig)):
            raise SettingsError(
                f"Error: {model_cls.__name__} is not subclass of BaseModel or pydantic.dataclasses.dataclass"
            )

        cli_settings = None
        cli_parse_args = True if cli_args is None else cli_args
        if cli_settings_source is not None:
            if isinstance(cli_parse_args, (Namespace, SimpleNamespace, dict)):
                cli_settings = cli_settings_source(parsed_args=cli_parse_args)
            else:
                cli_settings = cli_settings_source(args=cli_parse_args)
        elif isinstance(cli_parse_args, (Namespace, SimpleNamespace, dict)):
            raise SettingsError(
                "Error: `cli_args` must be list[str] or None when `cli_settings_source` is not used"
            )

        model_init_data["_cli_parse_args"] = cli_parse_args
        model_init_data["_cli_exit_on_error"] = cli_exit_on_error
        model_init_data["_cli_settings_source"] = cli_settings
        if not issubclass(model_cls, RootConfig):
            # If the model is not a subclass of RootConfig, we need to create a new
            # instance of RootConfig, use that to automatically load the configuration,
            # and then pass the loaded configuration to the model.
            # Note that this is a bit of a hack beacuse we don't actually use
            # the RootConfig for anything other than loading the configuration.

            class CliAppBaseSettings(RootConfig, model_cls):  # pyright: ignore[reportIncompatibleVariableOverride]
                __doc__ = model_cls.__doc__
                model_config = RootConfigDict(
                    nested_model_default_partial_update=True,
                    case_sensitive=True,
                    cli_hide_none_type=True,
                    cli_avoid_json=True,
                    cli_enforce_required=True,
                    cli_implicit_flags=True,
                    cli_kebab_case=True,
                )

            model = CliAppBaseSettings.auto_init(**model_init_data)
            model_init_data = {}
            for field_name, field_info in type(model).model_fields.items():
                model_init_data[_field_name_for_signature(field_name, field_info)] = (
                    getattr(model, field_name)
                )
            instance = model_cls(**model_init_data)
        else:
            instance = model_cls.auto_init(**model_init_data)

        return CLI._run_cli_cmd(instance, cli_cmd_method_name, is_required=False)

    @staticmethod
    def run_subcommand(
        model: PydanticModel,
        cli_exit_on_error: bool | None = None,
        cli_cmd_method_name: str = "cli_cmd",
    ) -> PydanticModel:
        """
        Runs the model subcommand. Running a model subcommand requires the `cli_cmd` method to be defined in
        the nested model subcommand class.

        Args:
            model: The model to run the subcommand from.
            cli_exit_on_error: Determines whether this function exits with error if no subcommand is found.
                Defaults to model_config `cli_exit_on_error` value if set. Otherwise, defaults to `True`.
            cli_cmd_method_name: The CLI command method name to run. Defaults to "cli_cmd".

        Returns:
            The ran subcommand model.

        Raises:
            SystemExit: When no subcommand is found and cli_exit_on_error=`True` (the default).
            SettingsError: When no subcommand is found and cli_exit_on_error=`False`.
        """

        subcommand = get_subcommand(
            model, is_required=True, cli_exit_on_error=cli_exit_on_error
        )
        return CLI._run_cli_cmd(subcommand, cli_cmd_method_name, is_required=True)


CliApp = CLI
