"""Custom coworking images: gallery folder, scaffold, visibility and imports."""

import json
import os

import pytest

from codx.junior.computer.custom_images import (
    ASSISTANT_PROFILE,
    CoworkingImageLibrary,
    CreateCoworkingImageRequest,
    UpdateCoworkingImageRequest,
    build_prompt,
    can_edit,
    visible_to,
)


def _library(tmp_path):
    return CoworkingImageLibrary(str(tmp_path), project_id="p1", project_name="Project One")


def test_create_writes_folder_with_manifest_dockerfile_and_readme(tmp_path):
    library = _library(tmp_path)
    item = library.create(
        CreateCoworkingImageRequest(
            name="Firefox + Jenkins",
            description="CI/CD desktop with a browser and the Jenkins tooling",
            base_image="lscr.io/linuxserver/webtop:ubuntu-kde",
            tags=["ci", "firefox"],
        ),
        created_by="ana",
    )

    assert item.slug == "firefox-jenkins"
    assert item.visibility == "project"
    assert item.status == "draft"
    assert item.assistant_profile == ASSISTANT_PROFILE
    assert "codx-junior-max" in item.assistant_prompt
    assert item.folder == f"{item.id}.firefox-jenkins"

    folder = os.path.join(str(tmp_path), "coworking", "images", item.folder)
    assert os.path.isdir(folder)
    for name in ("image.json", "Dockerfile", "README.md", "support", "media"):
        assert os.path.exists(os.path.join(folder, name)), name

    with open(os.path.join(folder, "image.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert manifest["slug"] == "firefox-jenkins"
    assert manifest["source"] == "dockerfile"
    # The prompt is derived on read: it never enters the manifest.
    assert manifest["assistant_prompt"] == ""

    with open(os.path.join(folder, "Dockerfile"), encoding="utf-8") as handle:
        dockerfile = handle.read()
    assert "ARG BASE_IMAGE=lscr.io/linuxserver/webtop:ubuntu-kde" in dockerfile
    assert "EXPOSE 3000" in dockerfile

    prompt = library.prompt_for(item.id)
    assert prompt == build_prompt(item, folder)


def test_visibility_scopes_filter_the_gallery(tmp_path):
    library = _library(tmp_path)
    library.create(CreateCoworkingImageRequest(name="Private", visibility="user"), created_by="ana")
    library.create(CreateCoworkingImageRequest(name="Team"), created_by="bob")
    library.create(
        CreateCoworkingImageRequest(name="Shared", visibility="custom", projects=["p2"]),
        created_by="bob",
    )
    library.create(CreateCoworkingImageRequest(name="Global", visibility="global"), created_by="cy")

    def names(**kwargs):
        return sorted(item.name for item in library.list_visible(**kwargs))

    assert names(username="ana", project_id="p1") == ["Global", "Private", "Team"]
    assert names(username="bob", project_id="p1") == ["Global", "Team"]
    assert names(username="bob", project_id="p2") == ["Global", "Shared"]
    assert names(username="bob", project_id="p3") == ["Global"]
    assert names(username="bob", project_id="p3", is_admin=True) == [
        "Global",
        "Private",
        "Shared",
        "Team",
    ]
    assert names(username="bob", project_id="p1", visibility="global") == ["Global"]
    assert names(username="bob", project_id="p1", q="tea") == ["Team"]


def test_custom_visibility_requires_projects(tmp_path):
    library = _library(tmp_path)
    with pytest.raises(ValueError):
        library.create(CreateCoworkingImageRequest(name="Broken", visibility="custom"))


def test_import_from_dockerhub_keeps_a_from_recipe(tmp_path):
    library = _library(tmp_path)
    item = library.import_from_dockerhub("jenkins/jenkins:lts-jdk17", created_by="ana", visibility="global")

    assert item.source == "dockerhub"
    assert item.docker_ref == "jenkins/jenkins:lts-jdk17"
    assert item.name == "jenkins"
    assert item.visibility == "global"
    content = library.read_file(item.id, "Dockerfile").content
    assert "ARG BASE_IMAGE=jenkins/jenkins:lts-jdk17" in content
    assert "imported" in item.tags


def test_import_from_git_writes_a_build_script(tmp_path):
    library = _library(tmp_path)
    item = library.import_from_git(
        "https://github.com/acme/coworking-ide.git",
        created_by="ana",
        git_ref="main",
    )

    assert item.source == "git"
    assert item.name == "coworking-ide"
    assert item.build_script == "build.sh"
    script = library.read_file(item.id, "build.sh").content
    assert 'git clone --depth 1 --branch "main"' in script


def test_file_api_stays_inside_the_folder_and_updates_metadata(tmp_path):
    library = _library(tmp_path)
    item = library.create(CreateCoworkingImageRequest(name="Editor"), created_by="ana")

    library.write_file(item.id, "support/motd.txt", "hola")
    assert library.read_file(item.id, "support/motd.txt").content == "hola"
    assert "support/motd.txt" in [entry.path for entry in library.files(item.id)]

    with pytest.raises(ValueError):
        library.write_file(item.id, "../escape.txt", "nope")

    updated = library.update(
        item.id, UpdateCoworkingImageRequest(visibility="custom", projects=["p2", "p3"])
    )
    assert updated.visibility == "custom"
    assert updated.projects == ["p2", "p3"]

    with pytest.raises(ValueError):
        library.update(item.id, UpdateCoworkingImageRequest(visibility="custom", projects=[]))
    with pytest.raises(ValueError):
        library.update(item.id, UpdateCoworkingImageRequest(visibility="everyone"))


def test_can_edit_belongs_to_the_owner_or_the_project(tmp_path):
    library = _library(tmp_path)
    item = library.create(CreateCoworkingImageRequest(name="Owned"), created_by="ana")

    assert can_edit(item, username="bob", project_id="p1") is True
    assert can_edit(item, username="bob", project_id="p9") is False
    item.visibility = "user"
    assert can_edit(item, username="bob", project_id="p1") is False
    assert can_edit(item, username="ana") is True
    assert can_edit(item, username="bob", project_id="p1", is_admin=True) is True
    assert visible_to(item, username="ana") is True
    assert visible_to(item, username="bob", project_id="p1") is False


def test_api_routes_expose_custom_images_before_dynamic_routes():
    from codx.junior.api.computer import router

    paths = [getattr(route, "path", "") for route in router.routes]
    assert "/computers/library/custom" in paths
    assert "/computers/library/custom/import" in paths
    assert paths.index("/computers/library/custom") < paths.index("/computers/library/{item_id}")
