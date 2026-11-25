#!/usr/bin/env python3
"""
One browser-use task, in its own interpreter.

The API never imports browser-use: it pins its own pydantic/pydantic-ai/MCP
versions and runs in a separate venv (``/opt/browser-use`` in the released
image, or ``CODX_BROWSER_USE_PYTHON``).  This worker is the whole protocol:

    stdin   one JSON object:
            {
              "task": "...", "cdp_url": "ws://...",
              "workspace": "/path", "system_message": "...",
              "model": "...", "base_url": "...", "api_key": "...",
              "max_steps": 30, "timeout": 300, "judge": false
            }

    stdout  exactly one JSON line:
            {"ok": true, "final_result": "...", "success": true,
             "status": "completed", "steps": 4,
             "usage": {"input": 1, "output": 2, ...},
             "cost_usd": 0.0012, "downloads": ["/path/file.pdf"],
             "duration_s": 12.3}
            or {"ok": false, "error": "..."}

Everything browser-use logs goes to stderr, so the parent can parse stdout
without guessing.  The worker attaches to an **existing** Chromium over CDP and
stops only its own harness daemon; the browser the human is watching keeps
running.

It is also imported by the tests, which pass a fake browser_use module.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any, Dict, List


def _value(source: Any, name: str, default: Any = None) -> Any:
    """Read an attribute from the browser-use result without exploding."""
    try:
        return getattr(source, name)
    except Exception:
        return default


def _result_payload(goal: str, raw: Any, started: float) -> Dict[str, Any]:
    """Map browser-use's ``AgentHistoryList`` onto the worker protocol."""
    final = None
    finalizer = _value(raw, "final_result")
    if callable(finalizer):
        try:
            final = finalizer()
        except Exception:
            final = None
    if not final:
        final = _value(raw, "final_result_text") or _value(raw, "text")

    success = _value(raw, "success")
    if success is None:
        checker = _value(raw, "is_successful")
        if callable(checker):
            try:
                success = checker()
            except Exception:
                success = None

    usage_raw = _value(raw, "usage") or {}
    try:
        usage = {str(key): int(value or 0) for key, value in dict(usage_raw).items()}
    except Exception:
        usage = {}

    downloads: List[str] = []
    for item in _value(raw, "downloads") or []:
        try:
            downloads.append(str(item))
        except Exception:
            continue

    steps = 0
    for name in ("number_of_steps", "steps"):
        candidate = _value(raw, name)
        if callable(candidate):
            try:
                steps = int(candidate())
            except Exception:
                steps = 0
        elif isinstance(candidate, int):
            steps = candidate
        if steps:
            break

    cost = _value(raw, "cost_usd")
    try:
        cost_usd = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost_usd = None

    return {
        "ok": True,
        "final_result": str(final) if final is not None else None,
        "success": bool(success) if success is not None else None,
        "status": str(_value(raw, "status") or "") or None,
        "steps": steps,
        "usage": usage,
        "cost_usd": cost_usd,
        "downloads": downloads,
        "duration_s": float(_value(raw, "duration_s") or 0.0) or (time.monotonic() - started),
        "task": goal,
    }


async def run_task(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run one task with browser-use and return the worker payload."""
    goal = str(request.get("task") or "").strip()
    cdp_url = str(request.get("cdp_url") or "").strip()
    workspace = str(request.get("workspace") or "").strip()
    if not goal:
        return {"ok": False, "error": "task is required"}
    if not cdp_url:
        return {"ok": False, "error": "cdp_url is required"}
    if not workspace:
        return {"ok": False, "error": "workspace is required"}

    from browser_use import Agent, Browser, ChatOpenAI  # optional dependency

    llm_kwargs: Dict[str, Any] = {}
    if request.get("base_url"):
        llm_kwargs["base_url"] = request["base_url"]
    if request.get("api_key"):
        llm_kwargs["api_key"] = request["api_key"]
    model = str(request.get("model") or "").strip()
    if not model:
        return {"ok": False, "error": "model is required (set the project LLM)"}
    llm = ChatOpenAI(model, **llm_kwargs)

    started = time.monotonic()
    browser = Browser(cdp_url=cdp_url)
    agent = Agent(
        goal,
        llm=llm,
        browser=browser,
        workspace=workspace,
        judge=bool(request.get("judge")),
        extend_system_message=str(request.get("system_message") or ""),
    )
    try:
        raw = await agent.run(
            max_steps=int(request.get("max_steps") or 30),
            timeout=float(request.get("timeout") or 300),
        )
        payload = _result_payload(goal, raw, started)
    finally:
        # Stop the daemon this run started only: the shared Chromium (and the
        # human's tabs) is never touched by a cdp_url browser.stop().
        try:
            await browser.stop()
        except Exception:
            pass
    return payload


def main() -> int:
    """Read the request from stdin, run it, print one JSON line."""
    real_stdout = sys.stdout
    # browser-use may print to stdout; keep stdout for our single JSON answer.
    sys.stdout = sys.stderr
    payload: Dict[str, Any] = {"ok": False, "error": "the worker returned no result"}
    try:
        raw = sys.stdin.read()
        try:
            request = json.loads(raw) if raw.strip() else {}
        except ValueError as ex:
            payload: Dict[str, Any] = {"ok": False, "error": f"invalid request JSON: {ex}"}
        else:
            if not isinstance(request, dict):
                payload = {"ok": False, "error": "request must be a JSON object"}
            else:
                try:
                    payload = asyncio.run(run_task(request))
                except Exception as ex:  # noqa: BLE001 - always answer the parent
                    payload = {"ok": False, "error": f"{ex.__class__.__name__}: {ex}"}
    finally:
        sys.stdout = real_stdout

    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
