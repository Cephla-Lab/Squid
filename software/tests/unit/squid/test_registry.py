"""Tests for Registry utility."""

import pytest
from squid.core.registry import Registry


class TestRegistry:
    """Test suite for Registry."""

    def test_register_decorator(self):
        """@registry.register decorator should register class."""
        registry = Registry[object]("test")

        @registry.register("my_impl")
        class MyImpl:
            def __init__(self, value):
                self.value = value

        assert "my_impl" in registry.available()

    def test_create_instance(self):
        """create() should instantiate registered class."""
        registry = Registry[object]("test")

        @registry.register("my_impl")
        class MyImpl:
            def __init__(self, value):
                self.value = value

        instance = registry.create("my_impl", 42)
        assert instance.value == 42

    def test_create_with_kwargs(self):
        """create() should pass kwargs to constructor."""
        registry = Registry[object]("test")

        @registry.register("configurable")
        class Configurable:
            def __init__(self, name, count=1):
                self.name = name
                self.count = count

        instance = registry.create("configurable", "test", count=5)
        assert instance.name == "test"
        assert instance.count == 5

    def test_available_lists_all(self):
        """available() should list all registered names."""
        registry = Registry[object]("test")

        @registry.register("impl_a")
        class ImplA:
            pass

        @registry.register("impl_b")
        class ImplB:
            pass

        available = registry.available()
        assert "impl_a" in available
        assert "impl_b" in available

    def test_unknown_raises_keyerror(self):
        """create() with unknown name should raise KeyError."""
        registry = Registry[object]("test")

        with pytest.raises(KeyError) as exc_info:
            registry.create("nonexistent")

        assert "nonexistent" in str(exc_info.value)
        assert "Available:" in str(exc_info.value)

    def test_register_factory(self):
        """register_factory() should register a factory function."""
        registry = Registry[str]("test")

        registry.register_factory("greeting", lambda name: f"Hello, {name}!")

        result = registry.create("greeting", "World")
        assert result == "Hello, World!"

    def test_get_class(self):
        """get_class() should return the registered class."""
        registry = Registry[object]("test")

        @registry.register("my_class")
        class MyClass:
            pass

        cls = registry.get_class("my_class")
        assert cls is MyClass

    def test_get_class_returns_none_for_factory(self):
        """get_class() should return None for factory registrations."""
        registry = Registry[object]("test")
        registry.register_factory("factory", lambda: None)

        cls = registry.get_class("factory")
        assert cls is None

    def test_registry_name_in_error(self):
        """Error message should include registry name."""
        registry = Registry[object]("camera")

        with pytest.raises(KeyError) as exc_info:
            registry.create("missing")

        assert "camera" in str(exc_info.value)
