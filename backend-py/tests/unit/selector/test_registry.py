import pytest

from app.modules.selector import (
    MemoryStorage,
    SelectorRegistry,
    load_from_dict,
)


def _root(**selectors):
    return load_from_dict({"selectors": selectors})


def test_registry_lists_configured_selectors():
    cfg = _root(traders={"namespace": "ns:t"}, providers={"namespace": "ns:p"})
    reg = SelectorRegistry(
        cfg,
        storage_overrides={"traders": MemoryStorage(), "providers": MemoryStorage()},
    )
    assert set(reg.names()) == {"traders", "providers"}


def test_registry_get_missing_raises():
    reg = SelectorRegistry(_root(), storage_overrides={})
    with pytest.raises(KeyError):
        reg.get("nope")


def test_registry_reload_adds_and_removes_selectors():
    cfg1 = _root(traders={"namespace": "ns:t"})
    reg = SelectorRegistry(
        cfg1,
        storage_overrides={
            "traders": MemoryStorage(),
            "providers": MemoryStorage(),
        },
    )
    assert reg.names() == ["traders"]

    cfg2 = _root(providers={"namespace": "ns:p"})
    reg.reload(cfg2)
    assert reg.names() == ["providers"]
    with pytest.raises(KeyError):
        reg.get("traders")


def test_registry_get_returns_same_instance_until_reload():
    cfg = _root(traders={"namespace": "ns:t"})
    reg = SelectorRegistry(cfg, storage_overrides={"traders": MemoryStorage()})
    a = reg.get("traders")
    b = reg.get("traders")
    assert a is b
