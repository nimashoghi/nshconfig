from __future__ import annotations

import logging
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler
from pydantic_core import CoreSchema, PydanticCustomError
from typing_extensions import TypeVar

log = logging.getLogger(__name__)


class MissingValue(BaseModel):
    model_config: ClassVar[ConfigDict] = {
        "strict": True,
        "frozen": True,
        "extra": "forbid",
    }

    NSHCONFIG___MISSING_SENTINEL: Literal["NSHCONFIG___MISSING_SENTINEL_VALUE"] = Field(
        default="NSHCONFIG___MISSING_SENTINEL_VALUE",
        title="Missing",
        # Different Pydantic versions serialize the JSON schema of this kind of
        # field differently. All versions emit {"const": "NSHCONFIG___MISSING_SENTINEL_VALUE"},
        # but some versions also emit {"type": "string"}, and some also emit
        # {"enum": ["NSHCONFIG___MISSING_SENTINEL_VALUE"]}.
        # We want to ensure that the JSON schema is always the same, so we
        # explicitly set the type and enum here, even for versions that don't
        # normally emit them.
        json_schema_extra={
            "type": "string",
            "enum": ["NSHCONFIG___MISSING_SENTINEL_VALUE"],
        },
    )


MISSING = cast(Any, MissingValue())
"""A sentinel value to indicate a missing field.
This is used to indicate that a field is missing from a model.
This is a valid value for any field that has the `AllowMissing` annotation."""


def _singleton_missing_config_new(cls, *args, **kwargs):
    """Always returns the existing MISSING singleton instance."""
    global MISSING
    log.debug(f"Intercepted {cls.__name__}() call, returning singleton.")
    return MISSING


if not TYPE_CHECKING:
    MissingValue.__new__ = classmethod(_singleton_missing_config_new)

T = TypeVar("T", infer_variance=True)

if TYPE_CHECKING:
    # If we add configurable attributes to AllowMissing, we'd probably need to stop hiding it from type checkers like this
    AllowMissing = Annotated[T, ...]
    # `AllowMissing[Sequence]` will be recognized by type checkers as `Sequence`

    _AllowMissing = object
else:

    @dataclass(frozen=True)
    class _AllowMissing:
        def __get_pydantic_core_schema__(
            self,
            source_type: Any,
            handler: GetCoreSchemaHandler,
        ) -> CoreSchema:
            return handler.generate_schema(typing.Union[source_type, MissingValue])

        def __getitem__(self, item: T) -> T:
            return cast(T, Annotated[item, self])

        def __call__(self):
            return self

    AllowMissing = _AllowMissing()


def validate_no_missing(model: BaseModel):
    for name, field in type(model).model_fields.items():
        # If the field doesn't have the `AllowMissing` annotation, ignore it.
        #   (i.e., just let Pydantic do its thing).
        if not any(isinstance(m, _AllowMissing) for m in field.metadata):
            log.debug(
                f"Skipping field '{name}' as it does not have AllowMissing annotation."
            )
            continue

        log.debug(f"Validating field '{name}' for missing value.")

        # By this point, the field **should** have some value.
        if not hasattr(model, name):
            raise PydanticCustomError(
                "field_not_set",
                'Field "{name}" is missing from the model.',
                {"name": name},
            )

        # Now, we error out if the field is missing.
        if getattr(model, name) is MISSING:
            raise PydanticCustomError(
                "field_MISSING",
                'Field "{name}" is still `MISSING`. Please provide a value for it.',
                {"name": name},
            )
