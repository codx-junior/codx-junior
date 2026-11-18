"""The build provenance behind ``GET /api/health``.

An unattended install asserts *which* build answered, so the shape of that payload
is a contract: the three keys are always present, the image descriptor is read and
the environment overrides it.
"""

import json

from codx.junior import version


def _isolate(monkeypatch, descriptor_path):
    """Point the module at a given descriptor and cut the git fallback."""
    monkeypatch.setattr(version, "BUILD_INFO_PATH", str(descriptor_path))
    monkeypatch.setattr(version, "GIT_PATH_CANDIDATES", [])
    version._git_info.cache_clear()


def test_build_info_always_returns_the_three_keys(monkeypatch, tmp_path):
    """No descriptor and no env: the payload is still complete, never a KeyError."""
    for name in ("CODX_JUNIOR_BUILD_VERSION", "CODX_JUNIOR_BUILD_REF", "CODX_JUNIOR_BUILD_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    _isolate(monkeypatch, tmp_path / "missing.json")

    info = version.build_info()

    assert sorted(info) == ["commit", "ref", "version"]
    assert set(info.values()) == {version.UNKNOWN}


def test_build_info_reads_the_image_descriptor(monkeypatch, tmp_path):
    """The release build bakes the tag into /etc/codx-junior/build-info.json."""
    for name in ("CODX_JUNIOR_BUILD_VERSION", "CODX_JUNIOR_BUILD_REF", "CODX_JUNIOR_BUILD_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    descriptor = tmp_path / "build-info.json"
    descriptor.write_text(json.dumps({"version": "1.2.3", "ref": "v1.2.3", "commit": "9f1ab2c"}))
    monkeypatch.setattr(version, "BUILD_INFO_PATH", str(descriptor))

    assert version.build_info() == {"version": "1.2.3", "ref": "v1.2.3", "commit": "9f1ab2c"}


def test_environment_overrides_the_descriptor(monkeypatch, tmp_path):
    """`docker run -e CODX_JUNIOR_BUILD_REF=...` must win over the baked file."""
    descriptor = tmp_path / "build-info.json"
    descriptor.write_text(json.dumps({"version": "1.2.3", "ref": "v1.2.3", "commit": "9f1ab2c"}))
    monkeypatch.setattr(version, "BUILD_INFO_PATH", str(descriptor))
    monkeypatch.setenv("CODX_JUNIOR_BUILD_REF", "v9.9.9")

    info = version.build_info()

    assert info["ref"] == "v9.9.9"
    assert info["version"] == "1.2.3"


def test_build_info_survives_a_corrupt_descriptor(monkeypatch, tmp_path):
    """A half-written descriptor must not break the readiness probe."""
    monkeypatch.delenv("CODX_JUNIOR_BUILD_REF", raising=False)
    descriptor = tmp_path / "build-info.json"
    descriptor.write_text("{not json")
    _isolate(monkeypatch, descriptor)

    assert version.build_info()["ref"] == version.UNKNOWN


def test_build_info_falls_back_to_the_checkout(monkeypatch, tmp_path):
    """No build args and no descriptor: report the commit the app runs from."""
    for name in ("CODX_JUNIOR_BUILD_VERSION", "CODX_JUNIOR_BUILD_REF", "CODX_JUNIOR_BUILD_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(version, "BUILD_INFO_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(version, "GIT_PATH_CANDIDATES", ["/fake/checkout"])

    def fake_git(*args, **kwargs):
        return "325e7e5" if args[0] == "describe" else "0" * 40

    monkeypatch.setattr(version, "_git", fake_git)
    version._git_info.cache_clear()

    assert version.build_info() == {
        "version": "325e7e5",
        "ref": "325e7e5",
        "commit": "0" * 40,
    }
    version._git_info.cache_clear()


def test_the_descriptor_beats_the_checkout(monkeypatch, tmp_path):
    """An image built for a release reports the tag, not the builder's clone."""
    for name in ("CODX_JUNIOR_BUILD_VERSION", "CODX_JUNIOR_BUILD_REF", "CODX_JUNIOR_BUILD_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    descriptor = tmp_path / "build-info.json"
    descriptor.write_text(json.dumps({"version": "1.2.3", "ref": "v1.2.3", "commit": "9f1ab2c"}))
    monkeypatch.setattr(version, "BUILD_INFO_PATH", str(descriptor))
    monkeypatch.setattr(version, "GIT_PATH_CANDIDATES", ["/fake/checkout"])
    monkeypatch.setattr(version, "_git", lambda *a, **k: "deadbeef")
    version._git_info.cache_clear()

    assert version.build_info()["ref"] == "v1.2.3"
    version._git_info.cache_clear()
