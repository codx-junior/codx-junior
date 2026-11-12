"""
Ad-hoc verification of the copy / move rules after adding numbered copies.

The pytest module `test_file_engine_transfers.py` covers the same cases, but
importing `codx.junior.engine.file_engine` through the package triggers the app
bootstrap (db + project discovery), which blocks inside this container. The
engine only needs those names at call time, so they are stubbed here and the
module is loaded straight from its file.

Run with: .venv_codx_junior_api/bin/python tests/_manual_unique_copy_check.py
"""

import asyncio
import importlib.util
import os
import sys
import tempfile
import types

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_PATH = os.path.join(API_ROOT, "codx", "junior", "engine", "file_engine.py")


def stub(name, **attributes):
    """Register a lightweight stand-in module in sys.modules."""
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _Anything:
    """Placeholder for the model classes the engine only annotates."""


stub("codx")
stub("codx.junior")
stub("codx.junior.db", Chat=_Anything, Message=_Anything)
stub("codx.junior.model")
stub("codx.junior.model.model", Profile=_Anything)
stub("codx.junior.project")
stub("codx.junior.project.project_discover", find_all_user_projects=lambda *a, **k: [])
stub("codx.junior.utils")
stub("codx.junior.utils.utils", write_file=None, exec_command=None)

spec = importlib.util.spec_from_file_location("file_engine_under_test", ENGINE_PATH)
engine_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine_module)
FileEngine = engine_module.FileEngine


class FakeSettings:
    def __init__(self, path):
        self.abs_project_path = path
        self.codx_path = os.path.join(path, ".codx")
        self.project_name = "check"
        self.project_id = "project-1"
        self.is_git_root = False

    def get_sub_projects(self):
        return []


class FakeSession:
    def __init__(self, path):
        self.settings = FakeSettings(path)


def write(path, content="content"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)
    return path


def run(coroutine):
    return asyncio.run(coroutine)


def check(label, condition):
    assert condition, f"FAILED: {label}"
    print(f"ok  {label}")


def main():
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        engine = FileEngine(FakeSession(tmp))

        # 1. Pasting the same file repeatedly numbers the destination
        source = write(os.path.join(tmp, "notes.md"), "hello")
        target = os.path.join(tmp, "backup")
        os.makedirs(target)

        first = run(engine.copy_files([source], target))
        second = run(engine.copy_files([source], target))
        third = run(engine.copy_files([source], target))
        check("first copy keeps the name", [i["destination"] for i in first["items"]] == [os.path.join(target, "notes.md")])
        check("second copy -> notes (1).md", [i["destination"] for i in second["items"]] == [os.path.join(target, "notes (1).md")])
        check("third copy -> notes (2).md", [i["destination"] for i in third["items"]] == [os.path.join(target, "notes (2).md")])
        check("no conflicts for copies", second["conflicts"] == [] and third["conflicts"] == [])
        check("the content is copied", open(os.path.join(target, "notes (1).md")).read() == "hello")

        # 2. A numbered name keeps counting instead of stacking suffixes
        numbered = run(engine.copy_files([os.path.join(target, "notes (1).md")], target))
        check("numbered copy -> notes (3).md", [i["destination"] for i in numbered["items"]] == [os.path.join(target, "notes (3).md")])

        # 3. Paste into the same folder duplicates the file
        again = run(engine.copy_files([source], tmp))
        check("paste in the same folder -> report (1)-style name", [i["destination"] for i in again["items"]] == [os.path.join(tmp, "notes (1).md")])
        check("the original stays", os.path.isfile(source))

        # 3. A folder copy into the same folder also gets a number
        write(os.path.join(tmp, "dist", "a.txt"), "a")
        folder_copy = run(engine.copy_files([os.path.join(tmp, "dist")], tmp))
        check("folder copy -> dist (1)", [i["destination"] for i in folder_copy["items"]] == [os.path.join(tmp, "dist (1)")])
        check("the folder tree is copied", open(os.path.join(tmp, "dist (1)", "a.txt")).read() == "a")

        # 4. auto_rename=False keeps the old conflict reporting
        strict = run(engine.copy_files([source], target, auto_rename=False))
        check("auto_rename=False reports the conflict", strict["items"] == [] and len(strict["conflicts"]) == 1)

        # 5. overwrite still replaces
        write(os.path.join(tmp, "a.txt"), "new")
        overwrite_target = os.path.join(tmp, "overwritten")
        write(os.path.join(overwrite_target, "a.txt"), "old")
        replaced = run(engine.copy_files([os.path.join(tmp, "a.txt")], overwrite_target, overwrite=True))
        check("overwrite copy works", len(replaced["items"]) == 1)
        check("overwrite copy replaced the content", open(os.path.join(overwrite_target, "a.txt")).read() == "new")

        # 6. A move keeps reporting the collision (nothing is numbered or lost)
        write(os.path.join(tmp, "dst", "a.txt"), "old")
        moved = run(engine.move_files([os.path.join(tmp, "a.txt")], os.path.join(tmp, "dst")))
        check("move reports the conflict", moved["items"] == [] and len(moved["conflicts"]) == 1)
        check("the move source is untouched", open(os.path.join(tmp, "a.txt")).read() == "new")

        # 7. Moving a file into its own folder is a no-op error, never a rename
        same = run(engine.move_files([os.path.join(tmp, "dst", "a.txt")], os.path.join(tmp, "dst")))
        check("move onto itself is rejected", "same path" in same["failed"][0]["error"])

        # 8. A folder can still not be copied inside itself
        inside = run(engine.copy_files([os.path.join(tmp, "dist")], os.path.join(tmp, "dist")))
        check("copy inside itself is rejected", "inside itself" in inside["failed"][0]["error"])

    print("\nALL CHECKS PASSED")
    return failures


if __name__ == "__main__":
    sys.exit(main())
