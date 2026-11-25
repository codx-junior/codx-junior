"""CompanyView — the company shell.

The shell is the company-first home of codx-junior: it owns the sections a
company works in (its tasks under "work", projects, the workplaces) and
aggregates every project the company links. There is no active-project
selector; project-scoped panels widen to the company project set.

The sources are asserted instead of rendered: the shell is a Vue SFC and this
suite runs without a browser or a Node runtime.
"""

from pathlib import Path

import pytest

CLIENT = Path(__file__).resolve().parents[2] / "client"
SHELL = CLIENT / "src" / "views" / "CompanyView.vue"


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


def test_the_shell_has_no_active_project_selector():
    """A company aggregates every project it links: there is no top-bar
    selector that scopes the shell to one project, and no project outside the
    company is offered."""
    source = read(SHELL)
    assert "cp-project-select" not in source, (
        "the company shell does not scope every screen to one active project"
    )
    assert "onSelectProject" not in source, (
        "no selector scopes every company screen to one project"
    )
    assert "companyProjectOptions" not in source, (
        "the removed selector no longer leaks inherited child projects as options"
    )
    assert "$storex.company.linkedProjects" in source, (
        "company surfaces read the projects the company defines"
    )
    assert "$storex.company.linkedProjectIds" in source, (
        "company-wide actions use every linked project"
    )


def test_company_panels_are_company_wide():
    """Security and the calendar widget aggregate every linked project instead
    of following a single active project chosen in the shell."""
    security = read(CLIENT / "src" / "components" / "company" / "CompanySecurity.vue")
    calendar = read(CLIENT / "src" / "components" / "company" / "CompanyCalendar.vue")
    assert "props.projectId" not in security.replace("props.projectIds", "")
    assert "api.security(props.companyId)" in security
    assert "projects: [projectId.value]" not in calendar
    assert "projects: projectIds.value" in calendar
