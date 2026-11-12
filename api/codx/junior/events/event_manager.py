from codx.junior.sio.session_channel import SessionChannel
from codx.junior.db import Chat, Message
from codx.junior.events.collection_events import (
    emit_collection_event,
)

class EventManager:
    def __init__(self,
            codx_path: str,
            channel: SessionChannel = None):
        if not channel:
            from codx.junior.sio.sio import sio
            channel = SessionChannel(sio=sio)

        self.codx_path = codx_path
        self.channel = channel

    def event_data(self, data: dict):
        data['codx_path'] = self.codx_path
        return data

    def send_notification(self, **kwargs):
        if not kwargs.get("type"):
            kwargs["type"] = "notification"
        self.channel.send_event('codx-junior', self.event_data(kwargs))

    def send_event(self, message: str):
        self.channel.send_event('codx-junior', self.event_data({ 'text': message, "type": "event" }))
        # self.log_info(f"Sending event {message}- SENT!")
    def send_knowled_event(self, **kwargs):
        self.channel.send_event('knowledge-event', self.event_data(kwargs))

    # Chat fields left OUT of a `chat-event` payload. The conversation is sent
    # one message at a time by `message_event`: shipping it again on every save
    # would exceed the socket frame limit for a long chat (a streaming turn
    # saves the chat every few seconds).
    CHAT_EVENT_EXCLUDED_FIELDS = {'messages'}

    @classmethod
    def chat_event_payload(cls, chat: Chat) -> dict:
        """
        Serialize *chat* for a `chat-event`.

        The event carries the CHANGED DATA, not just the chat reference: clients
        used to receive only ``{ id, owner_project_id }`` and answered every
        "changed" event with a full ``GET /chats`` request. During an AI turn
        that request was fired again for every persisted streaming chunk.

        The messages are excluded on purpose (see
        :attr:`CHAT_EVENT_EXCLUDED_FIELDS`); they travel on
        :meth:`message_event`.

        :param chat: The chat to serialize.
        :return: The chat document, without its conversation.
        """
        try:
            return chat.model_dump(exclude=cls.CHAT_EVENT_EXCLUDED_FIELDS)
        except AttributeError:
            # DEFENSIVE: not a pydantic model — keep the old, minimal payload.
            return {
                'id': getattr(chat, 'id', None),
                'owner_project_id': getattr(chat, 'owner_project_id', None),
            }

    def chat_event(self, chat: Chat, message: str = None, event_type: str = None):
        """
        Emit a chat change event carrying the chat document.

        Clients merge the payload in place and no longer request the chat for
        every event (see :meth:`chat_event_payload`).
        """
        self.channel.send_event('chat-event', self.event_data({
            'chat': self.chat_event_payload(chat),
            'text': message,
            'type': event_type
        }))
        # self.log_info(f"SEND MESSAGE {message}- SENT!")

    def message_event(self, chat: Chat, message: Message):
        self.channel.send_event('message-event', self.event_data({ 'chat': { 'id': chat.id, 'owner_project_id': chat.owner_project_id }, 'message': message.model_dump() }))
        # self.log_info(f"SEND MESSAGE {message.role} {message.doc_id}- SENT!")

    def message_removed_event(self, chat: Chat, message_doc_id: str):
        """
        Announce that a message was REMOVED from *chat*.

        A removal has no message content to stream, so ``save_chat``'s
        "changed" event alone cannot tell a client which message disappeared
        (that event never carries the conversation). Without this event another
        client would keep the deleted message on screen until the next full
        reload.

        :param chat: The chat the message was removed from.
        :param message_doc_id: ``doc_id`` of the removed message.
        """
        self.channel.send_event('message-event', self.event_data({
            'chat': { 'id': chat.id, 'owner_project_id': chat.owner_project_id },
            'message_removed_id': message_doc_id
        }))

    def chat_turn_event(self, payload: dict):
        """
        Emit the live state of the project's chat turns.

        The payload carries every active turn with its ``stalled`` flag (no
        progress for ``stale_seconds``) and ``alive`` (the lock owner is still
        running) plus the ``stale_seconds`` window itself. Clients subscribe to
        ``codx-junior-chat-turns`` and never poll: a message that is still
        "processing" but whose turn is stalled gets a re-run action.

        :param payload: ``{"turns": [...], "stale_seconds": n}``.
        """
        self.channel.send_event(
            'codx-junior-chat-turns',
            self.event_data(payload),
        )

    def orchestration_event(self, payload: dict):
        """
        Emit an orchestration-run event to connected clients.

        The payload contains ``run_id``, ``event``, ``status``, ``main_chat_id``,
        ``progress`` and event-specific data (step id, cost...). Clients can
        subscribe to ``codx-junior-orchestration`` for live run progress; the
        REST endpoints remain the source of truth.
        """
        self.channel.send_event(
            'codx-junior-orchestration',
            self.event_data(payload),
        )

    def board_manager_event(self, payload: dict):
        """
        Emit a board-manager event to connected clients.

        The payload carries ``board``, ``event`` (started/finished/error/...),
        ``message`` and event-specific data. The REST endpoints remain the
        source of truth; clients can subscribe to ``codx-junior-board-manager``
        for live status updates.
        """
        self.channel.send_event(
            'codx-junior-board-manager',
            self.event_data(payload),
        )

    def collection_event(self, collection: str, action: str, item: dict = None, **extra):
        """
        Announce that a cached collection changed (profiles, projects, teams...).

        Unlike the other events, this one is **broadcast to every client**: a
        collection is global state, so answering only the caller's session would
        leave the other clients stale. See
        :mod:`codx.junior.events.collection_events` for the payload shape.

        Args:
            collection: Collection name (``profiles``, ``projects``, ``teams``,
                ``boards``) — see ``COLLECTIONS`` in ``collection_events``.
            action: What happened (``created``, ``updated``, ``upserted``,
                ``deleted``, ``changed``).
            item: Identifying fields of the affected item.
            **extra: Additional payload fields.

        Returns:
            ``True`` when the event was queued (best effort, never raises).
        """
        return emit_collection_event(
            collection=collection,
            action=action,
            item=item,
            codx_path=self.codx_path,
            **extra,
        )

    def wiki_event(self, message: str):
        self.channel.send_event('wiki-event', self.event_data({ "info": message }))
        # self.log_info(f"SEND MESSAGE {message.role} {message.doc_id}- SENT!")
