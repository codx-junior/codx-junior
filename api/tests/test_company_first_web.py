"""COMPANY-FIRST WEB CONTRACT (routing and URL model).

codx-junior is a company-first product: the company owns projects (many of
them), so the company shell is the web entry point and every project screen is
mounted below the company workspace. The shell itself — its sections and the
project selector — is covered by test_companyview.py.

These assertions read the client sources, so the contract is verified in the
regular API suite without a browser or a Node runtime.
"""

import re
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[2] / "client"
ROUTER = CLIENT / "src" / "router" / "index.ts"
APP = CLIENT / "src" / "App.vue"


def read(path):
    assert path.exists(), f"{path} is part of the company-first shell and must exist"
    return path.read_text(encoding="utf-8")


def test_application_root_renders_the_company_shell():
    """"/" is company-first even on a fresh install: the company shell renders
    (and opens the onboarding wizard) instead of the legacy project welcome."""
    match = re.search(
        r"path: '/',\s*\n\s*name: 'root',\s*\n\s*component: (\w+),",
        read(ROUTER),
    )
    assert match, "the `/` route must declare path '/', name 'root' and a component"
    assert match.group(1) == "CompanyView", (
        "`/` must render the company shell, never the project welcome screen"
    )


def test_project_screens_are_mounted_under_the_company_workspace():
    """/<project>/... is never a top-level URL: it always lives inside the
    company that runs the project."""
    source = read(ROUTER)
    assert "/:companySlug/workspace/:projectName${route.path}" in source
    assert "/:companySlug/:section(${COMPANY_SECTION_RE})?" in source


def test_root_is_a_full_screen_company_route():
    """The shell owns the whole window on `/`, like on `/<companySlug>`."""
    assert "route.name === 'company-home' || route.name === 'root'" in read(APP)
