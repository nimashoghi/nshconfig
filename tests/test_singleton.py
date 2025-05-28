from __future__ import annotations

import threading
import time
from typing import ClassVar, Self

import pytest

import nshconfig as C
from nshconfig import Singleton


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
        singleton: ClassVar[Singleton[Base]] = Base.singleton  # pyright: ignore[reportIncompatibleVariableOverride]
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
