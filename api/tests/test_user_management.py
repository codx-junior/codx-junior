"""
Regression tests for the account settings save path.

Bug: ``UserSecurityManager.save_settings()`` persisted the WHOLE
``GlobalSettings`` object with ``write_global_settings(self.global_settings)``,
using the snapshot the manager took in ``__init__``. Every account save
(profile, email, avatar, assistant language, theme, workspace mode, password
reset, project permission) rewrote *every* section file, so the stale values of
the sections owned by other components (AI models, providers, git credentials,
working hours, agent settings...) were written back over whatever the Global
Settings screen had changed in the meantime.

The account screen may only write the sections it owns: ``users`` and
``user_logins``.
"""

import pathlib

import pytest

from codx.junior import global_settings as gs
from codx.junior.model.model import CodxUser
from codx.junior.security.user_management import UserSecurityManager
from codx.junior.settings_manager import GlobalSettingsManager


@pytest.fixture()
def isolated_settings(tmp_path, monkeypatch):
    """Point the settings manager at a throwaway config folder."""
    monkeypatch.setenv("CODX_JUNIOR_CONFIG_FOLDER", str(tmp_path))

    manager = GlobalSettingsManager()
    # Bind the instance explicitly: the manager singleton may have been created
    # at import time, before the environment variable was patched.
    manager.config_folder = str(tmp_path)
    manager.settings_dir = pathlib.Path(tmp_path) / "settings"
    manager.history_dir = pathlib.Path(tmp_path) / "history"
    manager._ensure_dirs()
    monkeypatch.setattr(gs, "_manager", manager)
    gs.invalidate_global_settings_cache()

    settings = gs.read_global_settings(refresh=True)
    settings.users = [
        CodxUser(username="admin", role="admin", avatar="/only_icon.png"),
        CodxUser(username="bob", role="user", email="old@example.com"),
    ]
    settings.user_logins = []
    gs.write_global_settings(settings)

    yield manager

    gs.invalidate_global_settings_cache()


def read_settings():
    return gs.read_global_settings(refresh=True)


def test_account_save_does_not_revert_a_section_owned_by_another_component(isolated_settings):
    """The failing case: a global change made after the manager snapshot."""
    # A security manager is built (snapshot taken here) by every authenticated
    # request.
    manager = UserSecurityManager()

    # The Global Settings screen changes a section this manager does not own.
    gs.write_settings_section("llm_model", "model-chosen-in-global-settings")
    gs.write_settings_section("codx_junior_avatar", "/only_icon.png")

    # Saving the account must leave those sections alone.
    manager.update_user(
        CodxUser(username="bob", email="bob@example.com", language="es")
    )

    settings = read_settings()
    assert settings.llm_model == "model-chosen-in-global-settings"
    assert settings.codx_junior_avatar == "/only_icon.png"


def test_account_save_persists_the_user_sections(isolated_settings):
    """The fix must not stop the account itself from being saved."""
    manager = UserSecurityManager()
    manager.update_user(
        CodxUser(
            username="bob",
            email="bob@example.com",
            avatar="/avatar.png",
            theme="dark",
            language="es",
        )
    )

    bob = next(u for u in read_settings().users if u.username == "bob")
    assert bob.email == "bob@example.com"
    assert bob.avatar == "/avatar.png"
    assert bob.theme == "dark"
    assert bob.language == "es"
    # Untouched accounts and the credential list survive the save.
    assert any(u.username == "admin" for u in read_settings().users)
    assert read_settings().user_logins == []


def test_account_save_leaves_the_settings_cache_fresh(isolated_settings):
    """After a save the cached settings must reflect the persisted state."""
    manager = UserSecurityManager()
    manager.update_user(CodxUser(username="bob", email="fresh@example.com"))

    cached = gs.read_global_settings()
    assert any(
        u.username == "bob" and u.email == "fresh@example.com" for u in cached.users
    )
