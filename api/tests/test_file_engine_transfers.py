"""
File manager operations tests: copy, move, compress and extract.

The explorer's copy & paste, duplicate, drag & drop, "Compress" and "Extract"
actions all land in the file engine, where the interesting rules live:

* a copied destination that already exists gets a numbered name
  (`notes (1).md`), while a move reports the collision,
* a folder cannot be copied/moved inside itself,
* archives are created and extracted in zip / tar / tar.gz / tar.bz2 flavours,
* an archive entry that escapes the destination folder ("zip slip") is rejected.
"""

import asyncio
import os
import tarfile
import zipfile

import pytest

from codx.junior.engine.file_engine import FileEngine


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class FakeSettings:
    """The subset of CODXJuniorSettings used by the file engine."""

    def __init__(self, path: str):
        self.abs_project_path = path
        self.codx_path = os.path.join(path, ".codx")
        self.project_name = "file-engine-test"
        self.project_id = "project-1"
        self.is_git_root = False

    def get_sub_projects(self):
        return []


class FakeSession:
    """Session stub: the file engine only needs its settings."""

    def __init__(self, path: str):
        self.settings = FakeSettings(path)


@pytest.fixture()
def engine(tmp_path):
    """File engine scoped to a temporary project folder."""
    return FileEngine(FakeSession(str(tmp_path)))


def write(path: str, content: str) -> str:
    """Create a text file (parent folders included) and return its path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)
    return path


def run(coroutine):
    """Run an engine coroutine (the tests do not depend on pytest-asyncio)."""
    return asyncio.run(coroutine)


# ---------------------------------------------------------------------------
# Copy / move
# ---------------------------------------------------------------------------


def test_copy_file_numbers_the_destination_instead_of_colliding(engine, tmp_path):
    source = write(str(tmp_path / "notes.md"), "hello")
    target = tmp_path / "backup"
    target.mkdir()

    first = run(engine.copy_files([source], str(target)))
    assert [item["destination"] for item in first["items"]] == [str(target / "notes.md")]
    assert first["conflicts"] == []
    assert (target / "notes.md").read_text() == "hello"

    # Pasting the same file again creates "notes (1).md" instead of colliding
    second = run(engine.copy_files([source], str(target)))
    assert [item["destination"] for item in second["items"]] == [str(target / "notes (1).md")]
    assert second["conflicts"] == []

    third = run(engine.copy_files([source], str(target)))
    assert [item["destination"] for item in third["items"]] == [str(target / "notes (2).md")]
    assert (target / "notes (1).md").read_text() == "hello"

    # A numbered name keeps counting instead of stacking suffixes
    numbered = run(engine.copy_files([str(target / "notes (1).md")], str(target)))
    assert [item["destination"] for item in numbered["items"]] == [str(target / "notes (3).md")]

    # A caller that wants the collision reported asks for it explicitly
    strict = run(engine.copy_files([source], str(target), auto_rename=False))
    assert strict["items"] == []
    assert strict["conflicts"][0]["destination"] == str(target / "notes.md")


def test_copy_into_the_same_folder_duplicates_with_a_numbered_name(engine, tmp_path):
    source = write(str(tmp_path / "report.md"), "content")

    result = run(engine.copy_files([source], str(tmp_path)))

    assert [item["destination"] for item in result["items"]] == [str(tmp_path / "report (1).md")]
    assert result["failed"] == []
    assert (tmp_path / "report (1).md").read_text() == "content"
    # The original is untouched
    assert (tmp_path / "report.md").read_text() == "content"


def test_copy_folder_into_the_same_folder_numbers_the_copy(engine, tmp_path):
    write(str(tmp_path / "dist" / "a.txt"), "a")

    result = run(engine.copy_files([str(tmp_path / "dist")], str(tmp_path)))

    assert [item["destination"] for item in result["items"]] == [str(tmp_path / "dist (1)")]
    assert (tmp_path / "dist (1)" / "a.txt").read_text() == "a"


def test_copy_file_overwrite_replaces_destination(engine, tmp_path):
    source = write(str(tmp_path / "a.txt"), "new")
    target = tmp_path / "out"
    write(str(target / "a.txt"), "old")

    result = run(engine.copy_files([source], str(target), overwrite=True))

    assert len(result["items"]) == 1
    assert (target / "a.txt").read_text() == "new"


def test_copy_folder_copies_the_whole_tree(engine, tmp_path):
    write(str(tmp_path / "src" / "nested" / "file.txt"), "content")
    target = tmp_path / "dst"
    target.mkdir()

    result = run(engine.copy_files([str(tmp_path / "src")], str(target)))

    assert len(result["items"]) == 1
    assert result["items"][0]["is_dir"] is True
    assert (target / "src" / "nested" / "file.txt").read_text() == "content"


def test_copy_folder_into_itself_is_rejected(engine, tmp_path):
    write(str(tmp_path / "src" / "file.txt"), "content")

    result = run(engine.copy_files([str(tmp_path / "src")], str(tmp_path / "src")))

    assert result["items"] == []
    assert "inside itself" in result["failed"][0]["error"]


def test_copy_with_name_suffix_duplicates_in_place(engine, tmp_path):
    source = write(str(tmp_path / "report.md"), "content")

    result = run(
        engine.copy_files([source], str(tmp_path), name_suffix=" (copy)")
    )

    assert len(result["items"]) == 1
    assert (tmp_path / "report (copy).md").read_text() == "content"


def test_move_files_moves_and_removes_the_source(engine, tmp_path):
    source = write(str(tmp_path / "move-me.txt"), "content")
    target = tmp_path / "dst"
    target.mkdir()

    result = run(engine.move_files([source], str(target)))

    assert result["action"] == "move"
    assert len(result["items"]) == 1
    assert not os.path.exists(source)
    assert (target / "move-me.txt").read_text() == "content"


def test_move_to_a_missing_folder_is_an_error(engine, tmp_path):
    source = write(str(tmp_path / "a.txt"), "content")

    with pytest.raises(ValueError):
        run(engine.move_files([source], str(tmp_path / "missing")))


# ---------------------------------------------------------------------------
# Compress
# ---------------------------------------------------------------------------


def test_create_zip_archive_and_extract_it_back(engine, tmp_path):
    write(str(tmp_path / "project" / "a.txt"), "a")
    write(str(tmp_path / "project" / "nested" / "b.txt"), "b")

    created = run(engine.create_archive([str(tmp_path / "project")], archive_format="zip"))
    archive_path = created["abs_archive_path"]

    assert created["format"] == ".zip"
    assert archive_path == str(tmp_path / "project.zip")
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
    assert "project/a.txt" in names
    assert "project/nested/b.txt" in names

    target = tmp_path / "extracted"
    result = run(engine.extract_archive(archive_path, target_dir=str(target)))

    assert result["extracted"] >= 3  # folders + files
    assert (target / "project" / "a.txt").read_text() == "a"
    assert (target / "project" / "nested" / "b.txt").read_text() == "b"


@pytest.mark.parametrize("archive_format", ["tar", "tar.gz", "tar.bz2", "tar.xz"])
def test_create_tar_flavours_and_extract_them_back(engine, tmp_path, archive_format):
    write(str(tmp_path / "data" / "file.txt"), "tar content")

    created = run(
        engine.create_archive([str(tmp_path / "data")], archive_format=archive_format)
    )
    archive_path = created["abs_archive_path"]

    assert archive_path.endswith(f".{archive_format}")
    with tarfile.open(archive_path, "r:*") as archive:
        assert "data/file.txt" in archive.getnames()

    target = tmp_path / "out"
    run(engine.extract_archive(archive_path, target_dir=str(target)))
    assert (target / "data" / "file.txt").read_text() == "tar content"


def test_create_archive_keeps_the_given_path_and_reports_conflicts(engine, tmp_path):
    write(str(tmp_path / "a.txt"), "a")
    archive_path = str(tmp_path / "custom-name.tar.gz")

    run(engine.create_archive([str(tmp_path / "a.txt")], archive_path=archive_path))
    assert os.path.isfile(archive_path)

    with pytest.raises(FileExistsError):
        run(engine.create_archive([str(tmp_path / "a.txt")], archive_path=archive_path))

    # Retrying with overwrite replaces the archive
    run(engine.create_archive([str(tmp_path / "a.txt")], archive_path=archive_path, overwrite=True))
    assert os.path.isfile(archive_path)


def test_create_archive_rejects_an_unsupported_format(engine, tmp_path):
    write(str(tmp_path / "a.txt"), "a")

    with pytest.raises(ValueError):
        run(engine.create_archive([str(tmp_path / "a.txt")], archive_format="rar"))


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------


def test_extract_reports_existing_files_without_overwrite(engine, tmp_path):
    write(str(tmp_path / "pack" / "a.txt"), "new")
    archive_path = str(tmp_path / "pack.zip")
    run(engine.create_archive([str(tmp_path / "pack")], archive_path=archive_path))

    target = tmp_path / "out"
    write(str(target / "pack" / "a.txt"), "old")

    with pytest.raises(FileExistsError):
        run(engine.extract_archive(archive_path, target_dir=str(target)))

    run(engine.extract_archive(archive_path, target_dir=str(target), overwrite=True))
    assert (target / "pack" / "a.txt").read_text() == "new"


def test_extract_refuses_entries_that_escape_the_destination(engine, tmp_path):
    archive_path = str(tmp_path / "evil.zip")
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escaped.txt", "pwned")

    with pytest.raises(ValueError):
        run(engine.extract_archive(archive_path, target_dir=str(tmp_path / "out")))

    assert not os.path.exists(str(tmp_path / "escaped.txt"))


def test_extract_rejects_unknown_formats(engine, tmp_path):
    plain = write(str(tmp_path / "file.rar"), "not really a rar")

    with pytest.raises(ValueError):
        run(engine.extract_archive(plain, target_dir=str(tmp_path / "out")))
