"""Offline tests for the media provider registry and the budget gate."""

import json
from pathlib import Path

import pytest

from codx.junior.media import budget as budget_module
from codx.junior.media import providers as providers_module
from codx.junior.media.budget import BudgetDecision, BudgetGate
from codx.junior.media.model import ProviderInfo
from codx.junior.media.providers import (
    MediaProvider,
    NoProviderAvailable,
    NullProvider,
    ProviderRegistry,
    clear,
    default_registry,
    register_default,
    use_registry,
)


class FakeProvider:
    """Minimal provider double: no network, no files."""

    def __init__(self, name, capability="image", available=True, local=True, cost=0.0):
        self.name = name
        self.capability = capability
        self.available_now = available
        self.local = local
        self.cost = cost

    def info(self):
        return ProviderInfo(
            name=self.name,
            capability=self.capability,
            available=self.available_now,
            local=self.local,
            cost_per_unit=self.cost,
        )

    def available(self):
        return self.available_now

    def estimate(self, request):
        return self.cost * float(request.get("units", 1))

    def run(self, request):
        return {"provider": self.name, "request": request}


def _registry(*providers):
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    return registry


# --------------------------------------------------------------- providers


def test_provider_protocol_matches_fake_provider():
    assert isinstance(FakeProvider("comfyui"), MediaProvider)


def test_registry_capability_filtering_and_local_first_order():
    registry = _registry(
        FakeProvider("zz-remote", local=False),
        FakeProvider("piper", capability="voice"),
        FakeProvider("comfyui"),
        FakeProvider("a-local"),
    )

    assert registry.names("image") == ["a-local", "comfyui", "zz-remote"]
    assert registry.names("voice") == ["piper"]
    assert registry.names("video") == []

    everything = registry.list()
    assert [info.name for info in everything] == [
        "a-local",
        "comfyui",
        "piper",
        "zz-remote",
    ]
    assert all(info.local for info in everything[:3])
    assert everything[-1].local is False


def test_registry_requires_a_name_and_exposes_containment():
    registry = ProviderRegistry()
    with pytest.raises(ValueError):
        registry.register(FakeProvider(""))

    registry.register(FakeProvider("comfyui"))
    assert "comfyui" in registry
    assert len(registry) == 1
    registry.unregister("comfyui")
    assert "comfyui" not in registry


def test_get_unknown_name_raises_key_error():
    registry = _registry(FakeProvider("comfyui"))
    with pytest.raises(KeyError):
        registry.get("missing")
    assert registry.get("comfyui").info().name == "comfyui"


def test_list_reflects_live_availability():
    provider = FakeProvider("comfyui", available=False)
    registry = _registry(provider)
    assert registry.list("image")[0].available is False
    provider.available_now = True
    assert registry.list("image")[0].available is True


def test_resolve_prefers_available_preferred_provider():
    registry = _registry(
        FakeProvider("a-local"),
        FakeProvider("comfyui"),
    )
    assert registry.resolve("image", preferred="comfyui").info().name == "comfyui"


def test_resolve_skips_unavailable_preferred_provider():
    registry = _registry(
        FakeProvider("comfyui", available=False),
        FakeProvider("sdxl-local"),
        FakeProvider("zz-remote", local=False),
    )
    assert registry.resolve("image", preferred="comfyui").info().name == "sdxl-local"


def test_resolve_ignores_preferred_provider_of_another_capability():
    registry = _registry(
        FakeProvider("piper", capability="voice"),
        FakeProvider("comfyui"),
    )
    assert registry.resolve("image", preferred="piper").info().name == "comfyui"


def test_resolve_raises_no_provider_available_listing_tried_names():
    registry = _registry(
        FakeProvider("comfyui", available=False),
        FakeProvider("sdxl-local", available=False),
        FakeProvider("piper", capability="voice"),
    )
    with pytest.raises(NoProviderAvailable) as excinfo:
        registry.resolve("image", preferred="comfyui")

    error = excinfo.value
    assert error.capability == "image"
    assert error.tried == ["comfyui", "sdxl-local"]
    assert "comfyui" in str(error)
    assert "sdxl-local" in str(error)
    assert "tried:" in str(error)


def test_resolve_unknown_capability_raises_with_empty_tried():
    registry = _registry(FakeProvider("comfyui"))
    with pytest.raises(NoProviderAvailable) as excinfo:
        registry.resolve("assemble")
    assert excinfo.value.capability == "assemble"
    assert excinfo.value.tried == []


def test_null_provider_is_unavailable_and_free():
    provider = NullProvider(capability="image")
    assert provider.info().name == "none"
    assert provider.info().capability == "image"
    assert provider.available() is False
    assert provider.estimate({"units": 5}) == 0.0
    with pytest.raises(NoProviderAvailable):
        provider.run({})


def test_null_provider_never_resolves():
    registry = _registry(NullProvider(capability="image"))
    with pytest.raises(NoProviderAvailable):
        registry.resolve("image")


def test_default_registry_helpers_and_use_registry():
    clear()
    try:
        assert len(default_registry) == 0
        register_default(FakeProvider("comfyui"))
        assert default_registry.names("image") == ["comfyui"]
        clear()
        assert len(default_registry) == 0

        other = _registry(FakeProvider("piper", capability="voice"))
        with use_registry(other) as active:
            assert active is other
            assert providers_module.default_registry is other
        assert providers_module.default_registry is default_registry
    finally:
        clear()


# ------------------------------------------------------------------ budget


def test_check_and_charge_accounting_within_run_budget():
    gate = BudgetGate(run_budget=10.0)

    decision = gate.check(4.0, "cover")
    assert isinstance(decision, BudgetDecision)
    assert decision.allowed is True
    assert bool(decision) is True
    assert decision.remaining_run == pytest.approx(10.0)

    assert gate.charge(4.0) == pytest.approx(4.0)
    assert gate.check(6.0).allowed is True
    assert gate.check(6.5).allowed is False
    assert gate.snapshot()["spent_run"] == pytest.approx(4.0)


def test_charge_over_run_budget_is_rejected_by_next_check_without_raising():
    gate = BudgetGate(run_budget=5.0)
    assert gate.charge(7.0) == pytest.approx(7.0)  # no exception raised

    decision = gate.check(0.01)
    assert decision.allowed is False
    assert "run budget" in decision.reason
    assert decision.remaining_run == 0.0


def test_daily_budget_is_independent_and_zero_means_unlimited():
    gate = BudgetGate(run_budget=100.0, daily_budget=3.0)
    gate.charge(2.5)
    decision = gate.check(1.0)
    assert decision.allowed is False
    assert "daily budget" in decision.reason

    free = BudgetGate()
    assert free.check(1_000_000.0).allowed is True
    assert free.snapshot()["remaining_run"] == float("inf")


def test_budget_gate_never_raises_on_bad_input():
    gate = BudgetGate(run_budget=1.0)
    assert gate.check(None).allowed in (True, False)
    assert gate.charge("not-a-number") == pytest.approx(0.0)


def test_reset_clears_counters():
    gate = BudgetGate(run_budget=10.0)
    gate.charge(3.0)
    gate.reset()
    assert gate.snapshot()["spent_run"] == 0.0
    assert gate.snapshot()["spent_day"] == 0.0


def test_json_persistence_roundtrip(tmp_path: Path):
    path = tmp_path / "media" / "budget.json"
    gate = BudgetGate(path=path, run_budget=10.0, daily_budget=100.0)
    gate.charge(3.5)

    assert path.exists()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["spent_run"] == pytest.approx(3.5)
    assert stored["spent_day"] == pytest.approx(3.5)

    restored = BudgetGate(path=path, run_budget=10.0, daily_budget=100.0)
    snapshot = restored.snapshot()
    assert snapshot["spent_run"] == pytest.approx(3.5)
    assert snapshot["spent_day"] == pytest.approx(3.5)
    assert restored.check(0.5).allowed is True
    assert restored.check(7.0).allowed is False

    restored.reset()
    assert json.loads(path.read_text(encoding="utf-8"))["spent_run"] == 0.0


def test_persistence_ignores_corrupt_file(tmp_path: Path):
    path = tmp_path / "budget.json"
    path.write_text("not json at all", encoding="utf-8")
    gate = BudgetGate(path=path, run_budget=5.0)
    assert gate.snapshot()["spent_run"] == 0.0
    assert gate.charge(1.0) == pytest.approx(1.0)


def test_media_core_is_offline_and_framework_free():
    for module in (providers_module, budget_module):
        source = Path(module.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("import fastapi", "import requests", "import httpx", "junior.settings"):
            assert forbidden not in source, (module.__name__, forbidden)
