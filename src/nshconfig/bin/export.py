import argparse
import importlib
import importlib.util
import inspect
import logging
import pkgutil
from collections.abc import Iterable
from pathlib import Path

from nshconfig import Config
from nshconfig._export import Export


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
        help="The output file to write the configurations to, defaults to stdout",
        required=False,
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
    args = parser.parse_args()

    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level)
    logging.debug(f"Arguments: {args}")

    # Find all modules to export
    modules: list[str] = _find_modules(args.module, args.recursive, args.ignore_module)

    # For each module, import it, find all subclasses of Config and export them
    config_cls_set = set[type[Config]]()
    for module_name in modules:
        logging.debug(f"Exporting configurations from {module_name}")
        for config_cls in _module_configs(module_name):
            logging.debug(f"Exporting {config_cls}")
            config_cls_set.add(config_cls)

    # Write to file
    export_lines: list[str] = []
    for config_cls in config_cls_set:
        export_lines.append(
            f"from {config_cls.__module__} import {config_cls.__name__} as {config_cls.__name__}"
        )

    # Same with type aliases
    alises: dict[str, tuple[str, object]] = {}
    for module_name in modules:
        for name, obj in _alias_configs(module_name):
            # If the alias is already defined, replace it if
            # the new alias is longer.
            if (existing := alises.get(name)) is None:
                alises[name] = (module_name, obj)
                continue

            existing_module, existing_obj = existing
            # Throw an error if the objects are different
            if existing_obj != obj:
                raise ValueError(
                    f"Type alias {name} is defined in both {existing_module} and {module_name}. "
                    "However, the objects are different. "
                    f"Existing: {existing_obj}, New: {obj}"
                )

            # Replace the alias if the new module is longer
            if len(module_name) > len(existing_module):
                alises[name] = (module_name, obj)

    for name, (module_name, obj) in alises.items():
        export_lines.append(f"from {module_name} import {name} as {name}")

    # Sort the export lines
    export_lines = sorted(export_lines)

    # Write the export lines to the output file
    if args.output:
        with open(args.output, "w") as f:
            for line in export_lines:
                f.write(line + "\n")
    else:
        for line in export_lines:
            print(line)

    # If we have the 'ruff' package installed, we can use it to format
    # the output file.
    if args.output:
        try:
            import subprocess

            import ruff

            ruff  # type: ignore # to prevent unused import warning

            if args.output:
                subprocess.run(
                    ["ruff", "format", str(args.output.absolute())],
                    check=True,
                )

        except ImportError:
            pass


def _find_modules(module_name: str, recursive: bool, ignore_modules: list[str]):
    # Find the module spec
    if (spec := importlib.util.find_spec(module_name)) is None:
        raise ImportError(f"Module {module_name} not found")

    modules = []
    if not any(module_name.startswith(ignore) for ignore in ignore_modules):
        modules.append(module_name)
    else:
        logging.debug(f"Ignoring module {module_name}")

    if not recursive:
        return modules

    # Find the directory containing the module
    if spec.origin is None:
        return modules

    module_dir = Path(spec.origin).parent

    # Walk through the directory and find all Python files
    for file_path in module_dir.rglob("*.py"):
        if file_path.name != "__init__.py":
            # Convert file path to module name
            relative_path = file_path.relative_to(module_dir)
            submodule_name = ".".join(relative_path.with_suffix("").parts)
            full_module_name = f"{module_name}.{submodule_name}"

            if not any(
                full_module_name.startswith(ignore) for ignore in ignore_modules
            ):
                modules.append(full_module_name)
            else:
                logging.debug(f"Ignoring module {full_module_name}")

    return modules


def _module_configs(module_name: str):
    # Import the module
    module = importlib.import_module(module_name)

    # Find all subclasses of Config
    for _, cls in inspect.getmembers(module, inspect.isclass):
        try:
            # Export subclasses of Config
            if issubclass(cls, Config):
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


if __name__ == "__main__":
    main()
