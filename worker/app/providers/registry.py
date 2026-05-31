"""Plugin registry — discovers and loads all BaseProvider subclasses.

Providers are auto-discovered from this package. To add a new provider,
create a file in worker/app/providers/ and subclass BaseProvider.
No registration needed — the registry finds it automatically.
"""
import importlib
import pkgutil
from pathlib import Path

from .base import BaseProvider

_providers: dict[str, BaseProvider] | None = None


def _load_all() -> dict[str, BaseProvider]:
    providers_pkg = Path(__file__).parent
    for _, module_name, _ in pkgutil.iter_modules([str(providers_pkg)]):
        if module_name in ("base", "registry"):
            continue
        importlib.import_module(f"app.providers.{module_name}")

    result = {}
    for cls in BaseProvider.__subclasses__():
        instance = cls()
        if instance.is_available():
            result[instance.name] = instance
    return result


def get_providers() -> dict[str, BaseProvider]:
    global _providers
    if _providers is None:
        _providers = _load_all()
    return _providers


def get_provider(name: str) -> BaseProvider | None:
    return get_providers().get(name)
