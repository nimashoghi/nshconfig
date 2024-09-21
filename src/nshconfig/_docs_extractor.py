"""Utilities related to attribute docstring extraction."""

import ast
import inspect
import textwrap
from typing import Any

from pydantic.fields import FieldInfo


class DocstringVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        super().__init__()

        self.target: str | None = None
        self.attrs: dict[str, str] = {}
        self.previous_node_type: type[ast.AST] | None = None

    def visit(self, node: ast.AST) -> Any:
        node_result = super().visit(node)
        self.previous_node_type = type(node)
        return node_result

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        if isinstance(node.target, ast.Name):
            self.target = node.target.id

    def visit_Expr(self, node: ast.Expr) -> Any:
        if isinstance(node.value, ast.Str) and self.previous_node_type is ast.AnnAssign:
            docstring = inspect.cleandoc(node.value.s)
            if self.target:
                self.attrs[self.target] = docstring
            self.target = None


def extract_docstrings_from_cls(cls: type[Any]) -> dict[str, str]:
    """Map model attributes and their corresponding docstring.

    Args:
        cls: The class of the Pydantic model to inspect.

    Returns:
        A mapping containing attribute names and their corresponding docstring.
    """
    try:
        source = inspect.getsource(cls)
    except OSError:
        # Source can't be parsed (maybe because running in an interactive terminal),
        # we don't want to error here.
        return {}

    # Required for nested class definitions, e.g. in a function block
    dedent_source = textwrap.dedent(source)

    visitor = DocstringVisitor()
    visitor.visit(ast.parse(dedent_source))
    return visitor.attrs


def update_fields_from_docstrings(
    fields: dict[str, FieldInfo],
    fields_docs: dict[str, str],
):
    for ann_name, field_info in fields.items():
        field_doc = fields_docs.get(ann_name)
        if field_doc is None:
            continue

        if not field_info.description:
            field_info.description = field_doc
        else:
            field_info.description = f"{field_doc}\n\n{field_info.description}"
