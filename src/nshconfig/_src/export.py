from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import logging
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typing_inspect
from typing_extensions import TypeAliasType

from .config import Config
from .json_schema import convert_schema

CODEGEN_MARKER = "__codegen__ = True"


@dataclass(frozen=True)
class Export:
    pass


def find_config_metadata(cls: type) -> tuple[str | None, str | None] | None:
    """
    Looks up through a module's hierarchy to find the .nshconfig.generated.json file
    and returns the typed dict and schema paths for the given class if they exist.

    Args:
        cls: The class to look up metadata for

    Returns:
        A tuple of (typed_dict_path, schema_path) if metadata is found, None otherwise.
        Each path can be None if that specific file is not generated.
    """
    try:
        # Get the module spec to find the module's location
        if (
            spec := importlib.util.find_spec(cls.__module__)
        ) is None or spec.origin is None:
            return None

        # Start from the module's directory and look up
        current_dir = Path(spec.origin).parent
        while current_dir.parent != current_dir:  # Stop at root
            metadata_file = current_dir / ".nshconfig.generated.json"
            if metadata_file.exists():
                # Found metadata file, load it
                with metadata_file.open() as f:
                    metadata = json.load(f)

                # Get the full class name
                class_fullname = f"{cls.__module__}.{cls.__name__}"

                # Look up typed dict path
                typed_dict_path = None
                if metadata.get("typed_dicts"):
                    typed_dict_path = metadata["typed_dicts"].get(class_fullname)

                # Look up schema path
                schema_path = None
                if metadata.get("json_schemas"):
                    schema_path = metadata["json_schemas"].get(class_fullname)

                # Return paths if we found either
                if typed_dict_path is not None or schema_path is not None:
                    return typed_dict_path, schema_path

            # Move up one directory
            current_dir = current_dir.parent

        return None

    except (ImportError, AttributeError, KeyError, json.JSONDecodeError):
        return None


def export_main():
    parser = argparse.ArgumentParser(
        description="Export the configurations in a given module"
    )
    parser.add_argument(
        "module",
        type=str,
        help="The module to export the configurations from",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="The output directory to write the configurations to",
        required=True,
    )
    parser.add_argument(
        "--remove-existing",
        "--rm",
        action=argparse.BooleanOptionalAction,
        help="Remove existing export files before exporting",
        default=True,
    )
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        help="Recursively export configurations from all submodules",
        default=True,
    )
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--ignore-module",
        action="append",
        help="Ignore the given module",
        default=[],
        type=str,
    )
    parser.add_argument(
        "--ignore-abc",
        action=argparse.BooleanOptionalAction,
        help="Ignore Abstract Base Classes",
    )
    parser.add_argument(
        "--export-generics",
        action=argparse.BooleanOptionalAction,
        help="Export generic types",
    )
    parser.add_argument(
        "-td",
        "--generate-typed-dicts",
        action=argparse.BooleanOptionalAction,
        help="Generate TypedDicts for config objects",
    )
    parser.add_argument(
        "-iod",
        "--generate-instance-or-dict",
        action=argparse.BooleanOptionalAction,
        help="Generate InstanceOrDict unions for config objects",
    )
    parser.add_argument(
        "-js",
        "--generate-json-schema",
        action=argparse.BooleanOptionalAction,
        help="Generate JSON schema for config objects",
    )
    parser.add_argument(
        "--all",
        action=argparse.BooleanOptionalAction,
        help="Generate __all__ declarations in export files",
        default=True,
    )
    args = parser.parse_args()

    # Extract typed arguments
    module: str = args.module
    output: Path = args.output
    remove_existing: bool = args.remove_existing
    recursive: bool = args.recursive
    verbose: bool = args.verbose
    ignore_module: list[str] = args.ignore_module
    ignore_abc: bool = args.ignore_abc
    export_generics: bool = args.export_generics
    generate_typed_dicts: bool = args.generate_typed_dicts
    generate_instance_or_dict: bool = args.generate_instance_or_dict
    generate_json_schema: bool = args.generate_json_schema
    generate_all: bool = args.all

    # Set up logging
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level)
    logging.debug(f"Arguments: {args}")

    # Just remove the output directory if remove_existing is True
    if remove_existing and output.exists():
        logging.critical(f"Removing existing output directory {output}")
        if output.is_dir():
            shutil.rmtree(output)
        else:
            output.unlink()

    # Find all modules to export
    modules: list[str] = _find_modules(module, recursive, ignore_module)

    # For each module, import it, find all subclasses of Config and export them
    config_cls_dict = defaultdict[str, set[type[Any]]](lambda: set[type[Any]]())
    alias_dict = defaultdict[str, dict[str, Any]](dict)
    for module_name in modules:
        if _is_generated_module(module_name):
            logging.debug(f"Skipping generated module {module_name}")
            continue

        logging.debug(f"Exporting configurations from {module_name}")
        for config_cls in _module_configs(
            module_name,
            ignore_abc,
            export_generics,
            module,
        ):
            logging.debug(f"Exporting {config_cls}")
            config_cls_dict[module_name].add(config_cls)

        for name, obj in _alias_configs(
            module_name,
            ignore_abc,
            export_generics,
            module,
        ):
            alias_dict[module_name][name] = obj

    # If `generate_typed_dicts`, we need to generate TypedDicts for the config objects.
    typed_dict_mapping: dict[str, Path] | None = None
    if generate_typed_dicts:
        typed_dict_mapping = _generate_typed_dicts(config_cls_dict, output)

    # If `generate_json_schema`, we need to generate JSON schema for the config objects.
    json_schema_mapping: dict[str, Path] | None = None
    if generate_json_schema:
        json_schema_mapping = _generate_json_schema(config_cls_dict, output)

    # Create export files
    _create_export_files(
        output,
        module,
        config_cls_dict,
        alias_dict,
        generate_typed_dicts,
        generate_instance_or_dict,
        generate_all=generate_all,
    )

    # Write some metadata so we can identify generated folders.
    with (output.parent / ".nshconfig.generated.json").open("w") as f:
        # Write the generated module and the output directory
        json.dump(
            {
                "module": module,
                "output": str(output.relative_to(output.parent)),
                "typed_dicts": {
                    k: str(v.relative_to(output.parent))
                    for k, v in typed_dict_mapping.items()
                }
                if typed_dict_mapping
                else None,
                "json_schemas": {
                    k: str(v.relative_to(output.parent))
                    for k, v in json_schema_mapping.items()
                }
                if json_schema_mapping
                else None,
            },
            f,
            indent=4,
        )


def _run_ruff(file_path: Path):
    """Run ruff format and fix imports on a file."""
    try:
        # First, format.
        subprocess.run(
            ["ruff", "format", "--silent", str(file_path.absolute())],
            check=True,
        )
        # Then, fix imports.
        subprocess.run(
            [
                "ruff",
                "check",
                "--silent",
                "--extend-select",
                "I",
                "--fix",
                str(file_path.absolute()),
            ],
            check=True,
        )
        # Then, format again.
        subprocess.run(
            ["ruff", "format", "--silent", str(file_path.absolute())],
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


IMPORTS_TEMPLATE = """\
if typ.TYPE_CHECKING:
    from {ConfigModule} import {ConfigClassName}
"""

TYPED_CREATOR_TEMPLATE = """\
@typ.overload
def Create{ConfigClassName}(**dict: typ.Unpack[{TypedDictName}]) -> {ConfigClassName}: ...

@typ.overload
def Create{ConfigClassName}(data: {TypedDictName} | {ConfigClassName}, /) -> {ConfigClassName}: ...

def Create{ConfigClassName}(*args, **kwargs):
    from {ConfigModule} import {ConfigClassName}

    if not args and kwargs:
        # Called with keyword arguments
        return {ConfigClassName}.from_dict(kwargs)
    elif len(args) == 1:
        return {ConfigClassName}.from_dict_or_instance(args[0])
    else:
        raise TypeError(
            f"Create{ConfigClassName} accepts either a {TypedDictName}, "
            f"keyword arguments, or a {ConfigClassName} instance"
        )
"""


def _typed_dict_name_for_config_cls_name(class_name: str) -> str:
    """
    Returns the name of the TypedDict for the given Config subclass.
    """
    return f"{class_name}TypedDict"


def _typed_dict_file_exports(class_name: str) -> list[str]:
    """
    Returns the exports for the typed dict file in a deterministic order.
    """
    typed_dict_name = _typed_dict_name_for_config_cls_name(class_name)
    return sorted([typed_dict_name, f"Create{class_name}"])


def _config_cls_to_typed_dict_code(config_cls: type[Config]) -> str:
    """
    Generates the TypedDict code for the given Config subclass.
    """

    # Convert the config cls to a JSON schema
    schema = config_cls.model_json_schema()

    # Convert the JSON schema to TypedDict code
    imports = IMPORTS_TEMPLATE.format(
        ConfigClassName=config_cls.__name__,
        ConfigModule=config_cls.__module__,
    )
    typed_dict_code = convert_schema(
        schema,
        _typed_dict_name_for_config_cls_name(config_cls.__name__),
        f"{imports}\n\n{CODEGEN_MARKER}",
    )

    # Generate the typed creator code
    typed_creator_code = TYPED_CREATOR_TEMPLATE.format(
        ConfigClassName=config_cls.__name__,
        TypedDictName=_typed_dict_name_for_config_cls_name(config_cls.__name__),
        ConfigModule=config_cls.__module__,
    )

    return f"{typed_dict_code}\n\n{typed_creator_code}"


def _class_fullname(cls: type) -> str:
    return f"{cls.__module__}.{cls.__name__}"


def _generate_typed_dicts(
    config_cls_dict: Mapping[str, set[type[Any]]],
    output_dir: Path,
) -> dict[str, Path]:
    """
    For each Config subclass in the config_cls_dict, generate a file with
    the TypedDict definition for the Config subclass. The output file
    path should follow the config subclass, but it should be relative to
    the output_dir. The filename should be `{config_cls_name}_typed_dict.py`.
    E.g., `mymodule.a.b.c.Config` should be written to `output_dir/a/b/c/Config_typed_dict.py`.
    """

    mapping: dict[str, Path] = {}
    # Create typed dict files
    for module_name, config_classes in config_cls_dict.items():
        for config_cls in config_classes:
            # Get the relative path from the module name
            if module_name == output_dir.name:
                relative_path = Path()
            else:
                relative_path = Path(*module_name.split(".")[1:])

            # Create the directory if it doesn't exist
            output_path = output_dir / relative_path
            output_path.mkdir(parents=True, exist_ok=True)

            # Generate the typed dict code
            typed_dict_code = _config_cls_to_typed_dict_code(config_cls)

            # Write the typed dict code to a file
            output_file = output_path / f"{config_cls.__name__}_typed_dict.py"
            with output_file.open("w") as f:
                f.write(typed_dict_code)

            _run_ruff(output_file)

            mapping[_class_fullname(config_cls)] = output_file

    return mapping


def _generate_json_schema(
    config_cls_dict: Mapping[str, set[type[Any]]],
    output_dir: Path,
) -> dict[str, Path]:
    """
    For each Config subclass in the config_cls_dict, generate a file with
    the JSON schema definition for the Config subclass. The output
    file path should follow the config subclass, but it should be relative
    to the output_dir. The filename should be `{config_cls_name}.schema.json`.
    E.g., `mymodule.a.b.c.Config` should be written to `output_dir/a/b/c/Config.schema.json`.
    """

    mapping: dict[str, Path] = {}

    # Create JSON schema files
    for module_name, config_classes in sorted(config_cls_dict.items()):
        for config_cls in sorted(config_classes, key=lambda c: c.__name__):
            # Get the relative path from the module name
            if module_name == output_dir.name:
                relative_path = Path()
            else:
                relative_path = Path(*module_name.split(".")[1:])

            # Create the directory if it doesn't exist
            output_path = output_dir / relative_path
            output_path.mkdir(parents=True, exist_ok=True)

            # Convert the config cls to a JSON schema
            schema = config_cls.model_json_schema()
            # Sort schema properties recursively
            schema = _sort_json_schema(schema)

            # Write the JSON schema to a file
            output_file = output_path / f"{config_cls.__name__}.schema.json"
            with output_file.open("w") as f:
                f.write(json.dumps(schema, indent=4, sort_keys=True))

            mapping[_class_fullname(config_cls)] = output_file

    return mapping


def _sort_json_schema(schema: dict) -> dict:
    """
    Sort JSON schema properties recursively.
    """
    result = {}
    # Process keys in sorted order
    for key in sorted(schema.keys()):
        value = schema[key]
        if isinstance(value, dict):
            result[key] = _sort_json_schema(value)
        elif isinstance(value, list):
            result[key] = [
                _sort_json_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def _is_generated_module(module_name: str) -> bool:
    try:
        module = importlib.import_module(module_name)
        return getattr(module, "__codegen__", False)
    except ImportError:
        return False


def _find_modules(module_name: str, recursive: bool, ignore_modules: list[str]):
    # Find the module spec
    if (spec := importlib.util.find_spec(module_name)) is None:
        raise ImportError(f"Module {module_name} not found")

    modules = []
    if not any(
        _is_submodule(module_name, ignore) for ignore in ignore_modules
    ) and not _is_generated_module(module_name):
        modules.append(module_name)
    else:
        logging.debug(f"Ignoring module {module_name}")

    if not recursive:
        return modules

    # Find the directory containing the module
    if spec.origin is None:
        return modules

    module_dir = Path(spec.origin).parent

    def add_module(path: Path, base_module: str):
        relative_path = path.relative_to(module_dir)
        if path.name == "__init__.py":
            submodule_name = ".".join(relative_path.parent.parts)
        else:
            submodule_name = ".".join(relative_path.with_suffix("").parts)

        full_module_name = (
            f"{base_module}.{submodule_name}" if submodule_name else base_module
        )

        if not any(full_module_name.startswith(ignore) for ignore in ignore_modules):
            modules.append(full_module_name)
        else:
            logging.debug(f"Ignoring module {full_module_name}")

    # Walk through the directory
    for file_path in module_dir.rglob("*.py"):
        if file_path.name == "__init__.py":
            # This is a package, add it
            add_module(file_path, module_name)
        elif not any(parent.name == "__init__.py" for parent in file_path.parents):
            # This is a standalone Python file not within a package, add it
            add_module(file_path, module_name)

    return modules


def _unwrap_type_alias(obj: Any):
    # If this is a `TypeAliasType`, resolve the actual type.
    if isinstance(obj, TypeAliasType):
        obj = obj.__value__

    return obj


def _should_export(obj: Any, ignore_abc: bool, export_generics: bool, root_module: str):
    # If this is a `TypeAliasType`, resolve the actual type.
    obj = _unwrap_type_alias(obj)

    # Check if the object's module starts with the root module
    try:
        obj_module = getattr(obj, "__module__", None)
        if obj_module and not _is_submodule(obj_module, root_module):
            return False
    except (AttributeError, TypeError):
        pass

    # First check for Export() metadata in the Annotated[] metadata
    def _has_export_metadata(obj: Any):
        try:
            if (
                metadata := getattr(obj, "__metadata__", None)
            ) is None or not isinstance(metadata, Iterable):
                return False

            if any(isinstance(m, Export) for m in metadata):
                return True
        except TypeError:
            return False

    if _has_export_metadata(obj):
        return True

    # Otherwise, resolve the types. If the type is an nshconfig.Config or
    #   a union of nshconfig.Config types, export it.
    def _is_config_type(
        obj: Any,
        ignore_abc: bool,
        export_generics: bool,
    ):
        try:
            # If this is a `TypeAliasType`, resolve the actual type.
            obj = _unwrap_type_alias(obj)

            # If this is a Config subclass, check its module and export it if appropriate
            if (
                inspect.isclass(obj)
                and issubclass(obj, Config)
                and (not ignore_abc or not inspect.isabstract(obj))
            ):
                # Check if the class's module starts with root_module
                return _is_submodule(obj.__module__, root_module)

            # If this an Annotated type, we need to check the inner type.
            if typing_inspect.get_origin(obj) is Annotated:
                return _is_config_type(
                    typing_inspect.get_args(obj)[0], ignore_abc, export_generics
                )

            # If this is a Union of Config types, recursively check each type, and
            #   if all types are Config types, export it.
            if typing_inspect.is_union_type(obj):
                return all(
                    _is_config_type(t, ignore_abc, export_generics)
                    for t in typing_inspect.get_args(obj)
                )

            # If this is a generic type, we have two cases:
            #  - TypeVar("T", bound=ConfigOrConfigSubclass): export it
            #  - TypeVar("T", ConfigOrConfigSubclass1, [ConfigOrConfigSubclass2, ...]): export it
            if export_generics and typing_inspect.is_typevar(obj):
                if (bound := typing_inspect.get_bound(obj)) is not None:
                    return _is_config_type(bound, ignore_abc, export_generics)
                if constraints := typing_inspect.get_constraints(obj):
                    return all(
                        _is_config_type(c, ignore_abc, export_generics)
                        for c in constraints
                    )

            # Otherwise, we don't export it.
            return False

        except TypeError:
            return False

    if _is_config_type(obj, ignore_abc, export_generics):
        return True

    return False


def _module_configs(
    module_name: str, ignore_abc: bool, export_generics: bool, root_module: str
):
    # Import the module
    module = importlib.import_module(module_name)

    # Find all subclasses of Config
    for _, cls in inspect.getmembers(module, inspect.isclass):
        if _should_export(cls, ignore_abc, export_generics, root_module):
            yield cls


def _alias_configs(
    module_name: str, ignore_abc: bool, export_generics: bool, root_module: str
):
    # Import the module
    module = importlib.import_module(module_name)

    # Also export type aliases that have the Export()
    # in their Annotated[] metadata.
    for name, obj in inspect.getmembers(module):
        if _should_export(obj, ignore_abc, export_generics, root_module):
            yield name, obj


def _create_export_files(
    output_dir: Path,
    base_module: str,
    config_cls_dict: dict,
    alias_dict: dict,
    generate_typed_dicts: bool,
    generate_instance_or_dict: bool,
    *,
    generate_all: bool = True,
):
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create the root export file
    _create_export_file(
        output_dir / "__init__.py",
        base_module,
        config_cls_dict,
        alias_dict,
        generate_typed_dicts,
        generate_instance_or_dict,
        root=output_dir,
        root_module=base_module,
        generate_all=generate_all,
    )

    # Create hierarchical export files
    all_modules = set(config_cls_dict.keys()) | set(alias_dict.keys())
    # Iterate through all modules found in config_cls_dict and alias_dict
    for module_name in all_modules:
        # Skip base module since we already created its export file
        if module_name == base_module:
            continue

        # Get the path relative to base module and split into components
        relative_path = module_name[len(base_module) + 1 :].split(".")
        current_path = output_dir
        current_module = base_module

        # Create directories and __init__.py files for each component
        for part in relative_path:
            # Build up the directory path and module name
            current_path = current_path / part
            current_path.mkdir(exist_ok=True)
            current_module = f"{current_module}.{part}"

            # Create __init__.py if it doesn't exist
            init_file = current_path / "__init__.py"
            if not init_file.exists():
                _create_export_file(
                    init_file,
                    current_module,
                    config_cls_dict,
                    alias_dict,
                    generate_typed_dicts,
                    generate_instance_or_dict,
                    root=output_dir,
                    root_module=base_module,
                    generate_all=generate_all,
                )

    # Format files using ruff if available
    _run_ruff(output_dir)


def _instance_or_dict_name_for_config_cls_name(class_name: str) -> str:
    """Returns the name of the InstanceOrDict type for the given Config subclass."""
    return f"{class_name}InstanceOrDict"


def _create_instance_or_dict_code(class_name: str) -> str:
    """Generates the InstanceOrDict union type code."""
    typed_dict_name = _typed_dict_name_for_config_cls_name(class_name)
    instance_or_dict_name = _instance_or_dict_name_for_config_cls_name(class_name)
    return f"""\
{instance_or_dict_name} = {class_name} | {typed_dict_name}
"""


def _create_export_file(
    file_path: Path,
    module_name: str,
    config_cls_dict: dict,
    alias_dict: dict,
    generate_typed_dicts: bool,
    generate_instance_or_dict: bool,
    ignore_autoformat: bool = False,
    *,
    root: Path,
    root_module: str,
    generate_all: bool = True,
):
    export_lines = []
    class_names = {}  # To keep track of class names and their modules
    alias_names = {}  # To keep track of alias names and their modules
    submodule_exports = set()
    all_exports = set() if generate_all else None  # Only track if generating __all__

    def _add_line(line: str):
        export_lines.append(line)

    # Add comments to ignore auto-formatting
    if ignore_autoformat:
        _add_line("# fmt: off")
        _add_line("# ruff: noqa")
        _add_line("# type: ignore")
        _add_line("")

    # Add codegen marker
    _add_line(f"{CODEGEN_MARKER}")
    _add_line("")

    # Collect Config classes, aliases, and submodules
    for module, config_classes in sorted(config_cls_dict.items()):
        if module == module_name or _is_submodule(module, module_name):
            for cls in sorted(config_classes, key=lambda c: c.__name__):
                class_name = cls.__name__
                if class_name not in class_names or len(module) < len(
                    class_names[class_name]
                ):
                    class_names[class_name] = module
                    if all_exports is not None:  # Only add if generating __all__
                        all_exports.add(class_name)

            if module != module_name:
                submodule = module[len(module_name) + 1 :].split(".")[0]
                submodule_exports.add(submodule)
                if all_exports is not None:  # Only add if generating __all__
                    all_exports.add(submodule)

    for module, aliases in sorted(alias_dict.items()):
        if module == module_name or _is_submodule(module, module_name):
            for name in sorted(aliases.keys()):
                if name not in alias_names or len(module) < len(alias_names[name]):
                    alias_names[name] = module
                    if all_exports is not None:  # Only add if generating __all__
                        all_exports.add(name)

            if module != module_name:
                submodule = module[len(module_name) + 1 :].split(".")[0]
                submodule_exports.add(submodule)
                if all_exports is not None:  # Only add if generating __all__
                    all_exports.add(submodule)

    # Direct imports of configs and aliases
    for class_name, module in sorted(class_names.items()):
        _add_line(f"from {module} import {class_name} as {class_name}")

    _add_line("")

    for alias_name, module in sorted(alias_names.items()):
        _add_line(f"from {module} import {alias_name} as {alias_name}")

    _add_line("")

    # Generate TypedDict imports
    if generate_typed_dicts:
        for class_name, module in sorted(class_names.items()):
            export_module = _to_export_module(
                module,
                root_module,
                root,
                file_path,
                f"{class_name}_typed_dict",
            )

            for export in _typed_dict_file_exports(class_name):
                _add_line(f"from .{export_module} import {export} as {export}")
                if all_exports is not None:  # Only add if generating __all__
                    all_exports.add(export)

            # Add InstanceOrDict type if both flags are enabled
            if generate_instance_or_dict:
                instance_or_dict_name = _instance_or_dict_name_for_config_cls_name(
                    class_name
                )
                _add_line(_create_instance_or_dict_code(class_name))
                if all_exports is not None:
                    all_exports.add(instance_or_dict_name)

        if class_names:
            _add_line("")

    # Add submodule exports
    for submodule in sorted(submodule_exports):
        _add_line(f"from . import {submodule} as {submodule}")

    # Add __all__ declaration after all imports only if enabled
    if all_exports:  # This check now handles both None and empty set cases
        _add_line("")
        _add_line("__all__ = [")
        for export in sorted(all_exports):
            _add_line(f'    "{export}",')
        _add_line("]")

    # Write export lines
    with file_path.open("w") as f:
        for line in export_lines:
            f.write(line + "\n")


def _to_export_module(
    module: str,
    root_module: str,
    root: Path,
    file_path: Path,
    leaf: str,
) -> str:
    # First, get the relative path of module from root_module
    root_path = Path(*root_module.split("."))
    module_path = Path(*module.split("."))
    relative_path = module_path.relative_to(root_path)

    # Then, join the relative path with the root directory
    export_module = root / relative_path

    # Finally, get that path relative to the file path
    export_module = export_module.relative_to(file_path.parent)
    export_module = export_module / leaf

    # Convert the path to a module name
    export_module = export_module.with_suffix("").as_posix().replace("/", ".")
    return export_module


def _is_submodule(module: str, parent_module: str) -> bool:
    """Check if a module is a submodule of another module.

    Args:
        module: The module to check
        parent_module: The potential parent module

    Returns:
        True if module is a submodule of parent_module, False otherwise

    Example:
        >>> _is_submodule("mymodule.submodule", "mymodule")
        True
        >>> _is_submodule("mymodule", "mymodule")
        True
        >>> _is_submodule("mymodulea.submodule", "mymodule")
        False
    """
    if module == parent_module:
        return True

    # Split both into parts
    module_parts = module.split(".")
    parent_parts = parent_module.split(".")

    # Parent must be shorter
    if len(parent_parts) > len(module_parts):
        return False

    # Check that all parent parts match exactly
    return all(m == p for m, p in zip(module_parts, parent_parts))
