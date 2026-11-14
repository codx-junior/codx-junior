"""Temporary diagnostic: log import progress so a hung import is visible."""

import time
import traceback

LOG = "/tmp/probe_imports_drafts.log"


def log(msg):
    with open(LOG, "a") as fh:
        fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


with open(LOG, "w") as fh:
    fh.write("start\n")

MODULES = [
    "slugify",
    "codx.junior.settings",
    "codx.junior.model.agent",
    "codx.junior.db",
    "codx.junior.chat.chat_prompt_builder",
    "codx.junior.api.chat",
]

for name in MODULES:
    log(f"importing {name}")
    try:
        __import__(name)
    except BaseException:
        log(f"FAILED {name}\n{traceback.format_exc()}")
    else:
        log(f"ok {name}")

log("done")
