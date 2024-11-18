"""Credits to: https://github.com/henriquegemignani/jsonschema-to-typeddict"""

from __future__ import annotations

from typing import Any, NamedTuple


class CodeResult(NamedTuple):
    block: str
    inline: str


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

    for i, item in enumerate(items):
        inner = _convert_schema_entry(f"{entry_name}__item{i}", item)
        if inner.block:
            block_result += f"{inner.block}\n"
        inner_results.append(inner.inline)

    return CodeResult(block_result, f"tuple[{', '.join(inner_results)}]")


def _convert_array_entry(entry_name: str, entry_value: dict) -> CodeResult:
    """Handles that are type = array"""
    if "prefixItems" in entry_value:
        return _convert_tuple_entry(entry_name, entry_value)

    inner = _convert_schema_entry(f"{entry_name}__item", entry_value["items"])
    return CodeResult(inner.block, f"list[{inner.inline}]")


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
    block_result = ""

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
            inner = _convert_schema_entry(f"{entry_name}__{prop_name}", prop_value)
            if inner.block:
                block_result += f"{inner.block}\n"

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
    block_result += f"{merged_dict}\n"
    return CodeResult(block_result, type_name)


def _convert_union(entry_name: str, alternatives: list) -> CodeResult:
    block_result = ""
    nested_inlines = []

    single = len(alternatives) == 1
    for i, alternative in enumerate(alternatives):
        inner_name = entry_name if single else f"{entry_name}__any{i}"
        inner = _convert_schema_entry(inner_name, alternative)
        if inner.block:
            block_result += f"{inner.block}\n"
        nested_inlines.append(inner.inline)

    return CodeResult(block_result, " | ".join(nested_inlines))


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

    return CodeResult("", " | ".join(literals))


def _convert_schema_entry(entry_name: str, entry_value: dict) -> CodeResult:
    if "$ref" in entry_value:
        if "#/$defs/" not in entry_value["$ref"]:
            msg = f"Invalid entry at {entry_name}: only $defs are supported in $ref ({entry_value})"
            raise ValueError(msg)
        return CodeResult("", _snake_case_to_pascal_case(entry_value["$ref"][8:]))

    if "anyOf" in entry_value:
        return _convert_any_of(entry_name, entry_value)

    if "oneOf" in entry_value:
        return _convert_one_of(entry_name, entry_value)

    if "enum" in entry_value:
        return _convert_enum(entry_name, entry_value["enum"])

    if entry_value == {}:  # empty object is Any
        return CodeResult("", "typ.Any")

    entry_type = entry_value.get("type")
    match entry_type:
        case "string":
            inline_result = "str"

        case "integer":
            inline_result = "int"

        case "number":
            inline_result = "float"

        case "boolean":
            inline_result = "bool"

        case "null":
            inline_result = "None"

        case "array":
            return _convert_array_entry(entry_name, entry_value)

        case "object":
            return _convert_object_entry(entry_name, entry_value)

        case _:
            msg = f"Invalid entry at {entry_name}: unknown type {entry_type}"
            raise ValueError(msg)

    return CodeResult("", inline_result)


def convert_schema(
    schema: Any,
    root_name: str | None = None,
    header: str | None = None,
) -> str:
    if root_name is None:
        assert (
            root_name := schema.get("title")
        ) is not None, "root_name must be provided if schema has no title"

    result = f"""from __future__ import annotations

import typing_extensions as typ

{header or ""}
"""
    # Add schema description if present
    description = _get_description(schema)
    if description:
        result += f'\n"""{description}"""\n'

    defs = schema.get("$defs", {})

    if defs:
        result += "\n# Definitions\n"

    for def_name, def_value in defs.items():
        inner = _convert_schema_entry(def_name, def_value)

        if inner.block:
            result += f"\n{inner.block}\n\n"

        def_pascal = _snake_case_to_pascal_case(def_name)
        if def_pascal != inner.inline:
            # Add definition description if present
            def_description = _get_description(def_value)
            if def_description:
                result += f'"""{def_description}"""\n'
            result += (
                f'{def_pascal} = typ.TypeAliasType("{def_pascal}", "{inner.inline}")\n'
            )

    result += "\n\n# Schema entries\n"

    root = _convert_schema_entry(root_name, schema)
    if root.block:
        result += root.block

    if root_name != root.inline:
        # Add root description if present and not already added
        if root_name != schema.get("title"):
            root_description = _get_description(schema)
            if root_description:
                result += f'"""{root_description}"""\n'
        result += f'\n{root_name} = typ.TypeAliasType("{root_name}", {root.inline})\n'

    return result
