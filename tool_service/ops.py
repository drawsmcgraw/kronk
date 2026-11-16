"""General ops on managed hosts — registry + read-only command classifier.

Phase A (docs/plans/MAGICMIRROR_PLAN.md "General ops agent" spec): the
devops agent can run READ-ONLY commands on hosts in the registry. The
classifier is the safety core — deterministic, server-side, never the
model. Mutations are refused until phase B adds the confirmation gate.

The SSH key the registry points at can do more than this policy allows (it
is a general-purpose key), so classify_readonly() is the control, not a
convenience. Its shape (M3, 2026-11-15; closed tables in M3 fix 1,
2026-11-16, docs/sprint/M3-fix1.md):

1. The command must be printable ASCII (0x20 to 0x7E). Any other character
   is refused: the classifier and the remote shell must split the line into
   the same words, and Python and bash disagree about which characters are
   blank. The string classified is the string executed; nothing is stripped.
2. Lex the line the way bash will, tracking quoting. Anything the shell
   would expand, redirect or chain is refused: `;` `&` `<` `>` `(` `)` `$`
   backticks, `[`, braces, `#`. The exceptions are a single `|` between
   commands, the two exact words `2>&1` and `2>/dev/null`, and `*` / `?`
   globs in the path operands of programs that print names, never contents.
3. Every pipe segment must start with a program in _RULES (bare name, or a
   path in a system bin directory). Refusal is the default.
4. Path rules on every argument of every program: DENIED_PATH_PATTERNS
   (secrets), and for programs that read file contents, no `..` component,
   no device file but /dev/null, no /proc link that re-roots the path.
5. The program's own rule: a closed table of the options it may be given
   and which of them take a value. An option that is not in the table is
   refused, so no option can name a file to write, follow a log, or shift
   the meaning of the operands after it.

remote_command() is the wrapper ops_exec sends: the classified line runs
under bash (the shell the lexer models) inside coreutils `timeout`, so a
command that never returns is killed on the host, not only abandoned by
the local wait.
"""
import fnmatch
import json
import os
import posixpath
import re
import shlex
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
# so relative paths, ~ and repeated slashes cannot route around it.
#
# Width is Drew's call (ruling of 2026-11-16): the credentials on the mirror
# are read-only ones, so the list is not strict. MagicMirror's config.js is
# readable and `grep -r` is allowed inside the recursion rule below; a
# recursive read of a deep directory can therefore still print a file this
# list names. The list is the plan's (.ssh/, id_* key files, .env files,
# shadow, key and token files, and /proc/<pid>/environ, which is printenv
# by another name) plus the four Drew added: shell history, .npmrc, Wi-Fi
# configuration, and pm2's saved process list (every process's environment).
DENIED_PATH_PATTERNS = (
    ".ssh",                                   # SSH directory and everything in it
    "id_*",                                   # SSH key files (id_rsa, id_ed25519, ...)
    ".env", ".env.*", "*.env",                # dotenv files
    "shadow*", "gshadow*",                    # /etc/shadow and its backups
    "*.key", "*_key", "*.pem", "*.p12", "*.pfx",  # key files
    "*token*", ".netrc", ".git-credentials",  # token files
    "environ",                                # /proc/<pid>/environ
    ".bash_history",                          # typed commands, some with secrets
    ".npmrc",                                 # npm registry auth tokens
    "wpa_supplicant*.conf",                   # Wi-Fi keys (wpa_supplicant)
    "system-connections", "*.nmconnection",   # Wi-Fi keys (NetworkManager)
    "dump.pm2*",                              # ~/.pm2/dump.pm2: saved process environments
)

# Recursive reads (grep -r, and git diff, which compares two directories
# when it is not in a repository) can reach a denied file without naming
# it. A recursive root must be at least this many components deep once ~
# and relative paths are resolved against the directory the command runs in
# (the login home; /home/<user>/x is 3), and must not be under these
# pseudo-filesystems.
RECURSIVE_ROOT_MIN_DEPTH = 3
RECURSIVE_ROOT_NEVER = ("/proc", "/sys", "/dev")
_HOME = "/home/~"                 # stands for the login home, wherever it is

# Files that never end, block, or lead somewhere else. Refused in every
# argument of every program that reads file contents. This is a rule about
# names, so it cannot be complete (a device node or FIFO can sit anywhere);
# remote_command()'s timeout is what bounds a read this list misses.
_PROC_NEVER = {"root", "cwd", "fd", "kmsg", "kcore", "mem", "pagemap", "map_files"}
_SYS_NEVER = (("sys", "kernel", "debug"), ("sys", "kernel", "tracing"))

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

# The whole redirection grammar the classifier accepts: these two words,
# exactly, standing alone (a word boundary on both sides). Each sends stderr
# somewhere that is not a file. Every other use of < or > is refused, so no
# redirection can name a file. tests/test_ops_exec.py checks the lexer
# against bash itself on random lines built from these pieces.
_STDERR_WORDS = ("2>&1", "2>/dev/null")


class _Word(str):
    """One word of a command line after quote removal. `glob` is True when
    the word holds an unquoted * or ?, which bash will expand on the host."""
    glob = False


def _syntax_refusal(cmd: str, i: int) -> _Refused:
    desc = _TWO_CHAR_SYNTAX.get(cmd[i:i + 2]) or _SHELL_SYNTAX[cmd[i]]
    return _Refused(f"contains {desc}: chaining, redirection, background, "
                    "substitution and expansion are not allowed (read-only "
                    "mode; use single quotes for a literal)")


def _lex(cmd: str) -> list[list[_Word]]:
    """Split a command line into pipe segments of words, the way bash would
    after quote removal. Refuses (raises _Refused) on anything bash would
    expand, redirect, chain or background, on unbalanced quotes, and on any
    character that is not printable ASCII."""
    for k, ch in enumerate(cmd):
        o = ord(ch)
        if o < 32 or o == 127:
            raise _Refused("contains a newline or control character "
                           f"(0x{o:02X} at position {k + 1}; chaining is not "
                           "allowed in read-only mode)")
        if o > 126:
            raise _Refused("contains a character outside printable ASCII "
                           f"(U+{o:04X} at position {k + 1}): the remote shell "
                           "does not split words where it looks like it "
                           "should; use plain ASCII")
    segments: list[list[_Word]] = [[]]
    word: list[str] = []
    in_word = False
    glob = False
    i, n = 0, len(cmd)

    def end_word():
        nonlocal word, in_word, glob
        if in_word:
            w = _Word("".join(word))
            w.glob = glob
            segments[-1].append(w)
        word, in_word, glob = [], False, False

    while i < n:
        ch = cmd[i]
        if not in_word and ch == "2":
            tok = next((t for t in _STDERR_WORDS if cmd.startswith(t, i)), None)
            if tok and (i + len(tok) == n or cmd[i + len(tok)] in " |"):
                i += len(tok)           # stderr redirection: not a word
                continue
        if ch == " ":
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
            if ch in "*?":
                glob = True
            word.append(ch)
            in_word = True
        i += 1
    end_word()
    return segments


# ── path rules ────────────────────────────────────────────────────────────────

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


def _path_candidates(arg: str) -> set[str]:
    """Every piece of an argument that the program could open as a path:
    the argument itself, the pieces around = : and , (--file=PATH,
    HEAD:PATH), and a value attached to a short flag (-f/dev/zero)."""
    cands = {arg, *re.split(r"[=:,]", arg)}
    if arg.startswith("-") and not arg.startswith("--"):
        m = re.search(r"[/~.]", arg)
        if m:
            cands.add(arg[m.start():])
    return cands


def _special_path_refusal(arg: str) -> str | None:
    for c in _path_candidates(arg):
        comps = [x for x in c.split("/") if x not in ("", ".")]
        if ".." in comps:
            return ("contains '..': where such a path leads cannot be "
                    "checked (a symlink on the way changes it); name the "
                    "path directly")
        if not c.startswith("/"):
            continue
        if comps[:1] == ["dev"] and comps != ["dev", "null"]:
            return ("is a device file: reading one can block forever or "
                    "never end (only /dev/null is allowed)")
        if comps[:1] == ["proc"] and _PROC_NEVER.intersection(comps[1:]):
            hit = sorted(_PROC_NEVER.intersection(comps[1:]))[0]
            return (f"is a /proc entry ({hit!r}) that blocks, never ends, or "
                    "is a link to another part of the file system")
        if tuple(comps[:3]) in _SYS_NEVER:
            return "is a kernel trace file: reading one blocks forever"
    return None


def _check_special_paths(prog: str, args: list[str]) -> None:
    for a in args:
        why = _special_path_refusal(a)
        if why:
            raise _Refused(f"{prog}: argument {a!r} {why}")


def _abs_path(path: str, base: str = _HOME) -> str:
    if path.startswith("~"):
        head, _, tail = path.partition("/")
        return f"/home/{head[1:] or '~'}/{tail}"
    if path.startswith("/"):
        return path
    return f"{base}/{path}"


def _recursive_root_refusal(root: str, base: str = _HOME,
                            noun: str = "recursive search root") -> str | None:
    p = _abs_path(root, base)
    if ".." in p.split("/"):
        return f"{noun} {root!r} contains '..'"
    p = posixpath.normpath(re.sub(r"/+", "/", p))
    for top in RECURSIVE_ROOT_NEVER:
        if p == top or p.startswith(top + "/"):
            return f"recursive search under {top} is not allowed"
    depth = len([c for c in p.split("/") if c])
    if depth < RECURSIVE_ROOT_MIN_DEPTH:
        return (f"{noun} {root!r} is too broad (it can reach "
                "secrets without naming them); search a deeper directory "
                "or name the files")
    return None


def _glob_refusal(word: str) -> _Refused:
    c = "*" if "*" in word else "?"
    names = ", ".join(sorted(k for k, r in _RULES.items()
                             if isinstance(r, Rule) and r.meta))
    return _Refused(f"contains unquoted '{c}' (glob): the shell expands it on "
                    "the host into names the classifier cannot see, so a glob "
                    "is allowed only in a path operand of a program that "
                    f"prints names and sizes, never contents ({names}); "
                    "quote it for a literal")


def _check_glob_operand(prog: str, word: str) -> None:
    """A glob in a path operand of a names-only program. The first
    character is fixed, so no match can be read as an option; a glob cannot
    start a component with '.', so it does not walk into hidden
    directories ('*' never matches a leading dot)."""
    if not word.startswith(("/", "./", "~/")):
        raise _Refused(f"{prog}: glob {word!r} must start with /, ./ or ~/ "
                       "(so that no match can be read as an option)")
    for comp in word.split("/"):
        if comp.startswith(".") and ("*" in comp or "?" in comp):
            raise _Refused(f"{prog}: glob {word!r} has a component that "
                           "starts with '.'; name hidden files and "
                           "directories in full")


# ── per-program rules ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Rule:
    """The options one program may be given. The table is closed: an option
    that is not listed is refused, whatever it does. Long options must be
    written in full (getopt accepts abbreviations; the table does not).

    flags       short letters that take no value.
    short_args  short letters that take a value: the rest of the cluster,
                or the next word.
    short_opt   short letters whose optional value can only be attached.
    next_opt    short_opt letter -> pattern; a next word that matches it is
                the value (journalctl -n 50, -b -1).
    longs       long names that take no value.
    long_args   long names that take a value, as --name=value or --name value.
    long_eq     long names whose value must be attached (--name=value).
    long_opt    long names with an optional value (--name or --name=value).
    dash_ok     options ("-e", "--since") whose separate value may start
                with '-'. For every other option such a value is refused:
                if the table were wrong about that option, the "value"
                would be an option the classifier never looked at.
    digits      -NUM is allowed (head -5, grep -3, git log -3).
    forbid      spelling -> reason, for options worth a specific refusal. A
                short letter is caught inside combined flags (-uo); a long
                name is caught when abbreviated (--out); both are refused
                after '--' as well.
    opts_first  options must come before the first operand. Set where the
                number of operands matters, so that the count is the same
                whether or not the program reorders its arguments (GNU
                programs do; with POSIXLY_CORRECT set they do not).
    opts_first_with  the same, but only when one of these options is given
                (grep -r: which operands are search roots must not depend
                on reordering either).
    dashdash    the program honours '--' (end of options).
    free        arguments are inert text (echo, printf, true).
    meta        the program prints names and metadata, never file contents:
                globs are allowed in its operands, and device files and '..'
                are allowed in its arguments.
    max_pos     operand limit. Words that are not options count, as does
                every word after '--', as does a lone '-'.
    check       extra check on (args, operands, option names seen).
    """
    flags: str = ""
    short_args: str = ""
    short_opt: str = ""
    next_opt: dict = field(default_factory=dict)
    longs: frozenset = frozenset()
    long_args: frozenset = frozenset()
    long_eq: frozenset = frozenset()
    long_opt: frozenset = frozenset()
    dash_ok: frozenset = frozenset()
    digits: bool = False
    forbid: dict = field(default_factory=dict)
    opts_first: bool = False
    opts_first_with: frozenset = frozenset()
    dashdash: bool = True
    free: bool = False
    meta: bool = False
    max_pos: int | None = None
    check: Callable | None = None


def _fs(s: str) -> frozenset:
    return frozenset(s.split())


def _R(flags="", args="", opt="", longs="", long_args="", long_eq="",
       long_opt="", dash_ok="", opts_first_with="", **kw) -> Rule:
    return Rule(flags=flags, short_args=args, short_opt=opt, longs=_fs(longs),
                long_args=_fs(long_args), long_eq=_fs(long_eq),
                long_opt=_fs(long_opt), dash_ok=_fs(dash_ok),
                opts_first_with=_fs(opts_first_with), **kw)


def _parse(prog: str, args: list, rule: Rule) -> tuple[list, list[str]]:
    """(operands, option names seen), reading the words the way getopt_long
    does. Raises on a forbidden option, an option that is not in the table,
    a missing value, and a separate value that starts with '-'."""
    if rule.free:
        return list(args), []
    late = None          # (option, operand) for the first option after an operand
    short_forbid = {f[1]: why for f, why in rule.forbid.items()
                    if len(f) == 2 and f[0] == "-" and f[1] != "-"}
    long_forbid = {f[2:]: why for f, why in rule.forbid.items() if f.startswith("--")}
    pos: list = []
    seen: list[str] = []
    end_opts = False
    i = 0

    def no_glob(a):
        if getattr(a, "glob", False):
            raise _glob_refusal(a)

    def take_value(opt: str) -> None:
        nonlocal i
        if i >= len(args):
            raise _Refused(f"{prog}: {opt} needs a value")
        v = args[i]
        no_glob(v)
        if v.startswith("-") and v != "-" and opt not in rule.dash_ok:
            raise _Refused(f"{prog}: {opt} takes a value, and the next word "
                           f"{v!r} starts with '-'; attach the value to the "
                           "option if that is what you mean")
        i += 1

    while i < len(args):
        a = args[i]
        i += 1
        if a == "--" and not end_opts:
            if not rule.dashdash:
                raise _Refused(f"{prog}: '--' is not allowed")
            end_opts = True
            continue
        if a == "-" or not a.startswith("-"):
            pos.append(a)
            continue
        no_glob(a)
        if pos and not end_opts and late is None:
            late = (a, pos[0])
        if a.startswith("--"):
            name, eq, _ = a[2:].partition("=")
            known = (name in rule.longs or name in rule.long_args
                     or name in rule.long_eq or name in rule.long_opt)
            if name and (end_opts or not known):
                for f, why in long_forbid.items():
                    if f.startswith(name):
                        raise _Refused(f"{prog}: --{f} {why}"
                                       + ("" if name == f else f" (given as {a!r})"))
            if end_opts:
                pos.append(a)
                continue
            if name in rule.longs and not eq:
                pass
            elif name in rule.long_opt:
                pass
            elif name in rule.long_args:
                if not eq:
                    take_value("--" + name)
            elif name in rule.long_eq:
                if not eq:
                    raise _Refused(f"{prog}: write --{name}=VALUE (the value "
                                   "must be attached)")
            elif name in rule.longs:
                raise _Refused(f"{prog}: --{name} takes no value")
            else:
                raise _Refused(f"{prog}: option {a!r} is not on the allowed "
                               f"list for {prog} (unknown options are refused "
                               "in read-only mode; long options must be "
                               "written in full)")
            seen.append("--" + name)
            continue
        body = a[1:]
        for j, c in enumerate(body):
            if c in short_forbid:
                raise _Refused(f"{prog}: -{c} {short_forbid[c]}"
                               + ("" if body == c else f" (given as {a!r})"))
            if c in rule.short_args:
                if not end_opts:
                    seen.append("-" + c)
                    if j == len(body) - 1:
                        take_value("-" + c)
                break
            if c in rule.short_opt:
                if not end_opts:
                    seen.append("-" + c)
                    pat = rule.next_opt.get(c)
                    if (j == len(body) - 1 and pat and i < len(args)
                            and re.fullmatch(pat, args[i])):
                        i += 1
                break
            if end_opts:
                continue
            if c in rule.flags or (rule.digits and c.isdigit()):
                seen.append("-" + c)
                continue
            raise _Refused(f"{prog}: option -{c}"
                           + ("" if body == c else f" (in {a!r})")
                           + f" is not on the allowed list for {prog} "
                           "(unknown options are refused in read-only mode)")
        if end_opts:
            pos.append(a)
    if late and (rule.opts_first or rule.opts_first_with.intersection(seen)):
        raise _Refused(f"{prog}: put options before the operands "
                       f"(got {late[0]!r} after {late[1]!r})")
    return pos, seen


def _apply(prog: str, args: list, rule: Rule) -> tuple[list, list[str]]:
    pos, seen = _parse(prog, args, rule)
    for p in pos:
        if getattr(p, "glob", False):
            if not rule.meta:
                raise _glob_refusal(p)
            _check_glob_operand(prog, p)
    if rule.max_pos is not None and len(pos) > rule.max_pos:
        raise _Refused(f"{prog}: at most {rule.max_pos} operand"
                       f"{'' if rule.max_pos == 1 else 's'} allowed, got {len(pos)}")
    if rule.check:
        rule.check(args, pos, seen)
    return pos, seen


_FOLLOW = "follows forever and never returns (burns the 30 s timeout); use -n N"
_LISTFILE = ("reads the list of files to open from a file the classifier "
             "cannot see")
_DEVREAD = "reads device files, which can block or never end"


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


def _grep_check(prog):
    def check(args, pos, seen):
        if "-r" not in seen and "--recursive" not in seen:
            return
        has_pattern_flag = any(s in ("-e", "-f", "--regexp", "--file") for s in seen)
        roots = pos if has_pattern_flag else pos[1:]
        if not roots:
            raise _Refused(f"{prog}: recursive search with no directory searches "
                           "the home directory; name a directory")
        for r in roots:
            why = _recursive_root_refusal(r)
            if why:
                raise _Refused(f"{prog}: {why}")
    return check


_GREP_FORBID = {
    "-R": "follows every symlink while recursing; use -r",
    "--dereference-recursive": "follows every symlink while recursing; use -r",
    "-d": "sets the directory action; use -r for recursion",
    "--directories": "sets the directory action; use -r for recursion",
    "-D": "sets the device action (grep could read devices and FIFOs, which never end)",
    "--devices": "sets the device action (grep could read devices and FIFOs, which never end)",
}


def _grep_rule(prog: str) -> Rule:
    return _R(
        "EFGPiyvwxcLlqsbHhnoTUZzaIr", args="ABCefm",
        longs="extended-regexp fixed-strings basic-regexp perl-regexp "
              "ignore-case no-ignore-case invert-match word-regexp line-regexp "
              "count files-without-match files-with-matches quiet silent "
              "no-messages byte-offset with-filename no-filename line-number "
              "only-matching initial-tab null null-data text binary "
              "line-buffered recursive",
        long_args="regexp file max-count after-context before-context context "
                  "label binary-files include exclude exclude-dir exclude-from "
                  "group-separator",
        long_opt="color colour", dash_ok="-e --regexp", digits=True,
        opts_first_with="-r --recursive",
        forbid=_GREP_FORBID, check=_grep_check(prog))


def _check_node(args):
    if args not in (["--version"], ["-v"]):
        raise _Refused("node: only 'node --version' is allowed (node runs code)")


# ps reads /proc and nothing else; it cannot write or follow. Its three
# option syntaxes are still read against closed lists, and the BSD 'e'
# (print every process's environment) is refused.
_PS_UNIX_FLAGS = "AadeNTrxfFjlMyHLZwcPVm"
_PS_UNIX_ARGS = "oOpuUgGCtsqkn"
_PS_BSD_FLAGS = "axurfjlsvXwThHLmSZcng"
_PS_BSD_ARGS = "oOkpqUN"
_PS_LONGS = _fs("forest no-headers no-heading headers deselect cumulative "
                "context version")
_PS_LONG_ARGS = _fs("sort format pid ppid user User group Group sid tty cols "
                    "columns lines rows width quick-pid")


def _check_ps(args):
    i = 0

    def value(opt, attached):
        nonlocal i
        if attached:
            return
        if i >= len(args):
            raise _Refused(f"ps: {opt} needs a value")
        if args[i].startswith("-") and opt not in ("--sort", "-k", "k"):
            raise _Refused(f"ps: {opt} takes a value, and the next word "
                           f"{args[i]!r} starts with '-'")
        i += 1

    while i < len(args):
        a = args[i]
        i += 1
        if a.startswith("--"):
            name, eq, _ = a[2:].partition("=")
            if name in _PS_LONGS and not eq:
                continue
            if name in _PS_LONG_ARGS:
                value("--" + name, bool(eq))
                continue
            raise _Refused(f"ps: option {a!r} is not on the allowed list for ps")
        if re.fullmatch(r"[\d,]+", a):
            continue                                    # pid list
        bsd = not a.startswith("-")
        body = a if bsd else a[1:]
        flags, takes = ((_PS_BSD_FLAGS, _PS_BSD_ARGS) if bsd
                        else (_PS_UNIX_FLAGS, _PS_UNIX_ARGS))
        if not body:
            raise _Refused("ps: a lone '-' is not allowed")
        for j, c in enumerate(body):
            if bsd and c == "e":
                raise _Refused(f"ps: BSD option {a!r} includes 'e', which prints "
                               "every process's environment (secrets)")
            if c in flags:
                continue
            if c in takes:
                value(c if bsd else "-" + c, attached=j < len(body) - 1)
                break
            raise _Refused(f"ps: option letter {c!r} in {a!r} is not on the "
                           "allowed list for ps")


# Subcommand programs. The subcommand must come first (after a short list of
# exact, value-less global flags): a flag with a value in front of it would
# otherwise let a value pose as the subcommand (systemctl -p status restart x).

def _verb_first(prog, args, pre_flags, allowed, extra_reason=None,
                pre_prefixes=()):
    """(verb, rest). verb is None when there are only pre-flags."""
    i = 0
    while i < len(args) and (args[i] in pre_flags
                             or (pre_prefixes and args[i].startswith(pre_prefixes))):
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
                  "-a", "--plain", "--no-legend", "-q", "--quiet", "--failed"}
_SYSTEMCTL_PRE_PREFIXES = ("--type=", "--state=")
_SYSTEMCTL_READ = {"status", "is-active", "is-enabled", "is-failed",
                   "is-system-running", "show", "cat", "list-units",
                   "list-unit-files", "list-timers", "list-sockets",
                   "list-dependencies", "get-default"}
_REMOTE_FLAGS = {"-H": "targets another host", "--host": "targets another host",
                 "-M": "targets another machine", "--machine": "targets another machine"}
_SYSTEMCTL_RULE = _R(
    "alqr", args="tpPno",
    longs="all full quiet recursive no-pager no-legend plain user system "
          "failed reverse after before value show-types with-dependencies "
          "no-ask-password global",
    long_args="type state property lines output timestamp",
    forbid={**_REMOTE_FLAGS,
            "--wait": "blocks until the system has finished starting"})


def _check_systemctl(args):
    _, rest = _verb_first("systemctl", args, _SYSTEMCTL_PRE, _SYSTEMCTL_READ,
                          {"show-environment": "it prints the manager's "
                           "environment, which holds secrets"},
                          _SYSTEMCTL_PRE_PREFIXES)
    _apply("systemctl", rest, _SYSTEMCTL_RULE)


# pm2 and npm parse options with their own libraries (commander, nopt), not
# getopt, so each allowed form is spelled out word by word.
_PM2_READ = {"list", "ls", "status", "logs", "describe", "show", "info"}
_PM2_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*")
_VERSION_ONLY = (["--version"], ["-v"])


def _check_pm2(args):
    if not args or args in _VERSION_ONLY:
        return
    if args[0].startswith("-"):
        raise _Refused("pm2: put the subcommand first")
    env = "it prints every process's environment (secrets)"
    verb, rest = _verb_first("pm2", args, set(), _PM2_READ,
                             {"jlist": env, "prettylist": env, "env": env})
    if verb in ("list", "ls", "status"):
        if rest:
            raise _Refused(f"pm2 {verb}: no options or operands are allowed "
                           "(--watch redraws forever and never returns)")
        return
    if verb in ("describe", "show", "info"):
        if len(rest) != 1 or not _PM2_NAME.fullmatch(rest[0]):
            raise _Refused(f"pm2 {verb}: give exactly one process name or id, "
                           "and no options")
        return
    names, nostream, i = [], False, 0
    while i < len(rest):
        a = rest[i]
        i += 1
        if a == "--":
            names += rest[i:]       # after '--' nothing is an option
            break
        if a == "--nostream":
            nostream = True
        elif a in ("--err", "--out", "--raw"):
            pass
        elif a == "--lines" or a.startswith("--lines="):
            if "=" in a:
                v = a.partition("=")[2]
            else:
                v = rest[i] if i < len(rest) else ""
                i += 1
            if not re.fullmatch(r"\d{1,4}", v):
                raise _Refused("pm2 logs: --lines takes a number up to 9999 "
                               "(without one, pm2 swallows the next option "
                               "as the count)")
        elif a.startswith("-"):
            raise _Refused(f"pm2 logs: option {a!r} is not on the allowed "
                           "list (--nostream, --lines N, --err, --out, --raw)")
        else:
            names.append(a)
    if not nostream:
        raise _Refused("pm2: 'pm2 logs' streams forever; add --nostream "
                       "(and --lines N)")
    if len(names) > 1 or any(not _PM2_NAME.fullmatch(x) for x in names):
        raise _Refused("pm2 logs: at most one process name or id")


_NPM_READ = {"ls", "list", "view", "outdated", "root", "prefix", "why"}
_NPM_FLAGS = {"--json", "--long", "-l", "--parseable", "-p", "--all", "-a",
              "--global", "-g"}
_NPM_NAME = re.compile(r"(@[A-Za-z0-9][A-Za-z0-9._~-]*/)?[A-Za-z0-9][A-Za-z0-9._~-]*"
                       r"(@[A-Za-z0-9._^~-]+)?")
_NPM_FIELD = re.compile(r"[A-Za-z0-9_.\[\]-]+")


def _check_npm(args):
    if not args or args in _VERSION_ONLY:
        return
    if args[0].startswith("-"):
        raise _Refused("npm: put the subcommand first")
    verb, rest = _verb_first("npm", args, set(), _NPM_READ)
    operands, i = [], 0
    while i < len(rest):
        a = rest[i]
        i += 1
        if a in _NPM_FLAGS:
            continue
        if a == "--depth" or a.startswith("--depth="):
            if "=" in a:
                v = a.partition("=")[2]
            else:
                v = rest[i] if i < len(rest) else ""
                i += 1
            if not re.fullmatch(r"\d{1,3}", v):
                raise _Refused(f"npm {verb}: --depth takes a number")
            continue
        if a.startswith("-"):
            raise _Refused(f"npm {verb}: option {a!r} is not on the allowed "
                           "list (--depth N, --json, --long, --parseable, "
                           "--all, --global): npm options can name "
                           "directories to write and registries to contact")
        operands.append(a)
    if verb in ("root", "prefix") and operands:
        raise _Refused(f"npm {verb}: no operands are allowed")
    for k, o in enumerate(operands):
        if _NPM_NAME.fullmatch(o) or (verb == "view" and k > 0
                                      and _NPM_FIELD.fullmatch(o)):
            continue
        raise _Refused(f"npm {verb}: operand {o!r} is not a registry package "
                       "name (paths, URLs and git addresses are not allowed)")


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
    for a in rest[1:]:
        if a.startswith("-"):
            raise _Refused(f"ip {obj}: option {a!r} after the object is not "
                           "allowed (put ip's options first)")


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
# everything else are refused there. After the subcommand each one has its
# own closed table. A value is accepted only attached (--date=short) for
# every long option below: git takes some of them either way and some only
# attached, and the attached form reads the same in both.
_GIT_GLOBAL_VALUE = {"-C", "--git-dir", "--work-tree"}
_GIT_GLOBAL_FLAG = {"--no-pager", "--no-optional-locks"}
_GIT_FORBID = {
    "--output": "writes a file",
    "--ext-diff": "runs an external diff program",
    "--no-index": "compares arbitrary paths outside the repository",
    "--contents": "reads a named file from outside the repository",
}
_GIT_DIFF_LONGS = (
    "patch no-patch numstat shortstat name-only name-status summary raw "
    "compact-summary patch-with-stat patch-with-raw minimal patience "
    "histogram text ignore-all-space ignore-space-change ignore-space-at-eol "
    "ignore-blank-lines ignore-cr-at-eol no-renames find-copies-harder "
    "no-prefix no-textconv no-ext-diff no-color full-index binary cc "
    "pickaxe-all pickaxe-regex function-context no-relative")
_GIT_DIFF_OPT = (
    "stat color word-diff color-words relative dirstat find-renames "
    "find-copies abbrev submodule ignore-submodules color-moved")
_GIT_DIFF_EQ = (
    "unified inter-hunk-context diff-filter diff-algorithm src-prefix "
    "dst-prefix line-prefix output-indicator-new output-indicator-old "
    "output-indicator-context word-diff-regex ws-error-highlight anchored")
_GIT_LOG_LONGS = (
    "oneline graph all no-decorate abbrev-commit no-abbrev-commit no-merges "
    "merges first-parent reverse follow full-history source relative-date "
    "date-order topo-order author-date-order cherry-pick cherry cherry-mark "
    "left-right left-only right-only boundary simplify-by-decoration "
    "simplify-merges sparse dense do-walk walk-reflogs full-diff log-size "
    "regexp-ignore-case extended-regexp fixed-strings perl-regexp "
    "basic-regexp invert-grep all-match parents children ancestry-path not "
    "no-notes root no-expand-tabs")
_GIT_LOG_OPT = "decorate pretty branches tags remotes no-walk notes show-notes expand-tabs"
_GIT_LOG_EQ = (
    "format date since until after before author committer grep max-count "
    "skip min-parents max-parents glob exclude decorate-refs "
    "decorate-refs-exclude encoding")
_GIT_LOG_RULE = _R(
    "pusRabwzmcrtiEFPg", args="nSGL", opt="MCBU",
    longs=_GIT_DIFF_LONGS + " " + _GIT_LOG_LONGS,
    long_opt=_GIT_DIFF_OPT + " " + _GIT_LOG_OPT,
    long_eq=_GIT_DIFF_EQ + " " + _GIT_LOG_EQ,
    digits=True, forbid=_GIT_FORBID)
_GIT_RULES = {
    "log": _GIT_LOG_RULE,
    "show": _GIT_LOG_RULE,
    "diff": _R(
        "pusRabwz", args="SG", opt="MCBU",
        longs=_GIT_DIFF_LONGS + " cached staged merge-base exit-code quiet check",
        long_opt=_GIT_DIFF_OPT, long_eq=_GIT_DIFF_EQ,
        digits=True, forbid=_GIT_FORBID),
    "status": _R(
        "sbvz", opt="u",
        longs="short branch long verbose show-stash ahead-behind "
              "no-ahead-behind renames no-renames null no-column",
        long_opt="porcelain ignored untracked-files column ignore-submodules "
                 "find-renames",
        forbid=_GIT_FORBID),
    "rev-parse": _R(
        "q",
        longs="show-toplevel git-dir is-inside-work-tree is-inside-git-dir "
              "is-bare-repository is-shallow-repository verify quiet symbolic "
              "symbolic-full-name show-prefix show-cdup absolute-git-dir "
              "git-common-dir all show-superproject-working-tree",
        long_opt="short abbrev-ref branches tags remotes",
        long_eq="path-format since until after before glob exclude "
                "disambiguate default",
        forbid=_GIT_FORBID),
    "describe": _R(
        longs="all tags always long contains exact-match first-parent debug",
        long_opt="dirty broken", long_eq="abbrev match exclude candidates",
        forbid=_GIT_FORBID),
    "ls-files": _R(
        "cdmoiksuvtfz",
        longs="cached deleted modified others ignored stage unmerged killed "
              "directory no-empty-directory eol full-name recurse-submodules "
              "debug deduplicate error-unmatch exclude-standard sparse",
        long_opt="abbrev",
        long_eq="exclude exclude-per-directory with-tree format",
        forbid=_GIT_FORBID),
    "shortlog": _R(
        "nsec", opt="w",
        longs="numbered summary email committer all no-merges merges first-parent",
        long_eq="format group since until after before author max-count",
        digits=True, forbid=_GIT_FORBID),
    "blame": _R(
        "bclstwfnepk", args="L", opt="MC",
        longs="show-name show-number show-email porcelain line-porcelain "
              "incremental root show-stats first-parent color-lines "
              "color-by-age minimal progress no-progress",
        long_opt="abbrev", long_eq="since date ignore-rev",
        forbid=_GIT_FORBID),
    "cat-file": _R(
        "tsep",
        longs="batch-all-objects buffer unordered follow-symlinks "
              "allow-unknown-type",
        long_opt="batch batch-check", forbid=_GIT_FORBID),
}
_GIT_READ = set(_GIT_RULES) | {"branch", "tag", "remote"}


def _git_listing(sub, args, letters, longs, eq_longs, number_flag=None,
                 l_lists=True):
    """branch/tag: listing forms only. Operands (patterns) only with
    --list; anything else would create, delete or rename. `git tag -l`
    always meant --list; `git branch -l` meant --create-reflog before git
    2.20, so for branch only the long spelling lets an operand through."""
    listing = False
    pos = []
    for a in args:
        if a == "--list" or (l_lists and a.startswith("-")
                             and not a.startswith("--")
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
    cwd, work_tree = _HOME, None
    i = 0
    while i < len(args):
        a = args[i]
        if a in _GIT_GLOBAL_VALUE:
            if i + 1 >= len(args):
                raise _Refused(f"git: {a} needs a value")
            v = args[i + 1]
            if v.startswith("-"):
                raise _Refused(f"git: {a} takes a directory, got {v!r}")
            if a == "-C":
                cwd = _abs_path(v, cwd) if v else cwd
            elif a == "--work-tree":
                work_tree = v
            i += 2
            continue
        if a.split("=", 1)[0] in ("--git-dir", "--work-tree") and "=" in a:
            if a.startswith("--work-tree="):
                work_tree = a.split("=", 1)[1]
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
    if work_tree is not None:
        # git reads working files under the work tree by the names in the
        # index; it gets the same floor as any other recursive read.
        why = _recursive_root_refusal(work_tree, cwd, noun="--work-tree")
        if why:
            raise _Refused(f"git: {why}")
    if sub == "branch":
        _git_listing("branch", rest, "arvli",
                     {"--list", "--all", "--remotes", "--verbose",
                      "--show-current", "--no-color", "--color",
                      "--no-column", "--ignore-case", "--no-abbrev"},
                     ("--contains=", "--no-contains=", "--merged=",
                      "--no-merged=", "--points-at=", "--sort=", "--format=",
                      "--abbrev=", "--color="), l_lists=False)
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
        pos, _ = _apply(f"git {sub}", rest, _GIT_RULES[sub])
        if sub == "diff":
            # Outside a repository, or given a path outside it, `git diff
            # A B` is `git diff --no-index A B`: it compares any two paths,
            # recursively. git decides that on the host, so every operand
            # is held to the recursive-read floor wherever it could resolve.
            bases = [cwd] + ([_abs_path(work_tree, cwd)] if work_tree else [])
            for o in pos:
                for b in bases:
                    why = _recursive_root_refusal(o, b, noun="path operand")
                    if why:
                        raise _Refused(
                            f"git diff: {why} (outside a repository git diff "
                            "compares any two paths, directories included)")


# The allowlist. A Rule is applied with _apply; a function does its own
# parsing. Programs not listed here are refused.
_RULES: dict = {
    # no file arguments
    "uptime": _R("phsV", longs="pretty help since version", max_pos=0),
    "whoami": _R(longs="help version", max_pos=0),
    "id": _R("agGnruzZ", longs="context group groups name real user zero"),
    "uname": _R("asnrvmpio",
                longs="all kernel-name nodename kernel-release kernel-version "
                      "machine processor hardware-platform operating-system",
                max_pos=0),
    "true": Rule(free=True), "echo": Rule(free=True), "printf": Rule(free=True),
    "which": _R("as"),
    "type": _R("afptP"),
    "lscpu": _R("abcJxyB", opt="epC",
                longs="all online offline json hex physical bytes",
                long_opt="extended parse caches"),
    "lsusb": _R("vt", args="sd", longs="verbose tree"),
    "lsmod": _R(max_pos=0),
    "pgrep": _R("acfilnovwx", args="dgGPstuUr",
                longs="list-full count full ignore-case list-name newest oldest "
                      "inverse lightweight exact",
                long_args="delimiter pgroup group parent session terminal euid "
                          "uid runstates older"),
    "pidof": _R("scnxqzwt", args="oSd"),
    # names and metadata only: never file contents
    "ls": _R("aAbBcCdfFgGhHiklLmnNopqQrRsStuUvxX1Z", args="IwT",
             longs="all almost-all author escape ignore-backups directory "
                   "file-type human-readable si dereference "
                   "dereference-command-line "
                   "dereference-command-line-symlink-to-dir inode "
                   "numeric-uid-gid literal no-group quote-name reverse "
                   "recursive size group-directories-first full-time kibibytes "
                   "hide-control-chars show-control-chars zero context",
             long_args="block-size format hide ignore indicator-style "
                       "quoting-style sort time time-style tabsize width",
             long_opt="color classify hyperlink", meta=True),
    "du": _R("0aAbcDhHklLmPsSx", args="Bdt",
             longs="null all apparent-size bytes total dereference-args "
                   "human-readable si count-links dereference no-dereference "
                   "separate-dirs summarize one-file-system inodes",
             long_args="block-size max-depth threshold exclude time-style",
             long_opt="time", forbid={"--files0-from": _LISTFILE}, meta=True),
    "stat": _R("fLt", args="c", longs="dereference file-system terse",
               long_args="format printf cached", meta=True),
    "df": _R("ahHiklPTv", args="Btx",
             longs="all human-readable si inodes local portability print-type "
                   "total no-sync",
             long_args="block-size type exclude-type", long_opt="output",
             forbid={"--sync": "calls sync(), which flushes pending writes to disk"},
             meta=True),
    "lsblk": _R("abdDfiJlmnOpPrsStzM", args="oxeIw", opt="T",
                longs="all bytes nodeps discard fs ascii json list perms "
                      "noheadings output-all paths pairs raw inverse scsi "
                      "topology zoned merge",
                long_args="output sort exclude include width", long_opt="tree",
                meta=True),
    "readlink": _R("femnqsvz",
                   longs="canonicalize canonicalize-existing canonicalize-missing "
                         "no-newline quiet silent verbose zero", meta=True),
    "realpath": _R("eLmPqsz",
                   longs="canonicalize-existing canonicalize-missing logical "
                         "physical quiet strip no-symlinks zero",
                   long_args="relative-to relative-base", meta=True),
    "dirname": _R("z", longs="zero", meta=True),
    "basename": _R("az", args="s", longs="multiple zero", long_args="suffix",
                   meta=True),
    # file readers: every argument is under the path rules
    "cat": _R("AbeEnstTuv",
              longs="show-all number-nonblank show-ends number squeeze-blank "
                    "show-tabs show-nonprinting"),
    "head": _R("qvz", args="cn", longs="quiet silent verbose zero-terminated",
               long_args="bytes lines", dash_ok="-c -n --bytes --lines",
               digits=True),
    "tail": _R("qvz", args="cn", longs="quiet silent verbose zero-terminated",
               long_args="bytes lines", dash_ok="-c -n --bytes --lines",
               digits=True,
               forbid={"-f": _FOLLOW, "-F": _FOLLOW, "--follow": _FOLLOW,
                       "--retry": _FOLLOW},
               check=_tail_check),
    "wc": _R("clmLw", longs="bytes chars lines words max-line-length",
             forbid={"--files0-from": _LISTFILE}),
    "cut": _R("snz", args="bcdf",
              longs="complement only-delimited zero-terminated",
              long_args="bytes characters delimiter fields output-delimiter",
              dash_ok="-d --delimiter"),
    "tr": _R("cCdst", longs="complement delete squeeze-repeats truncate-set1"),
    "column": _R("tx", args="sco", longs="table fillrows",
                 long_args="separator output-separator output-width columns"),
    "nl": _R("p", args="bdfhilnsvw", longs="no-renumber",
             long_args="body-numbering section-delimiter footer-numbering "
                       "header-numbering line-increment join-blank-lines "
                       "number-format number-separator starting-line-number "
                       "number-width",
             dash_ok="-v --starting-line-number"),
    "grep": _grep_rule("grep"), "egrep": _grep_rule("egrep"),
    "fgrep": _grep_rule("fgrep"),
    # zgrep is a shell script with its own option reader: no long options,
    # no -r (it refuses it), no -f (it copies the patterns to a temp file).
    "zgrep": _R("EFGHLPabchilnoqsvwxy", args="ABCem", dash_ok="-e", digits=True),
    "sort": _R("bdfghiMnRrVcCmsuz", args="ktS",
               longs="ignore-leading-blanks dictionary-order ignore-case "
                     "general-numeric-sort ignore-nonprinting month-sort "
                     "human-numeric-sort numeric-sort random-sort reverse "
                     "version-sort merge stable unique zero-terminated debug",
               long_args="key field-separator buffer-size sort parallel batch-size",
               long_opt="check",
               forbid={"-o": "writes a file", "--output": "writes a file",
                       "--compress-program": "runs a program",
                       "--files0-from": ("reads the list of files to print "
                                         "from a file the classifier cannot see"),
                       "-T": "names a directory to write temporary files in",
                       "--temporary-directory": "names a directory to write temporary files in",
                       "--random-source": "reads random bytes from a named file, which can be an endless device"}),
    "uniq": _R("cdDiuz", args="fsw",
               longs="count repeated ignore-case unique zero-terminated",
               long_args="skip-fields skip-chars check-chars",
               long_opt="all-repeated group", opts_first=True,
               check=_uniq_check),
    "file": _R("bihkLNnpr0Ez", args="eFmP",
               longs="brief mime mime-type mime-encoding dereference "
                     "no-dereference keep-going no-buffer no-pad preserve-date "
                     "raw print0 uncompress extension",
               long_args="exclude separator magic-file parameter",
               forbid={"-C": "writes a compiled magic file",
                       "--compile": "writes a compiled magic file",
                       "-f": _LISTFILE, "--files-from": _LISTFILE,
                       "-s": _DEVREAD, "--special-files": _DEVREAD}),
    "date": _R("uR", args="dr", opt="I", longs="utc universal rfc-email debug",
               long_args="date reference rfc-3339", long_opt="iso-8601",
               dash_ok="-d --date",
               forbid={"-s": "sets the clock", "--set": "sets the clock",
                       "-f": "reads dates from a named file, which can be an endless device",
                       "--file": "reads dates from a named file, which can be an endless device"},
               check=_date_check),
    "hostname": _R("adfiIsyAV",
                   longs="alias all-fqdns domain fqdn long ip-address "
                         "all-ip-addresses short yp nis version",
                   forbid={"-F": "sets the hostname from a file",
                           "--file": "sets the hostname from a file",
                           "-b": "sets the hostname", "--boot": "sets the hostname"},
                   check=_hostname_check),
    "hostnamectl": _R("j", longs="static transient pretty no-ask-password",
                      long_args="json", forbid=_REMOTE_FLAGS,
                      check=_hostnamectl_check),
    "ss": _R("hVHQOnraloempiTsbZz460tudwxSM", args="fA",
             longs="help version no-header no-queues oneline numeric resolve "
                   "all listening bound-inactive options extended memory "
                   "processes threads info summary bpf context contexts ipv4 "
                   "ipv6 packet tcp udp dccp raw unix sctp mptcp tipc vsock xdp",
             long_args="family query socket",
             forbid={"-K": "kills sockets", "--kill": "kills sockets",
                     "-D": "writes a dump file", "--diag": "writes a dump file",
                     "-E": "prints sockets as they close, forever, and never returns",
                     "--events": "prints sockets as they close, forever, and never returns",
                     "-F": "reads the filter from a named file",
                     "--filter": "reads the filter from a named file",
                     "-N": "switches network namespace",
                     "--net": "switches network namespace"}),
    "free": _R("bkmghwtlvL",
               longs="bytes kibi mebi gibi tebi pebi kilo mega giga tera peta "
                     "human si lohi total wide line committed",
               forbid={"-s": "repeats until stopped", "--seconds": "repeats until stopped",
                       "-c": "repeats the report; one report only",
                       "--count": "repeats the report; one report only"},
               max_pos=0),
    "sensors": _R("Afuj", longs="no-adapter fahrenheit bus-list",
                  forbid={"-s": "writes limits to the chip", "--set": "writes limits to the chip",
                          "-c": "reads a named configuration file",
                          "--config-file": "reads a named configuration file"}),
    "vmstat": _R("afmdDswtny", args="S",
                 longs="active forks slabs disk disk-sum stats wide timestamp "
                       "one-header no-first",
                 long_args="unit", check=_snapshot_check("vmstat")),
    "iostat": _R("cdhkmNtxyzsH", longs="human pretty", dashdash=False,
                 check=_snapshot_check("iostat")),
    "journalctl": _R(
        "aexqmkrNl", args="oupgcSUtFD", opt="bn",
        next_opt={"b": r"[+-]?\d+|all|[0-9a-f]{32}([+-]\d+)?", "n": r"\+?\d+|all"},
        longs="all pager-end reverse catalog no-catalog quiet merge dmesg "
              "fields full no-full no-pager no-tail utc user system "
              "list-boots disk-usage header verify show-cursor no-hostname "
              "version",
        long_args="since until unit user-unit identifier priority grep cursor "
                  "after-cursor output directory file root field facility "
                  "output-fields namespace",
        long_opt="boot lines case-sensitive",
        dash_ok="-S -U --since --until",
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
                "--cursor-file": "writes a cursor file"}),
    # programs with their own argument grammar
    "ps": _check_ps,
    "node": _check_node,
    "systemctl": _check_systemctl,
    "git": _check_git,
    "npm": _check_npm,
    "pm2": _check_pm2,
    "ip": _check_ip,
    "vcgencmd": _check_vcgencmd,
}

# Programs that print names and metadata, never file contents. Their
# operands may hold globs, and device files and '..' are harmless to them.
_META = frozenset(k for k, r in _RULES.items() if isinstance(r, Rule) and r.meta)
# Programs that open no file named in their arguments: the names-only ones,
# and the ones whose arguments are inert text.
_NO_READ = _META | frozenset(k for k, r in _RULES.items()
                             if isinstance(r, Rule) and r.free)


def _check_segment(words: list) -> None:
    if not words:
        raise _Refused("empty pipeline segment")
    prog, args = words[0], words[1:]
    if getattr(prog, "glob", False):
        raise _glob_refusal(prog)
    if "=" in prog:
        raise _Refused(f"env-assignment prefix {prog!r} is not allowed")
    if "/" in prog:
        d, _, base = prog.rpartition("/")
        if d not in _BIN_DIRS:
            raise _Refused(f"{prog!r}: name the program, or give its path in "
                           f"{', '.join(sorted(_BIN_DIRS))}")
    else:
        base = str(prog)
    if base == "sudo":
        raise _Refused("sudo is not allowed in read-only mode")
    if base in _REMOVED:
        raise _Refused(f"{base!r} is not on the read-only allowlist: "
                       f"{_REMOVED[base]}")
    rule = _RULES.get(base)
    if rule is None:
        raise _Refused(f"{base!r} is not on the read-only allowlist "
                       "(mutations are not enabled yet)")
    if base not in _META:
        for a in args:
            if getattr(a, "glob", False):
                raise _glob_refusal(a)
    _check_paths(base, args)
    if base not in _NO_READ:
        _check_special_paths(base, args)
    if isinstance(rule, Rule):
        _apply(base, args, rule)
    else:
        rule(args)


def classify_readonly(command: str) -> tuple[bool, str]:
    """(ok, reason). ok=True means the line is printable ASCII, uses no
    shell feature beyond a plain pipe and the two stderr words, every
    program in every pipe segment is read-only under its closed option
    table, and no argument breaks a path rule. Refusal is the default.

    The string judged is exactly the string given: nothing is stripped, so
    what ops_exec sends to the host is what was classified."""
    if command is None:
        return False, "empty command"
    if not isinstance(command, str):
        return False, "command must be a string"
    if not command.strip(" "):
        return False, "empty command"
    if len(command) > MAX_COMMAND_LEN:
        return False, "command too long"
    try:
        for words in _lex(command):
            _check_segment(words)
    except _Refused as e:
        return False, str(e)
    return True, "read-only"


# ── remote wrapper ────────────────────────────────────────────────────────────

# The local wait in ops_exec is 30 s. Killing the local ssh does not stop
# the command on the host (there is no terminal to hang up), so the host
# stops it itself: coreutils `timeout` sends TERM to the whole process
# group at REMOTE_TIMEOUT_S, and KILL REMOTE_KILL_AFTER_S later. Both fit
# inside the local wait. `timeout` exits 124 when it had to stop the
# command (137 when it took KILL).
REMOTE_TIMEOUT_S = 25
REMOTE_KILL_AFTER_S = 2
REMOTE_TIMEOUT_EXIT_CODES = (124, 137)


def remote_command(command: str, timeout_s: int = REMOTE_TIMEOUT_S) -> str:
    """The command line handed to ssh for a classified command.

    `bash -c` pins the shell that parses the line to the one _lex models,
    whatever the account's login shell is. The login shell itself sees only
    plain words and one single-quoted string. The command is passed whole
    and unchanged."""
    return (f"timeout -k {REMOTE_KILL_AFTER_S} {int(timeout_s)} "
            f"bash -c {shlex.quote(command)}")


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
