"""The codx user's language preference is part of the persisted profile.

It is used by chat (assistant language) and by voice conversation mode, which
resolves it to a speech locale and falls back to the browser when it is
``auto``/unset. These tests protect the model contract the admin user
maintenance screen writes to.
"""

from codx.junior.model.model import GlobalSettings
from codx.junior.model.user import CodxUser


def test_language_defaults_to_auto():
    assert CodxUser(username="u").language == "auto"


def test_language_keeps_a_stored_iso_value():
    assert CodxUser(username="u", language="es").language == "es"
    assert CodxUser(username="u", language="pt-BR").language == "pt-BR"


def test_language_survives_global_settings_serialization():
    settings = GlobalSettings(
        users=[CodxUser(username="u", role="user", language="ja")]
    )

    serialized = settings.model_dump(mode="json")

    assert serialized["users"][0]["language"] == "ja"
