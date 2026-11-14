"""
Offline tests for task archiving (``codx.junior.kanban.archive``).

Everything happens in a temporary ``tasks`` folder: no project, no Docker and no
database is needed. The ``ChatManager`` lifecycle test builds a real manager on
a throwaway ``codx`` folder, so the contract that matters to the product is
covered end to end:

    live task -> archived (invisible everywhere) -> restored (byte for byte)

Run with::

    PYTHONPATH=api python -m pytest api/tests/test_task_archive.py -q
"""

import io
import json
import os
import tarfile
import uuid

import pytest

from codx.junior.kanban.archive import TaskArchive, chat_id_from_filename
from codx.junior.kanban.model import ARCHIVE_DIR, is_store_file


CHAT_ID = "6f1c2b90-6d0a-4a2b-9c11-2f8d7e5a3b10"


def _write_chat(tasks_path: str, chat_id: str = CHAT_ID, name: str = "fix-login") -> str:
    """Create a chat file the way ``ChatManager.get_chat_file`` lays it out."""
    folder = os.path.join(tasks_path, "Bugs", "To do")
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, f"{name}.{chat_id}.json")
    payload = {
        "id": chat_id,
        "name": name,
        "board": "Bugs",
        "column": "To do",
        "status": "done",
        "messages": [{"role": "user", "content": "hello " * 200}],
    }
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2))
    return file_path


def test_chat_id_from_filename():
    assert chat_id_from_filename(f"fix-login.{CHAT_ID}.json") == CHAT_ID
    assert chat_id_from_filename(f"fix-login.{CHAT_ID}.yaml") == CHAT_ID
    assert chat_id_from_filename("kanban.json") is None


def test_archive_folder_is_storage_not_a_chat(tmp_path):
    archive_index = os.path.join(str(tmp_path), ARCHIVE_DIR, "index.json")
    payload = os.path.join(str(tmp_path), ARCHIVE_DIR, f"{CHAT_ID}.tar.gz")
    assert is_store_file(archive_index) is True
    assert is_store_file(payload) is True
    assert is_store_file(os.path.join(str(tmp_path), "Bugs", "To do", f"x.{CHAT_ID}.json")) is False


def test_archive_compresses_and_removes_the_live_file(tmp_path):
    tasks_path = str(tmp_path)
    chat_file = _write_chat(tasks_path)
    original_size = os.path.getsize(chat_file)

    archive = TaskArchive(tasks_path)
    entry = archive.archive(
        chat_file=chat_file,
        metadata={"name": "fix-login", "board": "Bugs", "column": "To do"},
        archived_by="tester",
    )

    assert os.path.isfile(chat_file) is False
    assert os.path.isfile(archive.archive_file(CHAT_ID)) is True
    assert entry["chat_id"] == CHAT_ID
    assert entry["archived_by"] == "tester"
    assert entry["original_size"] == original_size
    assert entry["size"] < original_size  # compressed, that is the whole point
    assert archive.is_archived(CHAT_ID) is True
    assert archive.get(CHAT_ID)["board"] == "Bugs"


def test_archive_is_idempotent(tmp_path):
    tasks_path = str(tmp_path)
    chat_file = _write_chat(tasks_path)
    archive = TaskArchive(tasks_path)

    first = archive.archive(chat_file=chat_file)
    second = archive.archive(chat_file=chat_file)

    assert first["size"] == second["size"]
    assert len(archive.entries()) == 1


def test_archive_missing_file_raises(tmp_path):
    archive = TaskArchive(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        archive.archive(chat_file=os.path.join(str(tmp_path), f"nope.{CHAT_ID}.json"))


def test_restore_returns_the_original_bytes(tmp_path):
    tasks_path = str(tmp_path)
    chat_file = _write_chat(tasks_path)
    with open(chat_file, "rb") as handle:
        original = handle.read()

    archive = TaskArchive(tasks_path)
    archive.archive(chat_file=chat_file)
    restored = archive.restore(CHAT_ID, target_dir=os.path.dirname(chat_file))

    assert restored == chat_file
    with open(restored, "rb") as handle:
        assert handle.read() == original
    assert archive.is_archived(CHAT_ID) is False
    assert os.path.isfile(archive.archive_file(CHAT_ID)) is False


def test_restore_unknown_task_raises(tmp_path):
    archive = TaskArchive(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        archive.restore(CHAT_ID, target_dir=str(tmp_path))


def test_restore_refuses_to_escape_the_target_folder(tmp_path):
    tasks_path = str(tmp_path)
    archive = TaskArchive(tasks_path)
    os.makedirs(archive.archive_path, exist_ok=True)

    # A crafted payload whose member name points outside the tasks tree.
    malicious = os.path.join(archive.archive_path, f"{CHAT_ID}.tar.gz")
    data = b'{"id": "x"}'
    with tarfile.open(malicious, "w:gz") as tar:
        info = tarfile.TarInfo(name="../../evil.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    archive._write_index({"version": 1, "tasks": {CHAT_ID: {"chat_id": CHAT_ID}}})

    with pytest.raises(ValueError):
        archive.restore(CHAT_ID, target_dir=os.path.join(tasks_path, "Bugs", "To do"))
    assert os.path.isfile(os.path.join(str(tmp_path), "evil.json")) is False


def test_forget_removes_payload_and_entry(tmp_path):
    tasks_path = str(tmp_path)
    chat_file = _write_chat(tasks_path)
    archive = TaskArchive(tasks_path)
    archive.archive(chat_file=chat_file)
    payload = archive.archive_file(CHAT_ID)

    assert archive.forget(CHAT_ID) is True
    assert os.path.isfile(payload) is False
    assert archive.forget(CHAT_ID) is False


def test_stats_reports_the_saved_space(tmp_path):
    tasks_path = str(tmp_path)
    chat_file = _write_chat(tasks_path)
    archive = TaskArchive(tasks_path)
    archive.archive(chat_file=chat_file)

    stats = archive.stats()
    assert stats["count"] == 1
    assert stats["archived_bytes"] > 0
    assert stats["saved_bytes"] > 0
    assert stats["ratio"] > 1


def _manager(codx_path: str):
    """Build a ChatManager on a throwaway codx folder (no project needed)."""
    from types import SimpleNamespace
    from codx.junior.chat_manager import ChatManager

    settings = SimpleNamespace(
        codx_path=codx_path,
        project_id="p-test",
        project_name="archive-test",
    )
    return ChatManager(
        settings=settings,
        event_manager=SimpleNamespace(chat_event=lambda **kwargs: None),
    )


def test_chat_manager_hides_archived_tasks_unless_requested(tmp_path):
    from codx.junior.db import Chat, Message

    manager = _manager(str(tmp_path))
    task = manager.save_chat(
        chat=Chat(
            name="archivable task",
            board="kanban",
            column="tasks",
            messages=[Message(role="user", content="please archive me")],
        )
    )
    assert manager.find_chat_path(task.id) is not None
    assert task.id in [chat.id for chat in manager.list_chats()]

    entry = manager.archive_chat(task.id, user="tester")
    assert entry["chat_id"] == task.id

    # Invisible everywhere: no file, and every listing hides it by default.
    assert manager.find_chat_path(task.id) is None
    assert manager.find_by_id(task.id) is None
    assert task.id not in [chat.id for chat in manager.list_chats()]
    assert task.id not in [chat.id for chat in manager.get_recent_chats()["chats"]]
    assert manager.task_archive.is_archived(task.id) is True

    # ... unless the caller explicitly asks for the archived ones.
    listed = manager.list_chats(include_archived=True)
    archived = [chat for chat in listed if chat.id == task.id]
    assert archived and archived[0].archived is True
    assert archived[0].messages == []

    # Restoring brings back the task and its conversation.
    restored = manager.restore_chat(task.id, user="tester")
    assert restored.id == task.id
    assert [message.content for message in restored.messages] == ["please archive me"]
    assert manager.find_chat_path(task.id) is not None
    assert task.id in [chat.id for chat in manager.list_chats()]
    assert manager.task_archive.is_archived(task.id) is False


def test_chat_manager_archive_unknown_task_raises(tmp_path):
    manager = _manager(str(tmp_path))
    with pytest.raises(ValueError):
        manager.archive_chat(str(uuid.uuid4()))
