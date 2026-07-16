"""bootstrap module — process-global registry handle."""
from __future__ import annotations

import pytest

from app.modules.selector import (
    MemoryStorage,
    SelectorRegistry,
    bootstrap,
    load_from_dict,
)


@pytest.fixture(autouse=True)
def _clean_bootstrap():
    bootstrap.reset_for_tests()
    yield
    bootstrap.reset_for_tests()


def _registry():
    cfg = load_from_dict({"selectors": {"t": {}}})
    return SelectorRegistry(cfg, storage_overrides={"t": MemoryStorage()})


def test_get_registry_returns_none_before_set():
    assert bootstrap.get_registry() is None


def test_set_then_get_registry():
    reg = _registry()
    bootstrap.set_registry(reg)
    assert bootstrap.get_registry() is reg


def test_require_registry_raises_without_install():
    with pytest.raises(RuntimeError, match="not been initialised"):
        bootstrap.require_registry()


def test_require_registry_after_install():
    reg = _registry()
    bootstrap.set_registry(reg)
    assert bootstrap.require_registry() is reg


def test_recomputers_empty_by_default():
    assert bootstrap.get_recomputers() == []


def test_register_and_list_recomputer():
    class _Stub:
        pass

    stub = _Stub()
    bootstrap.register_recomputer(stub)  # type: ignore[arg-type]
    assert bootstrap.get_recomputers() == [stub]


def test_register_and_list_timeout_job():
    class _Stub:
        pass

    stub = _Stub()
    bootstrap.register_timeout_job(stub)  # type: ignore[arg-type]
    assert bootstrap.get_timeout_jobs() == [stub]


def test_reset_clears_everything():
    bootstrap.set_registry(_registry())

    class _Stub:
        pass

    bootstrap.register_recomputer(_Stub())  # type: ignore[arg-type]
    bootstrap.register_timeout_job(_Stub())  # type: ignore[arg-type]
    bootstrap.reset_for_tests()
    assert bootstrap.get_registry() is None
    assert bootstrap.get_recomputers() == []
    assert bootstrap.get_timeout_jobs() == []
