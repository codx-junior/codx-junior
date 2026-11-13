"""Throwaway runtime check of the payload consumed by the Actors panel UI."""
import json
import sys

PROJECT = "/home/codx-junior-projects/codx-junior/codx-junior-platform"
sys.path.insert(0, f"{PROJECT}/api")

from codx.junior.settings import CODXJuniorSettings  # noqa: E402
from codx.junior.autonomy.service import ActorService  # noqa: E402

settings = CODXJuniorSettings.from_project_file(f"{PROJECT}/.codx/project.json")
service = ActorService(settings=settings)
status = service.status()

print("STATUS KEYS:", sorted(status.keys()))
print("ACTORS:", len(status.get("actors") or []))
print("WORKER PRESENT:", status.get("worker_present"))
print("DEFAULTS:", sorted((status.get("defaults") or {}).keys()))

for entry in status.get("actors") or []:
    print("---")
    print("actor:", entry.get("actor"), "| role:", entry.get("role"))
    print("boards:", entry.get("boards"), "| desk:", entry.get("board"))
    print("avatar:", bool(entry.get("avatar")), "| description:", (entry.get("description") or "")[:40])
    print("schedule:", entry.get("schedule", {}).get("enabled"),
          "| open:", entry.get("schedule", {}).get("open"),
          "|", entry.get("schedule", {}).get("description"))
    print("next_wake_at:", entry.get("next_wake_at"), "| due:", entry.get("due"),
          "| daily_spent:", entry.get("daily_spent"))
    print("config keys:", sorted((entry.get("config") or {}).keys()))
    print("state keys:", sorted((entry.get("state") or {}).keys()))

kanban = service._manager().load_kanban() or {}
raw = kanban.get("boards")
print("KANBAN boards type:", type(raw).__name__, "len:", len(raw or []))
sample = raw if isinstance(raw, list) else list((raw or {}).values())
print("BOARD names:", [b.get("name") or b.get("title") for b in (sample or [])][:10])

profiles = service._profiles().list_profiles()
print("PROFILES:", [p.name for p in profiles][:15])
print("OK")
