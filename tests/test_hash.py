from __future__ import annotations

from typing import ClassVar

import pytest

import nshconfig as C


class MyInnerConfigNotHashable(C.Config):
    model_config: ClassVar[C.ConfigDict] = {"set_default_hash": False}

    value: int = 0
    value2: str = "default"


class MyConfigNotHashable(C.Config):
    model_config: ClassVar[C.ConfigDict] = {"set_default_hash": False}

    inner: MyInnerConfigNotHashable = MyInnerConfigNotHashable()
    inner2: MyInnerConfigNotHashable = MyInnerConfigNotHashable()
    a: str = "default"
    b: str = "default"


def test_config_not_hashable():
    with pytest.raises(TypeError):
        hash(MyConfigNotHashable())

    with pytest.raises(TypeError):
        dict.fromkeys([MyConfigNotHashable(), MyConfigNotHashable()])


class MyInnerConfigHashable(C.Config):
    model_config: ClassVar[C.ConfigDict] = {"set_default_hash": True}

    value: int = 0
    value2: str = "default"


class MyConfigHashable(C.Config):
    model_config: ClassVar[C.ConfigDict] = {"set_default_hash": True}

    inner: MyInnerConfigHashable = MyInnerConfigHashable()
    inner2: MyInnerConfigHashable = MyInnerConfigHashable()
    a: str = "default"
    b: str = "default"


def test_config_hashable():
    # Test that the hash is consistent
    config1 = MyConfigHashable()
    config2 = MyConfigHashable()

    assert hash(config1) == hash(config2)
    assert config1 == config2

    # Test that the hash is consistent when using dict.fromkeys
    # with a list of hashable configs
    config_list = [MyConfigHashable(), MyConfigHashable()]
    config_list_dedup = list(dict.fromkeys(config_list))
    assert len(config_list_dedup) == 1

    # Test that the hash is consistent when using dict.fromkeys
    # with a list of hashable configs and non-hashable configs
    config_list = [MyConfigHashable(), MyConfigHashable(a="new")]
    config_list_dedup = list(dict.fromkeys(config_list))
    assert len(config_list_dedup) == 2
    assert config_list_dedup[0] == config_list[0]
    assert config_list_dedup[1] == config_list[1]


class MyConfigWithNonHashableProperty(C.Config):
    model_config: ClassVar[C.ConfigDict] = {"set_default_hash": True}

    prop: list[int] = [1, 2, 3]


def test_config_with_non_hashable_property():
    with pytest.raises(TypeError):
        hash(MyConfigWithNonHashableProperty())
