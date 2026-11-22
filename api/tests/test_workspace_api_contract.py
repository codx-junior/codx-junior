"""
Contract tests for the machine-client workspace API (change request UPR-2…UPR-7).

The hosted platform talks to a workspace with a bearer token and nothing else.
These tests pin the pieces that used to be missing or wrong:

* UPR-2 — the scoped ``CODX_JUNIOR_WORKSPACE_TOKEN`` authenticates a request.
* UPR-3 — a protected write answers ``401`` instead of running anonymously.
* UPR-4 — ``Authorization`` and ``authentication`` are both accepted, in that
  order of precedence.
* UPR-5 — ``codx_path`` accepts the project root and a missing path is a ``422``
  with an actionable body, never a ``500``.
* UPR-6 — email is a login identifier and bad credentials answer ``401``.
* UPR-7 — ``/api/health`` publishes the capability block.

Run with::

    cd api && pytest tests/test_workspace_api_contract.py -q
"""

import asyncio
import os
from types import SimpleNamespace

# Importing the FastAPI application requires the static folder to be configured;
# the tests never serve assets, they only call the health handler.
os.environ.setdefault("CODX_JUNIOR_STATIC_FOLDER", "/tmp/codx-junior-tests-static")

import bcrypt
import pytest
from fastapi import HTTPException

# Imported at collection time, BEFORE other tests install their lightweight
# ``codx.junior.api`` package stub (see ``_import_dsh_router``), so the real
# package is the one in ``sys.modules`` for the whole session.
import codx.junior.app as app_module

from codx.junior.model.user import CodxUser, CodxUserLogin
from codx.junior.security import user_management as user_management
from codx.junior.security import workspace_api


TOKEN = "workspace-token-0123456789abcdef"
PASSWORD = "correct-horse"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRequest:
    """The narrow request surface the auth dependency reads."""

    def __init__(self, headers=None):
        self.headers = dict(headers or {})
        self.state = SimpleNamespace()
        self.url = SimpleNamespace(path="/api/test")
        self.query_params = {}


class FakeResponse:
    def set_cookie(self, **kwargs):  # pragma: no cover - shape only
        self.cookie = kwargs


def _hashed_password(password: str = PASSWORD) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _real_manager(users=None, logins=None) -> user_management.UserSecurityManager:
    """A UserSecurityManager over an in-memory catalogue (no disk I/O)."""
    manager = user_management.UserSecurityManager.__new__(
        user_management.UserSecurityManager
    )
    manager.global_settings = SimpleNamespace(
        users=list(users if users is not None else [CodxUser(username="admin", role="admin")]),
        user_logins=list(logins or []),
    )
    # Never touch the signing secret / settings files from a test.
    manager.get_user_token = lambda user: "test.jwt.token"
    manager.get_user_from_token = lambda token: None
    manager.save_settings = lambda: None
    return manager


def _manager_factory(manager):
    """Patch target for ``UserSecurityManager`` (a class in production)."""
    return lambda *args, **kwargs: manager


def _login(body, monkeypatch, users_api, manager):
    monkeypatch.setattr(users_api, "UserSecurityManager", _manager_factory(manager))
    monkeypatch.setattr(
        users_api,
        "_ensure_default_company_membership",
        lambda user=None, **kwargs: user,
    )

    class Request:
        async def json(self):
            return body

    return asyncio.run(users_api.user_login(Request(), FakeResponse()))


def _detail(exc: HTTPException):
    return exc.value.detail if hasattr(exc, "value") else exc.detail


# ---------------------------------------------------------------------------
# UPR-4 — auth headers
# ---------------------------------------------------------------------------


def test_bearer_header_parsing_accepts_the_standard_and_legacy_forms():
    assert workspace_api.normalize_token("Bearer abc") == "abc"
    assert workspace_api.normalize_token("bearer abc") == "abc"
    assert workspace_api.normalize_token("abc") == "abc"
    # The bundled client sends this before a login.
    assert workspace_api.normalize_token("Bearer undefined") == ""
    assert workspace_api.normalize_token("") == ""


def test_authorization_is_accepted():
    token, header = workspace_api.extract_auth_token(
        FakeRequest({"authorization": f"Bearer {TOKEN}"})
    )
    assert (token, header) == (TOKEN, "authorization")


def test_authentication_remains_a_compatibility_alias():
    token, header = workspace_api.extract_auth_token(
        FakeRequest({"authentication": f"Bearer {TOKEN}"})
    )
    assert (token, header) == (TOKEN, "authentication")


def test_authorization_wins_when_both_headers_are_present():
    token, header = workspace_api.extract_auth_token(
        FakeRequest(
            {
                "authorization": "Bearer from-authorization",
                "authentication": "Bearer from-authentication",
            }
        )
    )
    assert (token, header) == ("from-authorization", "authorization")


def test_a_placeholder_authorization_falls_through_to_the_alias():
    token, header = workspace_api.extract_auth_token(
        FakeRequest(
            {
                "authorization": "Bearer undefined",
                "authentication": f"Bearer {TOKEN}",
            }
        )
    )
    assert (token, header) == (TOKEN, "authentication")


# ---------------------------------------------------------------------------
# UPR-2 / UPR-3 — the scoped token and the 401 dependency
# ---------------------------------------------------------------------------


def test_no_credential_is_a_401_with_a_stable_code():
    with pytest.raises(HTTPException) as exc:
        user_management.get_authenticated_user(FakeRequest())

    assert exc.value.status_code == 401
    assert _detail(exc)["code"] == workspace_api.AUTH_ERROR_MISSING
    assert exc.value.headers["WWW-Authenticate"].startswith("Bearer")


def test_an_invalid_token_is_a_401_and_never_a_500(monkeypatch):
    monkeypatch.setattr(
        user_management,
        "UserSecurityManager",
        _manager_factory(_real_manager()),
    )

    with pytest.raises(HTTPException) as exc:
        user_management.get_authenticated_user(
            FakeRequest({"authorization": "Bearer not-a-jwt"})
        )

    assert exc.value.status_code == 401
    assert _detail(exc)["code"] == workspace_api.AUTH_ERROR_INVALID


def test_the_scoped_workspace_token_authenticates_as_the_owner(monkeypatch):
    monkeypatch.setenv(workspace_api.WORKSPACE_TOKEN_ENV, TOKEN)
    manager = _real_manager(
        users=[
            CodxUser(username="admin", role="admin", email="owner@example.com"),
            CodxUser(username="member", role="user"),
        ]
    )
    monkeypatch.setattr(
        user_management, "UserSecurityManager", _manager_factory(manager)
    )

    user = user_management.get_authenticated_user(
        FakeRequest({"authorization": f"Bearer {TOKEN}"})
    )

    assert user.username == "admin"
    assert user.role == "admin"
    # The workspace token is the credential of the request: the owner's
    # reusable session JWT is never handed back.
    assert user.token == ""


def test_the_scoped_token_falls_back_to_a_service_principal(monkeypatch):
    monkeypatch.setenv(workspace_api.WORKSPACE_TOKEN_ENV, TOKEN)
    manager = _real_manager(users=[])
    monkeypatch.setattr(
        user_management, "UserSecurityManager", _manager_factory(manager)
    )

    user = user_management.get_authenticated_user(
        FakeRequest({"authorization": f"Bearer {TOKEN}"})
    )

    assert user.username == workspace_api.WORKSPACE_SERVICE_USERNAME
    assert user.role == "admin"


def test_the_scoped_token_never_borrows_a_regular_users_identity(monkeypatch):
    """Without an owner/admin account the token gets the service principal."""
    monkeypatch.setenv(workspace_api.WORKSPACE_TOKEN_ENV, TOKEN)
    manager = _real_manager(users=[CodxUser(username="member", role="user")])
    monkeypatch.setattr(
        user_management, "UserSecurityManager", _manager_factory(manager)
    )

    user = user_management.get_authenticated_user(
        FakeRequest({"authorization": f"Bearer {TOKEN}"})
    )

    assert user.username == workspace_api.WORKSPACE_SERVICE_USERNAME
    assert user.role == "admin"


def test_rotating_the_scoped_token_invalidates_the_old_value(monkeypatch):
    monkeypatch.setenv(workspace_api.WORKSPACE_TOKEN_ENV, "new-token-value")
    manager = _real_manager()
    monkeypatch.setattr(
        user_management, "UserSecurityManager", _manager_factory(manager)
    )

    assert workspace_api.is_workspace_token("new-token-value") is True
    assert workspace_api.is_workspace_token(TOKEN) is False

    with pytest.raises(HTTPException) as exc:
        user_management.get_authenticated_user(
            FakeRequest({"authorization": f"Bearer {TOKEN}"})
        )
    assert exc.value.status_code == 401


def test_the_optional_dependency_still_tolerates_an_anonymous_request():
    assert user_management.get_optional_authenticated_user(FakeRequest()) is None


def test_task_creation_refuses_an_anonymous_write():
    """UPR-3: ``POST /api/chats/tasks`` must answer 401 without a credential."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from codx.junior.api import chat as chat_api

    app = FastAPI()
    app.include_router(chat_api.router, prefix="/api")
    response = TestClient(app).post(
        "/api/chats/tasks?codx_path=/tmp/whatever/.codx",
        json={"name": "anonymous", "description": "must be refused"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == workspace_api.AUTH_ERROR_MISSING


def test_refresh_user_info_is_401_and_not_500_without_a_credential():
    """UPR-3/UPR-6: the handler used to read ``None.username`` and answer 500."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from codx.junior.api import users as users_api

    app = FastAPI()
    app.include_router(users_api.router, prefix="/api")
    response = TestClient(app).get("/api/users/me/refresh")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == workspace_api.AUTH_ERROR_MISSING


def _task_app():
    """A minimal app where the request session is a task-creating fake."""
    from fastapi import FastAPI, Request

    from codx.junior.api import chat as chat_api
    from codx.junior.db import Chat

    created = {}

    class FakeTaskManager:
        def create_task(self, **kwargs):
            created.update(kwargs)
            return Chat(
                id="task-1",
                name=kwargs["name"],
                board=kwargs.get("board") or "kanban",
                column=kwargs.get("column") or "tasks",
            )

    class FakeSession:
        def get_task_manager(self):
            return FakeTaskManager()

    app = FastAPI()
    app.include_router(chat_api.router, prefix="/api")

    @app.middleware("http")
    async def attach_session(request: Request, call_next):
        request.state.codx_junior_session = FakeSession()
        return await call_next(request)

    return app, created


def _workspace_manager(monkeypatch):
    monkeypatch.setenv(workspace_api.WORKSPACE_TOKEN_ENV, TOKEN)
    monkeypatch.setattr(
        user_management,
        "UserSecurityManager",
        _manager_factory(
            _real_manager(users=[CodxUser(username="owner", role="admin")])
        ),
    )


@pytest.mark.parametrize("header", ["authorization", "authentication"])
def test_a_scoped_token_still_creates_a_task(monkeypatch, header):
    """UPR-2/UPR-3: the scoped credential keeps working on both header forms."""
    from fastapi.testclient import TestClient

    _workspace_manager(monkeypatch)
    app, created = _task_app()
    response = TestClient(app).post(
        "/api/chats/tasks",
        json={"name": "First task", "description": "from the hosted console"},
        headers={header: f"Bearer {TOKEN}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == "task-1"
    # The task is attributed to the principal the scoped token acts as.
    assert created["user"] == "owner"


def test_a_scoped_token_refreshes_the_session(monkeypatch):
    """UPR-2 acceptance: ``GET /api/users/me/refresh`` answers 200 with the token."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from codx.junior.api import users as users_api

    _workspace_manager(monkeypatch)
    app = FastAPI()
    app.include_router(users_api.router, prefix="/api")
    response = TestClient(app).get(
        "/api/users/me/refresh",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["username"] == "owner"
    # The scoped token is the request credential: never leak the owner's JWT or
    # API key through the profile response.
    assert payload["token"] == ""
    assert payload["api_key"] == ""


# ---------------------------------------------------------------------------
# UPR-6 — email login and 401 for bad credentials
# ---------------------------------------------------------------------------


def test_login_resolution_matches_username_first_then_email_case_insensitively(
    monkeypatch,
):
    manager = _real_manager(
        users=[
            CodxUser(username="owner", email="Owner@Example.com", role="admin"),
        ],
        logins=[
            CodxUserLogin(username="owner", email="owner@example.com", password="x"),
        ],
    )

    assert manager.resolve_login("owner") == ("owner", "username")
    assert manager.resolve_login("OWNER@example.com") == ("owner", "email")
    assert manager.resolve_login("nobody@example.com") == (None, "")


def test_login_with_email_returns_the_session_token(monkeypatch):
    from codx.junior.api import users as users_api

    manager = _real_manager(
        users=[CodxUser(username="owner", email="owner@example.com", role="admin")],
        logins=[
            CodxUserLogin(
                username="owner",
                email="owner@example.com",
                password=_hashed_password(),
            )
        ],
    )

    user = _login(
        {"username": "owner@example.com", "password": PASSWORD},
        monkeypatch,
        users_api,
        manager,
    )

    assert user.username == "owner"
    assert user.token == "test.jwt.token"


def test_wrong_password_and_unknown_email_share_the_same_401(monkeypatch):
    from codx.junior.api import users as users_api

    manager = _real_manager(
        users=[CodxUser(username="owner", email="owner@example.com", role="admin")],
        logins=[
            CodxUserLogin(
                username="owner",
                email="owner@example.com",
                password=_hashed_password(),
            )
        ],
    )

    with pytest.raises(HTTPException) as wrong_password:
        _login(
            {"username": "owner@example.com", "password": "wrong-password"},
            monkeypatch,
            users_api,
            manager,
        )
    with pytest.raises(HTTPException) as unknown_email:
        _login(
            {"username": "nobody@example.com", "password": PASSWORD},
            monkeypatch,
            users_api,
            manager,
        )

    assert wrong_password.value.status_code == 401
    assert unknown_email.value.status_code == 401
    assert _detail(wrong_password) == _detail(unknown_email)
    assert _detail(wrong_password)["code"] == workspace_api.AUTH_ERROR_INVALID


def test_email_login_never_creates_a_missing_credential(monkeypatch):
    """A wrong password must not become the account's first password."""
    from codx.junior.api import users as users_api

    manager = _real_manager(
        users=[CodxUser(username="owner", email="owner@example.com", role="admin")],
        logins=[],
    )

    with pytest.raises(HTTPException) as exc:
        _login(
            {"username": "owner@example.com", "password": "anything"},
            monkeypatch,
            users_api,
            manager,
        )

    assert exc.value.status_code == 401
    assert manager.global_settings.user_logins == []


def test_an_ambiguous_email_is_rejected_explicitly(monkeypatch):
    from codx.junior.api import users as users_api

    manager = _real_manager(
        users=[
            CodxUser(username="owner", email="shared@example.com", role="admin"),
            CodxUser(username="owner2", email="shared@example.com", role="user"),
        ],
        logins=[
            CodxUserLogin(
                username="owner", email="shared@example.com", password=_hashed_password()
            )
        ],
    )

    with pytest.raises(HTTPException) as exc:
        _login(
            {"username": "shared@example.com", "password": PASSWORD},
            monkeypatch,
            users_api,
            manager,
        )

    assert exc.value.status_code == 409
    assert _detail(exc)["code"] == "ambiguous_login"


# ---------------------------------------------------------------------------
# UPR-5 — codx_path resolution
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "my-project"
    codx = root / ".codx"
    codx.mkdir(parents=True)
    (codx / "project.json").write_text("{}", encoding="utf-8")
    return root, codx.resolve()


def test_codx_path_accepts_the_project_root(project):
    root, codx = project

    assert workspace_api.resolve_codx_path(root) == str(codx)


def test_codx_path_accepts_the_dot_codx_folder_and_the_project_file(project):
    root, codx = project

    assert workspace_api.resolve_codx_path(codx) == str(codx)
    assert workspace_api.resolve_codx_path(codx / "project.json") == str(codx)


def test_an_unknown_codx_path_is_an_actionable_422():
    resolved, error = workspace_api.resolve_workspace_codx_path("/does/not/exist")

    assert resolved is None
    assert error["code"] == workspace_api.CODEX_PATH_NOT_FOUND
    assert workspace_api.PROJECTS_ENDPOINT in error["message"]


def test_a_missing_codx_path_uses_the_default_project(monkeypatch, project):
    _, codx = project
    projects = {
        "p1": SimpleNamespace(codx_path=str(codx), project_name="my-project")
    }

    resolved, error = workspace_api.resolve_workspace_codx_path(
        None, projects=projects
    )

    assert (resolved, error) == (str(codx), None)


def test_a_missing_codx_path_with_no_project_is_codx_path_required(monkeypatch):
    monkeypatch.setattr(workspace_api, "default_codx_path", lambda **kwargs: None)

    resolved, error = workspace_api.resolve_workspace_codx_path(None)

    assert resolved is None
    assert error["code"] == workspace_api.CODEX_PATH_REQUIRED


def test_default_project_selection_is_deterministic(project):
    _, codx = project
    projects = {
        "b": SimpleNamespace(codx_path=str(codx), project_name="b"),
        "a": SimpleNamespace(codx_path=str(codx), project_name="a"),
    }

    assert workspace_api.default_codx_path(projects=projects) == str(codx)


def test_require_project_session_never_raises_a_500(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "resolve_workspace_codx_path",
        lambda *args, **kwargs: (None, dict(workspace_api.CODEX_PATH_REQUIRED_ERROR)),
    )
    request = FakeRequest()

    with pytest.raises(HTTPException) as exc:
        app_module.require_project_session(request)

    assert exc.value.status_code == 422
    assert _detail(exc)["code"] == workspace_api.CODEX_PATH_REQUIRED


def test_require_project_session_attaches_the_default_project(monkeypatch):
    sentinel = SimpleNamespace(name="session")
    monkeypatch.setattr(
        app_module,
        "resolve_workspace_codx_path",
        lambda *args, **kwargs: ("/project/.codx", None),
    )
    monkeypatch.setattr(
        app_module, "get_codx_junior_session", lambda request, path: sentinel
    )
    request = FakeRequest()

    assert app_module.require_project_session(request) is sentinel
    assert request.state.codx_junior_session is sentinel


def test_a_task_without_codx_path_is_never_a_500(monkeypatch):
    """UPR-5: the task endpoint answers 422 when no project can be resolved."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from codx.junior.api import chat as chat_api

    monkeypatch.setattr(
        app_module,
        "resolve_workspace_codx_path",
        lambda *args, **kwargs: (None, dict(workspace_api.CODEX_PATH_REQUIRED_ERROR)),
    )
    app = FastAPI()
    app.include_router(chat_api.router, prefix="/api")
    app.dependency_overrides[chat_api.get_authenticated_user] = lambda: CodxUser(
        username="admin", role="admin"
    )

    response = TestClient(app).post(
        "/api/chats/tasks",
        json={"name": "first task", "description": "no codx_path"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == workspace_api.CODEX_PATH_REQUIRED


# ---------------------------------------------------------------------------
# UPR-7 — capability block
# ---------------------------------------------------------------------------


def test_capabilities_describe_the_supported_contract(monkeypatch):
    monkeypatch.setenv(workspace_api.WORKSPACE_TOKEN_ENV, TOKEN)
    monkeypatch.setattr(
        workspace_api, "default_codx_path", lambda **kwargs: "/projects/app/.codx"
    )

    capabilities = workspace_api.workspace_api_capabilities()

    assert capabilities["auth_headers"] == ["authorization", "authentication"]
    assert capabilities["auth_header_precedence"] == "authorization"
    assert capabilities["scoped_token"] is True
    assert capabilities["scoped_token_configured"] is True
    assert capabilities["login_by_email"] is True
    assert capabilities["tasks_endpoint"] == "/api/chats/tasks"
    assert capabilities["default_codx_path"] == "/projects/app/.codx"


def test_health_publishes_the_capability_block(monkeypatch):
    monkeypatch.setattr(
        app_module, "workspace_api_capabilities", lambda: {"contract_version": 1}
    )

    payload = app_module.api_health_check()

    assert payload["status"] == "ok"
    assert payload["workspace_api"] == {"contract_version": 1}
