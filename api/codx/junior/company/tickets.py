"""
Company-wide ticket workbench.

The company shell needs one place to see the tickets of **every** linked
project, place a new one on the project that must do the work, let the
autonomous worker handle it and review the delivery when it is done. The
per-project ticketing service already owns all the rules (status transitions,
consent gate, validation, worktree, review); this module only adds the company
layer:

* it resolves the company's visible projects and opens a
  :class:`~codx.junior.ticketing.service.TicketService` for each one;
* it aggregates the **incoming** tickets (the queues of those projects) and
  deduplicates by ticket id, so a ticket placed from one linked project onto
  another appears exactly once;
* it creates a ticket **as the company** (the source is the company, not
  another project) and enables unattended work by default: the target accepts
  the request because the company owns both ends;
* it exposes the human review of a finished delivery (accept or request
  changes and queue another run).

The heavy lifting stays in the ticketing package. Nothing here writes a ticket
by hand except the company/target resolution.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from codx.junior.ticketing.autonomy import (
    TicketAutonomyService,
    autonomy_enabled,
)
from codx.junior.ticketing.model import (
    TICKET_SIDE_TARGET,
    TicketRecord,
)
from codx.junior.ticketing.service import TicketError, TicketService

logger = logging.getLogger(__name__)

#: Prefix of the synthetic source id of a ticket placed by the company.
COMPANY_SOURCE_PREFIX = "company:"

#: How many tickets a company listing reads from each project by default.
DEFAULT_PROJECT_LIMIT = 100

#: Messages returned in the detail payload.
DEFAULT_MESSAGE_LIMIT = 120


class CompanyTicketError(RuntimeError):
    """Raised when a company ticket operation cannot be resolved."""


def _clean(value: Any) -> str:
    """Return a stripped string (empty for None)."""
    return str(value or "").strip()


class CompanyTicketService:
    """
    Company-scoped view over the per-project ticket queues.

    Args:
        manager: Company manager (catalog + settings resolution).
        user: Authenticated user (or ``None`` for a system operation).
        ticket_service_factory: Optional factory ``(settings) -> TicketService``
            used by tests to inject a double.
        autonomy_service_factory: Optional factory
            ``(settings) -> TicketAutonomyService`` used by tests.
    """

    def __init__(
        self,
        manager: Any,
        user: Any = None,
        ticket_service_factory: Optional[Callable[[Any], Any]] = None,
        autonomy_service_factory: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        self.manager = manager
        self.user = user
        self._ticket_service_factory = ticket_service_factory
        self._autonomy_service_factory = autonomy_service_factory

    # ------------------------------------------------------------------
    # Project resolution
    # ------------------------------------------------------------------

    def _username(self) -> Optional[str]:
        """Return the acting username in the shape TicketService expects."""
        username = getattr(self.user, "username", "") or (
            self.user if isinstance(self.user, str) else ""
        )
        return _clean(username) or None

    def _service(self, settings: Any) -> Any:
        """Build the ticket service of one project."""
        if self._ticket_service_factory is not None:
            return self._ticket_service_factory(settings)
        return TicketService(settings=settings, user=self._username())

    def _autonomy(self, settings: Any) -> Any:
        """Build the autonomy service of one project (target side)."""
        if self._autonomy_service_factory is not None:
            return self._autonomy_service_factory(settings)
        return TicketAutonomyService(settings=settings, user=self.user)

    def _role(self, company: Any) -> str:
        """Return the company role of the acting user ('' when not a member)."""
        user = self.user
        if user is None:
            return "admin"  # trusted internal/system caller
        username = _clean(
            getattr(user, "username", "")
            or (user if isinstance(user, str) else "")
        )
        owner = _clean(getattr(company, "owner", "") or "")
        if not owner:
            return "admin"  # an unowned company is being bootstrapped
        if str(getattr(user, "role", "") or "") == "admin":
            return "admin"
        if username and username == owner:
            return "admin"
        try:
            return _clean(user.company_role(company.id)) if hasattr(user, "company_role") else ""
        except Exception:
            return ""

    def _assert_can_read(self, company: Any) -> None:
        """Refuse readers outside a company that already has an owner."""
        if _clean(getattr(company, "owner", "") or "") and not self._role(company):
            raise CompanyTicketError(
                "You are not a member of this company."
            )

    def _assert_can_manage(self, company: Any) -> None:
        """Refuse writes from roles that cannot manage the company work."""
        self._assert_can_read(company)
        if self._role(company) not in ("admin", "manager"):
            raise CompanyTicketError(
                "Only a company manager can place, run or review tickets."
            )

    def _company(self, company: Any) -> Any:
        """Resolve a company id or document to the current document."""
        company_id = _clean(getattr(company, "id", company))
        return self.manager.get_company(company_id)

    def projects(self, company: Any) -> List[Dict[str, Any]]:
        """
        Return the company projects a ticket can be placed on.

        Each entry carries the display name, the availability flag (a linked
        project removed from disk cannot run work) and the project id used by
        the API. Child projects included by the company link are part of the
        visible set.

        Args:
            company: Company document (or id).

        Returns:
            Project descriptors in company order.
        """
        document = self._company(company)
        self._assert_can_read(document)
        try:
            catalog = {
                project.get("project_id"): project
                for project in self.manager._project_catalog()
            }
        except Exception as ex:  # the catalog is only used for display names
            logger.debug("company tickets: project catalog unavailable: %s", ex)
            catalog = {}
        projects: List[Dict[str, Any]] = []
        for project_id in self.manager.visible_project_ids(document):
            reference = document.project(project_id)
            try:
                settings = self.manager.project_settings(project_id)
            except Exception as ex:  # one broken project must not hide the rest
                logger.debug(
                    "company tickets: cannot resolve settings for '%s': %s",
                    project_id,
                    ex,
                )
                settings = None
            projects.append(
                {
                    "project_id": project_id,
                    "name": (
                        _clean(getattr(settings, "project_name", ""))
                        or _clean(getattr(reference, "name", ""))
                        or _clean((catalog.get(project_id) or {}).get("project_name"))
                        or project_id
                    ),
                    "role": _clean(getattr(reference, "role", "")),
                    "available": settings is not None,
                    "ticket_count": 0,
                }
            )
        return projects

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def _record_payload(self, record: TicketRecord, project: Dict[str, Any]) -> Dict[str, Any]:
        """Shape one record for the workbench list."""
        payload = record.dict()
        payload["project_id"] = project["project_id"]
        payload["project_name"] = project["name"]
        payload["review_pending"] = bool(
            record.review_pending
            or (
                record.status == "done"
                and not (record.autonomy or {}).get("reviewed_at")
            )
        )
        return payload

    def list_tickets(
        self,
        company: Any,
        project_id: str = "",
        status: str = "",
        include_closed: bool = True,
        limit: int = DEFAULT_PROJECT_LIMIT,
    ) -> Dict[str, Any]:
        """
        List the tickets of the company, grouped by target project.

        Args:
            company: Company document (or id).
            project_id: Optional project filter (target project id).
            status: Optional ticket status filter.
            include_closed: Include done/abandoned tickets.
            limit: Maximum tickets read from each project.

        Returns:
            ``company_id``, ``projects``, ``tickets``, ``counts``,
            ``worker_present`` and ``autonomy_enabled``.
        """
        document = self._company(company)
        self._assert_can_read(document)
        projects = self.projects(document)
        by_id = {project["project_id"]: project for project in projects}
        wanted_project = _clean(project_id)
        wanted_status = _clean(status).lower()

        seen: Dict[str, Dict[str, Any]] = {}
        for project in projects:
            if not project["available"]:
                continue
            if wanted_project and project["project_id"] != wanted_project:
                continue
            settings = self.manager.project_settings(project["project_id"])
            if settings is None:
                continue
            service = self._service(settings)
            try:
                records = service.query(
                    direction=TICKET_SIDE_TARGET,
                    status=wanted_status or None,
                    include_closed=include_closed,
                    limit=max(1, int(limit)),
                )
            except Exception as ex:  # one broken project must not hide the rest
                logger.debug(
                    "company tickets: cannot read '%s': %s",
                    project["project_id"],
                    ex,
                )
                continue
            for record in records:
                if not record.ticket_id or record.ticket_id in seen:
                    continue
                seen[record.ticket_id] = self._record_payload(record, project)
            project["ticket_count"] = sum(
                1
                for ticket in seen.values()
                if ticket["project_id"] == project["project_id"]
            )

        tickets = sorted(
            seen.values(),
            key=lambda ticket: _clean(ticket.get("updated_at")),
            reverse=True,
        )
        counts = {
            "all": len(tickets),
            "running": sum(
                1 for ticket in tickets
                if (ticket.get("autonomy") or {}).get("state") == "running"
            ),
            "queued": sum(
                1 for ticket in tickets
                if (ticket.get("autonomy") or {}).get("state") == "queued"
            ),
            "review": sum(1 for ticket in tickets if ticket.get("review_pending")),
            "blocked": sum(
                1 for ticket in tickets
                if (ticket.get("autonomy") or {}).get("state") in ("failed", "blocked")
            ),
            "done": sum(1 for ticket in tickets if ticket.get("status") == "done"),
        }
        try:
            from codx.junior.ticketing.worker import worker_present
        except Exception:  # pragma: no cover - worker module is optional
            worker_present = lambda: False  # noqa: E731
        return {
            "company_id": document.id,
            "projects": projects,
            "tickets": tickets,
            "counts": counts,
            "worker_present": bool(worker_present()),
            "autonomy_enabled": bool(autonomy_enabled()),
            "filter": {
                "project_id": wanted_project,
                "status": wanted_status,
                "include_closed": bool(include_closed),
            },
        }

    def _target(self, company: Any, ticket_id: str) -> Dict[str, Any]:
        """
        Resolve the company project that owns *ticket_id*.

        Args:
            company: Company document.
            ticket_id: Ticket id.

        Returns:
            ``project``, ``settings`` and ``chat``.

        Raises:
            CompanyTicketError: When no company project owns the ticket.
        """
        wanted = _clean(ticket_id)
        if not wanted:
            raise CompanyTicketError("a ticket id is required")
        for project in self.projects(company):
            if not project["available"]:
                continue
            settings = self.manager.project_settings(project["project_id"])
            if settings is None:
                continue
            manager = self._service(settings).own_manager()
            chat = manager.find_by_id(wanted)
            if chat is None or not TicketService.is_ticket(chat):
                continue
            return {"project": project, "settings": settings, "chat": chat}
        raise CompanyTicketError(
            f"ticket '{wanted}' was not found in the projects of this company"
        )

    def detail(self, company: Any, ticket_id: str) -> Dict[str, Any]:
        """
        Return one ticket with its conversation and autonomy evidence.

        Args:
            company: Company document (or id).
            ticket_id: Ticket id.

        Returns:
            ``ticket`` (record payload), ``autonomy``, ``messages``,
            ``project`` and ``can_manage``.

        Raises:
            CompanyTicketError: When the ticket is not part of the company.
        """
        document = self._company(company)
        self._assert_can_read(document)
        target = self._target(document, ticket_id)
        chat = target["chat"]
        record = TicketRecord.from_chat(chat, direction=TICKET_SIDE_TARGET)
        payload = self._record_payload(record, target["project"])
        try:
            autonomy = self._autonomy(target["settings"]).status(ticket_id)
            autonomy = autonomy.get("autonomy") or autonomy
        except Exception as ex:
            logger.debug("company tickets: autonomy status failed: %s", ex)
            autonomy = record.autonomy or {}
        messages = []
        for message in list(getattr(chat, "messages", []) or [])[-DEFAULT_MESSAGE_LIMIT:]:
            if getattr(message, "unsent", False):
                continue
            content = str(getattr(message, "content", "") or "")
            if not content.strip():
                continue
            messages.append(
                {
                    "doc_id": getattr(message, "doc_id", None),
                    "role": getattr(message, "role", "assistant"),
                    "content": content,
                    "user": getattr(message, "user", None),
                    "created_at": getattr(message, "created_at", None),
                    "done": getattr(message, "done", True),
                    "error": getattr(message, "error", None),
                }
            )
        return {
            "ticket": payload,
            "autonomy": autonomy,
            "messages": messages,
            "project": target["project"],
            "can_manage": self._role(document) in ("admin", "manager"),
        }

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def create(self, company: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place a ticket on one company project.

        Args:
            company: Company document (or id).
            payload: ``project_id``/``target_project``, ``name``,
                ``description``, optional ``kind``, ``priority``, ``profiles``,
                ``tags``, ``autonomous`` (default true), ``auto_accept``
                (default true), ``profile`` and ``validation``.

        Returns:
            The created ticket payload.

        Raises:
            CompanyTicketError: When the project is missing/unavailable or the
                ticketing service rejects the request.
        """
        document = self._company(company)
        self._assert_can_manage(document)
        project_id = _clean(payload.get("project_id") or payload.get("target_project"))
        project = next(
            (
                item
                for item in self.projects(document)
                if item["project_id"] == project_id
            ),
            None,
        )
        if project is None:
            raise CompanyTicketError(
                f"project '{project_id}' is not linked to this company"
            )
        if not project["available"]:
            raise CompanyTicketError(
                f"project '{project['name']}' is not discovered by this workspace"
            )
        settings = self.manager.project_settings(project_id)
        if settings is None:
            raise CompanyTicketError(f"project '{project_id}' has no settings")
        service = self._service(settings)
        autonomous = payload.get("autonomous")
        autonomous = True if autonomous is None else bool(autonomous)
        auto_accept = payload.get("auto_accept")
        auto_accept = True if auto_accept is None else bool(auto_accept)
        try:
            record = service.create(
                target=project_id,
                target_settings=settings,
                name=_clean(payload.get("name")),
                description=_clean(payload.get("description")),
                kind=payload.get("kind"),
                priority=payload.get("priority"),
                profiles=payload.get("profiles") or [],
                tags=payload.get("tags") or [],
                autonomous=autonomous,
                autonomy_profile=payload.get("profile"),
                auto_accept=auto_accept and autonomous,
                source_project_id=f"{COMPANY_SOURCE_PREFIX}{document.id}",
                source_project_name=f"{document.name} (company)",
                track_ledger=False,
            )
        except TicketError as ex:
            raise CompanyTicketError(str(ex)) from ex

        validation = payload.get("validation")
        if autonomous and validation:
            try:
                self._autonomy(settings).configure(
                    record.ticket_id,
                    enabled=True,
                    auto_accept=auto_accept,
                    validation=validation,
                )
            except TicketError as ex:
                logger.warning(
                    "company tickets: cannot apply validation to '%s': %s",
                    record.ticket_id,
                    ex,
                )
        return self.detail(document, record.ticket_id)

    def comment(self, company: Any, ticket_id: str, comment: str) -> Dict[str, Any]:
        """Append a comment to the ticket conversation."""
        document = self._company(company)
        self._assert_can_manage(document)
        target = self._target(document, ticket_id)
        try:
            self._service(target["settings"]).update(
                ticket_id, comment=comment
            )
        except TicketError as ex:
            raise CompanyTicketError(str(ex)) from ex
        return self.detail(document, ticket_id)

    def configure(self, company: Any, ticket_id: str, **options: Any) -> Dict[str, Any]:
        """Configure autonomy of one ticket (profile, budgets, validation)."""
        document = self._company(company)
        self._assert_can_manage(document)
        target = self._target(document, ticket_id)
        try:
            self._autonomy(target["settings"]).configure(ticket_id, **options)
        except TicketError as ex:
            raise CompanyTicketError(str(ex)) from ex
        return self.detail(document, ticket_id)

    def queue(self, company: Any, ticket_id: str) -> Dict[str, Any]:
        """Enable autonomy and queue the ticket for the background worker."""
        document = self._company(company)
        self._assert_can_manage(document)
        target = self._target(document, ticket_id)
        try:
            self._autonomy(target["settings"]).queue(ticket_id)
        except TicketError as ex:
            raise CompanyTicketError(str(ex)) from ex
        return self.detail(document, ticket_id)

    def cancel(self, company: Any, ticket_id: str) -> Dict[str, Any]:
        """Cancel a queued/running run without closing the ticket."""
        document = self._company(company)
        self._assert_can_manage(document)
        target = self._target(document, ticket_id)
        try:
            self._autonomy(target["settings"]).cancel(ticket_id)
        except TicketError as ex:
            raise CompanyTicketError(str(ex)) from ex
        return self.detail(document, ticket_id)

    def run(self, company: Any, ticket_id: str, wait: bool = False) -> Dict[str, Any]:
        """
        Start an autonomous run now.

        With ``wait=False`` (the UI default) the run happens in a daemon thread
        and the call returns immediately; the workbench polls the detail endpoint
        for the live state.

        Args:
            company: Company document.
            ticket_id: Ticket id.
            wait: Block until the run finishes (tests/debug).

        Returns:
            The ticket detail (``wait=True``) or ``{queued: true}``.
        """
        import asyncio

        document = self._company(company)
        self._assert_can_manage(document)
        target = self._target(document, ticket_id)
        service = self._autonomy(target["settings"])
        if wait:
            try:
                asyncio.run(service.run(ticket_id, force=True))
            except TicketError as ex:
                raise CompanyTicketError(str(ex)) from ex
            return self.detail(document, ticket_id)

        def _run() -> None:
            try:
                asyncio.run(service.run(ticket_id, force=True))
            except Exception as ex:  # pragma: no cover - background logging
                logger.exception(
                    "company tickets: background run failed for '%s': %s",
                    ticket_id,
                    ex,
                )

        thread = threading.Thread(
            target=_run,
            name=f"CompanyTicket-{ticket_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {"ticket_id": ticket_id, "queued": True, "wait": False}

    def review(
        self,
        company: Any,
        ticket_id: str,
        decision: str,
        note: str = "",
        queue: bool = True,
    ) -> Dict[str, Any]:
        """
        Accept a delivery or send it back for another run.

        Args:
            company: Company document.
            ticket_id: Ticket id.
            decision: ``accept`` or ``changes``.
            note: Reviewer comment.
            queue: On ``changes``, queue another autonomous run.

        Returns:
            The updated ticket detail.
        """
        document = self._company(company)
        self._assert_can_manage(document)
        target = self._target(document, ticket_id)
        reviewer = self._username()
        wanted = str(decision or "").strip().lower()
        wants_changes = wanted in (
            "changes", "change", "reject", "rejected", "reopen", "send_back"
        )
        try:
            self._service(target["settings"]).review(
                ticket_id,
                decision=decision,
                note=note,
                reviewed_by=reviewer,
                queue=bool(queue and wants_changes),
            )
        except TicketError as ex:
            raise CompanyTicketError(str(ex)) from ex
        return self.detail(document, ticket_id)

    # ------------------------------------------------------------------
    # Compatibility helper
    # ------------------------------------------------------------------

    def summary(self, company: Any) -> Dict[str, Any]:
        """Return the counts the company home can show without the full list."""
        listing = self.list_tickets(company, include_closed=True)
        return {
            "projects": listing["projects"],
            "counts": listing["counts"],
            "worker_present": listing["worker_present"],
        }
