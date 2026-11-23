"""Company logo: the image is a file next to the document, not JSON payload.

The logo used to live inline in ``company.json`` as a data URL. It is now stored
as ``logo.<ext>`` inside the company folder and the document only keeps the URL
of ``GET /api/companies/{id}/logo``, so a multi-megabyte brand render never
inflates the document (nor every snapshot read).
"""

import base64
import asyncio
import io
import json
import os
import tempfile
from types import SimpleNamespace

import pytest

os.environ.setdefault("CODX_JUNIOR_COMPANIES_PATH", tempfile.mkdtemp())

from codx.junior.company.manager import (  # noqa: E402
    MAX_LOGO_FILE_BYTES,
    CompanyManager,
    CompanyValidationError,
)

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAF"
    "AAH/q842iQAAAABJRU5ErkJggg=="
)
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"></svg>'


@pytest.fixture()
def manager(monkeypatch):
    root = tempfile.mkdtemp()
    monkeypatch.setenv("CODX_JUNIOR_COMPANIES_PATH", root)
    manager = CompanyManager()
    manager.root = root
    return manager


@pytest.fixture()
def company(manager):
    return manager.create_company({"name": "Acme"}, actor="tester")


def logo_files(manager, company):
    """Return the ``logo.*`` files stored next to the company document."""
    folder = manager.store.company_dir(company.id)
    return sorted(
        name for name in os.listdir(folder) if os.path.splitext(name)[0] == "logo"
    )


def test_upload_stores_a_file_and_the_document_keeps_a_url(manager, company):
    saved = manager.save_logo(company.id, PNG_BYTES, filename="mark.png", actor="tester")

    assert saved.logo.startswith(f"/api/companies/{company.id}/logo?v=")
    assert logo_files(manager, company) == ["logo.png"]

    document = json.loads(
        open(manager.store.company_path(company.id), encoding="utf-8").read()
    )
    assert document["logo"] == saved.logo
    # The image itself is NOT in the document anymore.
    assert "base64" not in json.dumps(document["logo"])

    asset = manager.logo_asset(company.id)
    assert asset["kind"] == "file"
    assert asset["media_type"] == "image/png"
    assert open(asset["path"], "rb").read() == PNG_BYTES


def test_a_new_url_is_minted_on_every_upload(manager, company):
    """The `v` cache buster follows the bytes, otherwise the browser keeps the old mark."""
    png = manager.save_logo(company.id, PNG_BYTES)
    same = manager.save_logo(company.id, PNG_BYTES)
    svg = manager.save_logo(company.id, SVG_BYTES)

    # Same bytes, same URL: the browser cache already holds the right image.
    assert png.logo == same.logo
    # Different image, different URL: the shell picks the new mark up at once.
    assert png.logo != svg.logo
    assert svg.logo.startswith(f"/api/companies/{company.id}/logo?v=")


def test_replacing_the_logo_removes_the_previous_file(manager, company):
    manager.save_logo(company.id, PNG_BYTES)
    manager.save_logo(company.id, SVG_BYTES)

    assert logo_files(manager, company) == ["logo.svg"]
    assert manager.logo_asset(company.id)["media_type"] == "image/svg+xml"


def test_clear_removes_the_file_and_the_field(manager, company):
    manager.save_logo(company.id, PNG_BYTES)

    cleared = manager.clear_logo(company.id, actor="tester")

    assert cleared.logo == ""
    assert logo_files(manager, company) == []
    with pytest.raises(CompanyValidationError):
        manager.logo_asset(company.id)


def test_a_company_without_logo_has_no_asset(manager, company):
    with pytest.raises(CompanyValidationError):
        manager.logo_asset(company.id)


def test_empty_payload_is_rejected(manager, company):
    with pytest.raises(CompanyValidationError):
        manager.save_logo(company.id, b"")


def test_oversized_payload_is_rejected(manager, company):
    with pytest.raises(CompanyValidationError):
        manager.save_logo(company.id, PNG_BYTES + b"\x00" * MAX_LOGO_FILE_BYTES)


def test_non_image_payload_is_rejected(manager, company):
    """The type is sniffed: an HTML file must not become a servable `logo.png`."""
    with pytest.raises(CompanyValidationError):
        manager.save_logo(company.id, b"<!doctype html><script>alert(1)</script>")

    assert logo_files(manager, company) == []


def test_legacy_inline_data_url_is_still_served_as_bytes(manager, company):
    """Companies saved before the file endpoint keep a data URL in the document."""
    inline = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()
    manager.update_company(company.id, {"logo": inline}, actor="tester")

    asset = manager.logo_asset(company.id)

    assert asset["kind"] == "inline"
    assert asset["media_type"] == "image/png"
    assert asset["content"] == PNG_BYTES


def test_external_url_is_reported_as_a_redirect_target(manager, company):
    manager.update_company(
        company.id, {"logo": "https://cdn.example.com/logo.png"}, actor="tester"
    )

    asset = manager.logo_asset(company.id)

    assert asset == {"kind": "url", "url": "https://cdn.example.com/logo.png"}


def test_the_served_url_is_an_accepted_logo_value(manager, company):
    """The URL saved by `save_logo` must pass validation, so a later PUT keeps it."""
    saved = manager.save_logo(company.id, PNG_BYTES)

    updated = manager.update_company(
        company.id, {"logo": saved.logo, "description": "Studio"}, actor="tester"
    )

    assert updated.logo == saved.logo


def test_upload_endpoint_stores_the_file_and_serves_it(manager, company, monkeypatch):
    """The router functions work end to end (auth is solved with a fake user)."""
    from fastapi import UploadFile

    from codx.junior.api import company as company_api

    user = SimpleNamespace(username="tester", role="admin", companies=[])
    upload = UploadFile(filename="mark.png", file=io.BytesIO(PNG_BYTES))

    saved = asyncio.run(company_api.api_upload_company_logo(company.id, upload, user))

    assert saved.logo.startswith(f"/api/companies/{company.id}/logo?v=")

    served = company_api.api_company_logo(company.id)
    assert served.media_type == "image/png"
    assert open(served.path, "rb").read() == PNG_BYTES
