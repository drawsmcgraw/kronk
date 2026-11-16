"""General ops on managed hosts — registry + read-only command classifier.

Phase A (docs/plans/MAGICMIRROR_PLAN.md "General ops agent" spec): the
devops agent can run READ-ONLY commands on hosts in the registry. The
classifier is the safety core — deterministic, server-side, never the
model. Mutations are refused until phase B adds the confirmation gate.

The SSH key the registry points at can do more than this policy allows (it
is a general-purpose key), so classify_readonly() is the control, not a
convenience. Its shape (M3, 2026-11-15):

1. Lex the command line the way the remote shell (bash) will, tracking
   quoting. Anything the shell would expand or redirect is refused
   outright: `;` `&` `<` `>` `(` `)` `$` backticks, unquoted globs and
   braces, `#`, newlines and other control characters. An unbalanced quote
   or a trailing backslash is unparseable and refused. The only operator
   allowed is a single `|` between commands.
2. Every pipe segment must start with a program in _RULES (bare name, or a
   path in a system bin directory). Refusal is the default: unknown
   programs, env-assignment prefixes and sudo are refused.
3. Every argument of every program is checked against DENIED_PATH_PATTERNS
   (secrets: SSH keys, .env files, shadow, key and token files).
4. The program's own rule then checks its flags (forbidden spellings,
   including combined short flags and abbreviated long options) and its
   operands (count limits, allowed subcommands).
"""
import fnmatch
import json
import os
import posixpath
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

OPS_HOSTS_FILE = Path(os.getenv("OPS_HOSTS", "/ops/hosts.json"))
OPS_AUDIT_LOG = Path(os.getenv("OPS_AUDIT_LOG", "/data/ops_audit.log"))
MAX_COMMAND_LEN = 4000


# ── host registry ─────────────────────────────────────────────────────────────

def load_registry() -> dict:
    """name -> {ssh_target, key, description}. Reads the bind-mounted
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
                "ssh_target": tgt, "key": key,
                "description": "MagicMirror Raspberry Pi",
            }
    return reg


def get_host(name: str) -> dict | None:
    return load_registry().get(name)


# ── read-only command classifier ──────────────────────────────────────────────

class _Refused(Exception):
    """Raised anywhere inside the classifier; the message is the reason."""


# Secrets deny list. Every argument of every command is split into path
# components (on / = : ,) and each component is matched, case-insensitively,
# against these shell-style patterns. A match anywhere refuses the command,
# so relative paths, ~, .. segments and repeated slashes cannot route around
# it. Widening the list is a one-line change here; how wide it should be is
# Drew's call (plan, "Still open", M3). This is the plan's list: .ssh/, id_*
# key files, .env files, shadow, key and token files. `environ` is
# /proc/<pid>/environ, the same leak as printenv, which the plan removes.
DENIED_PATH_PATTERNS = (
    ".ssh",                                   # SSH directory and everything in it
    "id_*",                                   # SSH key files (id_rsa, id_ed25519, ...)
    ".env", ".env.*", "*.env",                # dotenv files
    "shadow*", "gshadow*",                    # /etc/shadow and its backups
    "*.key", "*_key", "*.pem", "*.p12", "*.pfx",  # key files
    "*token*", ".netrc", ".git-credentials",  # token files
    "environ",                                # /proc/<pid>/environ
)

# Recursive reads (grep -r) can reach a denied file without naming it. A
# recursive root must be at least this many components deep once ~ and
# relative paths are resolved against the login home (/home/<user>/x is 3),
# must not contain '..', and must not be under these pseudo-filesystems.
RECURSIVE_ROOT_MIN_DEPTH = 3
RECURSIVE_ROOT_NEVER = ("/proc", "/sys", "/dev")

# A program may be named bare or by its path in one of these directories.
_BIN_DIRS = {"/bin", "/usr/bin", "/sbin", "/usr/sbin", "/usr/local/bin"}

# Programs that were on the allowlist and were taken off, with the reason
# given back to the model.
_REMOVED = {
    "env": "it runs any program given as an argument",
    "printenv": "it prints the environment, which holds secrets",
    "docker": ("never used on a managed host; 'logs -f' and 'stats' never "
               "return and 'inspect' prints container environments"),
}

# Shell syntax the classifier does not run. Keyed by the character that
# starts it; the value names the feature in the refusal.
_SHELL_SYNTAX = {
    ";": "';' (command chaining)",
    "&": "'&' (background job)",
    ">": "'>' (redirection)",
    "<": "'<' (redirection)",
    "(": "'(' (subshell)",
    ")": "')' (subshell)",
    "`": "'`' (command substitution)",
    "$": "'$' (variable expansion)",
    "*": "unquoted '*' (glob)",
    "?": "unquoted '?' (glob)",
    "[": "unquoted '[' (glob)",
    "{": "unquoted '{' (brace expansion)",
    "}": "unquoted '}' (brace expansion)",
    "#": "unquoted '#' (comment)",
}
_TWO_CHAR_SYNTAX = {
    "&&": "'&&' (command chaining)",
    "||": "'||' (command chaining)",
    "|&": "'|&' (pipe with stderr redirection)",
    "$(": "'$(' (command substitution)",
    "<(": "'<(' (process substitution)",
    ">(": "'>(' (process substitution)",
    "<<": "'<<' (here-document)",
    ">>": "'>>' (redirection)",
}


def _syntax_refusal(cmd: str, i: int) -> _Refused:
    desc = _TWO_CHAR_SYNTAX.get(cmd[i:i + 2]) or _SHELL_SYNTAX[cmd[i]]
    return _Refused(f"contains {desc}: chaining, redirection, background, "
                    "substitution and expansion are not allowed (read-only "
                    "mode; use single quotes for a literal)")


def _lex(cmd: str) -> list[list[str]]:
    """Split a command line into pipe segments of words, the way bash would
    after quote removal. Refuses (raises _Refused) on anything bash would
    expand, redirect, chain or background, and on unbalanced quotes."""
    for ch in cmd:
        if (ord(ch) < 32 and ch != "\t") or ord(ch) == 127:
            raise _Refused("contains a newline or control character "
                           "(chaining is not allowed in read-only mode)")
    segments: list[list[str]] = [[]]
    word: list[str] = []
    in_word = False
    i, n = 0, len(cmd)

    def end_word():
        nonlocal word, in_word
        if in_word:
            segments[-1].append("".join(word))
        word, in_word = [], False

    while i < n:
        ch = cmd[i]
        if ch in " \t":
            end_word()
        elif ch == "|":
            if cmd[i:i + 2] in _TWO_CHAR_SYNTAX:
                raise _syntax_refusal(cmd, i)
            end_word()
            segments.append([])
        elif ch == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                raise _Refused("could not parse command: unbalanced single quote")
            word.append(cmd[i + 1:j])
            in_word = True
            i = j
        elif ch == '"':
            i += 1
            in_word = True
            while True:
                if i >= n:
                    raise _Refused("could not parse command: unbalanced double quote")
                c = cmd[i]
                if c == '"':
                    break
                if c in "$`":
                    raise _syntax_refusal(cmd, i)
                if c == "\\" and i + 1 < n and cmd[i + 1] in '$`"\\':
                    word.append(cmd[i + 1])
                    i += 2
                    continue
                word.append(c)
                i += 1
        elif ch == "\\":
            if i + 1 >= n:
                raise _Refused("could not parse command: trailing backslash")
            word.append(cmd[i + 1])
            in_word = True
            i += 1
        elif ch in _SHELL_SYNTAX:
            raise _syntax_refusal(cmd, i)
        else:
            word.append(ch)
            in_word = True
        i += 1
    end_word()
    return segments


def _components(tok: str) -> list[str]:
    return [c for c in re.split(r"[/=:,]", tok.lower()) if c]


def _denied_component(tok: str) -> str | None:
    """The deny pattern this argument hits, or None."""
    comps = _components(tok)
    if tok.startswith("-") and not tok.startswith("--") and comps:
        # A value attached to a short flag (-fid_rsa, -f.env): also try
        # every suffix of the first component.
        first = comps[0]
        comps = comps + [first[k:] for k in range(1, len(first))]
    for comp in comps:
        for pat in DENIED_PATH_PATTERNS:
            if fnmatch.fnmatchcase(comp, pat):
                return pat
    return None


def _check_paths(prog: str, args: list[str]) -> None:
    for a in args:
        pat = _denied_component(a)
        if pat:
            raise _Refused(f"{prog}: argument {a!r} matches the secrets deny "
                           f"list ({pat!r}); secrets must not reach the model")


def _recursive_root_refusal(root: str) -> str | None:
    p = root
    if p.startswith("~"):
        head, _, tail = p.partition("/")
        p = f"/home/{head[1:] or '~'}/{tail}"
    elif not p.startswith("/"):
        p = "/home/~/" + p          # an SSH command starts in the login home
    if ".." in p.split("/"):
        return f"recursive search root {root!r} contains '..'"
    p = posixpath.normpath(re.sub(r"/+", "/", p))
    for top in RECURSIVE_ROOT_NEVER:
        if p == top or p.startswith(top + "/"):
            return f"recursive search under {top} is not allowed"
    depth = len([c for c in p.split("/") if c])
    if depth < RECURSIVE_ROOT_MIN_DEPTH:
        return (f"recursive search root {root!r} is too broad (it can reach "
                "secrets without naming them); search a deeper directory "
                "or name the files")
    return None


# ── per-program rules ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Rule:
    """How to read one program's arguments.

    forbid      flag spelling ("-o", "--output") -> why it is refused. A short
                letter is caught inside combined flags (-uo); a long name is
                caught when abbreviated (--out), since getopt accepts any
                unambiguous prefix.
    long_ok     exact long names that are allowed although they are a prefix
                of a forbidden one (journalctl --cursor vs --cursor-file).
    short_args  short flags that take a value (the rest of the cluster, or
                the next word). Listed only when certain: a wrong entry here
                would hide a following flag.
    short_opt   short flags whose optional value can only be attached.
    long_args   long names that take the next word as their value when
                written without '='.
    max_pos     operand limit. Words that are not flags count, as does every
                word after '--', as does a lone '-'.
    check       extra check on (args, operands, flags seen); raises _Refused.
    """
    forbid: dict = field(default_factory=dict)
    long_ok: frozenset = frozenset()
    short_args: str = ""
    short_opt: str = ""
    long_args: frozenset = frozenset()
    max_pos: int | None = None
    check: Callable | None = None


def _scan(prog: str, args: list[str], rule: Rule) -> tuple[list[str], list[str]]:
    """(operands, flags seen). Raises on a forbidden flag. Forbidden
    spellings are refused after '--' too: a file named '-o' is never
    needed, and not every program honours '--'."""
    short_forbid = {f[1] for f in rule.forbid if len(f) == 2 and f[0] == "-"}
    long_forbid = {f[2:]: why for f, why in rule.forbid.items() if f.startswith("--")}
    pos: list[str] = []
    seen: list[str] = []
    end_opts = False
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--" and not end_opts:
            end_opts = True
            continue
        if a == "-" or not a.startswith("-"):
            pos.append(a)
            continue
        if end_opts:
            pos.append(a)
        if a.startswith("--"):
            name, eq, _ = a[2:].partition("=")
            if name and name not in rule.long_ok:
                for f, why in long_forbid.items():
                    if f.startswith(name):
                        raise _Refused(f"{prog}: --{f} {why}"
                                       + ("" if name == f else f" (given as {a!r})"))
            if not end_opts:
                seen.append("--" + name)
                if not eq and name in rule.long_args:
                    i += 1
            continue
        body = a[1:]
        for j, c in enumerate(body):
            if c in short_forbid:
                why = rule.forbid["-" + c]
                raise _Refused(f"{prog}: -{c} {why}"
                               + ("" if body == c else f" (given as {a!r})"))
            if end_opts:
                continue
            seen.append("-" + c)
            if c in rule.short_args:
                if j == len(body) - 1:
                    i += 1
                break
            if c in rule.short_opt:
                break
    return pos, seen


def _apply(prog: str, args: list[str], rule: Rule) -> None:
    pos, seen = _scan(prog, args, rule)
    if rule.max_pos is not None and len(pos) > rule.max_pos:
        raise _Refused(f"{prog}: at most {rule.max_pos} operand"
                       f"{'' if rule.max_pos == 1 else 's'} allowed, got {len(pos)}")
    if rule.check:
        rule.check(args, pos, seen)


_ANY = Rule()

_FOLLOW = "follows forever and never returns (burns the 30 s timeout); use -n N"


def _tail_check(args, pos, seen):
    for p in pos:
        if p.startswith("+") and ("f" in p or "F" in p):
            raise _Refused(f"tail: {p!r} {_FOLLOW}")


def _date_check(args, pos, seen):
    for p in pos:
        if not p.startswith("+"):
            raise _Refused(f"date: operand {p!r} sets the clock; only +FORMAT "
                           "operands are allowed")


def _uniq_check(args, pos, seen):
    if len(pos) > 1:
        raise _Refused("uniq: at most one file operand; a second operand is an "
                       "output file that uniq writes")


def _hostname_check(args, pos, seen):
    if pos:
        raise _Refused("hostname: an operand sets the hostname; no operands allowed")


def _hostnamectl_check(args, pos, seen):
    if pos not in ([], ["status"]):
        raise _Refused("hostnamectl: only bare 'hostnamectl' or 'hostnamectl "
                       "status' is allowed (other subcommands set values)")


def _snapshot_check(prog):
    def check(args, pos, seen):
        if pos:
            raise _Refused(f"{prog}: an interval operand repeats until stopped; "
                           "one snapshot only (no operands)")
    return check


def _ps_check(args, pos, seen):
    for p in pos:
        if not p.isdigit() and "e" in p:
            raise _Refused(f"ps: BSD option {p!r} includes 'e', which prints "
                           "every process's environment (secrets)")


def _grep_check(prog):
    def check(args, pos, seen):
        recursive = "-r" in seen or any(
            s.startswith("--") and len(s) > 2 and "recursive".startswith(s[2:])
            for s in seen)
        if not recursive:
            return
        has_pattern_flag = any(
            s in ("-e", "-f") or (s.startswith("--") and len(s) > 2 and (
                "regexp".startswith(s[2:]) or "file".startswith(s[2:])))
            for s in seen)
        roots = pos if has_pattern_flag else pos[1:]
        if not roots:
            raise _Refused(f"{prog}: recursive search with no directory searches "
                           "the home directory; name a directory")
        for r in roots:
            why = _recursive_root_refusal(r)
            if why:
                raise _Refused(f"{prog}: {why}")
    return check


_GREP_RULE = lambda prog: Rule(  # noqa: E731
    forbid={
        "-R": "follows every symlink while recursing; use -r",
        "--dereference-recursive": "follows every symlink while recursing; use -r",
        "-d": "sets the directory action; use -r for recursion",
        "--directories": "sets the directory action; use -r for recursion",
    },
    short_args="ABCDefm",
    long_args=frozenset({"regexp", "file", "max-count", "context",
                         "after-context", "before-context", "label"}),
    check=_grep_check(prog))


def _node_check(args, pos, seen):
    if args not in (["--version"], ["-v"]):
        raise _Refused("node: only 'node --version' is allowed (node runs code)")


# Subcommand programs. The subcommand must come first (after a short list of
# exact, value-less global flags): a flag with a value in front of it would
# otherwise let a value pose as the subcommand (systemctl -p status restart x).

def _verb_first(prog, args, pre_flags, allowed, extra_reason=None):
    """(verb, rest). verb is None when there are only pre-flags."""
    i = 0
    while i < len(args) and args[i] in pre_flags:
        i += 1
    if i == len(args):
        return None, []
    verb = args[i]
    if verb.startswith("-"):
        firsts = ", ".join(sorted(pre_flags)) or "none"
        raise _Refused(f"{prog}: put options after the subcommand (allowed "
                       f"before it: {firsts}); got {verb!r}")
    if verb not in allowed:
        why = (extra_reason or {}).get(verb)
        raise _Refused(f"{prog} {verb!r} is not a read-only subcommand"
                       + (f": {why}" if why else "")
                       + f" (allowed: {', '.join(sorted(allowed))})")
    return verb, args[i + 1:]


_SYSTEMCTL_PRE = {"--user", "--system", "--no-pager", "--full", "-l", "--all",
                  "-a", "--plain", "--no-legend", "-q", "--quiet"}
_SYSTEMCTL_READ = {"status", "is-active", "is-enabled", "is-failed",
                   "is-system-running", "show", "cat", "list-units",
                   "list-unit-files", "list-timers", "list-sockets",
                   "list-dependencies", "get-default"}
_REMOTE_FLAGS = {"-H": "targets another host", "--host": "targets another host",
                 "-M": "targets another machine", "--machine": "targets another machine"}


def _check_systemctl(args):
    _, rest = _verb_first("systemctl", args, _SYSTEMCTL_PRE, _SYSTEMCTL_READ,
                          {"show-environment": "it prints the manager's "
                           "environment, which holds secrets"})
    _apply("systemctl", rest, Rule(forbid=_REMOTE_FLAGS))


_PM2_READ = {"list", "ls", "status", "logs"}


def _check_pm2(args):
    if args and not args[0].startswith("-"):
        verb, rest = _verb_first("pm2", args, set(), _PM2_READ, {
            "jlist": "it prints every process's environment (secrets)",
            "prettylist": "it prints every process's environment (secrets)",
            "show": "it prints environment variables (secrets)",
            "describe": "it prints environment variables (secrets)",
            "info": "it prints environment variables (secrets)",
            "env": "it prints environment variables (secrets)",
        })
        _, seen = _scan("pm2", rest, _ANY)
        if verb == "logs" and "--nostream" not in seen:
            raise _Refused("pm2: 'pm2 logs' streams forever; add --nostream "
                           "(and --lines N)")
        return
    if _scan("pm2", args, _ANY)[0]:
        raise _Refused("pm2: put the subcommand first")


_NPM_READ = {"ls", "list", "view", "outdated", "root", "prefix", "why"}


def _check_npm(args):
    if args and not args[0].startswith("-"):
        _verb_first("npm", args, set(), _NPM_READ)
        return
    if _scan("npm", args, _ANY)[0]:
        raise _Refused("npm: put the subcommand first")


_IP_GLOBALS = {"-4", "-6", "-s", "-stats", "-statistics", "-d", "-details",
               "-j", "-json", "-p", "-pretty", "-o", "-oneline", "-c",
               "-color", "-br", "-brief", "-h", "-human", "-human-readable"}
_IP_OBJECTS = {"a", "addr", "address", "l", "link", "r", "ro", "route",
               "n", "neigh", "neighbor", "neighbour"}


def _check_ip(args):
    i = 0
    while i < len(args) and args[i] in _IP_GLOBALS:
        i += 1
    if i == len(args):
        raise _Refused("ip: name an object (addr, link, route, neigh)")
    obj = args[i]
    if obj not in _IP_OBJECTS:
        raise _Refused(f"ip: {obj!r} is not an allowed option or object "
                       f"(objects: addr, link, route, neigh; options: "
                       f"{', '.join(sorted(_IP_GLOBALS))})")
    rest = args[i + 1:]
    if rest and rest[0] not in ("show", "list"):
        raise _Refused(f"ip {obj}: only a bare object or 'show'/'list' is "
                       f"allowed, got {rest[0]!r}")


_VCGENCMD_READ = {"measure_temp", "measure_clock", "measure_volts",
                  "get_throttled", "get_mem", "get_config", "version",
                  "commands"}


def _check_vcgencmd(args):
    if not args or args[0] not in _VCGENCMD_READ:
        raise _Refused("vcgencmd: allowed commands are "
                       f"{', '.join(sorted(_VCGENCMD_READ))}")
    if len(args) > 2 or any(a.startswith("-") for a in args):
        raise _Refused("vcgencmd: one command and at most one argument, no flags")


# git: global options before the subcommand are an exact list. -c and
# --config-env (run anything via config), --exec-path, -p/--paginate and
# everything else are refused there.
_GIT_GLOBAL_VALUE = {"-C", "--git-dir", "--work-tree"}
_GIT_GLOBAL_FLAG = {"--no-pager", "--no-optional-locks"}
_GIT_READ = {"status", "log", "show", "diff", "rev-parse", "describe",
             "ls-files", "shortlog", "blame", "cat-file", "branch", "tag",
             "remote"}
_GIT_RULE = Rule(forbid={
    "--output": "writes a file",
    "--ext-diff": "runs an external diff program",
    "--no-index": "compares arbitrary paths outside the repository",
})


def _git_listing(sub, args, letters, longs, eq_longs, number_flag=None):
    """branch/tag: listing forms only. Operands (patterns) only with
    -l/--list; anything else would create, delete or rename."""
    listing = False
    pos = []
    for a in args:
        if a == "--list" or (a.startswith("-") and not a.startswith("--")
                             and "l" in a[1:] and set(a[1:]) <= set(letters)):
            listing = True
        if not a.startswith("-"):
            pos.append(a)
        elif a in longs or any(a.startswith(p) for p in eq_longs):
            continue
        elif number_flag and re.fullmatch(number_flag, a):
            continue
        elif a.startswith("-") and not a.startswith("--") and len(a) > 1 \
                and set(a[1:]) <= set(letters):
            continue
        else:
            raise _Refused(f"git {sub}: only listing options are allowed, got {a!r}")
    if pos and not listing:
        raise _Refused(f"git {sub}: an operand without --list creates a {sub}; "
                       "use --list <pattern>")


def _check_git(args):
    i = 0
    while i < len(args):
        a = args[i]
        if a in _GIT_GLOBAL_VALUE:
            if i + 1 >= len(args):
                raise _Refused(f"git: {a} needs a value")
            i += 2
            continue
        if a.split("=", 1)[0] in ("--git-dir", "--work-tree") and "=" in a:
            i += 1
            continue
        if a in _GIT_GLOBAL_FLAG:
            i += 1
            continue
        if a == "--version" and i == len(args) - 1:
            return
        if a.startswith("-"):
            raise _Refused(f"git: option {a!r} before the subcommand is not "
                           "allowed (allowed: -C <dir>, --git-dir, --work-tree, "
                           "--no-pager, --no-optional-locks)")
        break
    if i >= len(args):
        raise _Refused("git: name a read-only subcommand")
    sub, rest = args[i], args[i + 1:]
    if sub not in _GIT_READ:
        raise _Refused(f"git {sub!r} is not a read-only subcommand "
                       f"(allowed: {', '.join(sorted(_GIT_READ))})")
    if sub == "branch":
        _git_listing("branch", rest, "arvli",
                     {"--list", "--all", "--remotes", "--verbose",
                      "--show-current", "--no-color", "--color",
                      "--no-column", "--ignore-case", "--no-abbrev"},
                     ("--contains=", "--no-contains=", "--merged=",
                      "--no-merged=", "--points-at=", "--sort=", "--format=",
                      "--abbrev=", "--color="))
    elif sub == "tag":
        _git_listing("tag", rest, "li",
                     {"--list", "--ignore-case", "--no-column", "--column"},
                     ("--contains=", "--no-contains=", "--merged=",
                      "--no-merged=", "--points-at=", "--sort=", "--format="),
                     number_flag=r"-n\d*")
    elif sub == "remote":
        flags = [a for a in rest if a.startswith("-")]
        pos = [a for a in rest if not a.startswith("-")]
        if not pos:
            bad = [a for a in flags if a not in ("-v", "--verbose")]
        elif pos[0] in ("show", "get-url"):
            ok = {"show": {"-n"}, "get-url": {"--push", "--all"}}[pos[0]]
            bad = [a for a in flags if a not in ok | {"-v", "--verbose"}]
        else:
            raise _Refused(f"git remote {pos[0]!r} is not read-only "
                           "(allowed: bare, -v, show, get-url)")
        if bad:
            raise _Refused(f"git remote: option {bad[0]!r} is not allowed")
    else:
        _apply(f"git {sub}", rest, _GIT_RULE)


# The allowlist. A Rule is applied with _apply; a function does its own
# parsing. Programs not listed here are refused.
_RULES: dict = {
    # no arguments that matter
    "uptime": Rule(max_pos=0), "whoami": _ANY, "id": _ANY, "uname": _ANY,
    "true": _ANY, "echo": _ANY, "printf": _ANY, "which": _ANY, "type": _ANY,
    "lsblk": _ANY, "lscpu": _ANY, "lsusb": _ANY, "lsmod": _ANY, "df": _ANY,
    "pgrep": _ANY, "pidof": _ANY,
    # file readers: arguments are covered by the deny list
    "cat": _ANY, "head": _ANY, "wc": _ANY, "cut": _ANY, "tr": _ANY,
    "column": _ANY, "nl": _ANY, "ls": _ANY, "du": _ANY, "stat": _ANY,
    "readlink": _ANY, "realpath": _ANY, "dirname": _ANY, "basename": _ANY,
    "grep": _GREP_RULE("grep"), "egrep": _GREP_RULE("egrep"),
    "fgrep": _GREP_RULE("fgrep"), "zgrep": _GREP_RULE("zgrep"),
    "tail": Rule(forbid={"-f": _FOLLOW, "-F": _FOLLOW, "--follow": _FOLLOW,
                         "--retry": _FOLLOW},
                 short_args="ncs", check=_tail_check),
    "sort": Rule(forbid={"-o": "writes a file", "--output": "writes a file",
                         "--compress-program": "runs a program",
                         "--files0-from": ("reads the list of files to print "
                                           "from a file the classifier cannot see")},
                 short_args="kStT"),
    "uniq": Rule(check=_uniq_check),
    "file": Rule(forbid={"-C": "writes a compiled magic file",
                         "--compile": "writes a compiled magic file"},
                 short_args="eFfmP"),
    "date": Rule(forbid={"-s": "sets the clock", "--set": "sets the clock"},
                 short_args="dfr", short_opt="I",
                 long_args=frozenset({"date", "file", "reference", "rfc-3339"}),
                 check=_date_check),
    "hostname": Rule(forbid={"-F": "sets the hostname from a file",
                             "--file": "sets the hostname from a file",
                             "-b": "sets the hostname", "--boot": "sets the hostname"},
                     check=_hostname_check),
    "hostnamectl": Rule(forbid=_REMOTE_FLAGS, check=_hostnamectl_check),
    "ss": Rule(forbid={"-K": "kills sockets", "--kill": "kills sockets",
                       "-D": "writes a dump file", "--diag": "writes a dump file"},
               short_args="fAFN"),
    "free": Rule(forbid={"-s": "repeats until stopped", "--seconds": "repeats until stopped"}),
    "sensors": Rule(forbid={"-s": "writes limits to the chip", "--set": "writes limits to the chip"}),
    "vmstat": Rule(check=_snapshot_check("vmstat")),
    "iostat": Rule(check=_snapshot_check("iostat")),
    "ps": Rule(short_args="oOpuUgGCtsq",
               long_args=frozenset({"sort", "format", "pid", "ppid", "user",
                                    "User", "group", "Group", "sid", "tty",
                                    "cols", "columns", "lines", "rows",
                                    "width", "quick-pid"}),
               check=_ps_check),
    "journalctl": Rule(
        forbid={"-f": _FOLLOW, "--follow": _FOLLOW,
                "--vacuum-size": "deletes journal files",
                "--vacuum-time": "deletes journal files",
                "--vacuum-files": "deletes journal files",
                "--rotate": "rotates journal files",
                "--flush": "moves journal files",
                "--relinquish-var": "changes journal storage",
                "--smart-relinquish-var": "changes journal storage",
                "--sync": "writes the journal",
                "--setup-keys": "writes sealing keys",
                "--update-catalog": "writes the catalog",
                "--cursor-file": "writes a cursor file"},
        long_ok=frozenset({"cursor"}),
        short_args="oDpgcSUtuFMi", short_opt="bn",
        long_args=frozenset({"since", "until", "unit", "user-unit",
                             "identifier", "priority", "grep", "cursor",
                             "after-cursor", "output", "directory", "file",
                             "root", "field", "machine", "facility",
                             "output-fields", "namespace"})),
    "node": Rule(check=_node_check),
    # subcommand programs
    "systemctl": _check_systemctl,
    "git": _check_git,
    "npm": _check_npm,
    "pm2": _check_pm2,
    "ip": _check_ip,
    "vcgencmd": _check_vcgencmd,
}


def _check_segment(words: list[str]) -> None:
    if not words:
        raise _Refused("empty pipeline segment")
    prog, args = words[0], words[1:]
    if "=" in prog:
        raise _Refused(f"env-assignment prefix {prog!r} is not allowed")
    if "/" in prog:
        d, _, base = prog.rpartition("/")
        if d not in _BIN_DIRS:
            raise _Refused(f"{prog!r}: name the program, or give its path in "
                           f"{', '.join(sorted(_BIN_DIRS))}")
    else:
        base = prog
    if base == "sudo":
        raise _Refused("sudo is not allowed in read-only mode")
    if base in _REMOVED:
        raise _Refused(f"{base!r} is not on the read-only allowlist: "
                       f"{_REMOVED[base]}")
    rule = _RULES.get(base)
    if rule is None:
        raise _Refused(f"{base!r} is not on the read-only allowlist "
                       "(mutations are not enabled yet)")
    _check_paths(base, args)
    if isinstance(rule, Rule):
        _apply(base, args, rule)
    else:
        rule(args)


def classify_readonly(command: str) -> tuple[bool, str]:
    """(ok, reason). ok=True means every program in every pipe segment is
    read-only under its rule, no argument names a secret, and the line uses
    no shell feature beyond a plain pipe. Refusal is the default."""
    cmd = (command or "").strip()
    if not cmd:
        return False, "empty command"
    if len(cmd) > MAX_COMMAND_LEN:
        return False, "command too long"
    try:
        for words in _lex(cmd):
            _check_segment(words)
    except _Refused as e:
        return False, str(e)
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
