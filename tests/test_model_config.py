"""Pydantic policy owned by Config and by project base classes."""

import json
import subprocess
import sys
from enum import Enum
from typing import Annotated, Any, Literal, TypeVar

import pytest
from pydantic import (
    AliasChoices,
    AliasPath,
    ConfigDict,
    Discriminator,
    Field,
    InstanceOf,
    OnErrorOmit,
    PrivateAttr,
    SkipValidation,
    Tag,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from typing_extensions import TypeAliasType

import nshconfig as C


class _DocumentedConfig(C.Config):
    batch_size: int = 32
    """Number of examples processed in one optimization step."""


def test_builtin_policy_is_strict_and_forbids_extra_input() -> None:
    class Builtin(C.Config):
        count: int

    with pytest.raises(ValidationError) as strict:
        Builtin.model_validate({"count": "1"})
    assert strict.value.errors()[0]["type"] == "int_type"

    with pytest.raises(ValidationError) as extra:
        Builtin.model_validate({"count": 1, "unknown": 2})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"


def test_builtin_policy_uses_attribute_docstrings_as_descriptions() -> None:
    assert _DocumentedConfig.model_config["use_attribute_docstrings"] is True
    description = "Number of examples processed in one optimization step."
    assert _DocumentedConfig.model_fields["batch_size"].description == description
    assert (
        _DocumentedConfig.model_json_schema()["properties"]["batch_size"][
            "description"
        ]
        == description
    )


@pytest.mark.parametrize(
    "override",
    [
        {"extra": "ignore"},
        {"frozen": False},
        {"validate_default": False},
        {"revalidate_instances": "never"},
        {"validate_by_alias": False},
        {"validate_by_name": False},
        {"from_attributes": True},
    ],
)
def test_subclasses_cannot_disable_lifecycle_invariants(
    override: dict[str, Any],
) -> None:
    setting = next(iter(override))
    with pytest.raises(TypeError, match=setting):

        class Invalid(C.Config):
            model_config = ConfigDict(**override)
            value: int


def test_project_base_can_change_non_lifecycle_policies() -> None:
    class Token:
        pass

    class ProjectConfig(C.Config, strict=False, arbitrary_types_allowed=True):
        pass

    class Job(ProjectConfig):
        count: int
        token: Token

    token = Token()
    job = Job(count="2", token=token)
    assert job.count == 2
    assert job.token is token
    assert job.model_config["extra"] == "forbid"
    assert job.model_config["frozen"] is True

    class AttributeInput:
        value = 3

    class Value(C.Config):
        value: int

    assert Value.model_validate(AttributeInput(), from_attributes=True).value == 3
    assert (
        TypeAdapter(Value).validate_python(AttributeInput(), from_attributes=True).value
        == 3
    )

    ignored = Value.model_validate({"value": 1, "extra": 2}, extra="ignore")
    allowed = TypeAdapter(Value).validate_python(
        {"value": 1, "extra": 2}, extra="allow"
    )
    assert ignored.value == allowed.value == 1
    assert allowed.__pydantic_extra__ == {"extra": 2}


def test_alias_and_canonical_name_are_both_valid_input_forms() -> None:
    class Aliased(C.Config):
        value: int = Field(alias="external")

    assert Aliased(value=1).value == 1
    assert Aliased(external=2).value == 2
    assert Aliased(value=1).model_dump() == {"value": 1}


def test_global_model_config_mutator_is_not_public_api() -> None:
    assert not hasattr(C, "set_model_config_defaults")


def test_lifecycle_private_attribute_name_is_reserved() -> None:
    name = "_nshconfig_state"
    with pytest.raises(TypeError, match=f"reserved private attribute.*{name}"):
        type(
            "Invalid",
            (C.Config,),
            {
                "__annotations__": {name: str},
                name: PrivateAttr(default="user data"),
            },
        )


def test_every_field_must_validate_its_default_for_canonical_publication() -> None:
    with pytest.raises(TypeError, match="disables default validation"):

        class Invalid(C.Config):
            source: int = Field(2, validate_default=False)
            copied: int = C.interp(lambda context: context.current().source)


@pytest.mark.parametrize("wrapper", [SkipValidation, InstanceOf, OnErrorOmit])
def test_validation_bypass_and_omission_annotations_are_rejected(wrapper: Any) -> None:
    class Child(C.Config):
        value: int

    annotation = wrapper[Child]
    with pytest.raises(TypeError, match="bypass or omit Config field validation"):

        class Invalid(C.Config):
            child: annotation  # type: ignore[valid-type]


def test_lifecycle_methods_cannot_be_overridden() -> None:
    with pytest.raises(TypeError, match="reserved Config lifecycle method '__eq__'"):

        class Invalid(C.Config):
            value: int

            def __eq__(self, other: object) -> bool:
                return True

    with pytest.raises(
        TypeError, match="reserved Config lifecycle method '__setattr__'"
    ):

        class InvalidWrite(C.Config):
            value: int

            def __setattr__(self, name: str, value: Any) -> None:
                object.__getattribute__(self, "__dict__")[name] = value

    with pytest.raises(
        TypeError,
        match="reserved Config lifecycle method '__get_pydantic_core_schema__'",
    ):

        class InvalidSchema(C.Config):
            value: Any

            @classmethod
            def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
                return handler(source)

    with pytest.raises(TypeError, match="reserved Config lifecycle method '__hash__'"):

        class InvalidHash(C.Config):
            value: int

            def __hash__(self) -> int:
                return 1


def test_lifecycle_settings_are_rechecked_when_a_schema_is_rebuilt() -> None:
    class Rebuilt(C.Config):
        value: int

    Rebuilt.model_config["frozen"] = False
    with pytest.raises(TypeError, match="frozen=False"):
        Rebuilt.model_rebuild(force=True)


def test_resolved_forward_references_are_reaudited_on_rebuild() -> None:
    class Forward(C.Config):
        value: "UnsafeType"  # noqa: F821

    unsafe_type = SkipValidation[int]
    with pytest.raises(TypeError, match="SkipValidation"):
        Forward.model_rebuild(
            force=True,
            _types_namespace={"UnsafeType": unsafe_type},
        )


def test_named_aliases_cannot_hide_validation_bypass_metadata() -> None:
    unsafe = TypeAliasType("UnsafeAlias", SkipValidation[int])

    with pytest.raises(TypeError, match="SkipValidation"):

        class Invalid(C.Config):
            value: unsafe  # type: ignore[valid-type]


def test_direct_config_fields_support_normal_finals_and_factories() -> None:
    class Child(C.Config):
        value: int = 1

    class Factory(C.Config):
        child: Child = Field(default_factory=Child)

    default_child = Child()

    class Value(C.Config):
        child: Child = default_child

    assert Factory().child == Child()
    assert Value().child == Child()
    assert C.is_draft(Factory.config_draft().child)
    assert C.is_draft(Value.config_draft().child)

    with pytest.raises(TypeError, match="without an intact constructor recipe"):

        class InvalidMapping(C.Config):
            child: Child = {"value": 2}  # type: ignore[assignment]


def test_callable_discriminators_are_rejected_for_incomplete_draft_semantics() -> None:
    class First(C.Config):
        kind: Literal["first"] = "first"

    class Second(C.Config):
        kind: Literal["second"] = "second"

    def choose(value: Any) -> str | None:
        if isinstance(value, dict):
            return value.get("kind")
        return getattr(value, "kind", None)

    branch = Annotated[
        Annotated[First, Tag("first")] | Annotated[Second, Tag("second")],
        Discriminator(choose),
    ]
    with pytest.raises(TypeError, match="callable union discriminator"):

        class Invalid(C.Config):
            value: branch  # type: ignore[valid-type]

    NamedBranch = TypeAliasType("NamedBranch", branch)
    with pytest.raises(TypeError, match="callable union discriminator"):

        class InvalidNamed(C.Config):
            value: NamedBranch

    branch_type = TypeVar("branch_type")
    GenericBranch = TypeAliasType(
        "GenericBranch",
        Annotated[branch_type, Discriminator(choose)],
        type_params=(branch_type,),
    )
    tagged_union = Annotated[First, Tag("first")] | Annotated[Second, Tag("second")]
    with pytest.raises(TypeError, match="callable union discriminator"):

        class InvalidParameterized(C.Config):
            value: GenericBranch[tagged_union]


def test_runtime_validation_overrides_follow_pydantic_while_fields_stay_frozen() -> (
    None
):
    class Value(C.Config):
        number: int

    final = Value(number=1)
    Value.model_config["frozen"] = False
    with pytest.raises(ValidationError) as frozen:
        final.number = 2
    assert frozen.value.errors()[0]["type"] == "frozen_instance"

    Value.model_config["extra"] = "allow"
    allowed = Value.model_validate({"number": 1, "surprise": 2}, extra="allow")
    assert allowed.__pydantic_extra__ == {"surprise": 2}


def test_model_hooks_are_trusted_pydantic_code() -> None:
    class Undeclared(C.Config):
        value: int = 1

        @model_validator(mode="after")
        def _inject(self) -> "Undeclared":
            object.__setattr__(self, "cache", 2)
            return self

    undeclared = Undeclared()
    assert undeclared.cache == 2

    class Extra(C.Config):
        value: int = 1

        @model_validator(mode="after")
        def _inject(self) -> "Extra":
            object.__setattr__(self, "__pydantic_extra__", {"surprise": 2})
            return self

    extra = Extra()
    assert extra.__pydantic_extra__ == {"surprise": 2}

    class Metadata(C.Config):
        value: int = 1

        @model_validator(mode="after")
        def _corrupt(self) -> "Metadata":
            object.__setattr__(self, "__pydantic_fields_set__", {"unknown"})
            return self

    metadata = Metadata()
    assert metadata.__pydantic_fields_set__ == {"unknown"}


def test_model_hooks_may_mutate_opaque_values() -> None:
    class Choice(Enum):
        item = []

    class Mutating(C.Config):
        choice: Choice = Choice.item

        @model_validator(mode="after")
        def _mutate_enum(self) -> "Mutating":
            self.choice.value.append(1)
            return self

    try:
        final = Mutating()
        assert final.choice.value == [1]
    finally:
        Choice.item.value.clear()


def test_model_construct_is_rejected_for_config_classes() -> None:
    class Value(C.Config):
        number: int

    with pytest.raises(TypeError, match=r"model_construct\(\) is unsafe"):
        Value.model_construct(number=1)


def test_recursive_and_discriminated_json_schemas_use_stable_direct_definitions() -> (
    None
):
    class Node(C.Config):
        value: int
        child: "Node | None" = None

    class Cat(C.Config):
        kind: Literal["cat"] = "cat"

    class Dog(C.Config):
        kind: Literal["dog"] = "dog"

    class Zoo(C.Config):
        pet: Cat | Dog = Field(discriminator="kind")

    node_schema = Node.model_json_schema()
    zoo_schema = Zoo.model_json_schema()

    assert set(node_schema["$defs"]) == {"Node"}
    assert node_schema["$defs"]["Node"]["properties"]["child"]["anyOf"][0] == {
        "$ref": "#/$defs/Node"
    }
    assert set(zoo_schema["$defs"]) == {"Cat", "Dog"}
    assert zoo_schema["properties"]["pet"]["discriminator"]["mapping"] == {
        "cat": "#/$defs/Cat",
        "dog": "#/$defs/Dog",
    }

    script = """
import json
import nshconfig as C

class Node(C.Config):
    value: int
    child: "Node | None" = None

print(json.dumps(Node.model_json_schema(), sort_keys=True, separators=(",", ":")))
"""
    outputs = [
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for _ in range(2)
    ]
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0]) == node_schema


def test_singleton_and_multi_value_literals_are_interpolation_targets() -> None:
    class Device(C.Config):
        source: Literal["cpu", "cuda"]
        copied: Literal["cpu", "cuda"] = C.interp(
            lambda context: context.current().source
        )
        singleton: Literal["only"] = C.interp(lambda context: "only")

    direct = Device(source="cuda")
    assert (direct.copied, direct.singleton) == ("cuda", "only")
    assert direct.__pydantic_fields_set__ == {"source"}

    work = Device.config_draft()
    work.source = "cpu"
    work.copied = C.interp(lambda context: context.current().source)
    work.singleton = C.interp(lambda context: "only")
    final = work.config_finalize()
    assert (final.copied, final.singleton) == ("cpu", "only")

    with pytest.raises(ValidationError) as caught:
        Device(source="cpu", copied=C.interp(lambda context: "tpu"))
    assert caught.value.errors()[0]["type"] == "literal_error"


def test_literal_interpolation_preserves_alias_and_default_factory_semantics() -> None:
    calls: list[tuple[str, Any]] = []

    def derive(data: dict[str, Any]) -> Any:
        calls.append(("factory", dict(data)))
        return C.interp(lambda context: context.current().source)

    class Device(C.Config):
        source: Literal["cpu", "cuda"]
        copied: Literal["cpu", "cuda"] = Field(
            default_factory=derive,
            validation_alias=AliasChoices("wire", AliasPath("payload", "copied")),
        )

        @field_validator("copied")
        @classmethod
        def observe_target(
            cls, value: Literal["cpu", "cuda"]
        ) -> Literal["cpu", "cuda"]:
            calls.append(("validator", value))
            return value

    defaulted = Device(source="cuda")
    assert defaulted.copied == "cuda"
    assert defaulted.__pydantic_fields_set__ == {"source"}
    assert calls == [("factory", {"source": "cuda"}), ("validator", "cuda")]

    calls.clear()
    explicit = Device.model_validate(
        {
            "source": "cpu",
            "payload": {"copied": C.interp(lambda context: context.current().source)},
        }
    )
    assert explicit.copied == "cpu"
    assert calls == [("validator", "cpu")]

    class CustomInput(dict[str, Any]):
        pass

    calls.clear()
    custom = Device.model_validate(
        CustomInput(
            source="cuda",
            wire=C.interp(lambda context: context.current().source),
        )
    )
    assert custom.copied == "cuda"
    assert calls == [("validator", "cuda")]


def test_literal_schema_remains_transparent_to_multi_tag_discriminators() -> None:
    class Cat(C.Config):
        kind: Literal["cat", "feline"] = "cat"

    class Dog(C.Config):
        kind: Literal["dog"] = "dog"

    Pet = Annotated[Cat | Dog, Field(discriminator="kind")]

    class Envelope(C.Config):
        pet: Pet

    assert type(Envelope(pet={"kind": "feline"}).pet) is Cat
    assert type(Envelope(pet={"kind": "dog"}).pet) is Dog
    assert Envelope.model_json_schema()["properties"]["pet"]["discriminator"] == {
        "mapping": {
            "cat": "#/$defs/Cat",
            "dog": "#/$defs/Dog",
            "feline": "#/$defs/Cat",
        },
        "propertyName": "kind",
    }

    with pytest.raises(ValidationError) as caught:
        Envelope(pet={"kind": C.interp(lambda context: "cat")})
    assert caught.value.errors()[0]["type"] == "nshconfig_discriminator_interpolation"


def test_opaque_private_slots_are_opaque_and_failing_key_repr_is_safe() -> None:
    class PrivateToken:
        __slots__ = ("__value",)

        def __init__(self) -> None:
            self.__value = 1

        def mutate(self) -> None:
            self.__value = 2

    class MutatesPrivateSlot(C.Config, arbitrary_types_allowed=True):
        token: PrivateToken

        @model_validator(mode="after")
        def mutate_token(self) -> "MutatesPrivateSlot":
            self.token.mutate()
            return self

    MutatesPrivateSlot(token=PrivateToken())

    class FailingRepr:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    class HiddenMarker(C.Config):
        values: dict[Any, Any]

    with pytest.raises(ValidationError) as pending:
        HiddenMarker(values={FailingRepr(): C.interp(lambda context: 1)})
    error = pending.value.errors()[0]
    assert error["type"] == "nshconfig_pending"
    assert error["ctx"]["path"].startswith("HiddenMarker.values[<")
