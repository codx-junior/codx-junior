"""Sanity check for the runtime-folder refactor (run in the api container)."""

import os
import sys

CODEX = "/home/codx-junior-projects/codx-junior/codx-junior-platform/.codx"


def main() -> int:
    from codx.junior.runtime import project_runtime_dir, runtime_dir, runtime_file

    base = project_runtime_dir(CODEX)
    print("project runtime dir:", base)
    print("runtime file        :", runtime_file(CODEX, "atlas", "atlas.db"))
    print("runtime dir         :", runtime_dir(CODEX, "board_manager"))
    assert base.startswith("/tmp/codx-junior-runtime/"), base
    assert not base.startswith(CODEX), base

    from codx.junior.board_manager.store import BoardManagerStore
    store = BoardManagerStore(CODEX)
    print("board state         :", store.state_path)
    print("board worker        :", store.worker_path)
    assert store.state_path.startswith(CODEX), store.state_path
    assert store.worker_path.startswith(base), store.worker_path

    from codx.junior.orchestration.store import RunStore
    runs = RunStore(CODEX)
    print("run doc             :", runs.run_path("abc"))
    print("run lock            :", runs.lock_path("abc"))
    print("run worker          :", runs.worker_path())
    assert runs.run_path("abc").startswith(CODEX), runs.run_path("abc")
    assert runs.lock_path("abc").startswith(base), runs.lock_path("abc")
    assert runs.worker_path().startswith(base), runs.worker_path()

    from codx.junior.knowledge.knowledge_graph import DependencyGraph
    from codx.junior.settings import CODXJuniorSettings
    settings = CODXJuniorSettings.from_codx_path(CODEX)
    print("dependency graph    :", DependencyGraph(settings).graph_file)

    from codx.junior.atlas.query import ProjectAtlas, atlas_db_path
    print("atlas db path       :", atlas_db_path(settings))
    with ProjectAtlas(settings) as atlas:
        print("atlas health        :", atlas.health().to_dict().get("files"))
        print("atlas db open       :", atlas.db_path)
        assert atlas.db_path.startswith(base), atlas.db_path

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
