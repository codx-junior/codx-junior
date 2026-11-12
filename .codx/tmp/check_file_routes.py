"""List the file routes registered by the CURRENT source (isolated process).

Used to verify that the endpoints the explorer calls (copy / move / archive /
extract) are actually part of the FastAPI app before restarting the API
server, which still runs the code loaded at container start.
"""
import sys

sys.path.insert(0, "/home/codx-junior/codx-junior/api")

from codx.junior.api.files import router  # noqa: E402

wanted = ("/files/copy", "/files/move", "/files/archive", "/files/extract", "/files/rename")
found = {}
for route in router.routes:
    path = getattr(route, "path", "")
    methods = ",".join(sorted(getattr(route, "methods", []) or []))
    if path in wanted:
        found[path] = methods

for path in wanted:
    print(f"{path:16s} {'POST' if path in found else 'MISSING':8s} {found.get(path, '')}")

missing = [path for path in wanted if path not in found]
print("OK" if not missing else f"MISSING: {missing}")
