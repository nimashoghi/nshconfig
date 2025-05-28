"""Credits to: https://github.com/henriquegemignani/jsonschema-to-typeddict"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from typing_extensions import override


@dataclass
class CodeImport:
    module: str
    name: str
    alias: str | None = None

    @override
    def __str__(self) -> str:
        if self.alias:
            return f"from {self.module} import {self.name} as {self.alias}"
        return f"from {self.module} import {self.name}"


@dataclass
class CodeResult:
    block: str
    inline: str
    imports: list[CodeImport] = field(default_factory=lambda: [])

    @classmethod
    def empty(cls) -> CodeResult:
        return cls("", "", [])


def _cap(word: str) -> str:
    if not word:
        return word
    return word[0].upper() + word[1:]


def _snake_case_to_pascal_case(snake: str) -> str:
    return "".join(_cap(word) for word in snake.split("_"))


def _get_description(entry_value: dict) -> str:
    """Extract and format description from schema entry"""
    description = entry_value.get("description", "")
    if description:
        # Ensure description starts with capital letter and ends with period
        description = description[0].upper() + description[1:]
        if not description.endswith("."):
            description += "."
    return description


def _convert_tuple_entry(entry_name: str, entry_value: dict) -> CodeResult:
    """Handles arrays that have prefixItems (tuple types)"""
    items = entry_value["prefixItems"]
    inner_results = []
    block_result = ""

    result = CodeResult.empty()
    for i, item in enumerate(items):
        inner = _convert_schema_entry(f"{entry_name}__item{i}", item, result)
        if inner.block:
            result.block += f"{inner.block}\n"
        inner_results.append(inner.inline)

    result.inline = f"tuple[{', '.join(inner_results)}]"
    return result


def _convert_array_entry(entry_name: str, entry_value: dict) -> CodeResult:
    """Handles that are type = array"""
    if "prefixItems" in entry_value:
        return _convert_tuple_entry(entry_name, entry_value)

    result = CodeResult.empty()
    inner = _convert_schema_entry(f"{entry_name}__item", entry_value["items"], result)
    result.inline = f"list[{inner.inline}]"
    return result


def _merge_conditional_into(entry_value: dict, alternative: dict) -> None:
    for prop_name, prop_value in alternative.get("properties", {}).items():
        if prop_name not in entry_value:
            if "properties" not in entry_value:
                entry_value["properties"] = {}
            entry_value["properties"][prop_name] = prop_value


def _merge_conditionals_into_main(entry_value: dict) -> None:
    for alternative in entry_value.pop("oneOf", []) + [
        entry_value.pop("then", None),
        entry_value.pop("else", None),
    ]:
        if alternative is not None:
            _merge_conditionals_into_main(alternative)
            _merge_conditional_into(entry_value, alternative)


def _convert_true_dict(entry_name: str, entry_value: dict) -> CodeResult:
    """Handles objects with no properties, and patternProperties or additionalProperties as schemas"""
    additionalProperties = entry_value.get("additionalProperties", True)
    patternProperties = entry_value.get("patternProperties", {})
    propertyNames = entry_value.get("propertyNames", {})

    key_types = []
    if propertyNames:
        key_types.append(propertyNames)
    if patternProperties or not key_types:
        key_types.append({"type": "string"})

    val_types = [alternative for _, alternative in patternProperties]
    if additionalProperties and isinstance(additionalProperties, dict):
        val_types.append(additionalProperties)

    key = _convert_union(f"{entry_name}__key", key_types)
    val = _convert_union(f"{entry_name}", val_types)

    block = f"{key.block}{val.block}"
    inline = f"dict[{key.inline}, {val.inline}]"
    return CodeResult(block, inline)


def _indent(text: str) -> str:
    tab = "    "
    return "\n".join(f"{tab}{line}" for line in text.split("\n"))


def _convert_object_entry(entry_name: str, entry_value: dict) -> CodeResult:
    """Handles that are type = object"""
    result = CodeResult.empty()

    _merge_conditionals_into_main(entry_value)

    properties = entry_value.get("properties", {})
    additionalProperties = entry_value.get("additionalProperties", True)
    patternProperties = entry_value.get("patternProperties", {})

    if not properties and (patternProperties or isinstance(additionalProperties, dict)):
        return _convert_true_dict(entry_name, entry_value)

    typed_dict = []
    if not additionalProperties:
        typed_dict.append("@typ.final")

    required_fields = entry_value.get("required", [])
    properties.pop(
        "$schema", None
    )  # ignore the schema field, as it's invalid syntax and not interesting

    type_name = _snake_case_to_pascal_case(entry_name)
    result.inline = type_name
    is_total = len(required_fields) > len(properties) - len(required_fields)

    # Add class description if present
    description = _get_description(entry_value)

    if is_total:
        typed_dict.append(f"class {type_name}(typ.TypedDict):")
    else:
        typed_dict.append(f"class {type_name}(typ.TypedDict, total=False):")

    if description:
        typed_dict.append(_indent(f'"""{description}"""'))

    if not properties:  # Add pass if no properties
        typed_dict.append(_indent("pass"))
    else:
        for prop_name, prop_value in properties.items():
            inner = _convert_schema_entry(
                f"{entry_name}__{prop_name}",
                prop_value,
                result,
            )
            if inner.block:
                result.block += f"{inner.block}\n"

            if (prop_name in required_fields) == is_total:
                prop_inline = inner.inline
            elif prop_name in required_fields:
                prop_inline = f"typ.Required[{inner.inline}]"
            else:
                prop_inline = f"typ.NotRequired[{inner.inline}]"

            # Add property description as docstring
            prop_description = _get_description(prop_value)
            typed_dict.append(_indent(f"{prop_name}: {prop_inline}"))
            if prop_description:
                typed_dict.append(_indent(f'"""{prop_description}"""'))
            typed_dict.append("")

    merged_dict = "\n".join(typed_dict)
    result.block += f"{merged_dict}\n"
    return result


def _convert_x_python(entry_name: str, entry_value: dict) -> CodeResult:
    result = CodeResult.empty()
    python_cls = entry_value["x-python-cls"]
    assert isinstance(python_cls, str), (
        f"Invalid entry at {entry_name}: x-python-cls must be a string ({python_cls})"
    )
    module, name = python_cls.rsplit(".", 1)

    imp = CodeImport(module, name)
    result.imports.append(imp)
    result.inline = python_cls
    return result


def _convert_x_python_union(entry_name: str, entry_value: dict) -> CodeResult:
    alternatives = entry_value["x-python-any-of"]
    assert len(alternatives) == 2

    result = CodeResult.empty()

    model, python = alternatives
    assert model["type"] != "x-python"
    model_result = _convert_schema_entry(f"{entry_name}TypedDict", model, result)
    result.block += model_result.block

    assert python["type"] == "x-python"
    python_result = _convert_schema_entry(entry_name, python, result)

    if entry_value.get("x-python-any-of-disable-typed-dict-generation", False):
        return python_result

    result.block += python_result.block

    result.inline = f"{model_result.inline} | {python_result.inline}"
    return result


def _convert_union(entry_name: str, alternatives: list) -> CodeResult:
    result = CodeResult.empty()
    nested_inlines = []

    single = len(alternatives) == 1
    for i, alternative in enumerate(alternatives):
        inner_name = entry_name if single else f"{entry_name}__any{i}"
        inner = _convert_schema_entry(inner_name, alternative, result)
        if inner.block:
            result.block += f"{inner.block}\n"
        nested_inlines.append(inner.inline)

    # Remove duplicates from nested_inlines
    nested_inlines = list(dict.fromkeys(nested_inlines))
    if len(nested_inlines) == 1:
        result.inline = nested_inlines[0]
    result.inline = " | ".join(nested_inlines)
    return result


def _convert_any_of(entry_name: str, entry_value: dict) -> CodeResult:
    """Handle entries that have an anyOf key"""
    return _convert_union(entry_name, entry_value["anyOf"])


def _convert_one_of(entry_name: str, entry_value: dict) -> CodeResult:
    """Handle entries that have a oneOf key"""
    return _convert_union(entry_name, entry_value["oneOf"])


def _convert_enum(entry_name: str, enum_values: list) -> CodeResult:
    """Handle entries that have an enum key"""
    literals = []
    for value in enum_values:
        if isinstance(value, str):
            if '"' in value:
                literals.append(f"typ.Literal['{value}']")
            else:
                literals.append(f'typ.Literal["{value}"]')
        elif isinstance(value, bool):
            literals.append(
                f"typ.Literal[{str(value).capitalize()}]"
            )  # Changed from lower() to capitalize()
        elif isinstance(value, (int, float)):
            literals.append(f"typ.Literal[{value}]")
        elif value is None:
            literals.append("typ.Literal[None]")
        else:
            raise ValueError(f"Unsupported enum value type: {type(value)}")

    # Remove duplicates from literals
    literals = list(dict.fromkeys(literals))
    if len(literals) == 1:
        return CodeResult("", literals[0])
    if len(literals) == 0:
        return CodeResult("", "typ.Never")
    return CodeResult("", " | ".join(literals))


def _convert_schema_entry(
    entry_name: str,
    entry_value: dict,
    result: CodeResult | None,
) -> CodeResult:
    new_result = _convert_schema_entry_worker(entry_name, entry_value)
    if result is not None:
        result.imports.extend(new_result.imports)
    return new_result


def _convert_schema_entry_worker(entry_name: str, entry_value: dict) -> CodeResult:
    if "$ref" in entry_value:
        if "#/$defs/" not in entry_value["$ref"]:
            msg = f"Invalid entry at {entry_name}: only $defs are supported in $ref ({entry_value})"
            raise ValueError(msg)
        return CodeResult("", _snake_case_to_pascal_case(entry_value["$ref"][8:]))

    if "anyOf" in entry_value:
        return _convert_any_of(entry_name, entry_value)

    if "x-python-any-of" in entry_value:
        return _convert_x_python_union(entry_name, entry_value)

    if "oneOf" in entry_value:
        return _convert_one_of(entry_name, entry_value)

    if "enum" in entry_value:
        return _convert_enum(entry_name, entry_value["enum"])

    if entry_value == {}:  # empty object is Any
        return CodeResult("", "typ.Any")

    entry_type = entry_value.get("type")
    result = CodeResult.empty()
    if entry_type == "string":
        result.inline = "str"
    elif entry_type == "integer":
        result.inline = "int"
    elif entry_type == "number":
        result.inline = "float"
    elif entry_type == "boolean":
        result.inline = "bool"
    elif entry_type == "null":
        result.inline = "None"
    elif entry_type == "array":
        result = _convert_array_entry(entry_name, entry_value)
    elif entry_type == "object":
        result = _convert_object_entry(entry_name, entry_value)
    elif entry_type == "x-python":
        result = _convert_x_python(entry_name, entry_value)
    else:
        msg = f"Invalid entry at {entry_name}: unknown type {entry_type}"
        raise ValueError(msg)

    return result


def convert_schema(
    schema: Any,
    root_name: str | None = None,
    header: str | None = None,
) -> str:
    if root_name is None:
        assert (root_name := schema.get("title")) is not None, (
            "root_name must be provided if schema has no title"
        )

    result = f"""from __future__ import annotations

import typing_extensions as typ

<|ADDITIONAL_IMPORTS|>

{header or ""}
"""
    # Add schema description if present
    description = _get_description(schema)
    if description:
        result += f'\n"""{description}"""\n'

    defs = schema.get("$defs", {})

    if defs:
        result += "\n# Definitions\n"

    imports: list[str] = []

    for def_name, def_value in defs.items():
        inner = _convert_schema_entry(def_name, def_value, None)
        imports.extend(i.module for i in inner.imports)

        if inner.block:
            result += f"\n{inner.block}\n\n"

        def_pascal = _snake_case_to_pascal_case(def_name)
        if def_pascal != inner.inline:
            # Add definition description if present
            def_description = _get_description(def_value)
            if def_description:
                result += f'"""{def_description}"""\n'
            result += (
                f'{def_pascal} = typ.TypeAliasType("{def_pascal}", {inner.inline})\n'
            )

    result += "\n\n# Schema entries\n"

    root = _convert_schema_entry(root_name, schema, None)
    if root.block:
        result += root.block
    imports.extend(i.module for i in root.imports)

    if root_name != root.inline:
        # Add root description if present and not already added
        if root_name != schema.get("title"):
            root_description = _get_description(schema)
            if root_description:
                result += f'"""{root_description}"""\n'
        result += f'\n{root_name} = typ.TypeAliasType("{root_name}", {root.inline})\n'

    imports = sorted(list(dict.fromkeys(imports)))
    imports = [f"import {i}" for i in imports]
    result = result.replace("<|ADDITIONAL_IMPORTS|>", "\n".join(imports))

    return result
