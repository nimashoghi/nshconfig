from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import logging
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any

import typing_inspect
from typing_extensions import TypeAliasType

from nshconfig import Config, Export

CODEGEN_MARKER = "__codegen__ = True"


def main():
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

    # Set up logging
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level)
    logging.debug(f"Arguments: {args}")

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
        ):
            logging.debug(f"Exporting {config_cls}")
            config_cls_dict[module_name].add(config_cls)

        for name, obj in _alias_configs(
            module_name,
            ignore_abc,
            export_generics,
        ):
            alias_dict[module_name][name] = obj

    # Just remove the output directory if remove_existing is True
    if remove_existing and output.exists():
        if output.is_dir():
            shutil.rmtree(output)
        else:
            output.unlink()

    # Create export files
    _create_export_files(
        output,
        module,
        config_cls_dict,
        alias_dict,
    )


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
        module_name.startswith(ignore) for ignore in ignore_modules
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


def _should_export(obj: Any, ignore_abc: bool, export_generics: bool):
    # If this is a `TypeAliasType`, resolve the actual type.
    obj = _unwrap_type_alias(obj)

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

            # If this is a Config subclass, export it
            if (
                inspect.isclass(obj)
                and issubclass(obj, Config)
                and (not ignore_abc or not inspect.isabstract(obj))
            ):
                return True

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


def _module_configs(module_name: str, ignore_abc: bool, export_generics: bool):
    # Import the module
    module = importlib.import_module(module_name)

    # Find all subclasses of Config
    for _, cls in inspect.getmembers(module, inspect.isclass):
        if _should_export(cls, ignore_abc, export_generics):
            yield cls


def _alias_configs(module_name: str, ignore_abc: bool, export_generics: bool):
    # Import the module
    module = importlib.import_module(module_name)

    # Also export type aliases that have the Export()
    # in their Annotated[] metadata.
    for name, obj in inspect.getmembers(module):
        if _should_export(obj, ignore_abc, export_generics):
            yield name, obj


def _create_export_files(
    output_dir: Path,
    base_module: str,
    config_cls_dict: dict,
    alias_dict: dict,
):
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create the root export file
    _create_export_file(
        output_dir / "__init__.py",
        base_module,
        config_cls_dict,
        alias_dict,
    )

    # Create hierarchical export files
    all_modules = set(config_cls_dict.keys()) | set(alias_dict.keys())
    for module_name in all_modules:
        if module_name == base_module:
            continue

        relative_path = module_name[len(base_module) + 1 :].split(".")
        current_path = output_dir
        current_module = base_module

        for part in relative_path:
            current_path = current_path / part
            current_path.mkdir(exist_ok=True)
            current_module = f"{current_module}.{part}"

            init_file = current_path / "__init__.py"
            if not init_file.exists():
                _create_export_file(
                    init_file,
                    current_module,
                    config_cls_dict,
                    alias_dict,
                )

    # Format files using ruff if available
    try:
        # First, format.
        subprocess.run(
            ["ruff", "format", str(output_dir.absolute())],
            check=True,
        )
        # Then, fix imports.
        subprocess.run(
            ["ruff", "check", "--select", "I", "--fix", str(output_dir.absolute())],
            check=True,
        )
        # Then, format again.
        subprocess.run(
            ["ruff", "format", str(output_dir.absolute())],
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _create_export_file(
    file_path: Path,
    module_name: str,
    config_cls_dict: dict,
    alias_dict: dict,
    ignore_autoformat: bool = False,
):
    export_lines = []
    class_names = {}  # To keep track of class names and their modules
    alias_names = {}  # To keep track of alias names and their modules
    submodule_exports = set()

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
        if module == module_name or module.startswith(f"{module_name}."):
            for cls in sorted(config_classes, key=lambda c: c.__name__):
                class_name = cls.__name__
                if class_name not in class_names or len(module) < len(
                    class_names[class_name]
                ):
                    class_names[class_name] = module

            if module != module_name:
                submodule = module[len(module_name) + 1 :].split(".")[0]
                submodule_exports.add(submodule)

    for module, aliases in sorted(alias_dict.items()):
        if module == module_name or module.startswith(f"{module_name}."):
            for name in sorted(aliases.keys()):
                if name not in alias_names or len(module) < len(alias_names[name]):
                    alias_names[name] = module

            if module != module_name:
                submodule = module[len(module_name) + 1 :].split(".")[0]
                submodule_exports.add(submodule)

    # Direct imports of configs and aliases
    for class_name, module in sorted(class_names.items()):
        _add_line(f"from {module} import {class_name} as {class_name}")

    if class_names:
        _add_line("")

    for alias_name, module in sorted(alias_names.items()):
        _add_line(f"from {module} import {alias_name} as {alias_name}")

    if alias_names:
        _add_line("")

    # Add submodule exports
    for submodule in sorted(submodule_exports):
        _add_line(f"from . import {submodule} as {submodule}")

    # Write export lines
    with file_path.open("w") as f:
        for line in export_lines:
            f.write(line + "\n")


if __name__ == "__main__":
    main()
