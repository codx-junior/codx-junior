"""CompanyView — the company shell.

The shell is the company-first home of codx-junior: it owns the sections a
company works in (its tasks under "work", projects, the workplaces) and the
project selector that gives every screen the active project of the company,
because a company can run several projects.

The sources are asserted instead of rendered: the shell is a Vue SFC and this
suite runs without a browser or a Node runtime.
"""

from pathlib import Path

import pytest

CLIENT = Path(__file__).resolve().parents[2] / "client"
SHELL = CLIENT / "src" / "views" / "CompanyView.vue"
CSS = CLIENT / "src" / "assets" / "company.css"


def read(path):
    assert path.exists(), f"{path} is part of the company shell and must exist"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "section",
    ["home", "work", "team", "projects", "business", "security", "coworking", "calendar"],
)
def test_the_shell_owns_the_company_sections(section):
    """Tasks ("work") and the workplaces used to be project-level screens; the
    company owns them now and each one is a section of the shell."""
    assert f"id: '{section}'" in read(SHELL)


def test_the_shell_offers_a_project_selector():
    """A company runs several projects: the active one is chosen in the top bar
    and every company screen follows it."""
    source = read(SHELL)
    assert "const companyProjectOptions = computed" in source, (
        "the selector lists the projects linked to the company"
    )
    assert "async function onSelectProject(" in source, (
        "switching the project updates the active project in place"
    )
    assert 'class="cp-project-select"' in source, "the selector is in the top bar"
    assert "$storex.company.companyProjects" in source, (
        "the options come from the projects the company runs"
    )
    assert ".cp-project-select {" in read(CSS), "the selector has its own styles"
