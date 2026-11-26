"""Run quality guardians over a change or a project.

The runner is the single place where a guardian becomes a decision:

1. resolve the enabled guardians (auto-creating them on first use);
2. resolve the files (changed, explicit, project-wide or by file type);
3. map every file to the guardians whose ``file_match`` owns it;
4. run the **fast lane** (deterministic checks) for each guardian;
5. when the policy/trigger allows it, run the **deep lane** (AI review) with
   a bounded file/call budget;
6. deduplicate and apply suppressions, produce a deterministic verdict and
   persist the run.

The runner never raises for a normal review problem (a missing linter, an AI
outage, an unreadable file): those become ``errors`` on a run that still shows
the deterministic evidence. Invalid *input* (unknown guardian, bad scope) raises
``ValueError`` so a tool/API caller gets a clear 400 instead of an empty report.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .ai import MAX_PROMPT_CHARS, review_with_ai
from .checks import run_checks
from .diff import build_change_set, normalize_path, walk_project
from .models import (
    GuardianAction,
    highest_severity,
    make_verdict,
    render_run_markdown,
    utc_now,
)
from .policy import GuardianPolicy, resolve_policy
from .profiles import (
    GUARDIAN_CATEGORY,
    bootstrap,
    guardian_settings,
    guardians_for_files,
    is_guardian,
    trigger_allowed,
)
from .store import RunStore

logger = logging.getLogger(__name__)

#: scopes accepted by :meth:`GuardianRunner.run`.
VALID_SCOPES = ("changed", "project", "files", "file_type")

#: modes accepted by :meth:`GuardianRunner.run`.
VALID_MODES = ("auto", "checks", "review")

#: triggers accepted by the runner.
VALID_TRIGGERS = ("change", "staged", "commit", "manual")

#: Default change scope per trigger.
CHANGE_SCOPE_BY_TRIGGER = {
    "change": "all",
    "staged": "staged",
    "commit": "all",
    "manual": "all",
}

#: Human file-type aliases used by ``scope='file_type'``.
FILE_TYPE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "python": (".py", ".pyi"),
    "js": (".js", ".jsx", ".mjs", ".cjs"),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "ts": (".ts", ".tsx"),
    "typescript": (".ts", ".tsx"),
    "vue": (".vue",),
    "svelte": (".svelte",),
    "astro": (".astro",),
    "css": (".css", ".scss", ".sass", ".less"),
    "docs": (".md", ".mdx", ".rst", ".adoc"),
    "markdown": (".md", ".mdx"),
    "tests": ("__tests__", "/tests/", "/test/", "_test.", ".test.", ".spec."),
    "test": ("__tests__", "/tests/", "/test/", "_test.", ".test.", ".spec."),
    "sql": (".sql",),
    "graphql": (".graphql", ".gql"),
    "proto": (".proto",),
    "go": (".go",),
    "rust": (".rs",),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "ruby": (".rb",),
    "php": (".php",),
    "csharp": (".cs",),
    "swift": (".swift",),
    "c": (".c", ".h"),
    "cpp": (".cc", ".cpp", ".hpp", ".cxx"),
    "shell": (".sh", ".bash", ".zsh", ".fish"),
    "json": (".json",),
    "yaml": (".yaml", ".yml"),
    "yml": (".yaml", ".yml"),
    "toml": (".toml",),
    "config": (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"),
    "docker": ("dockerfile", "docker-compose"),
    "ci": (".github/workflows/", ".gitlab-ci"),
    "infra": (".sh", ".bash", ".tf", ".tfvars", "dockerfile", ".github/workflows/", "k8s/", "helm/"),
}


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------
def new_run_id() -> str:
    """Return a unique, file-name-safe run id."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"run-{stamp}-{uuid.uuid4().hex[:6]}"


def _normalize_scope(scope: str) -> str:
    """Validate a review scope."""
    value = str(scope or "changed").strip().lower()
    if value not in VALID_SCOPES:
        raise ValueError(f"Invalid scope {value!r}. Valid values: {', '.join(VALID_SCOPES)}")
    return value


def _normalize_mode(mode: str) -> str:
    """Validate a review mode."""
    value = str(mode or "auto").strip().lower()
    if value not in VALID_MODES:
        raise ValueError(f"Invalid mode {value!r}. Valid values: {', '.join(VALID_MODES)}")
    return value


def _normalize_trigger(trigger: str) -> str:
    """Validate a review trigger."""
    value = str(trigger or "manual").strip().lower()
    if value not in VALID_TRIGGERS:
        raise ValueError(f"Invalid trigger {value!r}. Valid values: {', '.join(VALID_TRIGGERS)}")
    return value


def _compact_file(file_entry: Dict[str, Any], guardian_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return the bounded file entry stored on a run (no content/diff)."""
    entry = {
        "path": file_entry.get("path", ""),
        "status": file_entry.get("status", ""),
        "language": file_entry.get("language", ""),
        "additions": file_entry.get("additions", 0),
        "deletions": file_entry.get("deletions", 0),
        "truncated": bool(file_entry.get("truncated")),
    }
    if guardian_names:
        entry["guardians"] = list(guardian_names)
    return entry


def _file_type_values(file_type: Any) -> List[str]:
    """Split a file-type filter into lower-case values."""
    if file_type is None:
        return []
    if isinstance(file_type, str):
        values = [item.strip().lower() for item in file_type.split(",")]
    elif isinstance(file_type, (list, tuple, set)):
        values = [str(item).strip().lower() for item in file_type]
    else:
        values = [str(file_type).strip().lower()]
    return [value for value in values if value]


def matches_file_type(file_entry: Dict[str, Any], file_type: Any) -> bool:
    """Return True when *file_entry* matches a human file-type filter.

    Supports extensions (``.py``, ``py``), language names (``python``,
    ``typescript``), guardian domains (``docs``, ``infra``) and test paths.

    Args:
        file_entry: Normalized file payload.
        file_type: One or more values (comma separated string or list).

    Returns:
        bool: Whether any value matches.
    """
    values = _file_type_values(file_type)
    if not values:
        return True
    path = str(file_entry.get("path") or "").replace("\\", "/").lower()
    extension = os.path.splitext(path)[1]
    language = str(file_entry.get("language") or "").lower()
    for value in values:
        if value in (language, extension, extension.lstrip(".")):
            return True
        aliases = FILE_TYPE_ALIASES.get(value, ())
        for alias in aliases:
            if alias.startswith("/") or alias.endswith("/") or "/" in alias:
                if alias in path:
                    return True
                # A root-level ``tests/`` folder has no leading slash in a
                # project-relative path.
                if alias.startswith("/") and path.startswith(alias.lstrip("/")):
                    return True
            elif alias.startswith("."):
                if extension == alias:
                    return True
            elif alias in path:
                return True
        if value.startswith(".") and extension == value:
            return True
    return False


def _select_guardians(
    settings: Any,
    profiles: List[Any],
    *,
    names: Optional[List[str]],
    trigger: str,
    policy: GuardianPolicy,
) -> List[Any]:
    """Select the guardian profiles participating in a run.

    Args:
        settings: Project settings.
        profiles: All profiles.
        names: Explicit guardian names (``None`` = all applicable).
        trigger: Run trigger.
        policy: Effective policy.

    Returns:
        List[Profile]: Selected guardians.

    Raises:
        ValueError: When an explicit name is unknown or is not a guardian.
    """
    from .profiles import guardian_for_name

    if names:
        selected = []
        missing = []
        for name in names:
            profile = guardian_for_name(profiles, name)
            if profile is None:
                missing.append(name)
            else:
                selected.append(profile)
        if missing:
            known = sorted(
                profile.name for profile in profiles if is_guardian(profile, include_disabled=True)
            )
            raise ValueError(
                f"Unknown guardian(s): {', '.join(missing)}. "
                f"Available guardians: {', '.join(known) or 'none'}"
            )
        return selected

    selected = []
    for profile in profiles:
        if not is_guardian(profile, include_disabled=False):
            continue
        if not policy.is_guardian_enabled(profile.name):
            continue
        # An explicit manual run is an opt-in: do not hide a guardian because
        # it only declared the staged trigger.
        if trigger != "manual" and not trigger_allowed(profile, trigger):
            continue
        selected.append(profile)
    return selected


def _resolve_files(
    settings: Any,
    policy: GuardianPolicy,
    *,
    scope: str,
    trigger: str,
    paths: Optional[List[str]],
    file_type: Any,
    base: str,
    change_scope: Optional[str],
    max_files: int,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Resolve the normalized files a run should inspect."""
    if scope == "files" and not paths:
        raise ValueError("scope='files' requires at least one path")
    if scope in ("changed", "files"):
        selected_scope = change_scope or CHANGE_SCOPE_BY_TRIGGER.get(trigger, "all")
        if scope == "files":
            selected_scope = "files"
        files, errors = build_change_set(
            settings,
            paths=paths,
            base=base,
            scope=selected_scope,
            policy=policy,
            max_files=max_files,
            include_content=True,
            explicit_paths_are_new=False,
        )
    else:
        walk_limit = max(max_files * 20, 1000) if file_type else max(max_files * 8, 200)
        walked = walk_project(
            settings,
            policy=policy,
            extensions=[],
            max_files=walk_limit,
        )
        if paths:
            wanted = {
                normalize_path(settings, path)
                for path in paths
                if normalize_path(settings, path)
            }
            walked = [path for path in walked if path in wanted]
        if file_type:
            # Filter by path before reading file contents: a "tests" or
            # "infra" scope has no single extension, so matching only the
            # extension would miss the files that matter.
            from .diff import language_for_path

            walked = [
                path for path in walked
                if matches_file_type(
                    {"path": path, "language": language_for_path(path)}, file_type
                )
            ]
        walked = walked[:max_files]
        files, errors = build_change_set(
            settings,
            paths=walked,
            base=base,
            scope="files",
            policy=policy,
            max_files=max_files,
            include_content=True,
            explicit_paths_are_new=False,
        ) if walked else ([], [])

    if file_type:
        files = [file_entry for file_entry in files if matches_file_type(file_entry, file_type)]
    return files, errors


def _files_for_guardian(
    files: List[Dict[str, Any]],
    owned: Dict[str, List[Any]],
    guardian_name: str,
) -> List[Dict[str, Any]]:
    """Return the files owned by one guardian."""
    return [
        file_entry for file_entry in files
        if any(profile.name == guardian_name for profile in owned.get(file_entry.get("path", ""), []))
    ]


def _emit_run_event(settings: Any, run: Dict[str, Any]) -> None:
    """Broadcast a bounded quality event to connected clients (best effort)."""
    try:
        from codx.junior.sio.session_channel import SessionChannel
        from codx.junior.sio.sio import sio

        SessionChannel(sio=sio).send_event(
            "quality-event",
            {
                "codx_path": getattr(settings, "codx_path", ""),
                "project_id": getattr(settings, "project_id", ""),
                "project_name": getattr(settings, "project_name", ""),
                "run": {
                    "id": run.get("id"),
                    "created_at": run.get("created_at"),
                    "trigger": run.get("trigger"),
                    "scope": run.get("scope"),
                    "action": (run.get("verdict") or {}).get("action"),
                    "finding_count": len(
                        [
                            finding for finding in (run.get("findings") or [])
                            if str(finding.get("status") or "open") == "open"
                        ]
                    ),
                    "guardians": run.get("guardians") or [],
                },
            },
        )
    except Exception as ex:  # pragma: no cover - never fail a run for a socket
        logger.debug("Cannot emit the quality run event: %s", ex)


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------
class GuardianRunner:
    """Run quality guardians with optional AI review and durable history."""

    def __init__(
        self,
        settings: Any,
        *,
        session: Any = None,
        ai: Any = None,
        policy: Optional[GuardianPolicy] = None,
        store: Optional[RunStore] = None,
    ):
        self.settings = settings
        self.session = session
        self.ai = ai
        self.policy = policy or resolve_policy(settings)
        self.store = store or RunStore(settings)

    # ── Public entry points ──────────────────────────────────────────────
    def run(self, **kwargs: Any) -> Dict[str, Any]:
        """Run synchronously (safe outside an event loop).

        Raises:
            RuntimeError: When called from a thread with a running event loop;
                use :meth:`run_async` there.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async(**kwargs))
        raise RuntimeError(
            "GuardianRunner.run() cannot block inside a running event loop; "
            "use 'await runner.run_async(...)'."
        )

    async def run_async(
        self,
        *,
        trigger: str = "manual",
        scope: str = "changed",
        mode: str = "auto",
        paths: Optional[List[str]] = None,
        file_type: Any = None,
        guardians: Optional[List[str]] = None,
        base: str = "HEAD",
        change_scope: Optional[str] = None,
        max_files: Optional[int] = None,
        user: str = "",
    ) -> Dict[str, Any]:
        """Run guardians and return the persisted run.

        Args:
            trigger: ``change``, ``staged``, ``commit`` or ``manual``.
            scope: ``changed``, ``project``, ``files`` or ``file_type``.
            mode: ``auto`` (checks + policy-gated AI), ``checks`` or ``review``.
            paths: Explicit files (for ``scope='files'`` or to narrow another scope).
            file_type: Type filter (``python``, ``vue``, ``docs``, ``tests``...).
            guardians: Explicit guardian names (manual opt-in).
            base: Git reference for a changed review.
            change_scope: ``all``, ``staged`` or ``unstaged`` override.
            max_files: File cap override (policy default when omitted).
            user: User that triggered the run (for the record).

        Returns:
            Dict: The run (also persisted in ``.codx/quality/runs``).

        Raises:
            ValueError: On invalid scope/mode/trigger or unknown guardian.
        """
        trigger = _normalize_trigger(trigger)
        scope = _normalize_scope(scope)
        mode = _normalize_mode(mode)
        started = time.time()
        run_id = new_run_id()
        policy = self.policy
        errors: List[str] = []

        run: Dict[str, Any] = {
            "id": run_id,
            "schema": 1,
            "created_at": utc_now(),
            "updated_at": "",
            "project": getattr(self.settings, "project_name", "") or "",
            "project_id": getattr(self.settings, "project_id", "") or "",
            "trigger": trigger,
            "scope": scope,
            "mode": mode,
            "base": base,
            "user": user or "",
            "guardians": [],
            "files": [],
            "file_guardians": {},
            "checks": [],
            "findings": [],
            "reviews": [],
            "errors": errors,
            "ai_calls": 0,
            "ai_cost": 0.0,
            "duration_seconds": 0.0,
            "diff_truncated": False,
            "policy": {
                "block_above": policy.block_above,
                "approval_above": policy.approval_above,
                "warn_above": policy.warn_above,
            },
            "success": True,
        }

        if not policy.enabled:
            errors.append("Quality guardians are disabled for this project")
            run["verdict"] = make_verdict([], **run["policy"])
            run["duration_seconds"] = round(time.time() - started, 2)
            return run

        # ── Resolve guardians (lazy bootstrap, never from a file change) ──
        # Auto-creation is a USER action: opening the Quality panel, running a
        # manual/staged/commit review or asking for bootstrap. A file-change
        # burst must only run guardians that already exist: writing profiles
        # from a background save would surprise the user and create guardian
        # sets in every nested project.
        manager = self._profile_manager()
        profiles = manager.list_all_profiles()
        bootstrap_trigger = trigger in ("manual", "staged", "commit")
        if (
            not guardians
            and policy.auto_bootstrap
            and bootstrap_trigger
            and not any(is_guardian(profile, include_disabled=True) for profile in profiles)
        ):
            try:
                bootstrap(self.settings, force=False)
            except Exception as ex:
                logger.exception("Quality guardian bootstrap failed: %s", ex)
                errors.append(f"Bootstrap failed: {ex}")
            profiles = self._profile_manager().list_all_profiles()

        selected = _select_guardians(
            self.settings, profiles, names=guardians, trigger=trigger, policy=policy
        )
        explicit_selection = bool(guardians)
        if not selected:
            errors.append(
                "No quality guardian matched this run. Run the guardian bootstrap "
                "or enable a guardian profile with a file_match."
            )
            run["verdict"] = make_verdict([], **run["policy"])
            run["duration_seconds"] = round(time.time() - started, 2)
            self._persist(run)
            return run

        # ── Resolve files ─────────────────────────────────────────────────
        file_cap = max_files or policy.max_files_per_run
        try:
            files, file_errors = _resolve_files(
                self.settings,
                policy,
                scope=scope,
                trigger=trigger,
                paths=paths,
                file_type=file_type,
                base=base,
                change_scope=change_scope,
                max_files=file_cap,
            )
        except ValueError:
            raise
        except Exception as ex:
            logger.exception("Quality guardian could not resolve the change set: %s", ex)
            errors.append(f"Could not resolve files: {ex}")
            files = []
            file_errors = []
        errors.extend(file_errors)

        if not files:
            run["verdict"] = make_verdict([], **run["policy"])
            run["duration_seconds"] = round(time.time() - started, 2)
            errors.append("No files matched the requested scope")
            self._persist(run)
            return run

        run["diff_truncated"] = any(file_entry.get("truncated") for file_entry in files)
        file_paths = [file_entry.get("path", "") for file_entry in files]
        try:
            owned = guardians_for_files(
                self.settings, file_paths, profiles=profiles, policy=policy
            )
        except Exception as ex:
            logger.exception("Quality guardian matching failed: %s", ex)
            errors.append(f"Guardian matching failed: {ex}")
            owned = {}
        # Drop profiles that were not selected (explicit runs must not leak other
        # guardians into the run).
        selected_names = {profile.name for profile in selected}
        owned = {
            path: [profile for profile in owners if profile.name in selected_names]
            for path, owners in owned.items()
        }
        owned = {path: owners for path, owners in owned.items() if owners}

        run["files"] = [
            _compact_file(
                file_entry,
                guardian_names=[profile.name for profile in owned.get(file_entry.get("path", ""), [])],
            )
            for file_entry in files
        ]
        run["file_guardians"] = {
            path: [profile.name for profile in owners]
            for path, owners in owned.items()
        }
        run["guardians"] = sorted({
            profile.name for owners in owned.values() for profile in owners
        })

        if not run["guardians"]:
            errors.append("No guardian matched the selected files")
            run["verdict"] = make_verdict([], **run["policy"])
            run["duration_seconds"] = round(time.time() - started, 2)
            self._persist(run)
            return run

        # ── Run checks and reviews per guardian ───────────────────────────
        all_findings: List[Dict[str, Any]] = []
        ai_allowed = mode in ("auto", "review") and (
            explicit_selection or policy.ai_allowed(trigger)
        )
        if mode == "review" and not ai_allowed:
            errors.append(
                "AI review is disabled for this trigger by the quality policy; "
                "use mode='checks' or enable the AI lane."
            )
        ai_calls = 0

        for profile in selected:
            guardian_files = _files_for_guardian(files, owned, profile.name)
            if not guardian_files:
                continue
            block = guardian_settings(profile)
            if block is None:
                continue
            if not explicit_selection and trigger != "manual" and not trigger_allowed(profile, trigger):
                continue

            if mode in ("auto", "checks"):
                try:
                    findings, executions, check_errors = run_checks(
                        profile.name,
                        block.checks or [],
                        self.settings,
                        guardian_files,
                        policy=policy,
                        extra_ignore_paths=block.ignore_paths,
                    )
                    all_findings.extend(findings)
                    run["checks"].extend(executions)
                    errors.extend(check_errors)
                except Exception as ex:
                    logger.exception("Guardian %s checks failed: %s", profile.name, ex)
                    errors.append(f"{profile.name} checks: {ex}")

            if not ai_allowed:
                continue
            if ai_calls >= policy.max_ai_calls_per_run:
                errors.append(
                    f"AI review budget reached ({policy.max_ai_calls_per_run} call(s)); "
                    "remaining guardians ran checks only"
                )
                ai_allowed = False
                continue

            ai_files = self._ai_files(guardian_files, policy.max_ai_files_per_run)
            if not ai_files:
                continue
            try:
                review = await review_with_ai(
                    self.settings,
                    profile,
                    ai_files,
                    session=self.session,
                    ai=self.ai,
                    policy=policy,
                    cache=self.store,
                )
            except Exception as ex:
                logger.exception("Guardian %s AI review failed: %s", profile.name, ex)
                review = {
                    "guardian": profile.name,
                    "summary": "",
                    "findings": [],
                    "error": str(ex) or "AI review failed",
                    "cost": 0.0,
                    "model": "",
                    "cached": False,
                    "prompt_chars": 0,
                    "reviewed_paths": [],
                }
            all_findings.extend(review.get("findings") or [])
            if review.get("error") and not review.get("cached"):
                errors.append(f"{profile.name} review: {review.get('error')}")
            if not review.get("cached"):
                ai_calls += 1
            run["ai_calls"] = ai_calls
            run["ai_cost"] = round(float(run.get("ai_cost") or 0.0) + float(review.get("cost") or 0.0), 6)
            run["reviews"].append(
                {
                    "guardian": profile.name,
                    "summary": review.get("summary") or "",
                    "error": review.get("error") or "",
                    "model": review.get("model") or "",
                    "cached": bool(review.get("cached")),
                    "cost": float(review.get("cost") or 0.0),
                    "prompt_chars": int(review.get("prompt_chars") or 0),
                    "reviewed_paths": list(review.get("reviewed_paths") or []),
                }
            )

        # ── Deduplicate, suppress, decide ────────────────────────────────
        findings = self._finalize_findings(all_findings, policy)
        run["findings"] = findings
        run["verdict"] = make_verdict(findings, **run["policy"])
        run["summary"] = self._summary(run)
        run["duration_seconds"] = round(time.time() - started, 2)
        if not any(finding.get("status") == "open" for finding in findings) and not errors:
            run["summary"] = run["verdict"]["summary"]

        self._persist(run)
        return run

    # ── Internals ────────────────────────────────────────────────────────
    def _profile_manager(self):
        """Return a ProfileManager for the project."""
        from codx.junior.profiles.profile_manager import ProfileManager

        return ProfileManager(settings=self.settings)

    def _ai_files(self, files: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        """Pick the files a guardian should send to the model.

        Deleted files and empty payloads are useless to a reviewer; the rest are
        ordered by the size of the change so the most consequential files fit in
        the bounded prompt.

        Args:
            files: Files owned by the guardian.
            limit: Maximum files.

        Returns:
            List[dict]: Selected file payloads.
        """
        candidates = [
            file_entry for file_entry in files
            if file_entry.get("status") != "deleted"
            and (file_entry.get("diff") or file_entry.get("content"))
        ]
        candidates.sort(
            key=lambda file_entry: (
                -(int(file_entry.get("additions") or 0) + int(file_entry.get("deletions") or 0)),
                str(file_entry.get("path") or ""),
            )
        )
        return candidates[: max(1, int(limit or 1))]

    def _finalize_findings(
        self, raw_findings: List[Dict[str, Any]], policy: GuardianPolicy
    ) -> List[Dict[str, Any]]:
        """Deduplicate findings and apply the project suppressions/ignores."""
        findings: List[Dict[str, Any]] = []
        seen: set = set()
        ignored_rules = {str(rule) for rule in (policy.ignore_rules or [])}
        for finding in raw_findings:
            if not isinstance(finding, dict):
                continue
            identifier = str(finding.get("id") or "")
            if not identifier:
                identifier = str(finding.get("rule_id") or "")
            if identifier in seen:
                continue
            seen.add(identifier)
            rule = str(finding.get("rule_id") or "")
            if rule in ignored_rules or "*" in ignored_rules:
                finding["status"] = "suppressed"
                finding["status_reason"] = "ignored by the project quality policy"
            else:
                suppression = self.store.suppression_for(finding)
                if suppression:
                    finding["status"] = "suppressed"
                    finding["suppression_id"] = suppression.get("id", "")
                    finding["status_reason"] = suppression.get("reason") or "suppressed by the user"
            finding.setdefault("status", "open")
            findings.append(finding)
        # Open findings first, then by severity for a stable UI.
        from .models import SEVERITY_RANK, normalize_severity

        findings.sort(
            key=lambda finding: (
                0 if str(finding.get("status") or "open") == "open" else 1,
                -SEVERITY_RANK[normalize_severity(finding.get("severity"))],
                str(finding.get("path") or ""),
                str(finding.get("rule_id") or ""),
            )
        )
        return findings

    def _summary(self, run: Dict[str, Any]) -> str:
        """Build a compact human summary of the run."""
        verdict = run.get("verdict") or {}
        summaries = [
            str(review.get("summary") or "").strip()
            for review in (run.get("reviews") or [])
            if review.get("summary")
        ]
        if summaries:
            return " ".join(summaries)[:2000]
        return str(verdict.get("summary") or "")

    def _persist(self, run: Dict[str, Any]) -> Dict[str, Any]:
        """Store and broadcast a run (best effort for the broadcast)."""
        try:
            self.store.save_run(run)
        except Exception as ex:
            logger.exception("Cannot persist quality guardian run '%s': %s", run.get("id"), ex)
            run.setdefault("errors", []).append(f"Could not persist the run: {ex}")
        _emit_run_event(self.settings, run)
        return run

    # ── Reports ──────────────────────────────────────────────────────────
    @staticmethod
    def render(run: Dict[str, Any], *, title: str = "") -> str:
        """Render a run as Markdown (delegates to the model helper)."""
        return render_run_markdown(run, title=title)


# -----------------------------------------------------------------------------
# Convenience functions used by tools/API/tests
# -----------------------------------------------------------------------------
def run_guardians(
    settings: Any,
    *,
    session: Any = None,
    ai: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run guardians synchronously (convenience wrapper)."""
    return GuardianRunner(settings, session=session, ai=ai).run(**kwargs)


async def run_guardians_async(
    settings: Any,
    *,
    session: Any = None,
    ai: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run guardians asynchronously (convenience wrapper)."""
    return await GuardianRunner(settings, session=session, ai=ai).run_async(**kwargs)


def latest_report(settings: Any, *, title: str = "") -> str:
    """Return the latest run of the project as Markdown (or an empty string)."""
    run = RunStore(settings).latest_run()
    return render_run_markdown(run, title=title) if run else ""


def quality_state(settings: Any) -> Dict[str, Any]:
    """Return guardians + policy + latest runs for the UI/API."""
    from .profiles import list_guardians

    store = RunStore(settings)
    state = store.summary()
    state.update(list_guardians(settings, include_disabled=True, include_candidates=True))
    return state


# Made with ❤️ by codx-junior
