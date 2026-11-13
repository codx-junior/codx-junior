"""Media provider protocol, registry and resolution helpers.

Dependency free: stdlib plus the pydantic ``ProviderInfo`` from
``codx.junior.media.model``. No network, no web framework and no settings
imports, so the registry stays unit testable offline.

The registry answers the only question the pipeline asks: *who can serve this
capability right now?* — and, when nobody can, it raises ``NoProviderAvailable``
instead of returning ``None``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, List, Optional, Protocol, runtime_checkable

from .model import ProviderInfo

# The concrete provider implementations live in ``media/providers/`` (see
# ``providers/image_local.py``). That folder is deliberately *not* a python
# package: a ``providers/__init__.py`` would shadow this module and drop
# ``ProviderRegistry`` from ``codx.junior.media.providers``. Exposing the folder
# through ``__path__`` keeps the registry here while
# ``codx.junior.media.providers.image_local`` stays importable.
__path__ = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers")]


class NoProviderAvailable(Exception):
    """Raised when no registered provider can serve a capability."""

    def __init__(self, capability: str, tried: Optional[Iterable[str]] = None) -> None:
        self.capability = capability
        self.tried: List[str] = [str(name) for name in (tried or [])]
        message = f"no media provider available for capability '{capability}'"
        if self.tried:
            message += f" (tried: {', '.join(self.tried)})"
        super().__init__(message)


@runtime_checkable
class MediaProvider(Protocol):
    """Contract every media provider implements."""

    def info(self) -> ProviderInfo:
        """Static description of the provider: name, capability, model, cost."""
        ...

    def available(self) -> bool:
        """Whether the provider can produce something right now."""
        ...

    def estimate(self, request: dict) -> float:
        """Expected cost of ``request`` in the project currency (0.0 = free)."""
        ...

    def run(self, request: dict) -> dict:
        """Execute the request and return the produced payload."""
        ...


def _safe_available(provider: MediaProvider) -> bool:
    """``provider.available()`` that treats any failure as 'not available'.

    A provider probing a local binary or a socket may raise; resolution must
    never explode because of that, it only has to move on to the next provider.
    """
    try:
        return bool(provider.available())
    except Exception:  # noqa: BLE001 - availability probing is best effort
        return False


class ProviderRegistry:
    """In memory registry of media providers keyed by provider name."""

    def __init__(self) -> None:
        self._providers: Dict[str, MediaProvider] = {}

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, name: object) -> bool:
        return name in self._providers

    def register(self, provider: MediaProvider) -> MediaProvider:
        """Store ``provider`` under its ``info().name`` and return it."""
        name = provider.info().name
        if not name:
            raise ValueError("a media provider needs a non empty name")
        self._providers[name] = provider
        return provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def clear(self) -> None:
        self._providers.clear()

    def get(self, name: str) -> MediaProvider:
        """Return the provider registered as ``name`` or raise ``KeyError``."""
        try:
            return self._providers[name]
        except KeyError:
            raise KeyError(f"unknown media provider '{name}'") from None

    def list(self, capability: Optional[str] = None) -> List[ProviderInfo]:
        """Provider descriptions, local first then by name."""
        infos = [self._describe(provider) for provider in self._providers.values()]
        if capability:
            infos = [info for info in infos if info.capability == capability]
        return sorted(infos, key=lambda info: (0 if info.local else 1, info.name))

    def names(self, capability: Optional[str] = None) -> List[str]:
        return [info.name for info in self.list(capability)]

    def resolve(
        self, capability: str, preferred: Optional[str] = None
    ) -> MediaProvider:
        """Return a provider able to serve ``capability``.

        The preferred provider wins when it is registered, declared for the
        capability and available; otherwise the first available provider in
        registry order (local first, then by name). Raises
        ``NoProviderAvailable`` with the names that were tried.
        """
        tried: List[str] = []
        if preferred:
            provider = self._providers.get(preferred)
            if provider is not None:
                tried.append(preferred)
                if self._serves(provider, capability):
                    return provider

        for name in self._ordered_names(capability):
            if name in tried:
                continue
            provider = self._providers[name]
            tried.append(name)
            if self._serves(provider, capability):
                return provider

        raise NoProviderAvailable(capability, tried=tried)

    def _serves(self, provider: MediaProvider, capability: str) -> bool:
        try:
            info = provider.info()
        except Exception:  # noqa: BLE001 - a broken provider is simply skipped
            return False
        return info.capability == capability and _safe_available(provider)

    def _ordered_names(self, capability: Optional[str]) -> List[str]:
        return [info.name for info in self.list(capability)]

    @staticmethod
    def _describe(provider: MediaProvider) -> ProviderInfo:
        info = provider.info()
        available = _safe_available(provider)
        if info.available != available:
            info = info.model_copy(update={"available": available})
        return info


class NullProvider:
    """Placeholder provider so callers never deal with ``None``.

    It is always unavailable and never costs anything: resolving through it
    surfaces as ``NoProviderAvailable`` from ``run()``.
    """

    def __init__(self, capability: str = "image", name: str = "none") -> None:
        self._capability = capability
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self._name,
            capability=self._capability,
            available=False,
            local=True,
            model=None,
            reason="null provider: no implementation for this capability",
        )

    def available(self) -> bool:
        return False

    def estimate(self, request: dict) -> float:
        return 0.0

    def run(self, request: dict) -> dict:
        raise NoProviderAvailable(self._capability, tried=[self._name])


# ---------------------------------------------------------------------------
# module level default registry
# ---------------------------------------------------------------------------

default_registry = ProviderRegistry()


def register_default(provider: MediaProvider) -> MediaProvider:
    """Register ``provider`` on the module level ``default_registry``."""
    return default_registry.register(provider)


def clear() -> None:
    """Forget every provider of the module level ``default_registry``."""
    default_registry.clear()


def default_image_registry() -> ProviderRegistry:
    """Registry with the built in image providers.

    ``LocalCoverProvider`` always works offline (Pillow) and wins resolution by
    being the only available provider; ``ComfyUICoverProvider`` is the optional
    real target and is only picked when it is reachable.
    """
    from codx.junior.media.providers.image_local import (
        ComfyUICoverProvider,
        LocalCoverProvider,
    )

    registry = ProviderRegistry()
    registry.register(LocalCoverProvider())
    registry.register(ComfyUICoverProvider())
    return registry


@contextmanager
def use_registry(registry: ProviderRegistry) -> Iterator[ProviderRegistry]:
    """Temporarily make ``registry`` the module level default registry."""
    global default_registry
    previous = default_registry
    default_registry = registry
    try:
        yield registry
    finally:
        default_registry = previous
