"""General ops on managed hosts — registry + read-only command classifier.

Phase A (docs/plans/MAGICMIRROR_PLAN.md "General ops agent" spec): the
devops agent can run READ-ONLY commands on hosts in the registry. The
classifier is the safety core — deterministic, server-side, never the
model. Mutations are refused until phase B adds the confirmation gate.

Design: a command runs iff every program in it (across pipe segments) is
read-only, with no shell chaining/redirect/substitution/background. Pipes
between allowlisted read programs are allowed.
"""
import json
import os
import shlex
import time
from pathlib import Path

OPS_HOSTS_FILE = Path(os.getenv("OPS_HOSTS", "/ops/hosts.json"))
OPS_AUDIT_LOG = Path(os.getenv("OPS_AUDIT_LOG", "/data/ops_audit.log"))
MAX_COMMAND_LEN = 4000


# ── host registry ─────────────────────────────────────────────────────────────

def load_registry() -> dict:
    """name -> {ssh_target, key, sudo, description}. Reads the bind-mounted
    hosts.json; falls back to synthesizing the `magicmirror` host from the
    existing MM_SSH_* env so the mirror works with no new file. The registry
    IS the opt-in boundary — a host absent here is unreachable. The kronk
    box is deliberately never listed (the agent can't target its own host)."""
    reg: dict = {}
    try:
        loaded = json.loads(OPS_HOSTS_FILE.read_text())
        if isinstance(loaded, dict):
            reg = {k: v for k, v in loaded.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        pass
    if "magicmirror" not in reg:
        tgt, key = os.getenv("MM_SSH_TARGET"), os.getenv("MM_SSH_KEY")
        if tgt and key:
            reg["magicmirror"] = {
                "ssh_target": tgt, "key": key, "sudo": True,
                "description": "MagicMirror Raspberry Pi",
            }
    return reg


def get_host(name: str) -> dict | None:
    return load_registry().get(name)


# ── read-only command classifier ──────────────────────────────────────────────

# Always read-only, whatever the args.
_SAFE = {
    "uptime", "cat", "ls", "tail", "head", "grep", "egrep", "fgrep", "zgrep",
    "wc", "sort", "uniq", "cut", "tr", "column", "nl", "date", "hostname",
    "hostnamectl", "whoami", "id", "uname", "which", "type", "pgrep", "pidof",
    "ss", "lsblk", "lscpu", "lsusb", "lsmod", "free", "df", "du", "vcgencmd",
    "echo", "printf", "env", "printenv", "stat", "file", "readlink",
    "realpath", "dirname", "basename", "true", "sensors", "iostat", "vmstat",
}

# Dual-use programs: only these read-only subcommands (first arg) are allowed.
_DUAL = {
    "systemctl":  {"status", "is-active", "is-enabled", "is-failed", "show",
                   "cat", "list-units", "list-unit-files", "list-timers",
                   "get-default", "show-environment"},
    "git":        {"status", "log", "show", "diff", "rev-parse", "branch",
                   "remote", "describe", "tag", "ls-files", "shortlog",
                   "blame", "cat-file"},
    "npm":        {"ls", "list", "view", "outdated", "root", "prefix", "why"},
    "pm2":        {"list", "jlist", "prettylist", "show", "describe", "info",
                   "logs", "status"},
    "ip":         {"addr", "link", "route", "a", "r", "neigh"},
    "docker":     {"ps", "images", "logs", "inspect", "stats", "version",
                   "info", "top"},
    "ps":         None,   # ps aux etc — always read
    "journalctl": None,   # read by nature (dangerous flags forbidden below)
}
# ps/journalctl are None = "no subcommand restriction" but still checked for
# the forbidden-flag denylist.
_ALWAYS_OK_DUAL = {"ps", "journalctl"}
_FORBIDDEN_FLAGS = {"--vacuum-size", "--vacuum-time", "--vacuum-files",
                    "--rotate", "--flush", "--relinquish-var", "-i",
                    "--in-place"}

# Shell constructs that could chain, redirect, background, or substitute —
# forbidden entirely in phase A. Pipe (`|`) is the one allowed connector.
_FORBIDDEN_SUBSTR = [";", "&", ">", "<", "`", "$(", "||", "\n"]


def classify_readonly(command: str) -> tuple[bool, str]:
    """(ok, reason). ok=True means every program is read-only and there is
    no chaining/redirect/substitution — safe to run as-is."""
    cmd = (command or "").strip()
    if not cmd:
        return False, "empty command"
    if len(cmd) > MAX_COMMAND_LEN:
        return False, "command too long"
    for bad in _FORBIDDEN_SUBSTR:
        if bad in cmd:
            return False, (f"contains {bad!r} — chaining, redirection, "
                           "background, and command substitution are not "
                           "allowed (read-only mode)")

    for seg in cmd.split("|"):
        seg = seg.strip()
        if not seg:
            return False, "empty pipeline segment"
        try:
            toks = shlex.split(seg)
        except ValueError as e:
            return False, f"could not parse command: {e}"
        if not toks:
            return False, "empty pipeline segment"
        prog = toks[0]
        if prog == "sudo":
            return False, "sudo is not allowed in read-only mode"
        if "=" in prog:  # FOO=bar cmd — env-assignment prefix
            return False, f"env-assignment prefix {prog!r} is not allowed"
        base = prog.rsplit("/", 1)[-1]   # tolerate /usr/bin/uptime

        if base in _SAFE:
            continue
        if base in _DUAL:
            allowed = _DUAL[base]
            # flags may carry a value: --vacuum-size=1M → check the name part
            if any(t.split("=", 1)[0] in _FORBIDDEN_FLAGS for t in toks[1:]):
                return False, f"{base} used with a forbidden (mutating) flag"
            if base in _ALWAYS_OK_DUAL:
                continue
            sub = next((t for t in toks[1:] if not t.startswith("-")), None)
            if sub is None or sub in allowed:
                continue
            return False, (f"{base} {sub!r} is not a read-only subcommand "
                           f"(allowed: {', '.join(sorted(allowed))})")
        return False, (f"{base!r} is not on the read-only allowlist "
                       "(mutations are not enabled yet)")
    return True, "read-only"


# ── audit ──────────────────────────────────────────────────────────────────────

def audit_exec(host: str, command: str, exit_code, out_len: int,
               allowed: bool, note: str = "") -> None:
    """Append-only record of every exec attempt (allowed or refused). Trust
    after the fact requires reconstructing what ran on a managed host."""
    line = json.dumps({
        "ts": round(time.time(), 3), "host": host, "command": command,
        "allowed": allowed, "exit_code": exit_code, "out_len": out_len,
        "note": note,
    })
    try:
        with OPS_AUDIT_LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass  # audit is best-effort; never block the op on a log failure
