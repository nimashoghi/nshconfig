from __future__ import annotations

from abc import ABC
from collections.abc import Sequence
from typing import Annotated, Literal

import pytest
from typing_extensions import TypeAliasType

import nshconfig as C
from nshconfig._src.utils import PYDANTIC_VERSION


def test_nested_annotation_case_from_nshtrainer():
    # This test is a simplified version of the one in nshtrainer,
    # which currently fails with pydantic>=2.11, but works with pydantic<2.11.
    # Track the following issue: https://github.com/pydantic/pydantic/issues/11682
    class PluginBaseConfig(C.Config, ABC):
        pass

    # For pydantic versions > 2.6.0 and <= 2.7.0, this test will fail.
    # All we can do here is to warn the user about the issue.
    if PYDANTIC_VERSION > "2.6.0" and PYDANTIC_VERSION <= "2.7.0":
        with pytest.warns(
            UserWarning,
            match="Pydantic versions > 2.6.0 and <= 2.7.0 have a bug with complex, recursive types",
        ):
            plugin_registry = C.Registry(PluginBaseConfig, discriminator="name")
            return

    plugin_registry = C.Registry(PluginBaseConfig, discriminator="name")

    PluginConfig = TypeAliasType(  # type: ignore
        "PluginConfig", Annotated[PluginBaseConfig, plugin_registry]
    )

    @plugin_registry.register
    class Plugin1(PluginBaseConfig):
        name: Literal["plugin1"] = "plugin1"
        value: int = 42

    @plugin_registry.register
    class Plugin2(PluginBaseConfig):
        name: Literal["plugin2"] = "plugin2"

        nested_plugins: Sequence[PluginBaseConfig] | None = None

    class RootConfig(C.Config):
        plugins: Sequence[PluginConfig] | None = None

    # This should work, but it currently throws.
    _ = RootConfig(plugins=[])
