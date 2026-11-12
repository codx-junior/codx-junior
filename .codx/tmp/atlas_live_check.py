"""
Live atlas check — runs the REAL atlas code inside the api container.

Usage (one command per docker exec, no shell):

    <venv>/bin/python .../.codx/tmp/atlas_live_check.py structure
    <venv>/bin/python .../.codx/tmp/atlas_live_check.py health
    <venv>/bin/python .../.codx/tmp/atlas_live_check.py search

It is deliberately split in three phases so a slow phase can fail without
losing the evidence of the others.
"""

import os
import sqlite3
import sys
import traceback

ROOT = os.environ.get("CODX_JUNIOR_PATH", "/home/codx-junior/codx-junior")
sys.path.insert(0, os.path.join(ROOT, "api"))

PROJECT_FILE = os.environ.get(
    "ATLAS_PROJECT_FILE",
    "/home/codx-junior-projects/codx-junior/codx-junior-platform/.codx/project.json",
)


def section(title):
    print("")
    print("=" * 78)
    print(title)
    print("=" * 78)


def ok(label, condition, detail=""):
    print("[%s] %s%s" % ("PASS" if condition else "FAIL", label, (" — " + detail) if detail else ""))
    return bool(condition)


def load_settings():
    from codx.junior.settings import CODXJuniorSettings

    return CODXJuniorSettings.from_project_file(PROJECT_FILE)


def related_rows(settings):
    from codx.junior.atlas.related import related_projects

    return related_projects(settings)


# ── phase 1: project structure ──────────────────────────────────────────────

def phase_structure():
    import codx.junior
    import codx.junior.atlas
    from codx.junior.atlas.related import child_paths

    section("CODE UNDER TEST")
    print("codx.junior      :", os.path.dirname(codx.junior.__file__))
    print("codx.junior.atlas:", os.path.dirname(codx.junior.atlas.__file__))

    settings = load_settings()
    section("1. PROJECT")
    print("name                  :", settings.project_name)
    print("root                  :", settings.abs_project_path)
    print("codx_path             :", settings.codx_path)
    print("project_atlas_enabled :", settings.project_atlas_enabled)
    print("search_related       :", settings.search_related_projects)
    print("max_files             :", getattr(settings, "project_atlas_max_files", None))
    print("ignore patterns       :", settings.get_ignore_patterns())

    rows = related_rows(settings)
    section("2. RELATED PROJECTS RESOLVED BY THE ATLAS (%d)" % len(rows))
    for row in rows:
        print("  %-24s %-11s indexed=%-5s %s" % (
            row["name"], row["kind"], row["indexed"], row["root"]))
    print("")
    print("child paths excluded from the parent index:",
          child_paths(settings, settings.abs_project_path))

    section("3. ROOT ATLAS BOUNDARY (must not contain child project files)")
    db = os.path.join(settings.codx_path, "atlas", "atlas.db")
    print("db:", db, os.path.getsize(db) if os.path.isfile(db) else "MISSING")
    if os.path.isfile(db):
        con = sqlite3.connect(db)
        total = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        leaked = [r[0] for r in con.execute(
            "SELECT path FROM files WHERE path LIKE 'api/%' OR path LIKE 'client/%' "
            "OR path LIKE 'docs/%' OR path LIKE 'wiki/%'")
        ][:5]
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        con.close()
        print("indexed files:", total)
        print("index_version:", meta.get("index_version"), " built_at:", meta.get("built_at"))
        print("warnings     :", meta.get("warnings"))
        ok("no child-project paths leaked into the root index", not leaked, str(leaked))
        ok("index version is the new one (2)", str(meta.get("index_version")) == "2",
           "got %s" % meta.get("index_version"))


# ── phase 2: per-project atlas health ───────────────────────────────────────

def phase_health():
    from codx.junior.atlas import ProjectAtlas

    settings = load_settings()
    rows = related_rows(settings)
    targets = [("(self) " + (settings.project_name or "root"), settings)]
    targets += [(row["name"], row["settings"]) for row in rows]

    section("4. ATLAS HEALTH PER PROJECT (real index, real code)")
    print("%-28s %7s %8s %7s %7s %10s %s" % (
        "project", "files", "symbols", "edges", "docs", "db_bytes", "warnings"))
    total_files = 0
    for name, project_settings in targets:
        try:
            with ProjectAtlas(project_settings) as atlas:
                health = atlas.health()
            total_files += health.files
            print("%-28s %7d %8d %7d %7d %10d %s" % (
                name, health.files, health.symbols, health.edges,
                health.docs, health.db_bytes, "; ".join(health.warnings) or "-"))
        except Exception as ex:
            print("%-28s ERROR %s" % (name, ex))
            traceback.print_exc()
    print("")
    ok("every project reports an atlas", total_files > 0, "%d files total" % total_files)


# ── phase 3: the model-facing tools, across projects ────────────────────────

def _llm(response):
    return getattr(response, "llm_response", None) or getattr(response, "user_response", "") or ""


def _atlas_tools():
    """
    Import ``codx.junior.tools.atlas_tools`` WITHOUT executing the tools package
    ``__init__``.

    That ``__init__`` imports every tool module (docker, browser, computer,
    company...); in the running API they are already loaded, but in this bare
    process the import alone takes minutes / blocks, which has nothing to do
    with the atlas. Registering a synthetic parent package keeps the real one
    out of the way.
    """
    import importlib.util
    import types

    # ``codx.junior`` must be imported first: the module below uses relative
    # imports and the bare ``import codx.junior.tools...`` statement would also
    # bind the attribute, which fails before the package exists.
    import codx.junior.settings  # noqa: F401

    if "codx.junior.tools" not in sys.modules:
        parent = types.ModuleType("codx.junior.tools")
        parent.__path__ = [os.path.join(ROOT, "api", "codx", "junior", "tools")]
        sys.modules["codx.junior.tools"] = parent

    name = "codx.junior.tools.atlas_tools"
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(ROOT, "api", "codx", "junior", "tools", "atlas_tools.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def phase_map():
    atlas_tools = _atlas_tools()

    settings = load_settings()
    section("5. project_map() — does the model get told the project structure?")
    text = _llm(atlas_tools.project_map(settings=settings))
    tail = text[text.find("Related projects"):] if "Related projects" in text else ""
    print(text[:600], flush=True)
    print("...", flush=True)
    print(tail[:1200] if tail else "<<NO Related projects SECTION>>", flush=True)
    ok("project_map documents the related projects", bool(tail))
    ok("project_map names codx-api", "codx-api" in text)


def phase_root():
    atlas_tools = _atlas_tools()

    settings = load_settings()
    section("6. project_locate() on the ROOT — api/ symbols must be invisible here")
    root_hit = _llm(atlas_tools.project_locate("AtlasIndexer", settings=settings))
    print(root_hit[:700], flush=True)
    ok("root atlas does not serve api/ symbols", "atlas/indexer.py" not in root_hit)


def phase_child():
    atlas_tools = _atlas_tools()

    settings = load_settings()
    section("7. project_locate(project='codx-api') — real cross-project search")
    child_hit = _llm(atlas_tools.project_locate(
        "AtlasIndexer", project="codx-api", settings=settings))
    print(child_hit[:1200], flush=True)
    ok("child project search returns evidence", "indexer.py" in child_hit)

    section("7b. unknown project name — does the error teach the structure?")
    bad = _llm(atlas_tools.project_locate("router", project="does-not-exist", settings=settings))
    print(bad[:700], flush=True)
    ok("unknown project lists the available ones", "codx-api" in bad and "vailable" in bad)


def phase_all():
    atlas_tools = _atlas_tools()

    settings = load_settings()
    section("8. project_locate(project='all') — this project + every related one")
    all_hit = _llm(atlas_tools.project_locate("atlas", project="all", settings=settings, limit=5))
    print(all_hit[:2500], flush=True)
    projects_seen = [name for name in
                     ("codx-api", "codx-junior-ui", "wiki", "codx-docs",
                      "codx-junior-installer", "codx-junior-design")
                     if name in all_hit]
    print("")
    print("projects mentioned:", projects_seen, flush=True)
    ok("multi-project search spans more than one project", len(projects_seen) >= 2)


def phase_cross():
    """Compact: per-project hit counts for several concepts + path readability."""
    import re
    import time

    atlas_tools = _atlas_tools()
    settings = load_settings()

    section("9. CROSS-PROJECT SEARCH — hits per project (project='all')")
    for query in ("AtlasIndexer", "milvus", "docker compose"):
        started = time.time()
        text = _llm(atlas_tools.project_locate(query, project="all", settings=settings, limit=3))
        elapsed = time.time() - started
        blocks = re.split(r"\n## `([^`]+)`", text)
        hits = {}
        for index in range(1, len(blocks) - 1, 2):
            name, body = blocks[index], blocks[index + 1]
            match = re.search(r"atlas: (\d+) evidence", body)
            hits[name] = int(match.group(1)) if match else -1
        found = {name: count for name, count in hits.items() if count > 0}
        print("%-22s %5.1fs  projects=%d  with-hits=%s" % (
            query, elapsed, len(hits), found or "NONE"), flush=True)

    section("10. RETURNED PATHS REALLY EXIST (absolute root + relative path)")
    text = _llm(atlas_tools.project_locate("AtlasIndexer", project="codx-api", settings=settings))
    match = re.search(r"- root: `([^`]+)`", text)
    hits = re.findall(r"^\d+\. `([^`]+:\d+)", text, re.MULTILINE)
    root = match.group(1) if match else ""
    resolved = []
    for hit in hits[:3]:
        rel = hit.rsplit(":", 1)[0]
        absolute = os.path.join(root, rel)
        resolved.append((rel, os.path.isfile(absolute)))
        print("  %-45s exists=%s" % (rel, os.path.isfile(absolute)), flush=True)
    ok("every reported child-project path is readable", bool(resolved) and all(e for _, e in resolved))


def phase_unit():
    """Run api/tests/test_atlas.py with the same interpreter as the API."""
    import unittest

    api = os.path.join(ROOT, "api")
    tests_dir = os.path.join(api, "tests")
    sys.path.insert(0, api)
    section("11. UNIT TESTS — api/tests/test_atlas.py")
    print("code :", api, flush=True)
    print("tests:", tests_dir, os.path.isdir(tests_dir), flush=True)
    suite = unittest.TestLoader().discover(
        tests_dir, pattern="test_atlas*.py", top_level_dir=api)
    print("collected:", suite.countTestCases(), flush=True)
    result = unittest.TextTestRunner(verbosity=1, stream=sys.stdout).run(suite)
    ok("atlas unit tests pass", result.wasSuccessful(),
       "run=%d failures=%d errors=%d" % (
           result.testsRun, len(result.failures), len(result.errors)))


def phase_trace():
    """trace/impact: do they accept ``project=`` like locate does?"""
    atlas_tools = _atlas_tools()
    settings = load_settings()

    section("12. project_trace / project_impact — cross-project support?")
    plain = _llm(atlas_tools.project_trace("AtlasIndexer", settings=settings))
    with_project = _llm(atlas_tools.project_trace(
        "AtlasIndexer", project="codx-api", settings=settings))
    print("trace(root)            :", (plain.splitlines() or [""])[0], flush=True)
    print("trace(project=codx-api):", (with_project.splitlines() or [""])[0], flush=True)
    print("project= silently ignored by trace:", plain == with_project, flush=True)

    root_impact = _llm(atlas_tools.project_impact(["entrypoint.sh"], settings=settings))
    child_impact = _llm(atlas_tools.project_impact(
        ["codx/junior/atlas/indexer.py"], project="codx-api", settings=settings))
    print("")
    print("impact(root, entrypoint.sh)       :", (root_impact.splitlines() or [""])[0], flush=True)
    print("impact(project=codx-api, api path):", (child_impact.splitlines() or [""])[0], flush=True)


PHASES = {
    "structure": phase_structure,
    "health": phase_health,
    "map": phase_map,
    "root": phase_root,
    "child": phase_child,
    "all": phase_all,
    "cross": phase_cross,
    "unit": phase_unit,
    "trace": phase_trace,
}


def main():
    phase = (sys.argv[1] if len(sys.argv) > 1 else "structure").lower()
    if phase == "everything":
        for name in ("structure", "health", "map", "root", "child", "all"):
            PHASES[name]()
        return
    if phase not in PHASES:
        print("unknown phase %r; use one of %s" % (phase, ", ".join(PHASES)))
        return
    PHASES[phase]()


if __name__ == "__main__":
    main()
