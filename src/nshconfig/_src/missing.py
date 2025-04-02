from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, cast

import pydantic
from pydantic import BaseModel
from pydantic.annotated_handlers import GetCoreSchemaHandler
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

log = logging.getLogger(__name__)


@pydantic.dataclasses.dataclass(frozen=True, slots=True)
class _NSHCONFIG_MISSING_CLS:
    __nshconfig_missing__: Literal[True] = True


@dataclass(slots=True, frozen=True)
class AllowMissing:
    strict: bool = True
    """Use strict mode for the field."""

    def __get_pydantic_core_schema__(
        self,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        base_schema = handler(source_type)

        # JSON schema
        missing_literal_schema = core_schema.model_fields_schema(
            {
                "__nshconfig_missing__": core_schema.model_field(
                    core_schema.literal_schema([True]),
                    frozen=True,
                ),
            },
            strict=self.strict,
        )
        json_schema = core_schema.union_schema(
            [base_schema, missing_literal_schema],
            strict=self.strict,
            mode="left_to_right",
        )

        # Python schema
        python_schema = core_schema.union_schema(
            [base_schema, core_schema.is_instance_schema(_NSHCONFIG_MISSING_CLS)],
            strict=self.strict,
            mode="left_to_right",
        )

        # Final schema
        schema = core_schema.union_schema(
            [
                handler(source_type),
                cast(Any, _NSHCONFIG_MISSING_CLS).__pydantic_core_schema__,
            ],
            strict=self.strict,
            mode="left_to_right",
        )
        return schema


MISSING = cast(Any, _NSHCONFIG_MISSING_CLS())


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
