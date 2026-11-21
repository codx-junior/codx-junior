"""
Ticketing: cross-project requests expressed as chats.

A project can place a **ticket** on another project — one of its child projects
or a linked project with read/write permission — to request a bug fix, a feature
or a change. The ticket is a chat, so the two projects communicate in the same
conversation the target uses to work, and the target keeps full control of its
queue (review, accept, decline, schedule, assign profiles, ask a human...).

The package is split so importing it never drags the chat machinery along:

* :mod:`codx.junior.ticketing.model` — the models and the lifecycle constants.
  It depends on ``pydantic`` only, which is what lets
  :class:`codx.junior.db.Chat` type its ``ticket`` field with it.
* :mod:`codx.junior.ticketing.service` — :class:`TicketService`, the single
  implementation behind the LLM tools and the HTTP API. It imports
  ``ChatManager``, so consumers import it from the module directly::

      from codx.junior.ticketing.service import TicketService, TicketError

The tools live in :mod:`codx.junior.tools.ticket_tools` (``ticket_create``,
``ticket_update``, ``ticket_query``, ``ticket_abandon``) and the HTTP router in
:mod:`codx.junior.api.tickets`.

Made with ❤️ by codx-junior
"""

from .model import (
    ChatTicket,
    ChatTicketEvent,
    TicketRecord,
    TICKET_BOARD,
    TICKET_DEFAULT_KIND,
    TICKET_KINDS,
    TICKET_MODE,
    TICKET_SIDE_SOURCE,
    TICKET_SIDE_TARGET,
    TICKET_STATUSES,
    TICKET_STATUS_ABANDONED,
    TICKET_STATUS_ACCEPTED,
    TICKET_STATUS_DECLINED,
    TICKET_STATUS_DONE,
    TICKET_STATUS_OPEN,
    TICKET_TERMINAL_STATUSES,
    can_transition,
    column_for_status,
    normalize_kind,
    normalize_status,
    status_for_column,
    ticket_columns,
)

__all__ = [
    "ChatTicket",
    "ChatTicketEvent",
    "TicketRecord",
    "TICKET_BOARD",
    "TICKET_DEFAULT_KIND",
    "TICKET_KINDS",
    "TICKET_MODE",
    "TICKET_SIDE_SOURCE",
    "TICKET_SIDE_TARGET",
    "TICKET_STATUSES",
    "TICKET_STATUS_ABANDONED",
    "TICKET_STATUS_ACCEPTED",
    "TICKET_STATUS_DECLINED",
    "TICKET_STATUS_DONE",
    "TICKET_STATUS_OPEN",
    "TICKET_TERMINAL_STATUSES",
    "can_transition",
    "column_for_status",
    "normalize_kind",
    "normalize_status",
    "status_for_column",
    "ticket_columns",
]

# Made with ❤️ by codx-junior
