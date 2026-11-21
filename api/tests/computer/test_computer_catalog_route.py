"""The merged catalog route is wired on the computers router."""

from codx.junior.api.computer import router


def test_catalog_route_is_registered():
    paths = [getattr(route, "path", "") for route in router.routes]

    assert any(path.endswith("/images/catalog") for path in paths)
