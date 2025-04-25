from __future__ import annotations

import logging
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic.annotated_handlers import GetCoreSchemaHandler
from pydantic_core import CoreSchema, PydanticCustomError

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


@dataclass(slots=True, frozen=True)
class AllowMissing:
    def __get_pydantic_core_schema__(
        self,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        return handler.generate_schema(typing.Union[source_type, MissingValue])


def validate_no_missing(model: BaseModel):
    for name, field in type(model).__pydantic_fields__.items():
        # If the field doesn't have the `AllowMissing` annotation, ignore it.
        #   (i.e., just let Pydantic do its thing).
        if not any(isinstance(m, AllowMissing) for m in field.metadata):
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
