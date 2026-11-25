"""
Install-time feature profile API (``/api/features``).

The client loads this once before resolving the shell: a ``core`` 0.0.1 install
gets the project workspace by default and never opens the company wizard, while
an ``all`` install keeps the company-first product. The endpoint is public (the
profile is not a secret) and cheap: the manifest is cached in the process.

See :mod:`codx.junior.features` for the profiles and the manifest.
"""

from fastapi import APIRouter

from codx.junior.features import describe

router = APIRouter()


@router.get("/features")
def api_features():
    """
    Return the resolved feature profile.

    Shape::

        {
          "version": "0.0.1",
          "profile": "core" | "all" | "custom",
          "active": ["install", ...],
          "hidden": ["company-business", ...],
          "company": false,
          "company_wizard": false,
          "manifest_hash": "..."
        }
    """
    return describe()
