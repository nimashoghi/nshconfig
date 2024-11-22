from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, cast

from pydantic import BaseModel
from pydantic_core import PydanticCustomError
from typing_extensions import TypeAliasType, TypeVar


@dataclass
class AllowMissingAnnotation:
    pass


MISSING = cast(Any, None)

T = TypeVar("T", infer_variance=True)
if TYPE_CHECKING:
    AllowMissing = TypeAliasType(
        "AllowMissing",
        Annotated[T, AllowMissingAnnotation()],
        type_params=(T,),
    )
else:
    AllowMissing = TypeAliasType(
        "AllowMissing",
        Annotated[T | None, AllowMissingAnnotation()],
        type_params=(T,),
    )


def validate_no_missing_values(model: BaseModel):
    for name, field in model.model_fields.items():
        # If the field doesn't have the `AllowMissing` annotation, ignore it.
        #   (i.e., just let Pydantic do its thing).
        allow_missing_annotation = next(
            (m for m in field.metadata if isinstance(m, AllowMissingAnnotation)),
            None,
        )
        if allow_missing_annotation is None:
            continue

        # By this point, the field **should** have some value.
        if not hasattr(model, name):
            raise PydanticCustomError(
                "field_not_set",
                'Field "{name}" is missing from the model.',
                {"name": name},
            )

        # Now, we error out if the field is missing.
        if getattr(model, name) is None:
            raise PydanticCustomError(
                "field_MISSING",
                'Field "{name}" is still `MISSING`. Please provide a value for it.',
                {"name": name},
            )
