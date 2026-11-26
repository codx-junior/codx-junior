"""Project quality guardians.

A guardian is a **profile** whose ``file_match`` selects the files it owns.
The profile Markdown is its review prompt; its ``guardian`` block declares the
deterministic checks and the triggers. Guardians are created automatically from
the detected stack, refreshed without overwriting user polish, run on file
changes / staged changes / manual review, and produce structured findings with
a deterministic verdict.

Quick start::

    from codx.junior.quality.guardians import bootstrap, run_guardians

    bootstrap(project_settings)                 # create missing guardians
    run = run_guardians(project_settings, trigger="manual", scope="changed")
    print(run["verdict"]["action"], len(run["findings"]))

The package is intentionally usable from three places:

* the background worker (synchronous, deterministic lane on file changes);
* the tools (async, can call the project AI);
* the REST API / UI (reads runs, dismisses findings, runs a guardian on demand).

Made with ❤️ by codx-junior
"""

from .ai import build_prompt, parse_review, review_with_ai
from .catalog import ProjectSignals, build_templates, scan_project
from .diff import build_change_set
from .checks import run_checks, run_fix_commands
from .models import (
    GuardianAction,
    GuardianSeverity,
    finding_to_dict,
    make_verdict,
    render_run_markdown,
)
from .policy import (
    GuardianPolicy,
    quality_path,
    resolve_policy,
    save_policy,
    stored_policy,
)
from .profiles import (
    GUARDIAN_CATEGORY,
    adopt_profile,
    bootstrap,
    enable_guardian,
    get_guardian,
    guardian_settings,
    guardian_summary,
    guardians_for_files,
    has_guardians,
    is_guardian,
    list_guardians,
    remember,
    update_guardian,
)
from .runner import (
    GuardianRunner,
    latest_report,
    quality_state,
    run_guardians,
    run_guardians_async,
)
from .store import RunStore

__all__ = [
    "GUARDIAN_CATEGORY",
    "GuardianAction",
    "GuardianPolicy",
    "GuardianRunner",
    "GuardianSeverity",
    "ProjectSignals",
    "RunStore",
    "adopt_profile",
    "bootstrap",
    "build_change_set",
    "build_prompt",
    "build_templates",
    "enable_guardian",
    "finding_to_dict",
    "get_guardian",
    "guardian_settings",
    "guardian_summary",
    "guardians_for_files",
    "has_guardians",
    "is_guardian",
    "latest_report",
    "list_guardians",
    "make_verdict",
    "parse_review",
    "quality_path",
    "quality_state",
    "remember",
    "render_run_markdown",
    "resolve_policy",
    "review_with_ai",
    "run_checks",
    "run_fix_commands",
    "run_guardians",
    "run_guardians_async",
    "save_policy",
    "scan_project",
    "stored_policy",
    "update_guardian",
]

# Made with ❤️ by codx-junior
