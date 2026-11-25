"""
Browser session tools for codx-junior agents.

These tools let the LLM drive the same real Chromium browser the user is
watching and controlling through the live VNC view (managed by
``BrowserManager``).  There is no separate "AI browser": user and AI share one
page, one set of cookies and one history.

The browser container also mounts the project folders under
``/home/<project_name>/<folder>`` and writes its downloads inside the project,
so the agent and the user exchange files with the project instead of being
isolated in the container.

Tools
-----
- ``start_session``      – create & start (or resume) a browser session.
- ``browse_action``      – navigate / click / fill / execute JS / read text /
                           back / forward / refresh / wait / press / select /
                           hover / scroll / upload / links / tabs (list, open,
                           switch, close).
- ``browser_agent``      – hand a whole multi-step goal to the browser-use
                           agent (its own model loop) on the same browser.
- ``get_screenshot``     – capture a PNG screenshot of the current page.
- ``get_source_code``    – return the current page HTML (prefer ``snapshot``).
- ``browser_activity``   – recent user/AI actions on the session (co-browsing
                           awareness), including real VNC input.
- ``browser_status``     – connection, tabs and who controls the page.
- ``browser_control``    – handoff / take control / give back / pause.
- ``finish_session``     – stop (default) or delete the session. Stopping keeps
                           the Chromium profile; deleting removes it too.

The tools no longer open a Playwright connection per call: ``BrowserManager``
keeps one persistent runtime per session (see ``browser/runtime.py``), which
also captures human input and runs whole scripts deterministically.

Settings
--------
Every tool receives ``settings`` (CODXJuniorSettings) through the
``project_settings`` injection mechanism and ``chat_id`` (from the running
SmolAgent) when its signature declares it.  All tools are ``async`` and return
:class:`ToolResponse` so the chat can render a user-facing summary while the
model receives machine-readable content.
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from codx.junior.settings import CODXJuniorSettings
from codx.junior.browser import tabs as browser_tabs
from codx.junior.browser import events as browser_events
from codx.junior.browser.scripts import BrowserScriptStore, normalize_site
from .model import ToolResponse

logger = logging.getLogger(__name__)

#: Actions whose result includes a compact ``page`` summary.  This removes the
#: usual follow-up ``browse_action(action="snapshot")`` turn: the model sees the
#: new URL, refs and text in the same ToolResponse as the action.
PAGE_BRIEF_ACTIONS = {
    "navigate", "click", "execute", "back", "forward", "refresh",
    "press", "select", "upload", "batch", "new_tab", "switch_tab", "close_tab",
}

#: Settings metadata applied to every browser tool in the ``TOOLS`` registry.
#: Both ``async`` and ``is_async`` keys are provided for compatibility with the
#: two conventions present in the codebase.
BROWSER_TOOL_SETTINGS: Dict[str, Any] = {
    "async": True,
    "is_async": True,
    "project_settings": True,
    # ``browser_agent`` needs the asking user: the LLM gateway checks their
    # wallet and attributes the delegated agent's token usage to them.
    "inject_user": True,
    # ... and the model this chat turn resolved (chat model > profile model >
    # project default), so the browser agent speaks with the same brain.
    "inject_llm_model": True,
    "dual_response": True,
    "scope": "chat",
    # Browser state changes between calls: never serve a cached result.
    "cache": False,
}

#: Hard cap on the HTML returned by ``get_source_code`` (keeps LLM context sane).
MAX_SOURCE_CHARS = 20000
#: Hard cap on visible text returned by ``browse_action(text)``.
MAX_TEXT_CHARS = 8000

#: Actions that operate on tabs instead of the current page content.
TAB_ACTIONS = ("tabs", "new_tab", "switch_tab", "close_tab")

#: Actions that can use an explicit tab index.
INDEX_ACTIONS = ("switch_tab", "close_tab")


def _settings_from(kwargs: Dict[str, Any]) -> CODXJuniorSettings:
    """Extract and validate injected project settings."""
    settings: CODXJuniorSettings = kwargs.get("settings")
    if not settings:
        raise Exception("Invalid project settings")
    return settings


def _manager(settings: CODXJuniorSettings):
    """Return the cached BrowserManager (owns the persistent runtimes)."""
    from codx.junior.browser.browser_manager import get_browser_manager
    return get_browser_manager(settings)


def _resolve_session(manager, session_id: Optional[str] = None, chat_id: Optional[str] = None):
    """Resolve a session by id, falling back to the chat's shared session."""
    session = manager.get_session(session_id) if session_id else None
    if not session and chat_id:
        session = manager.get_session_for_chat(chat_id)
    return session


def _browser_store(settings):
    """Open the project's browser login store (raises ValueError if unusable)."""
    from codx.junior.browser.store import BrowserStore, StoreError
    try:
        return BrowserStore(settings)
    except StoreError as ex:
        raise ValueError(ex.render()) from ex


def _store_error(ex) -> ValueError:
    from codx.junior.browser.store import StoreError
    return ValueError(ex.detail or ex.code) if isinstance(ex, StoreError) else ValueError(str(ex))


def _cdp_only_note(session) -> Optional[str]:
    """
    Return why the AI cannot drive a browser session, or None when it can.

    Browser sessions are driven over CDP.  Chromium-family images expose it;
    Firefox-family images do not, so those sessions are live-view only and the
    AI must hand the page to the human instead of retrying an action that can
    never work.
    """
    if str(getattr(session, "kind", "") or "").lower() != "browser":
        return None
    if str(getattr(session, "control", "") or "").lower() == "cdp":
        return None
    engine = getattr(session, "engine", "") or "this browser"
    return (
        f"This browser session is live-view only: its image runs {engine}, which has no "
        "Chrome DevTools Protocol, so the AI cannot drive it. Use a Chromium-based image "
        "(Chromium, Chrome, Ungoogled Chromium, Brave, Edge, Opera, Vivaldi) for AI "
        "browsing; Safari requires a macOS VM session; Firefox/LibreWolf/Zen/Mullvad are "
        "available for manual testing in the live view."
    )


def _shared_folder_lines(session) -> List[str]:
    """Describe every folder mounted in the session (empty when none)."""
    return [
        f"  - {share.name or share.folder or '/'} → {share.mount_path}"
        f"{' (read-only)' if share.readonly else ''}"
        f"  [project: {share.folder or '/'}]"
        for share in session.shared_folders
    ]


def _summary(session) -> str:
    """Build a short human-readable summary of a session."""
    folders = ", ".join(share.mount_path for share in session.shared_folders) or "(none)"
    return (
        f"session_id={session.id}\n"
        f"name={session.name or '(unnamed)'}\n"
        f"status={session.status}\n"
        f"chats={', '.join(session.chat_ids) or '(none)'}\n"
        f"live_view={session.vnc_url or '(starting)'}\n"
        f"home={session.home_path or '(not shared)'}\n"
        f"mounted_folders={folders}\n"
        f"downloads={session.downloads_mount or '(none)'} (host: {session.downloads_path or '-'})\n"
        f"current_url={session.current_url or '(blank)'}\n"
        f"current_title={session.current_title or '(blank)'}"
    )


def _sharing_note(session) -> str:
    """Describe the folder mapping for the LLM (empty when nothing is shared)."""
    if not session.shares_files():
        return ""
    lines = _shared_folder_lines(session)
    note = ""
    if lines:
        note = (
            "The browser has these project folders mounted (open them with "
            "file://<mount_path>/<file>):\n" + "\n".join(lines) + "\n"
        )
    if session.downloads_path:
        note += (
            f"Downloads are saved inside the project at '{session.downloads_path}' "
            f"(browser path '{session.downloads_mount}'). Attach a local file with "
            "browse_action(action='upload', selector=<file input or upload button>, "
            "files=['<file name>']): a bare name is looked up in the Downloads folder, "
            "and the bytes are streamed to the browser."
        )
    return note.strip()


def _recipe_root(settings: CODXJuniorSettings) -> str:
    """Project folder that owns ``.codx/browser_scripts``."""
    for value in (
        getattr(settings, "abs_project_path", None),
        getattr(settings, "project_path", None),
    ):
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _known_recipe_note(settings: CODXJuniorSettings, url: Optional[str]) -> str:
    """List the recorded recipes for *url* so the model reuses them at once."""
    root = _recipe_root(settings)
    if not root:
        return ""
    try:
        store = BrowserScriptStore(root)
        entries = store.search_scripts(site=normalize_site(url), limit=5)
        if not entries:
            return ""
        return (
            "Recorded recipes for this site — prefer run_browser_script over "
            "re-discovering the page:\n" + store.to_markdown(entries)
        )
    except Exception as ex:  # pragma: no cover - best effort prompt hint
        logger.debug("Could not list recorded recipes: %s", ex)
        return ""


def _persist_to_chat(settings: CODXJuniorSettings, session, chat_id: Optional[str]) -> None:
    """Persist a session into its chat's meta_data (best effort)."""
    if not chat_id:
        return
    try:
        from codx.junior.chat_manager import ChatManager
        chat_manager = ChatManager(settings)
        chat = chat_manager.find_by_id(chat_id)
        if chat:
            _manager(settings).save_session_to_chat(session, chat)
            chat_manager.save_chat(chat)
    except Exception as ex:
        logger.warning("Could not persist browser session to chat %s: %s", chat_id, ex)


async def start_session(
    name: str = None,
    chat_id: str = None,
    initial_url: str = None,
    description: str = None,
    share_project: bool = None,
    share_folders: List[str] = None,
    downloads_folder: str = None,
    **kwargs,
) -> ToolResponse:
    """
    Start (or resume) a browser session connected to the current chat.

    Creates and starts an isolated Chromium container.  The returned
    ``session_id`` is used by the other browser tools, and ``live_view`` is the
    URL the user opens to watch and control the same browser.

    Project folders are mounted inside the container under
    ``/home/<project_name>/<folder>`` and every download is saved inside the
    project, so files are shared between the user, the AI and the project.

    Args:
        name: Human-readable session name (defaults to a timestamped name).
        chat_id: Associated chat id (injected by SmolAgent); used to reuse the
            chat's existing session and to make it visible in the chat UI.
        initial_url: Optional URL opened when the browser starts.
        description: Optional free-form description of the session.
        share_project: Mount the whole project at ``/home/<project_name>``.
            Omit it to mount the project root only when ``share_folders`` is
            empty.
        share_folders: Project folders to mount (``["docs", "data"]``); each one
            keeps its relative path inside ``/home/<project_name>``.
        downloads_folder: Project-relative folder receiving the downloads
            (default ``.codx/browser_downloads/<session_id>``).
        **kwargs: Injected ``settings``.

    Returns:
        ToolResponse with the session details and live-view URL.
    """
    settings = _settings_from(kwargs)
    manager = _manager(settings)

    session_name = (name or "").strip() or f"Browser {datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

    try:
        existing = manager.get_session_for_chat(chat_id) if chat_id else None
        if existing:
            if existing.status != "running":
                try:
                    existing = await manager.restart_session(existing.id)
                except Exception as ex:
                    logger.warning("Could not restart browser session %s: %s", existing.id, ex)

            if existing and existing.status == "running":
                recipe_note = _known_recipe_note(
                    settings, initial_url or existing.current_url
                )
                return ToolResponse(
                    user_response=(
                        f"♻️ Browser session already running.\n"
                        f"[Open the live browser]({existing.vnc_url})\n"
                        f"```\n{_summary(existing)}\n```"
                    ),
                    llm_response=(
                        "A browser session is already running for this chat. "
                        f"Use this session_id in subsequent calls: {existing.id}. "
                        f"Live view: {existing.vnc_url}. "
                        f"{_sharing_note(existing)}"
                        + (f"\n{recipe_note}" if recipe_note else "")
                    ),
                )

        session = await asyncio.to_thread(
            manager.create_session,
            session_name,
            chat_id,
            initial_url,
            description,
            share_project,
            share_folders,
            None,
            downloads_folder,
        )
        _persist_to_chat(settings, session, chat_id)
        cdp_note = _cdp_only_note(session)
        image_note = ""
        if not cdp_note:
            # The runtime owns the recorder/watcher; warm it now (short wait) so
            # the first human click is captured AND the model gets the initial
            # browser image in this same result instead of spending a turn on
            # action='state'. A slow container falls back to background warming.
            try:
                await asyncio.wait_for(manager.warm_runtime(session), timeout=2.5)
            except Exception:
                try:
                    asyncio.create_task(manager.warm_runtime(session))
                except RuntimeError:
                    pass
            try:
                runtime = await manager.get_runtime(session)
                image = runtime.image()
                if image.get("tabs"):
                    image_note = "\nInitial browser image (no need to snapshot first):\n" + _format_image(
                        image, text_chars=1200
                    )
            except Exception as ex:
                logger.debug("start_session: initial browser image unavailable: %s", ex)

        recipe_note = _known_recipe_note(settings, initial_url or session.current_url)
        llm = (
            f"Browser session '{session.name}' started. session_id: {session.id}. "
            f"Live view (same browser the user sees): {session.vnc_url}. "
            "Use browse_action/get_screenshot/get_source_code/browser_activity "
            "with this session_id. For a long multi-step flow (upload files, fill a "
            "whole form, book a slot) prefer browser_agent(task=...). "
            "Call finish_session when done. "
            f"{_sharing_note(session)}"
            + (f"\n{recipe_note}" if recipe_note else "")
            + (f"\n{cdp_note}" if cdp_note else "")
            + image_note
        )
        return ToolResponse(
            user_response=(
                f"✅ Browser session '{session.name}' ready.\n"
                f"[Open the live browser]({session.vnc_url})\n"
                f"```\n{_summary(session)}\n```"
            ),
            llm_response=llm,
        )
    except Exception as ex:
        logger.exception("start_session failed: %s", ex)
        return ToolResponse(
            user_response=f"❌ Failed to start browser session: {ex}",
            llm_response=f"Failed to start browser session: {ex}",
        )


def _format_image(image: Dict[str, Any], text_chars: int = 2000) -> str:
    """Render the background browser image (tabs + active text) as compact text."""
    tabs = (image or {}).get("tabs") or []
    active = next((tab for tab in tabs if tab.get("active")), None)
    lines = []
    for tab in tabs:
        marker = " *active*" if tab.get("active") else ""
        lines.append(
            f"- [{tab.get('index')}] {tab.get('title') or '(untitled)'} — "
            f"{tab.get('url')}{marker}"
        )
    age = (image or {}).get("age_ms")
    data = (
        f"browser image: {len(tabs)} tab(s), updated {age if age is not None else '?'} ms ago"
        + ("\n" + "\n".join(lines) if lines else " (no tabs yet)")
    )
    if active and active.get("text"):
        data += f"\n\nactive tab text:\n{str(active['text'])[:text_chars]}"
    data += (
        f"\n\nscreenshots cached: {sum(1 for tab in tabs if tab.get('screenshot'))}"
        f" · reconnects: {(image or {}).get('reconnects')}"
        f" · connected for {(image or {}).get('connected_for_s')} s"
    )
    return data


async def browse_action(
    action: str,
    url: str = None,
    selector: str = None,
    value: str = None,
    script: str = None,
    key: str = None,
    timeout: int = None,
    index: int = None,
    x: int = 0,
    y: int = 0,
    ref: str = None,
    text: str = None,
    files: List[str] = None,
    steps: List[Dict[str, Any]] = None,
    variables: Dict[str, Any] = None,
    session_id: str = None,
    chat_id: str = None,
    user: Any = None,
    **kwargs,
) -> ToolResponse:
    """
    Perform an action on the shared browser page or on its tabs.

    This is the single fast path for page interaction: the session's persistent
    runtime keeps the Playwright connection open, so an action no longer pays a
    ~0.5 s connect/disconnect cost.  For several actions use ``action="batch"``
    (or a saved script with ``run_browser_script``): the whole list runs on one
    connection and one LLM turn.

    Args:
        action: One of ``navigate``, ``click``, ``fill``, ``execute``,
            ``text``, ``links``, ``snapshot``, ``back``, ``forward``,
            ``refresh``, ``wait``, ``press``, ``select``, ``hover``,
            ``scroll``, ``upload``, ``tabs``, ``new_tab``, ``switch_tab``,
            ``close_tab``, ``batch``, ``save_session``, ``restore_session``,
            ``list_sessions``, ``login``, ``state``.
        url: Target URL (navigate/new_tab) or URL fragment to wait for.
        selector: CSS selector (element actions).
        value: Text to fill or option to select; the name/id for
            ``save_session``, ``restore_session``, ``list_sessions`` and ``login``.
        script: JavaScript to run (for ``execute``; use ``return ...``).
            Top-level ``await`` is supported.
        key: Keyboard key for ``press``.
        timeout: Timeout in milliseconds for waits.  For ``wait`` without a
            target it is how long to watch for the human/page to change
            (default 20000, max 60000); the call returns as soon as anything
            happens, so it is one call instead of polling ``browser_activity``.
        index: Tab index for ``switch_tab`` / ``close_tab``.
        x: Horizontal scroll offset.
        y: Vertical scroll offset.
        ref: Element ref returned by ``snapshot`` (preferred over CSS).
        text: Visible text to click/wait for.
        files: Files for ``upload``: names inside the session's Downloads
            folder (``photo.jpg``), paths relative to a shared folder, browser
            paths (``/downloads/photo.jpg``) or host absolute paths.  The bytes
            are streamed to the file input, so the file does not need to exist
            inside the container.
        steps: Action list for ``batch``.
        variables: ``{{name}}`` values for ``batch``.
        session_id: Session to act on (returned by ``start_session``).
        chat_id: Alternative to ``session_id`` — resolves the chat's session.
        **kwargs: Injected ``settings``.

    Returns:
        ToolResponse with the action result, current URL and title.
    """
    settings = _settings_from(kwargs)
    manager = _manager(settings)
    session = _resolve_session(manager, session_id=session_id, chat_id=chat_id)

    if not session:
        return ToolResponse(
            user_response="❌ No browser session found. Call `start_session` first.",
            llm_response="No browser session available. Start one with start_session.",
        )
    if session.status != "running":
        try:
            session = await manager.restart_session(session.id)
        except Exception as ex:
            return ToolResponse(
                user_response=f"❌ Session is not running and could not be restarted: {ex}",
                llm_response=f"Session {session.id} is not running (status: {session.status}) and restart failed: {ex}",
            )
        if not session:
            return ToolResponse(
                user_response="❌ The browser session no longer exists.",
                llm_response="The browser session was deleted or cannot be resolved.",
            )

    cdp_note = _cdp_only_note(session)
    if cdp_note:
        return ToolResponse(
            user_response=f"ℹ️ {cdp_note}",
            llm_response=(
                f"{cdp_note}\nDo not retry page actions on this session: tell the user and "
                "either continue in the live view yourself or start a CDP-enabled session."
            ),
        )

    action = (action or "").lower().strip()
    params = {
        key_name: value
        for key_name, value in {
            "url": url, "selector": selector, "value": value, "script": script,
            "key": key, "timeout": timeout, "index": index, "x": x, "y": y,
            "ref": ref, "text": text,
            "files": (
                [str(files)] if isinstance(files, (str, bytes))
                else [str(item) for item in files] if files else None
            ),
        }.items()
        if value is not None
    }
    if action == "batch":
        params["steps"] = len(steps or [])
    started_at = time.monotonic()
    runtime = None
    try:
        runtime = await manager.get_runtime(session)
        if action == "navigate":
            if not url:
                raise ValueError("url is required for navigate")
            title = await runtime.run("navigate", url)
            data = f"navigated to {url}\ntitle: {title}"

        elif action == "click":
            if ref:
                await runtime.run("click_ref", ref)
                data = f"clicked ref {ref}"
            elif text:
                await runtime.run("click_text", text)
                data = f"clicked text {text!r}"
            elif selector:
                await runtime.run("click", selector)
                data = f"clicked {selector}"
            else:
                raise ValueError("click needs ref, selector or text")

        elif action == "fill":
            if value is None:
                raise ValueError("value is required for fill")
            if ref:
                await runtime.run("fill_ref", ref, str(value))
                data = f"filled ref {ref}"
            elif selector:
                await runtime.run("fill", selector, str(value))
                data = f"filled {selector}"
            else:
                raise ValueError("fill needs ref or selector")

        elif action == "upload":
            from codx.junior.browser import uploads as browser_uploads

            # No selector is valid: the runtime picks the page's file input (or
            # the input a click opens). Pass one when the page has several.
            target = selector or (f'[data-codx-ref="{str(ref).replace(chr(34), "")}"]' if ref else None)
            try:
                resolved, payloads = browser_uploads.prepare_upload(
                    session, files or [], value=value
                )
            except browser_uploads.UploadError as ex:
                raise ValueError(str(ex)) from ex
            receipt = await runtime.run("upload_files", target, payloads)
            where = target or "the page's file input"
            data = (
                f"uploaded {receipt.get('count', len(payloads))} file(s) to {where} "
                f"(via {receipt.get('strategy') or 'input'}): "
                + ", ".join(item.describe() for item in resolved)
                + "\nThe page should now receive the file(s); verify the preview/attachment "
                "before submitting."
            )

        elif action in ("save_session", "save_site_session"):
            name = str(value or "").strip()
            if not name:
                raise ValueError("save_session needs value=<name for the saved site session>")
            state = await runtime.run("export_state")
            store = _browser_store(settings)
            try:
                meta = store.save_session(name, state, getattr(user, "username", None))
            except Exception as ex:
                raise _store_error(ex) from ex
            data = (
                f"saved site session '{meta['name']}': {meta['cookie_count']} cookie(s), "
                f"{meta['origin_count']} origin(s). Apply it to any browser of this project "
                "with action='restore_session' value='" + meta["name"] + "'. "
                "The cookie values never leave the store."
            )

        elif action in ("restore_session", "apply_session"):
            name = str(value or "").strip()
            if not name:
                raise ValueError("restore_session needs value=<saved session name or id>")
            store = _browser_store(settings)
            try:
                meta, state = store.session_state(name, getattr(user, "username", None))
            except Exception as ex:
                raise _store_error(ex) from ex
            receipt = await runtime.run("import_state", state)
            data = (
                f"restored site session '{meta['name']}': {receipt.get('cookies', 0)} cookie(s) "
                f"and {receipt.get('localStorage', 0)} localStorage item(s). "
                "The page was reloaded when it belonged to a restored origin; "
                "otherwise open the site and it will be logged in."
            )

        elif action in ("list_sessions", "site_sessions"):
            store = _browser_store(settings)
            entries = store.sessions()
            if not entries:
                data = "no saved site sessions yet (action='save_session' value=<name>)"
            else:
                lines = []
                for entry in entries:
                    origins = ", ".join(entry.get("origins") or []) or "no origins"
                    lines.append(
                        f"- {entry.get('name')} ({entry.get('cookie_count', 0)} cookies; "
                        f"{origins}; updated {entry.get('updated_at') or '?'})"
                    )
                data = "saved site sessions:\n" + "\n".join(lines)

        elif action == "login":
            name = str(value or "").strip()
            if not name:
                raise ValueError("login needs value=<credential name or id>")
            store = _browser_store(settings)
            try:
                meta, password = store.credential(name, getattr(user, "username", None))
            except Exception as ex:
                raise _store_error(ex) from ex
            if url:
                await runtime.run("navigate", url)
            receipt = await runtime.run(
                "login", meta.get("username") or "", password, True
            )
            data = (
                f"filled the login form of '{meta['name']}' "
                f"({receipt.get('fields', 0)} field(s)"
                + (", submitted with Enter" if receipt.get("submitted") else "")
                + "). The password was never returned to the model. "
                "Check the page: if the site needs a specific button, click it."
            )

        elif action == "execute":
            if not script:
                raise ValueError("script is required for execute")
            result = await runtime.run("execute_script", script)
            serialized = json.dumps(result, ensure_ascii=False, default=str)
            data = f"script result: {serialized[:4000]}"

        elif action in ("text", "read"):
            page_text = (await runtime.text() or "").strip()
            data = f"page text:\n{page_text[:MAX_TEXT_CHARS]}"

        elif action == "links":
            links = await runtime.run("get_links", read_only=True)
            lines = [f"- {link.get('text') or '(no text)'} → {link.get('href')}" for link in links[:200]]
            data = f"links ({len(links)}):\n" + "\n".join(lines)

        elif action in ("state", "browser_state"):
            # No CDP call: the runtime keeps this image refreshed in the
            # background (tabs, active text, viewport screenshot).
            data = _format_image(runtime.image())

        elif action in ("snapshot", "page"):
            # Prefer the prefetched snapshot: it was captured right after the
            # previous action, so this call avoids a CDP round-trip.
            snapshot = await runtime.snapshot()
            data = _format_snapshot(snapshot)

        elif action == "back":
            title = await runtime.run("go_back")
            data = f"went back\ntitle: {title}"

        elif action == "forward":
            title = await runtime.run("go_forward")
            data = f"went forward\ntitle: {title}"

        elif action == "refresh":
            title = await runtime.run("reload")
            data = f"page refreshed\ntitle: {title}"

        elif action == "wait":
            # Waits never change the page, so they are allowed while the human
            # owns the lease: that is exactly when the AI needs to wait.
            if text:
                await runtime.run(
                    "wait_for_text", str(text), timeout=timeout or 5000, read_only=True
                )
                data = f"text appeared: {text!r}"
            elif selector:
                await runtime.run(
                    "wait_for_selector", selector, timeout=timeout or 5000, read_only=True
                )
                data = f"element appeared: {selector}"
            elif url:
                await runtime.run(
                    "wait_for_url", url, timeout=timeout or 5000, read_only=True
                )
                data = f"url matched: {url}"
            else:
                # Co-driving: park one call until the human acts or the page
                # navigates, instead of polling browser_activity once per turn.
                result = await runtime.wait_for_change(timeout or 20000)
                data = _format_wait(result)

        elif action == "press":
            if not selector or not key:
                raise ValueError("selector and key are required for press")
            await runtime.run("press", selector, key)
            data = f"pressed {key} on {selector}"

        elif action == "select":
            if not selector or value is None:
                raise ValueError("selector and value are required for select")
            await runtime.run("select", selector, value)
            data = f"selected {value} on {selector}"

        elif action == "hover":
            if not selector:
                raise ValueError("selector is required for hover")
            await runtime.run("hover", selector)
            data = f"hovered {selector}"

        elif action == "scroll":
            await runtime.run("scroll", x=x or 0, y=y or 0)
            data = f"scrolled by x={x or 0}, y={y or 0}"

        elif action == "tabs":
            tabs = await runtime.run_tabs("tabs")
            data = f"open tabs ({len(tabs)}):\n" + browser_tabs.format_tabs(tabs)

        elif action == "new_tab":
            tab = await runtime.run_tabs("new_tab", url=url)
            data = (
                f"opened tab [{tab['index']}] {tab['title'] or '(untitled)'}\n"
                f"url: {tab['url']}"
            )

        elif action == "switch_tab":
            tab = await runtime.run_tabs("switch_tab", index=index)
            data = (
                f"switched to tab [{tab['index']}] {tab['title'] or '(untitled)'}\n"
                f"url: {tab['url']}"
            )

        elif action == "close_tab":
            tab = await runtime.run_tabs("close_tab", index=index)
            data = (
                f"closed tab [{tab['closed_index']}] "
                f"{tab['closed_title'] or tab['closed_url']}\n"
                f"active tab is now [{tab['index']}] {tab['title'] or '(untitled)'}\n"
                f"url: {tab['url']}"
            )

        elif action == "batch":
            if not steps:
                raise ValueError("steps is required for batch")
            result = await runtime.run_steps(steps, variables or {})
            data = _format_batch(result)

        else:
            raise ValueError(
                "unknown action '%s' (use navigate|click|fill|execute|text|links|"
                "snapshot|back|forward|refresh|wait|press|select|hover|scroll|"
                "upload|save_session|restore_session|list_sessions|login|tabs|new_tab|"
                "switch_tab|close_tab|batch)" % action
            )

        session = getattr(runtime, "session", None) or session
        session.mark_active()
        elapsed_ms = round((time.monotonic() - started_at) * 1000)
        data = f"{data}\n- took {elapsed_ms} ms"
        # The action already changed the page: return a compact page summary
        # in the same result instead of making the model ask for a snapshot.
        if action in PAGE_BRIEF_ACTIONS:
            try:
                brief = await runtime.page_brief()
            except Exception as ex:
                logger.debug("page_brief after '%s' failed: %s", action, ex)
                brief = {}
            if brief:
                data = (
                    f"{data}\npage: "
                    + json.dumps(brief, ensure_ascii=False, default=str)[:4500]
                )
        # Landing on a site is the cheapest moment to surface its recorded
        # recipes: the model can run one instead of exploring the page.
        if action in ("navigate", "new_tab", "switch_tab"):
            recipe_note = _known_recipe_note(settings, session.current_url)
            if recipe_note:
                data = f"{data}\n\n{recipe_note}"
        manager.log_activity(
            session,
            actor="ai",
            action=action,
            chat_id=chat_id,
            params=params,
            url=session.current_url,
            title=session.current_title,
            duration_ms=(time.monotonic() - started_at) * 1000,
            result={"message": data[:1000]},
        )
        logger.info("browse_action '%s' completed for session %s", action, session.id[:8])
        return ToolResponse(
            user_response=(
                f"✅ `{action}` done on session {session.id[:8]}.\n\n{data}\n\n"
                f"URL: {session.current_url or '(blank)'}\nTitle: {session.current_title or '(blank)'}"
            ),
            llm_response=(
                f"Action '{action}' on session {session.id} succeeded.\n{data}\n"
                f"URL: {session.current_url or '(blank)'}\nTitle: {session.current_title or '(blank)'}"
            ),
        )
    except Exception as ex:
        control_error = ex.__class__.__name__ == "BrowserControlError"
        logger.info("browse_action '%s' stopped: %s", action, ex)
        session = getattr(runtime, "session", None) or session
        manager.log_activity(
            session,
            actor="ai",
            action=action or "unknown",
            chat_id=chat_id,
            params=params,
            success=False,
            error=str(ex),
            duration_ms=(time.monotonic() - started_at) * 1000,
        )
        if control_error:
            if getattr(session, "controller", "ai") == "ai":
                # Not a hand-off: the human is actively typing/clicking and the
                # quiet-gap wait expired.  Give the model the one call that
                # actually helps instead of a blind retry.
                return ToolResponse(
                    user_response=(
                        f"⏸ The user is still interacting with the page: {ex}\n\n"
                        f"[Open the live browser]({session.vnc_url or ''}) to see it."
                    ),
                    llm_response=(
                        f"The action '{action}' was not executed because the human is "
                        f"still interacting with the shared browser: {ex}\n"
                        "Call browse_action(action='wait') to park one call until they "
                        "pause, or browser_activity(actor='user') to see what they did. "
                        "Do not retry the same action immediately."
                    ),
                )
            return ToolResponse(
                user_response=(
                    f"⏸ Paused: {ex}\n\n"
                    f"The user is driving. [Open the live browser]({session.vnc_url or ''}) "
                    "and continue when they hand control back."
                ),
                llm_response=(
                    f"The AI paused because the human has control of the shared browser: {ex}\n"
                    f"Live view: {session.vnc_url}. Use browser_activity to see what the user did, "
                    "or browser_control(action='status'). Do not retry until the user gives control back."
                ),
            )
        return ToolResponse(
            user_response=f"❌ `{action}` failed: {ex}",
            llm_response=f"Action '{action}' on session {session.id} failed: {ex}",
        )


async def browser_agent(
    task: str,
    max_steps: int = None,
    timeout: int = None,
    llm_model: str = None,
    session_id: str = None,
    chat_id: str = None,
    user: Any = None,
    **kwargs,
) -> ToolResponse:
    """
    Hand a whole multi-step goal to the browser-use agent.

    ``browse_action`` is one action per call: right for a click, a fill or a
    quick read.  A real site flow ("upload this picture to LinkedIn and publish
    the post", "fill the whole application form", "find and book the earliest
    slot") costs dozens of calls and a lot of context that way.

    This tool delegates the goal to `browser-use`, which runs its own agent loop
    (its model writes Python cells against raw CDP in a persistent REPL) on the
    *same* browser the human sees in the live view.  It uploads files from the
    session's Downloads/shared folders and returns one final answer, plus the
    steps, token usage, cost and downloads.  SmolAgent keeps the conversation
    with the human; the browsing sub-conversation belongs to browser-use.

    Args:
        task: The goal, in natural language. Include the specific values (post
            text, file name, form data) - the agent has no access to this chat.
        max_steps: Maximum model requests for the run (default 30, max 200).
        timeout: Seconds for the agent loop (default 300, max 900).
        llm_model: Injected: the model this chat turn resolved (chat model >
            profile model > project default). The browser agent always uses the
            chat's model; the gateway prices it with that model's rates.
        session_id: Session to drive (default: the chat's session).
        chat_id: Injected chat id (activity attribution).
        user: Injected user (wallet check and analytics attribution).

    Returns:
        ToolResponse: Final answer + run summary, or why the run could not start.
    """
    from codx.junior.browser import use_agent

    settings = _settings_from(kwargs)
    manager = _manager(settings)
    session = _resolve_session(manager, session_id=session_id, chat_id=chat_id)
    if not session:
        return ToolResponse(
            user_response="❌ No browser session found. Call `start_session` first.",
            llm_response=(
                "No browser session is available. Call start_session first, then "
                "browser_agent with the same session_id."
            ),
        )
    if session.status != "running":
        try:
            session = await manager.restart_session(session.id)
        except Exception as ex:
            return ToolResponse(
                user_response=f"❌ Browser session could not be started: {ex}",
                llm_response=(
                    f"Session {session.id} is not running (status: {session.status}) and "
                    f"restart failed: {ex}. Fix the session before using browser_agent."
                ),
            )

    cdp_note = _cdp_only_note(session)
    if cdp_note:
        return ToolResponse(
            user_response=f"ℹ️ {cdp_note}",
            llm_response=(
                f"{cdp_note}\nDo not delegate a browsing task to this session; tell the "
                "user and use a Chromium-based image for AI browsing."
            ),
        )

    mode = use_agent.agent_mode()
    if mode == "native":
        return ToolResponse(
            user_response=(
                "ℹ️ The browser-use agent is disabled (CODX_BROWSER_AGENT=native). "
                "The shared browser still works through `browse_action`."
            ),
            llm_response=(
                "browser-use is disabled in this deployment (CODX_BROWSER_AGENT=native). "
                "Use browse_action(action='snapshot'/'batch'/'upload') to drive the page."
            ),
        )
    if not use_agent.browser_use_available():
        return ToolResponse(
            user_response=(
                "❌ The browser-use agent is not installed.\n\n"
                f"Install it with `{use_agent.BROWSER_USE_INSTALL}` and restart the API. "
                "Meanwhile `browse_action` drives the same browser, including "
                "`action='upload'` for file inputs."
            ),
            llm_response=(
                "browser-use is not installed, so browser_agent cannot run. "
                f"Install it with {use_agent.BROWSER_USE_INSTALL} and restart the API. "
                "Until then use browse_action(action='snapshot' then click/fill/upload); "
                "it is the same browser and the same session."
            ),
        )

    started_at = time.monotonic()
    runtime = None
    try:
        runtime = await manager.get_runtime(session)
    except Exception as ex:
        logger.warning("browser_agent: runtime unavailable: %s", ex)
        runtime = None

    runner = use_agent.BrowserUseRunner(
        settings=settings,
        session=session,
        manager=manager,
        chat_id=chat_id,
        user=user,
    )
    try:
        result = await runner.run(
            task,
            max_steps=max_steps,
            timeout=timeout,
            model=llm_model,
            runtime=runtime,
        )
    except use_agent.BrowserUsePaused as ex:
        return ToolResponse(
            user_response=(
                f"⏸ browser-use paused: {ex}\n\n"
                f"[Open the live browser]({session.vnc_url or ''}) to continue."
            ),
            llm_response=(
                f"browser-use stopped because the human took the live view of session "
                f"{session.id}: {ex}\n"
                "Do not retry immediately. Call browse_action(action='wait') to park one "
                "call until they pause, or browser_activity(actor='user') to see what they "
                "did, then continue from the current page when control comes back."
            ),
            cost=float(getattr(ex, "cost_cxj", 0.0) or 0.0),
        )
    except use_agent.BrowserUseError as ex:
        return ToolResponse(
            user_response=f"❌ browser-use could not complete the task: {ex}",
            llm_response=(
                f"browser-use failed for session {session.id}: {ex}\n"
                "Inspect the live view / browser_activity, fix the page state (or the "
                "missing file name), then retry browser_agent or continue step by step "
                "with browse_action."
            ),
            cost=float(getattr(ex, "cost_cxj", 0.0) or 0.0),
        )
    except Exception as ex:  # pragma: no cover - unexpected browser-use failure
        logger.exception("browser_agent failed")
        return ToolResponse(
            user_response=f"❌ browser_agent failed: {ex}",
            llm_response=f"browser_agent crashed for session {session.id}: {ex}",
        )
    finally:
        # The agent may have left the browser on another page/tab: resync the
        # session so the UI and the next tool call start from what is on screen.
        if runtime is not None:
            try:
                state = await runtime.state()
                session.current_url = state.get("current_url") or session.current_url
                session.current_title = state.get("current_title") or session.current_title
            except Exception as ex:
                logger.debug("Could not refresh session state after browser_agent: %s", ex)

    # The tool timeout may have been the real end of the run; still show what
    # the browser did so the next turn can continue from a known state.
    try:
        brief = await runtime.page_brief() if runtime is not None else {}
    except Exception:
        brief = {}

    summary = result.summary_line()
    if result.gateway:
        accounting = (
            f"cost: {result.cost_cxj:.6f} cxj (accounted in this chat through the "
            "codx-junior LLM gateway)"
        )
    else:
        accounting = (
            "cost: not accounted - the LLM gateway is disabled "
            f"({use_agent.llm_gateway.ENV_ENABLED}=off), so these model calls went "
            "straight to the provider"
        )
    answer = (result.final_result or "").strip() or "(the agent returned no final answer)"
    downloads = "\n".join(f"- {path}" for path in result.downloads[:20])
    data = (
        f"{answer}\n\n"
        f"browser-use: {summary}\n"
        f"{accounting}\n"
        f"task: {result.task[:400]}\n"
        f"model: {result.model or '(project default)'}\n"
        f"workspace: {result.workspace}"
        + (f"\ndownloads:\n{downloads}" if downloads else "")
    )
    if brief:
        data += "\npage: " + json.dumps(brief, ensure_ascii=False, default=str)[:3500]
    if result.success is False:
        data += (
            "\nThe agent reported it did NOT finish the goal. Inspect the page, then "
            "retry with a more specific task or finish with browse_action."
        )

    logs: List[str] = [
        f"{datetime.utcnow().isoformat(timespec='milliseconds')}Z INFO "
        f"browser-use run: {summary} "
        f"({(time.monotonic() - started_at):.1f}s, status={result.status or 'done'})"
    ]

    return ToolResponse(
        user_response=(
            f"✅ browser-use finished on session {session.id[:8]}.\n\n{data}\n\n"
            f"URL: {session.current_url or '(blank)'}\nTitle: {session.current_title or '(blank)'}"
        ),
        cost=result.cost_cxj,
        llm_response=(
            f"browser-use completed the task on session {session.id}. {summary}\n"
            f"{data}\n"
            f"Current URL: {session.current_url or '(blank)'}; title: {session.current_title or '(blank)'}. "
            "The run is in browser_activity. Continue from this state with browse_action "
            "if anything is still missing; do not repeat the whole flow. If the agent says "
            "it needs the human (login, MFA, captcha, payment), call "
            "browser_control(action='handoff', reason=...) with its exact ask and stop."
        ),
        logs=logs,
    )


def _format_wait(result: Dict[str, Any]) -> str:
    """Render a co-driving wait result (change + activity) compactly."""
    if not isinstance(result, dict):
        return str(result)
    if result.get("changed"):
        head = (
            f"page changed ({result.get('reason')}) after "
            f"{result.get('waited_ms')} ms"
        )
    else:
        head = f"no change within {result.get('waited_ms')} ms"
    lines = [
        head,
        f"url: {result.get('url') or ''}",
        f"title: {result.get('title') or ''}",
        f"controller: {result.get('controller') or '?'}",
    ]
    for entry in result.get("activity") or []:
        target = (entry.get("params") or {}).get("target") or {}
        detail = ""
        if isinstance(target, dict):
            detail = target.get("text") or target.get("selector") or target.get("name") or ""
        if detail:
            detail = f" · {str(detail)[:80]}"
        lines.append(
            f"- {entry.get('actor') or '?'}: {entry.get('action') or '?'}"
            f"{detail} ({entry.get('ts')})"
        )
    page = result.get("page")
    if isinstance(page, dict):
        compact = dict(page)
        compact["interactive"] = (compact.get("interactive") or [])[:40]
        compact["text"] = (compact.get("text") or "")[:2000]
        lines.append(
            "page: " + json.dumps(compact, ensure_ascii=False, default=str)[:6000]
        )
    return "\n".join(lines)[:12000]


def _format_snapshot(snapshot: Dict[str, Any]) -> str:
    """Render a snapshot compactly for the model (JSON, bounded)."""
    if not isinstance(snapshot, dict):
        return str(snapshot)
    payload = dict(snapshot)
    interactive = payload.get("interactive") or []
    payload["interactive"] = interactive[:80]
    text = payload.get("text") or ""
    payload["text"] = text[:4000]
    return json.dumps(payload, ensure_ascii=False, indent=None)[:14000]


def _format_batch(result: Dict[str, Any]) -> str:
    """Render a batch/script result without flooding the context."""
    lines = []
    for step in result.get("steps") or []:
        status = "ok" if step.get("ok") else "FAIL"
        lines.append(
            f"- [{step.get('index')}] {status} {step.get('action')} ({step.get('ms')} ms)"
            + (f" — {step.get('error')}" if step.get("error") else "")
        )
    summary = (
        f"batch {'ok' if result.get('ok') else 'failed'} in {result.get('ms')} ms"
        + (f" at step {result.get('failed_step')}: {result.get('error')}" if result.get("error") else "")
    )
    outputs = result.get("outputs") or {}
    if outputs:
        summary += "\noutputs: " + json.dumps(outputs, ensure_ascii=False, default=str)[:4000]
    return summary + ("\n" + "\n".join(lines) if lines else "")


async def get_screenshot(
    session_id: str = None,
    chat_id: str = None,
    full_page: bool = False,
    **kwargs,
) -> ToolResponse:
    """
    Take a screenshot of the current page.

    Args:
        session_id: Session to capture (or ``chat_id`` to resolve it).
        chat_id: Alternative to ``session_id``.
        full_page: Capture the whole scrollable page (slow, huge payload) or
            the viewport (default; served from the background image when fresh).
        **kwargs: Injected ``settings``.

    Returns:
        ToolResponse whose ``user_response`` embeds the PNG image.
    """
    settings = _settings_from(kwargs)
    manager = _manager(settings)
    session = _resolve_session(manager, session_id=session_id, chat_id=chat_id)

    if not session or session.status != "running":
        return ToolResponse(
            user_response="❌ No running browser session (call `start_session` first).",
            llm_response="No running browser session available for screenshot.",
        )

    started_at = time.monotonic()
    try:
        runtime = await manager.get_runtime(session)
        data_uri = await runtime.screenshot(full_page=full_page)
        session = getattr(runtime, "session", None) or session
        manager.log_activity(
            session,
            actor="ai",
            action="screenshot",
            chat_id=chat_id,
            params={"full_page": bool(full_page)},
            url=session.current_url,
            title=session.current_title,
            duration_ms=(time.monotonic() - started_at) * 1000,
        )
        return ToolResponse(
            user_response=f"📸 Screenshot of session {session.id[:8]}:\n\n![screenshot]({data_uri})",
            llm_response=(
                f"Screenshot captured for session {session.id} "
                f"(url: {session.current_url or '(blank)'}, title: {session.current_title or '(blank)'})."
            ),
        )
    except Exception as ex:
        logger.info("get_screenshot failed: %s", ex)
        manager.log_activity(
            session, actor="ai", action="screenshot", chat_id=chat_id,
            success=False, error=str(ex),
            duration_ms=(time.monotonic() - started_at) * 1000,
        )
        return ToolResponse(
            user_response=f"❌ Screenshot failed: {ex}",
            llm_response=f"Screenshot failed for session {session.id}: {ex}",
        )


async def get_source_code(
    session_id: str = None,
    chat_id: str = None,
    **kwargs,
) -> ToolResponse:
    """
    Return the current page HTML source.

    Args:
        session_id: Session to read (or ``chat_id`` to resolve it).
        chat_id: Alternative to ``session_id``.
        **kwargs: Injected ``settings``.

    Returns:
        ToolResponse with the (truncated) HTML.
    """
    settings = _settings_from(kwargs)
    manager = _manager(settings)
    session = _resolve_session(manager, session_id=session_id, chat_id=chat_id)

    if not session or session.status != "running":
        return ToolResponse(
            user_response="❌ No running browser session (call `start_session` first).",
            llm_response="No running browser session available for source retrieval.",
        )

    started_at = time.monotonic()
    try:
        runtime = await manager.get_runtime(session)
        html = await runtime.run("get_content", read_only=True)
        truncated = (html or "")[:MAX_SOURCE_CHARS]
        session = getattr(runtime, "session", None) or session
        manager.log_activity(
            session,
            actor="ai",
            action="source",
            chat_id=chat_id,
            url=session.current_url,
            title=session.current_title,
            duration_ms=(time.monotonic() - started_at) * 1000,
            result={"chars": len(html or "")},
        )
        return ToolResponse(
            user_response=(
                f"📄 Source of session {session.id[:8]} "
                f"(url: {session.current_url}, title: {session.current_title}):\n"
                f"```html\n{truncated}\n```"
            ),
            llm_response=(
                f"HTML source for session {session.id} "
                f"(url: {session.current_url}, title: {session.current_title}):\n{truncated}"
            ),
        )
    except Exception as ex:
        logger.info("get_source_code failed: %s", ex)
        manager.log_activity(
            session, actor="ai", action="source", chat_id=chat_id,
            success=False, error=str(ex),
            duration_ms=(time.monotonic() - started_at) * 1000,
        )
        return ToolResponse(
            user_response=f"❌ Failed to read source: {ex}",
            llm_response=f"Source retrieval failed for session {session.id}: {ex}",
        )


async def browser_activity(
    session_id: str = None,
    chat_id: str = None,
    limit: int = 30,
    actor: str = None,
    since: str = None,
    **kwargs,
) -> ToolResponse:
    """
    Return the recent user/AI actions on the shared browser session.

    This is the co-browsing awareness tool.  Human clicks, edits, submits and
    scrolls performed in the live VNC view are captured by the in-page recorder
    and drained by the session runtime, so they appear here as ``actor=user``
    entries (input values are redacted).  Use ``since`` after a long action to
    get only what changed, and ``actor="user"`` to ignore the AI's own steps.
    """
    settings = _settings_from(kwargs)
    manager = _manager(settings)
    session = _resolve_session(manager, session_id=session_id, chat_id=chat_id)

    if not session:
        return ToolResponse(
            user_response="❌ No browser session found.",
            llm_response="No browser session available. Start one with start_session.",
        )

    # Pick up events that happened since the last drain (the watcher runs every
    # second, but a just-finished user edit must be visible immediately).
    runtime = manager.peek_runtime(session.id)
    if runtime is not None:
        try:
            await runtime.drain()
        except Exception as ex:
            logger.debug("browser_activity drain failed: %s", ex)

    entries = manager.get_activity(session.id, limit=max(int(limit or 30) * 3, 100))
    if actor:
        entries = [entry for entry in entries if (entry.get("actor") or "") == actor]
    if since:
        entries = [entry for entry in entries if (entry.get("ts") or "") >= since]
    entries = entries[-max(1, min(int(limit or 30), 200)):]
    if not entries:
        return ToolResponse(
            user_response=f"🧾 No activity recorded yet for session {session.id[:8]}.",
            llm_response=f"No activity recorded for session {session.id}.",
        )

    lines = []
    for entry in entries:
        actor_name = entry.get("actor") or "?"
        action = entry.get("action") or "?"
        status = "ok" if entry.get("success", True) else "error"
        url = entry.get("url") or ""
        target = (entry.get("params") or {}).get("target") or {}
        if not isinstance(target, dict):
            target = {"text": str(target)}
        detail = target.get("text") or target.get("selector") or target.get("name") or ""
        if detail:
            detail = f" · {str(detail)[:80]}"
        lines.append(f"- [{entry.get('ts')}] {actor_name}: {action}{detail} ({status}) {url}".rstrip())
    text = "\n".join(lines)
    session = getattr(runtime, "session", None) or session
    control = (
        f"controller={session.controller}"
        + (f", handoff={session.handoff_reason}" if session.handoff_reason else "")
        + (", user_active=yes" if session.user_is_active() else "")
    )
    return ToolResponse(
        user_response=f"🧾 Recent activity for session {session.id[:8]}:\n{text}",
        llm_response=(
            f"Recent activity for session {session.id} ({control}):\n{text}"
        ),
    )


async def finish_session(
    session_id: str = None,
    chat_id: str = None,
    delete: bool = False,
    **kwargs,
) -> ToolResponse:
    """
    Finish a browser session. Stopping keeps its profile; deleting removes it.

    Args:
        session_id: Session to finish (or ``chat_id`` to resolve it).
        chat_id: Alternative to ``session_id``.
        delete: When True the container is removed and the session folder
            (including its Chromium profile) is deleted. When False (default)
            the container is only stopped and the profile is preserved for a
            later restart.
        **kwargs: Injected ``settings``.

    Returns:
        ToolResponse with the finish result.
    """
    settings = _settings_from(kwargs)
    manager = _manager(settings)
    session = _resolve_session(manager, session_id=session_id, chat_id=chat_id)

    if not session:
        return ToolResponse(
            user_response="❌ No browser session found to finish.",
            llm_response="No browser session found to finish.",
        )

    try:
        runtime = manager.peek_runtime(session.id)
        if runtime is not None:
            await runtime.close()
            manager._runtimes.pop(session.id, None)
        if delete:
            await asyncio.to_thread(manager.delete_session, session.id)
            verb = "deleted"
        else:
            await asyncio.to_thread(manager.stop_session, session.id)
            verb = "stopped"

        logger.info("finish_session: %s session %s", verb, session.id[:8])
        return ToolResponse(
            user_response=f"🧹 Browser session {session.id[:8]} {verb}.",
            llm_response=(
                f"Browser session {session.id} {verb}. "
                + ("Profile preserved." if not delete else "Profile deleted with the session.")
                + (
                    f" Downloaded files stay in the project at '{session.downloads_path}'."
                    if session.downloads_path else ""
                )
            ),
        )
    except Exception as ex:
        logger.exception("finish_session failed: %s", ex)
        return ToolResponse(
            user_response=f"❌ Failed to finish session: {ex}",
            llm_response=f"Failed to finish session {session.id}: {ex}",
        )


async def browser_status(
    session_id: str = None,
    chat_id: str = None,
    **kwargs,
) -> ToolResponse:
    """Return cheap status of the shared browser (connection, controller, tabs)."""
    settings = _settings_from(kwargs)
    manager = _manager(settings)
    session = _resolve_session(manager, session_id=session_id, chat_id=chat_id)
    if not session:
        return ToolResponse(
            user_response="❌ No browser session found.",
            llm_response="No browser session available. Start one with start_session.",
        )
    try:
        runtime = await manager.get_runtime(session)
        state = await runtime.state()
    except Exception as ex:
        return ToolResponse(
            user_response=f"❌ Could not read the browser status: {ex}",
            llm_response=f"Browser status unavailable: {ex}",
        )
    text = "\n".join(f"{key}: {value}" for key, value in state.items())
    if state.get("ai_may_act"):
        text += (
            "\nnote: the page is quiet — you may act now. A recent click or "
            "keystroke is not a hand-off; only controller='user' is."
        )
    elif state.get("controller") == "ai":
        text += (
            "\nnote: the user is typing. Do not fail or retry blindly: "
            "browse_action(action='wait') parks one call until they pause."
        )
    return ToolResponse(
        user_response=f"ℹ️ Browser status for session {session.id[:8]}:\n```\n{text}\n```",
        llm_response=f"Browser status for session {session.id}:\n{text}",
    )


async def browser_control(
    action: str,
    reason: str = None,
    session_id: str = None,
    chat_id: str = None,
    **kwargs,
) -> ToolResponse:
    """Manage the human/AI control lease of the shared browser.

    Actions:
        ``status``      — show who is driving (alias of browser_status);
        ``handoff``     — pause the AI and ask the human to take over (MFA,
                          captcha, approval). The AI must stop after this call;
        ``take_control``— the human takes control and keeps it;
        ``give_back``   — hand control back to the AI;
        ``pause``       — alias of take_control with an AI reason.
    """
    settings = _settings_from(kwargs)
    manager = _manager(settings)
    session = _resolve_session(manager, session_id=session_id, chat_id=chat_id)
    if not session:
        return ToolResponse(
            user_response="❌ No browser session found.",
            llm_response="No browser session available. Start one with start_session.",
        )
    action = (action or "status").strip().lower()
    try:
        runtime = await manager.get_runtime(session)
    except Exception as ex:
        return ToolResponse(
            user_response=f"❌ Could not open the browser runtime: {ex}",
            llm_response=f"Browser runtime unavailable: {ex}",
        )
    if action in ("status", "info"):
        state = await runtime.state()
        return ToolResponse(
            user_response=f"ℹ️ {state.get('controller', 'ai')} controls session {session.id[:8]}.",
            llm_response=f"Browser control state: {state}",
        )
    if action in ("handoff", "request_handoff", "request"):
        if not reason:
            return ToolResponse(
                user_response="❌ `reason` is required for a handoff.",
                llm_response="A handoff needs a short reason (login, MFA, captcha, approval...).",
            )
        runtime.request_handoff(reason, chat_id=chat_id)
        session = getattr(runtime, "session", None) or session
        browser_events.broadcast_control(session)
        return ToolResponse(
            user_response=(
                f"🙋 Handoff requested: {reason}\n\n"
                f"[Open the live browser]({session.vnc_url or ''}) and complete it, "
                "then click **I'm done** (or use Give back to AI)."
            ),
            llm_response=(
                f"Control handed to the user because: {reason}. "
                f"Live view: {session.vnc_url}. The AI is paused; do not act until the user "
                "finishes and control is given back."
            ),
        )
    if action in ("take_control", "take", "pause"):
        runtime.take_control(chat_id=chat_id, reason=reason or "paused by the AI")
        session = getattr(runtime, "session", None) or session
        browser_events.broadcast_control(session)
        return ToolResponse(
            user_response=f"⏸ The user now has control of session {session.id[:8]}.",
            llm_response=(
                "The human now controls the page. The AI must not act. "
                "Call browser_activity to follow their work."
            ),
        )
    if action in ("give_back", "release", "resume"):
        runtime.release(chat_id=chat_id)
        session = getattr(runtime, "session", None) or session
        browser_events.broadcast_control(session)
        return ToolResponse(
            user_response=f"▶️ Control returned to the AI for session {session.id[:8]}.",
            llm_response="The AI has control again. Continue from the current page state.",
        )
    return ToolResponse(
        user_response=f"❌ Unknown control action '{action}'.",
        llm_response="Use status, handoff, take_control, give_back or pause.",
    )


# ---------------------------------------------------------------------------
# Tool JSON schemas
# ---------------------------------------------------------------------------

START_SESSION_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "start_session",
        "description": (
            "Start (or resume) the real Chromium browser shared with the user. Provide a short, "
            "human-readable name. Returns a session_id (use it in the other browser tools) and a "
            "live-view URL the user can open to watch and control the same browser. A session may "
            "be shared by several chats. Project folders are mounted inside the browser under "
            "/home/<project_name>/<folder> (open file://<mount>/<file>) and downloads are saved "
            "inside the project, so files can be shared with the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable name for the session (e.g. 'flight search').",
                },
                "initial_url": {
                    "type": "string",
                    "description": "Optional URL to open when the browser starts.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional short description of the session.",
                },
                "share_project": {
                    "type": "boolean",
                    "description": (
                        "Mount the whole project at /home/<project_name>. Omit it to mount only "
                        "the folders listed in share_folders (or the whole project when the list "
                        "is empty)."
                    ),
                },
                "share_folders": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Project folders to mount, e.g. ['docs', 'data']. Each one keeps its "
                        "relative path inside /home/<project_name>."
                    ),
                },
                "downloads_folder": {
                    "type": "string",
                    "description": (
                        "Project folder receiving the browser downloads (default "
                        "'.codx/browser_downloads/<session_id>')."
                    ),
                },
                "chat_id": {
                    "type": "string",
                    "description": "Chat id used to reuse an existing session and make it visible in the chat UI (optional).",
                },
            },
            "required": ["name"],
        },
    },
}

BROWSE_ACTION_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "browse_action",
        "description": (
            "Drive the shared browser page. Prefer action='snapshot' first: it returns "
            "interactive element refs (click/fill by ref) and the main text, much cheaper "
            "than full HTML. Mutating actions (navigate/click/batch/...) already include a "
            "compact `page` summary in their result, so do not snapshot again unless you need "
            "more than the returned refs/text. Use action='batch' for several steps in one call. "
            "Actions: navigate (url), click/fill (ref or selector or text), execute (script), "
            "text, links, snapshot, back, forward, refresh, wait (text/selector/url, or "
            "no target to wait for the human/page to change), "
            "press, select, hover, scroll, upload (files), tabs, new_tab, switch_tab, "
            "close_tab, batch. Use action='upload' to attach files from the session's "
            "Downloads/shared folders to a file input or an upload button: it streams "
            "the bytes, so it works for a remote browser and needs no container path. "
            "Hidden inputs are not in a snapshot: target them with selector="
            "'input[type=file]' (inside the dialog if one is open). "
            "action='state' returns the background browser image (every tab with URL/title, "
            "the active tab's text summary and screenshot availability) with no CDP wait: use "
            "it to decide what to do, and snapshot only when you need clickable refs. "
            "Login stores: action='save_session' value=<name> captures the cookies and "
            "localStorage of this browser; action='restore_session' value=<name> applies a "
            "saved one to this browser (any Chromium-family image of the project); "
            "action='list_sessions' lists them; action='login' value=<credential name> "
            "fills a login form with a stored credential without returning the password."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "navigate", "click", "fill", "execute", "text", "links",
                        "snapshot", "back", "forward", "refresh", "wait", "press",
                        "select", "hover", "scroll", "upload", "tabs", "new_tab",
                        "switch_tab", "close_tab", "batch", "save_session",
                        "restore_session", "list_sessions", "login", "state",
                    ],
                    "description": "Action to perform on the page or on its tabs.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session id returned by start_session.",
                },
                "url": {
                    "type": "string",
                    "description": "Target URL (navigate/new_tab) or URL fragment to wait for.",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector (click / fill / wait / press / select / hover).",
                },
                "ref": {
                    "type": "string",
                    "description": "Element ref from action='snapshot' (preferred over CSS).",
                },
                "text": {
                    "type": "string",
                    "description": "Visible text to click or wait for.",
                },
                "value": {
                    "type": "string",
                    "description": (
                        "Text to fill, option to select, (single file) the upload file, "
                        "or the name/id for save_session, restore_session and login."
                    ),
                },
                "script": {
                    "type": "string",
                    "description": (
                        "JavaScript to run (execute); use 'return ...' to get a value. "
                        "Top-level await is supported."
                    ),
                },
                "key": {"type": "string", "description": "Keyboard key for press (e.g. Enter)."},
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Wait timeout in milliseconds (max 60000). For wait with no "
                        "target it defaults to 20000: one call parks until the human "
                        "acts or the page navigates."
                    ),
                },
                "index": {
                    "type": "integer",
                    "description": "Tab index for switch_tab/close_tab (0-based, as listed by tabs).",
                },
                "x": {"type": "integer", "description": "Horizontal scroll offset."},
                "y": {"type": "integer", "description": "Vertical scroll offset."},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "upload: files to attach. Names inside the session's Downloads folder "
                        "(e.g. 'photo.jpg'), paths relative to a shared folder, browser paths "
                        "(e.g. '/downloads/photo.jpg') or host absolute paths. The bytes are "
                        "streamed to the browser."
                    ),
                },
                "steps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "For action='batch': ordered steps, e.g. "
                        "[{action:'goto',url:'...'},{action:'fill',ref:'e3',value:'...'},"
                        "{action:'click',text:'Sign in'},{action:'wait',text:'Dashboard'}]."
                    ),
                },
                "variables": {
                    "type": "object",
                    "description": "Values for {{name}} placeholders in batch steps.",
                },
            },
            "required": ["action"],
        },
    },
}

GET_SCREENSHOT_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "get_screenshot",
        "description": "Capture a PNG screenshot of the current browser page. Viewport captures are served from a background prefetch when the page has not changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session id returned by start_session.",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture the whole page (default true) or only the viewport.",
                },
            },
            "required": [],
        },
    },
}

GET_SOURCE_CODE_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "get_source_code",
        "description": "Return the current page HTML (capped). Prefer browse_action(action='snapshot') for reading; use this only when the markup itself matters.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session id returned by start_session.",
                }
            },
            "required": [],
        },
    },
}

BROWSER_ACTIVITY_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "browser_activity",
        "description": (
            "Return the recent actions performed by the user and the AI on the shared browser "
            "session. Use it to notice what the user did directly in the live view."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session id returned by start_session.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recent entries (default 30).",
                },
            },
            "required": [],
        },
    },
}

FINISH_SESSION_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "finish_session",
        "description": (
            "Finish the browser session. By default the container is stopped and the "
            "Chromium profile is kept for a later restart; set delete=true to remove "
            "the container, session metadata and profile."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session id returned by start_session.",
                },
                "delete": {
                    "type": "boolean",
                    "description": "Delete the container and profile (true) or only stop it and keep the profile (false, default).",
                },
            },
            "required": [],
        },
    },
}


BROWSER_STATUS_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "browser_status",
        "description": (
            "Return the shared browser status: connection, current URL/title, open tabs, "
            "who controls the page and whether the user is active. Cheap; use it before "
            "acting if you are unsure whether the browser is connected or the human took over."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session id (optional)."},
            },
            "required": [],
        },
    },
}

BROWSER_CONTROL_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "browser_control",
        "description": (
            "Manage the human/AI control lease of the shared browser. "
            "Use action='handoff' when the task needs the human (login, MFA, captcha, payment "
            "approval): the AI pauses and the UI shows a banner. Use action='give_back' after "
            "the user is done, and action='status' to inspect the lease."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "handoff", "take_control", "give_back", "pause"],
                    "description": "Control operation.",
                },
                "reason": {
                    "type": "string",
                    "description": "Human-readable reason for a handoff/pause.",
                },
                "session_id": {"type": "string", "description": "Session id (optional)."},
            },
            "required": ["action"],
        },
    },
}


BROWSER_AGENT_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "browser_agent",
        "description": (
            "Delegate a whole multi-step browsing goal to the browser-use agent, which "
            "runs its own model loop on the SAME browser the human sees (same page, "
            "cookies and live view). Use it for long flows instead of many browse_action "
            "calls: uploading a local file (e.g. a picture from the session's Downloads "
            "folder) and submitting a post, filling a long form, booking a slot. It "
            "returns one final answer plus steps/tokens/cost. Do not use it for a single "
            "click/fill/read - browse_action is cheaper there. The agent does not see "
            "this chat: put every value it needs (text, file name, choices) in `task`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "The complete goal in natural language, with the concrete values "
                        "(post text, file name inside Downloads, form data, URL)."
                    ),
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum model requests for the run (default 30, max 200).",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Seconds for the agent loop (default 300, max 900).",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session to drive (default: the chat's session).",
                },
            },
            "required": ["task"],
        },
    },
}
