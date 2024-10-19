from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import logging
import shutil
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from nshconfig import Config
from nshconfig._export import Export

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
        action="store_true",
        help="Ignore Abstract Base Classes",
    )
    args = parser.parse_args()

    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level)
    logging.debug(f"Arguments: {args}")

    # Find all modules to export
    modules: list[str] = _find_modules(args.module, args.recursive, args.ignore_module)

    # For each module, import it, find all subclasses of Config and export them
    config_cls_dict = defaultdict(set)
    alias_dict = defaultdict(dict)
    for module_name in modules:
        if _is_generated_module(module_name):
            logging.debug(f"Skipping generated module {module_name}")
            continue

        logging.debug(f"Exporting configurations from {module_name}")
        for config_cls in _module_configs(module_name, args.ignore_abc):
            logging.debug(f"Exporting {config_cls}")
            config_cls_dict[module_name].add(config_cls)

        for name, obj in _alias_configs(module_name):
            alias_dict[module_name][name] = obj

    # Just remove the output directory if remove_existing is True
    if args.remove_existing and args.output.exists():
        if args.output.is_dir():
            shutil.rmtree(args.output)
        else:
            args.output.unlink()

    # Create export files
    _create_export_files(args.output, args.module, config_cls_dict, alias_dict)


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


def _module_configs(module_name: str, ignore_abc: bool):
    # Import the module
    module = importlib.import_module(module_name)

    # Find all subclasses of Config
    for _, cls in inspect.getmembers(module, inspect.isclass):
        try:
            # Export subclasses of Config
            if issubclass(cls, Config):
                if ignore_abc and inspect.isabstract(cls):
                    continue
                yield cls

        except TypeError:
            continue


def _is_export(metadata: Iterable):
    for m in metadata:
        # First check for Export() metadata.
        if isinstance(m, Export):
            return True

        # Then, we check for any Pydantic metadata.
        try:
            modulebase = m.__module__.split(".", 1)[0]
            if modulebase == "pydantic":
                return True
        except Exception:
            continue


def _alias_configs(module_name: str):
    # Import the module
    module = importlib.import_module(module_name)

    # Also export type aliases that have the Export()
    # in their Annotated[] metadata.
    for name, obj in inspect.getmembers(module):
        try:
            if (
                (metadata := getattr(obj, "__metadata__", None))
                and isinstance(metadata, Iterable)
                and _is_export(metadata)
            ):
                yield name, obj

        except TypeError:
            continue


def _create_export_files(
    output_dir: Path, base_module: str, config_cls_dict: dict, alias_dict: dict
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
    for module_name in config_cls_dict.keys():
        if module_name == base_module:
            continue
        relative_path = module_name[len(base_module) + 1 :].replace(".", "/")
        module_dir = output_dir / relative_path
        module_dir.mkdir(parents=True, exist_ok=True)
        _create_export_file(
            module_dir / "__init__.py", module_name, config_cls_dict, alias_dict
        )

    # Format files using ruff if available
    try:
        import subprocess

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
    except ImportError:
        pass


def _create_export_file(
    file_path: Path,
    module_name: str,
    config_cls_dict: dict,
    alias_dict: dict,
    ignore_autoformat: bool = False,
):
    export_lines = []
    lineset = set()
    class_names = {}  # To keep track of class names and their modules
    alias_names = {}  # To keep track of alias names and their modules

    def _add_line(line: str, no_check: bool = False):
        if no_check:
            export_lines.append(line)
            return
        if line not in lineset:
            lineset.add(line)
            export_lines.append(line)

    # Add comments to ignore auto-formatting
    if ignore_autoformat:
        _add_line("# fmt: off", no_check=True)
        _add_line("# ruff: noqa", no_check=True)
        _add_line("# type: ignore", no_check=True)
        _add_line("", no_check=True)

    # Add codegen marker
    _add_line(f"{CODEGEN_MARKER}", no_check=True)
    _add_line("", no_check=True)

    # Add imports
    _add_line("from typing import TYPE_CHECKING", no_check=True)
    _add_line("", no_check=True)

    submodule_exports = set()

    # Collect Config classes and aliases
    for module, config_classes in config_cls_dict.items():
        if module.startswith(module_name):
            for cls in config_classes:
                class_name = cls.__name__
                if class_name in class_names:
                    if len(module) < len(class_names[class_name]):
                        class_names[class_name] = module
                else:
                    class_names[class_name] = module

            if module != module_name:
                submodule = module[len(module_name) + 1 :].split(".")[0]
                submodule_exports.add(submodule)

    for module, aliases in alias_dict.items():
        if module.startswith(module_name):
            for name, obj in aliases.items():
                if name in alias_names:
                    if len(module) < len(alias_names[name]):
                        alias_names[name] = module
                else:
                    alias_names[name] = module

            if module != module_name:
                submodule = module[len(module_name) + 1 :].split(".")[0]
                submodule_exports.add(submodule)

    # Config/alias imports
    _add_line("# Config/alias imports", no_check=True)
    _add_line("", no_check=True)

    # Add type checking imports
    _add_line("if TYPE_CHECKING:", no_check=True)
    for class_name, module in class_names.items():
        _add_line(f"    from {module} import {class_name} as {class_name}")
    for alias_name, module in alias_names.items():
        _add_line(f"    from {module} import {alias_name} as {alias_name}")
    _add_line("else:", no_check=True)

    # Add dynamic import function
    _add_line("    def __getattr__(name):", no_check=True)
    _add_line("        import importlib", no_check=True)
    _add_line("        if name in globals():", no_check=True)
    _add_line("            return globals()[name]", no_check=True)

    for class_name, module in class_names.items():
        _add_line(f"        if name == '{class_name}':", no_check=True)
        _add_line(
            f"            return importlib.import_module('{module}').{class_name}",
            no_check=True,
        )

    for alias_name, module in alias_names.items():
        _add_line(f"        if name == '{alias_name}':", no_check=True)
        _add_line(
            f"            return importlib.import_module('{module}').{alias_name}",
            no_check=True,
        )

    _add_line(
        "        raise AttributeError(f\"module '{__name__}' has no attribute '{name}'\")",
        no_check=True,
    )

    # Add submodule exports
    _add_line("", no_check=True)
    _add_line("# Submodule exports", no_check=True)
    for submodule in sorted(submodule_exports):
        _add_line(f"from . import {submodule} as {submodule}")

    # Write export lines
    with file_path.open("w") as f:
        for line in export_lines:
            f.write(line + "\n")


if __name__ == "__main__":
    main()
