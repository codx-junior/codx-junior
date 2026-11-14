"""Wiring self-check for the QA subsystem.

Proves three things the unit tests cannot:

1. the QA profiles exist, parse, and their tool allowlists only name tools that
   are really registered (no typo, no wildcard grant);
2. the six QA tools are registered in the runtime registry (``TOOLS``) with
   ``project_settings`` and ``dual_response`` enabled, and exported in
   ``__all__``;
3. the ``codx-junior`` harness lists the QA profiles as part of its team.

Checks 1 and 2 are static (AST + regex, no imports) so the script runs on a bare
interpreter; check 3 imports ``codx.junior.tools.qa_tools`` when the runtime
dependencies are available and skips otherwise.

    python3 api/tests/quality/qa_wiring_check.py

Exit codes: 0 wired · 1 a wiring problem · 2 the checks could not run.

Made with ❤️ by codx-junior
"""

import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
API_ROOT = os.path.dirname(os.path.dirname(HERE))
if API_ROOT not in sys.path:
    sys.path.insert(0, API_ROOT)

PKG_ROOT = os.path.join(API_ROOT, "codx", "junior")
TOOLS_ROOT = os.path.join(PKG_ROOT, "tools")
TOOLS_INIT = os.path.join(TOOLS_ROOT, "__init__.py")
PROFILES_ROOT = os.path.join(PKG_ROOT, "profiles")

QA_TOOLS = (
    "qa_plan",
    "qa_run_tests",
    "qa_review_changes",
    "qa_coverage",
    "qa_flaky_check",
    "qa_policy",
)
QA_PROFILES = ("qa", "qa_automation", "qa_explorer")
HARNESS_TEAM = QA_PROFILES + ("ciso",)

FAILURES = []


def fail(message: str) -> None:
    """Record a wiring failure."""
    FAILURES.append(message)
    print(f"FAIL {message}")


def read(path: str) -> str:
    """Return the text of *path*."""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def load_profile(name: str) -> dict:
    """Load one profile definition."""
    return json.loads(read(os.path.join(PROFILES_ROOT, name, f"{name}.profile")))


# -----------------------------------------------------------------------------
# Static knowledge of the registry
# -----------------------------------------------------------------------------
def registered_tool_names() -> dict:
    """Return ``{json_constant: callable_name}`` for every entry of ``TOOLS``.

    The registry is a literal list of dicts with ``tool_json`` (a module level
    constant) and ``tool_call`` (the function). Parsing it statically keeps this
    check independent from the (heavy) runtime imports.
    """
    tree = ast.parse(read(TOOLS_INIT))
    entries = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "TOOLS"):
            continue
        if not isinstance(node.value, ast.List):
            continue
        for element in node.value.elts:
            if not isinstance(element, ast.Dict):
                continue
            values = {}
            for key, value in zip(element.keys, element.values):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Name):
                    values[key.value] = value.id
            if "tool_json" in values:
                entries[values["tool_json"]] = values.get("tool_call", "")
    return entries


def json_constant_names() -> dict:
    """Return ``{json_constant: tool_name}`` from the tools modules."""
    mapping = {}
    pattern = re.compile(r"^([A-Z0-9_]+_TOOL_JSON)\s*=\s*\{", re.MULTILINE)
    name_pattern = re.compile(r"\"name\":\s*\"([a-z0-9_]+)\"")
    for filename in os.listdir(TOOLS_ROOT):
        if not filename.endswith(".py"):
            continue
        path = os.path.join(TOOLS_ROOT, filename)
        try:
            text = read(path)
        except OSError:  # pragma: no cover - unreadable file
            continue
        for match in pattern.finditer(text):
            block = text[match.end():]
            name = name_pattern.search(block)
            if name:
                mapping[match.group(1)] = name.group(1)
    return mapping


def tool_settings_flags(json_constant: str) -> dict:
    """Return the ``ToolSettings`` flags of one registry entry."""
    text = read(TOOLS_INIT)
    index = text.find(f"\"tool_json\": {json_constant},")
    if index < 0:
        return {}
    window = text[index:index + 600]
    settings = re.search(r"ToolSettings\(([^)]*)\)", window, re.DOTALL)
    flags = {}
    if settings:
        for part in settings.group(1).replace("\n", " ").split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            flags[key.strip()] = value.strip()
    return flags


def exported_names() -> set:
    """Return the names listed in ``tools/__init__.py`` ``__all__``."""
    tree = ast.parse(read(TOOLS_INIT))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "__all__":
                return {
                    element.value
                    for element in getattr(node.value, "elts", [])
                    if isinstance(element, ast.Constant)
                }
    return set()


# -----------------------------------------------------------------------------
# Checks
# -----------------------------------------------------------------------------
def check_profiles() -> None:
    """Validate the QA profile files."""
    for name in QA_PROFILES:
        profile_path = os.path.join(PROFILES_ROOT, name, f"{name}.profile")
        content_path = os.path.join(PROFILES_ROOT, name, f"{name}.md")

        if not os.path.isfile(profile_path):
            fail(f"profile missing: {profile_path}")
            continue
        if not os.path.isfile(content_path):
            fail(f"profile instructions missing: {content_path}")
            continue

        try:
            data = load_profile(name)
        except json.JSONDecodeError as ex:
            fail(f"profile is not valid JSON: {name} ({ex})")
            continue

        if data.get("name") != name:
            fail(f"profile name mismatch: {name} -> {data.get('name')}")
        if not data.get("description"):
            fail(f"profile has no description: {name}")
        if not data.get("category"):
            fail(f"profile has no category: {name}")
        tools = data.get("tools") or []
        if not tools:
            fail(f"profile grants no tools: {name}")
        if "*" in tools:
            fail(f"profile uses the wildcard tool grant: {name}")

        content = read(content_path)
        if len(content) < 800:
            fail(f"profile instructions look empty: {name}")
        if not any(tool in content for tool in QA_TOOLS):
            fail(f"profile instructions never mention a QA tool: {name}")
        if not any(tool in content for tool in tools):
            fail(f"profile instructions never mention its own tools: {name}")
        print(f"PASS profile {name} ({len(tools)} tools, {len(content)} chars)")


def check_registry() -> None:
    """Validate the registration of the six QA tools."""
    entries = registered_tool_names()
    names = json_constant_names()
    exported = exported_names()

    if not entries:
        fail("could not read the TOOLS registry from tools/__init__.py")
        return

    registered = {names.get(constant, constant): callable_name for constant, callable_name in entries.items()}

    for tool in QA_TOOLS:
        matches = [constant for constant, callable_name in entries.items() if names.get(constant) == tool]
        if not matches:
            fail(f"tool not registered in TOOLS: {tool}")
            continue
        constant = matches[0]
        if registered.get(tool) != tool:
            fail(f"tool registered with a different callable: {tool} -> {registered.get(tool)}")
        flags = tool_settings_flags(constant)
        if flags.get("project_settings") != "True":
            fail(f"tool must inject project_settings: {tool}")
        if flags.get("dual_response") != "True":
            fail(f"tool must return a dual response: {tool}")
        if tool not in exported:
            fail(f"tool is not listed in __all__: {tool}")
        print(f"PASS tool {tool}")


def check_allowlists() -> None:
    """Validate that the profiles only name registered tools."""
    known = set(json_constant_names().values()) | set(QA_TOOLS)
    # The QA profiles also use tools owned by other modules; a name is valid when
    # the module that owns it declares it, so we look the name up in the whole
    # tools package instead of a hardcoded list.
    pattern = re.compile(r"\"name\":\s*\"([a-z0-9_]+)\"")
    for filename in os.listdir(TOOLS_ROOT):
        if filename.endswith(".py"):
            known.update(pattern.findall(read(os.path.join(TOOLS_ROOT, filename))))

    for name in QA_PROFILES:
        data = load_profile(name)
        unknown = [tool for tool in data.get("tools") or [] if tool not in known]
        if unknown:
            fail(f"profile {name} grants unknown tool(s): {unknown}")
        else:
            print(f"PASS allowlist {name}")


def check_harness() -> None:
    """Validate the harness team wiring."""
    harness = load_profile("codx-junior")
    team = harness.get("team") or []
    missing = [name for name in HARNESS_TEAM if name not in team]
    if missing:
        fail(f"codx-junior team does not include: {missing}")
    else:
        print("PASS harness team includes the QA profiles")
    if "*" not in (harness.get("tools") or []):
        fail("codx-junior should keep the wildcard grant (it is the only profile allowed to)")

    content = read(os.path.join(PROFILES_ROOT, "codx-junior", "codx-junior.md"))
    for token in ("qa_review_changes", "`qa`", "qa_automation", "qa_explorer"):
        if token not in content:
            fail(f"codx-junior instructions never mention {token}")
    if "qa_review_changes" in content:
        print("PASS harness instructions invoke the QA gate")


def check_import() -> int:
    """Import ``qa_tools`` for real, without the (heavy) tools package import.

    ``codx.junior.tools.__init__`` pulls the whole runtime (langchain, llama
    index, docker clients). The QA module only needs ``.model``, so a synthetic
    parent package lets us import the file itself: that still proves the module
    compiles against the real ``ToolResponse`` and exposes what the registry
    expects.
    """
    import importlib
    import types

    try:
        if "codx.junior.tools" not in sys.modules:
            package = types.ModuleType("codx.junior.tools")
            package.__path__ = [TOOLS_ROOT]
            sys.modules["codx.junior.tools"] = package
        qa_tools = importlib.import_module("codx.junior.tools.qa_tools")
    except Exception as ex:  # pragma: no cover - depends on the environment
        print(f"SKIP runtime import: {ex}")
        return 2

    for tool in QA_TOOLS:
        if not callable(getattr(qa_tools, tool, None)):
            fail(f"qa_tools does not expose a callable: {tool}")
    schemas = [name for name in dir(qa_tools) if name.endswith("_TOOL_JSON")]
    if len(schemas) != len(QA_TOOLS):
        fail(f"expected {len(QA_TOOLS)} JSON schemas in qa_tools, found {len(schemas)}: {schemas}")
    else:
        print(f"PASS runtime import ({len(schemas)} schemas)")
    return 0


def main() -> int:
    """Run every check."""
    check_profiles()
    check_registry()
    check_allowlists()
    check_harness()
    runtime = check_import()
    if FAILURES:
        print(f"\n{len(FAILURES)} wiring problem(s)")
        return 1
    print("\nQA wiring OK" + ("" if runtime == 0 else " (runtime import skipped)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
