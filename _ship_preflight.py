"""Pre-flight: can the API boot on the shipped code (main @ 6de05bf)?"""
import os
import sys

ROOT = "/home/codx-junior/codx-junior"
os.environ.setdefault("CODX_JUNIOR_PATH", ROOT)
sys.path.insert(0, os.path.join(ROOT, "api"))

mods = [
    "codx.junior.kanban.model",
    "codx.junior.chat_manager",
    "codx.junior.main",
]
failures = []
for name in mods:
    try:
        __import__(name)
        print(f"  OK   {name}")
    except Exception as exc:  # noqa: BLE001
        failures.append(name)
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")

print("RESULT:", "PASS" if not failures else f"FAIL {failures}")
sys.exit(0 if not failures else 1)
