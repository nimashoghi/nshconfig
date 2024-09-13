import argparse
import importlib
import importlib.util
import inspect
import logging
import pkgutil
from pathlib import Path

from .. import Config


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
    args = parser.parse_args()

    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level)

    # Find all modules to export
    modules: list[str] = _find_modules(args.module, args.recursive)

    # For each module, import it, find all subclasses of Config and export them
    config_cls_set = set[type[Config]]()
    for module_name in modules:
        for config_cls in _module_configs(module_name):
            config_cls_set.add(config_cls)

    export_lines: list[str] = []
    for config_cls in config_cls_set:
        export_lines.append(
            f"from {config_cls.__module__} import {config_cls.__name__} as {config_cls.__name__}"
        )
    export_lines = sorted(export_lines)

    # Write the export lines to the output file
    if args.output:
        with open(args.output, "w") as f:
            for line in export_lines:
                f.write(line + "\n")
    else:
        for line in export_lines:
            print(line)


def _find_modules(module_name: str, recursive: bool):
    # Find the module spec
    if (spec := importlib.util.find_spec(module_name)) is None:
        raise ImportError(f"Module {module_name} not found")

    if spec.submodule_search_locations is None:
        return [module_name]

    # Find all submodules
    modules = [module_name]
    if recursive:
        for _, name, _ in pkgutil.walk_packages(spec.submodule_search_locations):
            submodule = f"{module_name}.{name}"
            modules.extend(_find_modules(submodule, recursive))

    return modules


def _module_configs(module_name: str):
    # Import the module
    module = importlib.import_module(module_name)

    # Find all subclasses of Config
    for x, cls in inspect.getmembers(module, inspect.isclass):
        try:
            if not issubclass(cls, Config):
                continue
        except TypeError:
            continue

        yield cls


if __name__ == "__main__":
    main()
