"""Benchmark the chat storage hot paths (regression guard for PRs).

Measures the operations the UI feels:

* ``load``  — ``find_by_id`` (the ``GET /api/chats?id=`` path),
* ``save``  — ``save_chat`` with no message change,
* ``update`` — ``update_message`` of one message (streamed chunk / tool event),
* ``batch``  — ``apply_messages`` for several messages at once.

Usage::

    PYTHONPATH=api python api/scripts/chat_store_benchmark.py
    PYTHONPATH=api python api/scripts/chat_store_benchmark.py --source <chat.json>

With no ``--source`` a synthetic chat is generated: ``--messages`` messages of
``--size-kb`` KB each (defaults: 30 messages x 64 KB ~= 2 MB, representative of
a long task card). The first (cold) save is reported separately because it is
the only one that has to sanitise the whole conversation: every later write
reuses the per-message sanitisation.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import tempfile
import time
from types import SimpleNamespace

from codx.junior.chat_manager import ChatManager
from codx.junior.db import Chat, Message


class _Events:
    """No-op event manager: the benchmark measures storage, not sockets."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _manager(root: str) -> ChatManager:
    codx = os.path.join(root, ".codx")
    os.makedirs(os.path.join(codx, "tasks", "kanban", "tasks"), exist_ok=True)
    return ChatManager(
        settings=SimpleNamespace(
            codx_path=codx, project_id="bench", project_name="bench"
        ),
        event_manager=_Events(),
    )


def _synthetic_chat(messages: int, size_kb: int) -> Chat:
    line = "the tool printed this line and it is not a secret\n"
    body = (line * ((size_kb * 1024) // len(line) + 1))[: size_kb * 1024]
    now = "2026-01-01T00:00:00"
    return Chat(
        id="bench-chat",
        name="bench chat",
        board="kanban",
        column="tasks",
        created_at=now,
        updated_at=now,
        messages=[
            Message(
                doc_id=f"m{index}",
                role="assistant" if index % 2 else "user",
                user="bench",
                content=f"message {index}\n{body}",
                created_at=now,
                updated_at=now,
            )
            for index in range(messages)
        ],
    )


def _load_source_chat(path: str) -> Chat:
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    document["id"] = "bench-chat"
    document["name"] = "bench chat"
    document["board"] = "kanban"
    document["column"] = "tasks"
    return Chat(**document)


def _timed(func, repeats: int = 3):
    samples = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = func()
        samples.append((time.perf_counter() - started) * 1000.0)
    return result, statistics.median(samples), max(samples)


def run(args) -> int:
    root = tempfile.mkdtemp(prefix="chat-store-bench-")
    try:
        manager = _manager(root)
        chat = (
            _load_source_chat(args.source)
            if args.source
            else _synthetic_chat(args.messages, args.size_kb)
        )

        # Prime the disposable index (schema creation + empty build). This is a
        # ONE-TIME per project cost and would otherwise pollute the cold save.
        started = time.perf_counter()
        manager._chat_index_store()
        manager._start_chat_index_build()
        while not manager._chat_index_ready():
            time.sleep(0.02)
        index_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        manager.save_chat(chat=chat)
        cold = (time.perf_counter() - started) * 1000.0
        size_mb = os.path.getsize(chat.file_path) / 1e6

        def _load():
            return manager.find_by_id("bench-chat")

        def _save():
            return manager.save_chat(chat=_load())

        def _update():
            loaded = _load()
            edited = loaded.messages[len(loaded.messages) // 2]
            edited.content += " (edited)"
            return manager.update_message(chat=loaded, message=edited)

        def _batch():
            loaded = _load()
            for message in loaded.messages[:3]:
                message.content += " (batch)"
            return manager.apply_messages(
                chat=loaded, messages=list(loaded.messages[:3])
            )

        loaded, load_ms, load_max = _timed(_load)
        _save_result, save_ms, save_max = _timed(_save)
        _update_result, update_ms, update_max = _timed(_update)
        _batch_result, batch_ms, batch_max = _timed(_batch)

        print(
            f"chat: {len(chat.messages)} messages, {size_mb:.2f} MB on disk"
        )
        print(f"index init (one-time):          {index_ms:8.1f} ms")
        print(f"cold save (full sanitisation):  {cold:8.1f} ms")
        print(f"load:                           {load_ms:8.1f} ms (max {load_max:.1f})")
        print(f"save (nothing changed):         {save_ms:8.1f} ms (max {save_max:.1f})")
        print(f"update 1 message:               {update_ms:8.1f} ms (max {update_max:.1f})")
        print(f"batch update 3 messages:        {batch_ms:8.1f} ms (max {batch_max:.1f})")
        print(f"messages in loaded chat:        {len(loaded.messages)}")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="Seed from a real chat JSON file")
    parser.add_argument("--messages", type=int, default=30)
    parser.add_argument("--size-kb", type=int, default=64)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
