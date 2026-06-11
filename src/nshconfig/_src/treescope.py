"""Optional treescope integration: rich notebook rendering of configs and drafts.

Import-guarded: nothing here runs unless ``treescope`` is installed and rendering.
Finals render with default-valued fields dimmed; drafts render their pending state
(``[pending: instance ...]`` / ``[pending: class default ...]``) and unset fields.
"""

from typing import TYPE_CHECKING, Any

from .interp import Interp

if TYPE_CHECKING:
    from pydantic import BaseModel
    from treescope import renderers, rendering_parts


def render_config(
    obj: "BaseModel",
    path: "str | None",
    subtree_renderer: "renderers.TreescopeSubtreeRenderer",
) -> "rendering_parts.Rendering":
    from treescope import rendering_parts

    from .config import Config, is_draft

    draft = is_draft(obj)
    d = object.__getattribute__(obj, "__dict__")
    children = []
    items: list[tuple[str, Any, bool]] = []  # (name, value-or-label, dimmed)
    for name, f in type(obj).__pydantic_fields__.items():
        if name in d:
            v = d[name]
            if isinstance(v, Interp):
                items.append((name, rendering_parts.text(f"[pending: instance {v!r}]"), False))
            else:
                dimmed = (
                    name not in obj.__pydantic_fields_set__
                    if draft
                    else (not f.is_required() and not isinstance(f.default, Interp) and v == f.default)
                )
                items.append((name, v, dimmed))
        else:
            dflt = f.default
            ann = f.annotation
            if isinstance(dflt, Interp):
                items.append((name, rendering_parts.text(f"[pending: class default {dflt!r}]"), True))
            elif f.is_required():
                if isinstance(ann, type) and issubclass(ann, Config):
                    items.append((name, rendering_parts.text(f"<untouched {ann.__name__}>"), True))
                else:
                    items.append((name, rendering_parts.text("[UNSET]"), False))

    # Sort so dimmed (default-valued) fields render last, like v1.
    items.sort(key=lambda t: t[2])
    for i, (name, value, dimmed) in enumerate(items):
        child_path = None if path is None else f"{path}.{name}"
        rendered = (
            value
            if isinstance(value, rendering_parts.RenderableTreePart)
            else subtree_renderer(value, path=child_path).renderable
        )
        comma = (
            rendering_parts.siblings(
                ",", rendering_parts.fold_condition(collapsed=rendering_parts.text(" "))
            )
            if i < len(items) - 1
            else rendering_parts.fold_condition(expanded=rendering_parts.text(","))
        )
        line = rendering_parts.build_full_line_with_annotations(
            rendering_parts.siblings_with_annotations(f"{name}=", rendered), comma
        )
        if dimmed:
            line = rendering_parts.fold_condition(
                collapsed=rendering_parts.comment_color(line),
                expanded=rendering_parts.comment_color_when_expanded(line),
            )
        children.append(line)

    prefix = rendering_parts.siblings(
        rendering_parts.text("draft ") if draft else rendering_parts.text(""),
        rendering_parts.maybe_qualified_type_name(type(obj)),
        "(",
    )
    return rendering_parts.build_foldable_tree_node_from_children(
        prefix=prefix,
        children=children,
        suffix=rendering_parts.text(")"),
        path=path,
    )
