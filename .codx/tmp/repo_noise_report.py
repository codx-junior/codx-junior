"""Report which tracked repo paths look like runtime/generated noise.

Run inside the api container:
    python .codx/tmp/repo_noise_report.py
"""

import collections
import subprocess

ROOT = "/home/codx-junior-projects/codx-junior/codx-junior-platform"

# Directories/patterns that are written by the running platform, not by humans.
RUNTIME_PREFIXES = (
    ".codx/board_manager/",
    ".codx/harness/",
    ".codx/orchestration/",
    ".codx/browser_sessions/",
    ".codx/chat_turns/",
    ".codx/computer_library/",
    ".codx/computer_downloads/",
    ".codx/tasks/",
    ".codx/logs/",
    ".codx/atlas/",
    "api/wiki/",
    "wiki/",
    "client/.codx/wiki/",
)

GENERATED_FILES = (
    ".codx/dependency_graph.json",
    ".codx/domain_map.json",
    ".codx/project_summary.md",
    ".codx/wiki_settings.json",
    "wiki/docs.json",
    "wiki/update_status.json",
    "wiki/llms.txt",
)


def main() -> None:
    tracked = subprocess.run(
        ["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()

    print(f"tracked files total: {len(tracked)}")

    by_prefix = collections.Counter()
    for path in tracked:
        for prefix in RUNTIME_PREFIXES:
            if path.startswith(prefix):
                by_prefix[prefix] += 1
                break

    print("\ntracked paths that live under a runtime/generated folder:")
    total = 0
    for prefix, count in sorted(by_prefix.items(), key=lambda kv: -kv[1]):
        print(f"  {count:6d}  {prefix}")
        total += count
    print(f"  {total:6d}  TOTAL")

    print("\ntracked single generated files:")
    for path in GENERATED_FILES:
        if path in tracked:
            print(f"  tracked  {path}")

    modified = subprocess.run(
        ["git", "-C", ROOT, "status", "--porcelain"], capture_output=True, text=True
    ).stdout.splitlines()
    print(f"\nworking tree entries: {len(modified)}")


if __name__ == "__main__":
    main()
