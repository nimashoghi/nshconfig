from __future__ import annotations

import threading
import time
from typing import ClassVar

import pytest
from typing_extensions import Self

import nshconfig as C
from nshconfig import Singleton, singleton


# Test configuration class
class MyConfig(C.Config):
    singleton: ClassVar[Singleton[Self]] = Singleton[Self]()

    value: str
    number: int = 0


# Another test configuration class to verify multiple singletons work
class AnotherConfig(C.Config):
    singleton: ClassVar[Singleton[Self]] = Singleton[Self]()

    name: str


# Basic tests (unchanged)
def test_basic_initialization_with_kwargs():
    """Test initializing a singleton with keyword arguments."""
    # Reset to ensure clean state
    MyConfig.singleton.reset()

    # Initialize with kwargs
    config = MyConfig.singleton.initialize(value="test", number=42)
    assert config.value == "test"
    assert config.number == 42

    # Get the same instance
    same_config = MyConfig.singleton.instance()
    assert same_config is config

    # Reset for other tests
    MyConfig.singleton.reset()


def test_initialization_with_instance():
    """Test initializing a singleton with an existing instance."""
    # Reset to ensure clean state
    MyConfig.singleton.reset()

    # Create a standalone instance
    standalone = MyConfig(value="standalone")

    # Initialize with the instance
    config = MyConfig.singleton.initialize(standalone)
    assert config is standalone
    assert config.value == "standalone"

    # Get the same instance
    same_config = MyConfig.singleton.instance()
    assert same_config is standalone

    # Reset for other tests
    MyConfig.singleton.reset()


def test_try_instance():
    """Test the try_instance method which returns None when not initialized."""
    # Reset to ensure clean state
    MyConfig.singleton.reset()

    # Before initialization, try_instance returns None
    assert MyConfig.singleton.try_instance() is None

    # After initialization, it returns the instance
    config = MyConfig.singleton.initialize(value="test")
    assert MyConfig.singleton.try_instance() is config

    # Reset for other tests
    MyConfig.singleton.reset()


def test_error_on_uninitialized_instance():
    """Test that accessing an uninitialized instance raises an error."""
    # Reset to ensure clean state
    MyConfig.singleton.reset()

    # Trying to get instance before initialization should raise
    with pytest.raises(RuntimeError):
        MyConfig.singleton.instance()

    # Initialize to avoid errors in future tests
    MyConfig.singleton.initialize(value="test")
    MyConfig.singleton.reset()


def test_error_on_empty_initialize():
    """Test that initialize() without args raises TypeError."""
    # Reset to ensure clean state
    MyConfig.singleton.reset()

    # initialize() without args should raise TypeError
    with pytest.raises(TypeError):
        MyConfig.singleton.initialize()

    # Reset for other tests
    MyConfig.singleton.reset()


def test_warning_on_reinitialize():
    """Test that attempting to reinitialize warns and returns the original instance."""
    # Reset to ensure clean state
    MyConfig.singleton.reset()

    # First initialization
    config1 = MyConfig.singleton.initialize(value="first")

    # Second initialization should warn and return the existing instance
    with pytest.warns(RuntimeWarning):
        config2 = MyConfig.singleton.initialize(value="second")

    assert config2 is config1
    assert config2.value == "first"  # Should still have the original value

    # Reset for other tests
    MyConfig.singleton.reset()


def test_multiple_singleton_classes():
    """Test that multiple classes with singletons work independently."""
    # Reset both singletons
    MyConfig.singleton.reset()
    AnotherConfig.singleton.reset()

    # Initialize both
    test_config = MyConfig.singleton.initialize(value="test")
    another_config = AnotherConfig.singleton.initialize(name="another")

    # Verify they're separate singletons
    assert MyConfig.singleton.instance() is test_config
    assert AnotherConfig.singleton.instance() is another_config
    assert MyConfig.singleton.instance() is not AnotherConfig.singleton.instance()

    # Reset for other tests
    MyConfig.singleton.reset()
    AnotherConfig.singleton.reset()


def test_reset():
    """Test that reset() properly clears the instance."""
    # Initialize
    config = MyConfig.singleton.initialize(value="test")

    # Reset
    MyConfig.singleton.reset()

    # try_instance should now return None
    assert MyConfig.singleton.try_instance() is None

    # instance() should raise RuntimeError
    with pytest.raises(RuntimeError):
        MyConfig.singleton.instance()

    # We can initialize again with different values
    new_config = MyConfig.singleton.initialize(value="new")
    assert new_config is not config
    assert new_config.value == "new"

    # Reset for other tests
    MyConfig.singleton.reset()


def test_thread_safety():
    """Test that initialization is thread-safe."""
    # Reset to ensure clean state
    MyConfig.singleton.reset()

    # We'll use these to track what happens
    initialized_configs = []
    exceptions = []

    with pytest.warns(RuntimeWarning):

        def worker():
            try:
                # Add a small random delay to increase chance of race conditions
                time.sleep(0.001)

                # Try to initialize
                config = MyConfig.singleton.initialize(
                    value=f"thread-{threading.get_ident()}"
                )
                initialized_configs.append(config)
            except Exception as e:
                exceptions.append(e)

        # Start multiple threads
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

    # Verify no exceptions occurred
    assert not exceptions, f"Exceptions occurred: {exceptions}"

    # All threads should have gotten the same instance
    assert len(set(id(config) for config in initialized_configs)) == 1

    # Reset for other tests
    MyConfig.singleton.reset()


# New test for inheritance behavior
def test_inheritance_hierarchies():
    """Test that Singleton behaves correctly with inheritance hierarchies."""

    # Base class with a singleton
    class Base(C.Config):
        singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
        base_value: str

    # Derived class that doesn't define its own singleton - this should be an error
    class DerivedThrows(Base):
        derived_value: str

    # Derived class that defines its own singleton - this is correct
    class DerivedCorrect(Base):
        singleton: ClassVar[Singleton[Self]] = Singleton[Self]()  # pyright: ignore[reportIncompatibleVariableOverride]
        derived_value: str

    # Derived class that explicitly uses the base singleton - opt-in footgun
    class DerivedFootgun(Base):
        singleton: ClassVar[Singleton[Base]] = Base.singleton
        derived_value: str

    # Initialize the Base singleton
    base_instance = Base.singleton.initialize(base_value="base")

    # Trying to access DerivedThrows.singleton should throw TypeError
    with pytest.raises(TypeError):
        DerivedThrows.singleton.instance()

    # DerivedCorrect should work fine with its own singleton
    derived_correct = DerivedCorrect.singleton.initialize(
        base_value="correct", derived_value="correct-derived"
    )
    assert derived_correct.base_value == "correct"
    assert derived_correct.derived_value == "correct-derived"

    # Base singleton should work and be separate
    assert Base.singleton.instance() is base_instance
    assert DerivedCorrect.singleton.instance() is derived_correct
    assert Base.singleton.instance() is not DerivedCorrect.singleton.instance()

    # DerivedFootgun uses the base singleton explicitly, which should work
    # but it will be the same instance as the base
    print(f"DerivedFootgun: {DerivedFootgun.singleton}")
    with pytest.warns(RuntimeWarning):  # Already initialized
        derived_footgun = DerivedFootgun.singleton.initialize()

    assert derived_footgun is base_instance
    assert DerivedFootgun.singleton.instance() is Base.singleton.instance()

    # Test creating instances directly (should be independent)
    direct_base = Base(base_value="direct-base")
    direct_derived = DerivedCorrect(base_value="direct-derived", derived_value="direct")

    assert direct_base is not direct_derived
    assert direct_base is not base_instance
    assert direct_derived is not derived_correct


# Additional test for reset behavior with inheritance
def test_inheritance_reset():
    """Test reset behavior with inheritance hierarchies."""

    # Base class with a singleton
    class Parent(C.Config):
        singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
        parent_value: str

    # Child class that correctly defines its own singleton
    class Child(Parent):
        singleton: ClassVar[Singleton[Self]] = Singleton[Self]()  # pyright: ignore[reportIncompatibleVariableOverride]
        child_value: str

    # Grandchild class that correctly defines its own singleton
    class Grandchild(Child):
        singleton: ClassVar[Singleton[Self]] = Singleton[Self]()  # pyright: ignore[reportIncompatibleVariableOverride]
        grandchild_value: str

    # Initialize all singletons
    parent = Parent.singleton.initialize(parent_value="parent")
    child = Child.singleton.initialize(parent_value="child-parent", child_value="child")
    grandchild = Grandchild.singleton.initialize(
        parent_value="gc-parent", child_value="gc-child", grandchild_value="grandchild"
    )

    # Reset parent and verify it doesn't affect children
    Parent.singleton.reset()
    assert Parent.singleton.try_instance() is None
    assert Child.singleton.instance() is child
    assert Grandchild.singleton.instance() is grandchild

    # Reset child and verify it doesn't affect parent or grandchild
    Child.singleton.reset()
    assert Child.singleton.try_instance() is None
    with pytest.raises(RuntimeError):  # Parent was reset earlier
        Parent.singleton.instance()
    assert Grandchild.singleton.instance() is grandchild

    # Initialize parent again and verify independence
    new_parent = Parent.singleton.initialize(parent_value="new-parent")
    assert new_parent is not parent
    assert new_parent.parent_value == "new-parent"


# Tests for global singleton functionality
def test_global_singleton_basic():
    """Test basic functionality of global singletons created with singleton()."""

    class GlobalConfig(C.Config):
        value: str
        number: int = 0

    # Create a global singleton
    global_singleton = singleton(GlobalConfig)

    # Initialize with kwargs
    config = global_singleton.initialize(value="test", number=42)
    assert config.value == "test"
    assert config.number == 42

    # Get the same instance
    same_config = global_singleton.instance()
    assert same_config is config

    # Reset for other tests
    global_singleton.reset()


def test_global_singleton_with_instance():
    """Test initializing a global singleton with an existing instance."""

    class GlobalConfig(C.Config):
        value: str

    global_singleton = singleton(GlobalConfig)

    # Create a standalone instance
    standalone = GlobalConfig(value="standalone")

    # Initialize with the instance
    config = global_singleton.initialize(standalone)
    assert config is standalone
    assert config.value == "standalone"

    # Get the same instance
    same_config = global_singleton.instance()
    assert same_config is standalone

    # Reset for other tests
    global_singleton.reset()


def test_global_singleton_try_instance():
    """Test the try_instance method for global singletons."""

    class GlobalConfig(C.Config):
        value: str

    global_singleton = singleton(GlobalConfig)

    # Before initialization, try_instance returns None
    assert global_singleton.try_instance() is None

    # After initialization, it returns the instance
    config = global_singleton.initialize(value="test")
    assert global_singleton.try_instance() is config

    # Reset for other tests
    global_singleton.reset()


def test_global_singleton_error_on_uninitialized():
    """Test that accessing an uninitialized global singleton raises an error."""

    class GlobalConfig(C.Config):
        value: str

    global_singleton = singleton(GlobalConfig)

    # Trying to get instance before initialization should raise
    with pytest.raises(RuntimeError):
        global_singleton.instance()


def test_global_singleton_error_on_empty_initialize():
    """Test that initialize() without args raises TypeError for global singletons."""

    class GlobalConfig(C.Config):
        value: str

    global_singleton = singleton(GlobalConfig)

    # initialize() without args should raise TypeError
    with pytest.raises(TypeError):
        global_singleton.initialize()


def test_global_singleton_warning_on_reinitialize():
    """Test that attempting to reinitialize a global singleton warns and returns the original instance."""

    class GlobalConfig(C.Config):
        value: str

    global_singleton = singleton(GlobalConfig)

    # First initialization
    config1 = global_singleton.initialize(value="first")

    # Second initialization should warn and return the existing instance
    with pytest.warns(RuntimeWarning):
        config2 = global_singleton.initialize(value="second")

    assert config2 is config1
    assert config2.value == "first"  # Should still have the original value

    # Reset for other tests
    global_singleton.reset()


def test_global_singleton_multiple_separate():
    """Test that multiple global singletons for different classes work independently."""

    class ConfigA(C.Config):
        value_a: str

    class ConfigB(C.Config):
        value_b: str

    singleton_a = singleton(ConfigA)
    singleton_b = singleton(ConfigB)

    # Initialize both
    config_a = singleton_a.initialize(value_a="a")
    config_b = singleton_b.initialize(value_b="b")

    # Verify they're separate singletons
    assert singleton_a.instance() is config_a
    assert singleton_b.instance() is config_b
    assert singleton_a.instance() is not singleton_b.instance()

    # Reset for other tests
    singleton_a.reset()
    singleton_b.reset()


def test_global_singleton_thread_safety():
    """Test that global singleton initialization is thread-safe."""

    class GlobalConfig(C.Config):
        value: str

    global_singleton = singleton(GlobalConfig)

    # We'll use these to track what happens
    initialized_configs = []
    exceptions = []

    with pytest.warns(RuntimeWarning):

        def worker():
            try:
                # Add a small random delay to increase chance of race conditions
                time.sleep(0.001)

                # Try to initialize
                config = global_singleton.initialize(
                    value=f"thread-{threading.get_ident()}"
                )
                initialized_configs.append(config)
            except Exception as e:
                exceptions.append(e)

        # Start multiple threads
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

    # Verify no exceptions occurred
    assert not exceptions, f"Exceptions occurred: {exceptions}"

    # All threads should have gotten the same instance
    assert len(set(id(config) for config in initialized_configs)) == 1

    # Reset for other tests
    global_singleton.reset()


def test_global_singleton_reset():
    """Test that reset() properly clears the global singleton instance."""

    class GlobalConfig(C.Config):
        value: str

    global_singleton = singleton(GlobalConfig)

    # Initialize
    config = global_singleton.initialize(value="test")

    # Reset
    global_singleton.reset()

    # try_instance should now return None
    assert global_singleton.try_instance() is None

    # instance() should raise RuntimeError
    with pytest.raises(RuntimeError):
        global_singleton.instance()

    # We can initialize again with different values
    new_config = global_singleton.initialize(value="new")
    assert new_config is not config
    assert new_config.value == "new"

    # Reset for other tests
    global_singleton.reset()


def test_global_vs_descriptor_singleton_independence():
    """Test that global singletons and descriptor singletons are independent."""

    class MixedConfig(C.Config):
        singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
        value: str

    # Create a global singleton for the same class
    global_singleton = singleton(MixedConfig)

    # Initialize both
    descriptor_config = MixedConfig.singleton.initialize(value="descriptor")
    global_config = global_singleton.initialize(value="global")

    # They should be different instances
    assert descriptor_config is not global_config
    assert descriptor_config.value == "descriptor"
    assert global_config.value == "global"

    # Each singleton should return its own instance
    assert MixedConfig.singleton.instance() is descriptor_config
    assert global_singleton.instance() is global_config

    # Reset both for other tests
    MixedConfig.singleton.reset()
    global_singleton.reset()


def test_global_singleton_repr():
    """Test the __repr__ method for global singletons."""

    class GlobalConfig(C.Config):
        value: str

    global_singleton = singleton(GlobalConfig)

    # Check repr before initialization
    repr_str = repr(global_singleton)
    assert "GlobalConfig" in repr_str
    assert "uninitialized" in repr_str

    # Initialize and check repr again
    config = global_singleton.initialize(value="test")
    repr_str = repr(global_singleton)
    assert "GlobalConfig" in repr_str
    assert "initialized" in repr_str
    assert str(config) in repr_str

    # Reset for other tests
    global_singleton.reset()


def test_global_singleton_inheritance():
    """Test global singletons with inheritance."""

    class BaseGlobal(C.Config):
        base_value: str

    class DerivedGlobal(BaseGlobal):
        derived_value: str

    base_singleton = singleton(BaseGlobal)
    derived_singleton = singleton(DerivedGlobal)

    # Initialize both
    base_config = base_singleton.initialize(base_value="base")
    derived_config = derived_singleton.initialize(
        base_value="derived-base", derived_value="derived"
    )

    # They should be separate
    assert base_config is not derived_config
    assert base_singleton.instance() is base_config
    assert derived_singleton.instance() is derived_config

    # Verify types and values
    assert isinstance(base_config, BaseGlobal)
    assert not isinstance(base_config, DerivedGlobal)
    assert isinstance(derived_config, DerivedGlobal)
    assert isinstance(derived_config, BaseGlobal)

    assert base_config.base_value == "base"
    assert derived_config.base_value == "derived-base"
    assert derived_config.derived_value == "derived"

    # Reset for other tests
    base_singleton.reset()
    derived_singleton.reset()
