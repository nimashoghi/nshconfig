from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    try:
        from treescope import renderers, rendering_parts  # type: ignore
    except ImportError:
        pass


def render_object_constructor(
    object_type: type[Any],
    attributes: Mapping[str, Any],
    path: str | None,
    subtree_renderer: "renderers.TreescopeSubtreeRenderer",
    roundtrippable: bool = False,
    color: str | None = None,
) -> "rendering_parts.Rendering":
    try:
        from treescope import rendering_parts
    except ImportError:
        logging.exception("Failed to import treescope.rendering_parts")
        raise

    if roundtrippable:
        constructor = rendering_parts.siblings(
            rendering_parts.maybe_qualified_type_name(object_type), "("
        )
        closing_suffix = rendering_parts.text(")")
    else:
        constructor = rendering_parts.siblings(
            rendering_parts.roundtrip_condition(roundtrip=rendering_parts.text("<")),
            rendering_parts.maybe_qualified_type_name(object_type),
            "(",
        )
        closing_suffix = rendering_parts.siblings(
            ")",
            rendering_parts.roundtrip_condition(roundtrip=rendering_parts.text(">")),
        )

    # Sort attributes so attributes with default values are at the end.
    attributes_list = [
        (name, value, is_default) for name, (value, is_default) in attributes.items()
    ]
    attributes_list = sorted(attributes_list, key=lambda x: x[2])

    children = []
    for i, (name, value, is_default) in enumerate(attributes_list):
        child_path = None if path is None else f"{path}.{name}"

        if i < len(attributes) - 1:
            # Not the last child. Always show a comma, and add a space when
            # collapsed.
            comma_after = rendering_parts.siblings(
                ",",
                rendering_parts.fold_condition(collapsed=rendering_parts.text(" ")),
            )
        else:
            # Last child: only show the comma when the node is expanded.
            comma_after = rendering_parts.fold_condition(
                expanded=rendering_parts.text(",")
            )

        child_line = rendering_parts.build_full_line_with_annotations(
            rendering_parts.siblings_with_annotations(
                f"{name}=",
                subtree_renderer(value, path=child_path),
            ),
            comma_after,
        )
        if is_default:
            child_line = rendering_parts.fold_condition(
                collapsed=rendering_parts.comment_color(child_line),
                expanded=rendering_parts.comment_color_when_expanded(child_line),
            )
        children.append(child_line)

    return rendering_parts.build_foldable_tree_node_from_children(
        prefix=constructor,
        children=children,
        suffix=closing_suffix,
        path=path,
        background_color=color,
    )
