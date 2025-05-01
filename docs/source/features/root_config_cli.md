# RootConfig & CLI Integration

`nshconfig` provides `RootConfig`, a specialized configuration class that integrates seamlessly with `pydantic-settings` to load settings from various sources, including environment variables, `.env` files, secrets directories, and command-line arguments. It also offers the `CLI` utility to easily turn your configuration models into command-line applications.

## `RootConfig`

`RootConfig` inherits from both `nshconfig.Config` and `pydantic_settings.BaseSettings`, combining the features of both. It's the recommended base class for your top-level application configuration.

```python
from nshconfig import RootConfig

class AppSettings(RootConfig):
    """My application settings."""
    debug_mode: bool = False
    """Enable debug mode"""
    api_key: str
    """API key for external service"""
    database_url: str = "sqlite:///./default.db"
    """Database connection URL"""

    # You can configure pydantic-settings behavior via model_config
    model_config = {
        "env_prefix": "MYAPP_",  # Environment variables should start with MYAPP_
        "env_file": ".env",      # Load settings from .env file
        "secrets_dir": "/run/secrets", # Load secrets from files in this directory
        "cli_parse_args": True,  # Enable parsing from CLI arguments
        "use_attribute_docstrings": True, # Ensure docstrings are used for descriptions
    }

# Initialize settings using auto_init
# This will load from .env, environment variables (MYAPP_DEBUG_MODE, MYAPP_API_KEY, ...),
# secrets (/run/secrets/api_key), and CLI arguments (--debug-mode, --api-key, ...)
settings = AppSettings.auto_init()

print(settings.model_dump_json(indent=2))
```

### `auto_init`

Instead of relying on the potentially confusing magic `__init__` behavior of `pydantic-settings.BaseSettings` (which tries to load settings automatically upon instantiation), `nshconfig.RootConfig` provides the explicit `auto_init` class method.

`auto_init` performs the core logic of `pydantic-settings`: it discovers, loads, and validates configuration values from all configured sources (environment, files, CLI, etc.) and returns a fully validated instance of your `RootConfig` class.

You can pass keyword arguments to `auto_init` to provide initial values or override specific settings source parameters (like `_env_file`, `_secrets_dir`, `_cli_parse_args`, etc.).

### Controlling `__init__` Behavior

By default (`unset_magic_init_method=True` in `RootConfigDict`), `RootConfig` disables the automatic settings loading within the standard `__init__` method. This makes `__init__` behave like a standard Pydantic `BaseModel` constructor, improving clarity and compatibility with type checkers and IDEs.

If you need the original `pydantic-settings` behavior where `__init__` *also* loads settings, you can configure it:

```python
class LegacySettings(RootConfig):
    # ... fields ...

    model_config = {
        "unset_magic_init_method": False, # Re-enable magic __init__
        # ... other settings ...
    }

# Now, __init__ will also load settings (though auto_init is still recommended)
settings = LegacySettings(api_key="provided_at_init")
```

## `CLI` Utility (`CliApp`)

The `nshconfig.CLI` class (also available as `nshconfig.CliApp`) provides static methods to run your `Config` or `RootConfig` classes as command-line applications.

### Basic Usage with `cli_cmd`

To make a configuration class runnable, define a `cli_cmd` method within it. This method will be executed after the configuration is loaded and validated via `CLI.run`.

```python
import asyncio
from nshconfig import RootConfig, CLI

class ToolConfig(RootConfig):
    input_file: str
    """Path to the input file"""
    output_file: str = "output.txt"
    """Path to the output file"""
    force: bool = False
    """Overwrite output file if it exists"""

    model_config = {
        "cli_parse_args": True,
        "use_attribute_docstrings": True,
    }

    def cli_cmd(self) -> None:
        """The main logic of the command-line tool."""
        print(f"Processing {self.input_file}...")
        # ... actual processing logic ...
        print(f"Writing output to {self.output_file} (Force: {self.force})")
        print("Done.")

    # Example of an async command
    # async def cli_cmd(self) -> None:
    #     print(f"Processing {self.input_file} asynchronously...")
    #     await asyncio.sleep(1)
    #     print(f"Writing output to {self.output_file} (Force: {self.force})")
    #     print("Done.")


if __name__ == "__main__":
    # Run the config as a CLI app.
    # CLI.run() will:
    # 1. Instantiate ToolConfig using auto_init (loading from CLI args).
    # 2. Call the cli_cmd() method on the instance.
    config_instance = CLI.run(ToolConfig)

    # config_instance holds the validated configuration used
    print(f"CLI run finished. Final config: {config_instance.input_file=}")

```

You can then run this script from your terminal:

```bash
python your_script.py --input-file data.csv --force
# Output:
# Processing data.csv...
# Writing output to output.txt (Force: True)
# Done.
# CLI run finished. Final config: config_instance.input_file='data.csv'
```

`CLI.run` handles parsing arguments (using the underlying `pydantic-settings` and `argparse` integration), instantiating the model via `auto_init`, and then executing the `cli_cmd` method. It also correctly handles `async def cli_cmd` methods.

### Subcommands

`pydantic-settings` (and therefore `nshconfig`) supports defining subcommands using nested models annotated with `nshconfig.CliSubCommand`.

```python
from nshconfig import RootConfig, Config, CLI, CliSubCommand
from typing import Annotated
from collections.abc import Sequence

class UserConfig(Config):
    name: str
    """User name"""
    email: str | None = None
    """User email"""

    model_config = {"use_attribute_docstrings": True}

    def cli_cmd(self) -> None:
        print(f"Running user command for {self.name}")
        if self.email:
            print(f"Email: {self.email}")

class GroupConfig(Config):
    group_name: str
    """Group name"""
    members: list[str] = []
    """List of members"""

    model_config = {"use_attribute_docstrings": True}

    def cli_cmd(self) -> None:
        print(f"Running group command for {self.group_name}")
        print(f"Members: {', '.join(self.members) or 'None'}")

class AdminTool(RootConfig):
    """Admin tool with user and group subcommands."""
    verbose: bool = False
    """Enable verbose output"""
    user: Annotated[UserConfig | None, CliSubCommand()] = None
    group: Annotated[GroupConfig | None, CliSubCommand()] = None

    model_config = {
        "cli_parse_args": True,
        "use_attribute_docstrings": True,
    }

    # Optional: Define a top-level command if no subcommand is given
    # def cli_cmd(self) -> None:
    #     print("Admin tool main command. Use 'user' or 'group' subcommand.")


if __name__ == "__main__":
    # CLI.run will parse args, identify the subcommand, instantiate it,
    # and run its cli_cmd.
    config_instance = CLI.run(AdminTool)

    # You can also manually run the subcommand if needed
    # active_subcommand_name = config_instance.cli_active_subcommand()
    # if active_subcommand_name:
    #     print(f"Active subcommand: {active_subcommand_name}")
    #     # CLI.run_subcommand(config_instance) # Runs the active subcommand's cli_cmd
```

Running this script:

```bash
# Get help
python admin_tool.py --help

# Run the user subcommand
python admin_tool.py user --name Alice --email alice@example.com
# Output:
# Running user command for Alice
# Email: alice@example.com

# Run the group subcommand
python admin_tool.py group --group-name admins --members bob carol
# Output:
# Running group command for admins
# Members: bob, carol
```

`CLI.run` automatically detects and executes the `cli_cmd` of the chosen subcommand. You can also use `CLI.run_subcommand(instance)` to explicitly run the active subcommand's `cli_cmd` on an already instantiated model.

### CLI Methods on `Config`

The base `nshconfig.Config` class also gains a few CLI-related helper methods:

*   `config.cli_run_subcommand() -> Config`: Runs the `cli_cmd` of the active subcommand, if any.
*   `Config.cli_available_subcommands() -> list[str]`: Returns a list of field names that are defined as subcommands.
*   `config.cli_active_subcommand() -> str | None`: Returns the name of the subcommand field that was activated via CLI arguments, or `None`.

For more advanced CLI customization options (like argument groups, positional arguments, custom parsers), refer to the [pydantic-settings Command Line Support documentation](https://docs.pydantic.dev/pydantic-settings/usage/cli/). `nshconfig` exposes the necessary types like `CliPositionalArg`, `CliExplicitFlag`, etc., via `from nshconfig import ...`.
