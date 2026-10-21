"""Every module in the package must import: catches dead or half-edited code
that no targeted test happens to touch (a syntax slip, a removed helper, a
circular import introduced by a refactor)."""
from __future__ import annotations

import importlib
import pkgutil

import jarvis_foundry


def test_every_module_imports_cleanly() -> None:
    failures: list[str] = []
    imported = 0
    for module in pkgutil.walk_packages(jarvis_foundry.__path__, "jarvis_foundry."):
        if module.name.endswith("__main__"):
            continue  # entry points run on import by design
        imported += 1
        try:
            importlib.import_module(module.name)
        except BaseException as error:  # noqa: BLE001 - report every failure at once
            failures.append(f"{module.name}: {type(error).__name__}: {str(error)[:160]}")
    assert imported > 300, f"suspiciously few modules discovered: {imported}"
    assert not failures, "modules failed to import:\n" + "\n".join(failures)
