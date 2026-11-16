# M3 fix 1 report: the read-only exec classifier, after its end-to-end test

Fix agent, 2026-11-16 (fix round 1 of at most 3), branch `refactor`.
Inputs: `docs/sprint/M3-criteria.md`, `docs/sprint/M3-build.md`,
`docs/sprint/M3-e2e.md` (findings F1 to F6), and Drew's ruling on the deny
list of 2026-11-16. Commits: `120ad12` (classifier, wrapper, tests, doc
notes) and the commit that adds this report and the replay files.

No command was sent to the MagicMirror Pi or any managed host. Production's
`/ops/exec` and `/magicmirror/update` were not called. Real shell behaviour
was exercised in a network-less sandbox on this machine (details under
"How it was checked").

## Summary

The tester found six ways past `classify_readonly()`. Each is closed as a
class, not as a list of strings:

| Finding | Class | Fix |
| --- | --- | --- |
| F1 | The classifier and the shell read different strings | One character set (printable ASCII); nothing is stripped; the string classified is the string sent |
| F2 | The classifier guessed which options take a value | A closed option table for every program; unknown options refused |
| F3 | A program read paths the path rules did not look at | Path rules on every argument; the recursive-read floor on every operand of `git diff` |
| F4, F5 | Commands that never return | No following, repeating or watching option is in any table; device files refused; the host itself stops the command at 25 s |
| F6 | An option could name a place to write | Closed tables again; npm and pm2 forms spelled out word by word |

Results on the tester's own table (1,995 of its 2,000 rows parsed; three
long rows are printed trimmed in the report, one is blank):

- All 103 rows tagged F1 to F6 in the must-refuse groups were allowed
  before. 101 are refused now. The other two (`grep -r --color never BEGIN`
  and the `--colour` spelling) are not bypasses; see F2.
- 61 other rows went from allowed to refused: the four deny-list additions,
  raw devices, npm and pm2 options, and forms the tester marked harmless
  that fall outside the closed tables. Listed under "Still refused".
- 23 rows went from refused to allowed: the restored forms.
- Of the 232 rows in the tester's "should stay allowed" group, 231 are
  allowed. The one refused is `ls<TAB>-la` (tab is not printable ASCII).

## What changed, by finding

Line numbers are `tool_service/ops.py` at `120ad12` unless a file is named.

### F1. One character set; the string classified is the string run

Class: `classify_readonly()` classified `command.strip()` while `ops_exec`
sent the command unstripped, and Python's idea of whitespace is wider than
bash's. More generally, any character that the lexer and the shell treat
differently is a hole.

Change:

- `_lex()` (line 193) refuses every character outside 0x20 to 0x7E before it
  reads anything. Control characters keep the old reason ("contains a newline
  or control character", now with the byte and position); everything above
  0x7E gets "contains a character outside printable ASCII (U+00A0 at
  position 20) …" (line 205). Tab is refused too: the brief said printable
  ASCII only, and no audit-log command contains one.
- `classify_readonly()` (line 1431) no longer strips. Leading and trailing
  spaces are ordinary separators to the lexer. It also returns a refusal for
  a non-string instead of raising (tester's observation 7).
- `ops_exec` (`tool_service/main.py:950`) sends `req.command` whole.

I chose refusal over teaching the lexer bash's blank set, as the brief
preferred. The tokenizer was also checked against bash directly, because
"refuse what is odd" only helps if the two agree on what is left:
`test_lexer_agrees_with_bash_on_random_lines` builds random lines from
quotes, backslashes, pipes, every shell metacharacter, a tab and a no-break
space, and for every line the lexer accepts, runs it in bash with an empty
`PATH` and a `command_not_found_handle` that records the words each command
received. 20,000 accepted lines, 0 differences (output under "Tests").

Tests: `test_classifier_judges_the_exact_string`,
`test_ops_exec_sends_the_string_it_classified`, `test_classifier_never_raises`,
the 21 F1 rows of `E2E_BYPASSES`, the "F1 class" rows of `FIX1_REFUSED`.

### F2. Closed option tables

Class: the classifier knew a few options per program and let the rest
through. Any option it did not know that takes a value shifted the meaning
of the words after it. For `grep -r` that turned the pattern into a search
root and left grep searching the login home.

Change: `Rule` (line 403) is now a closed table. Each program lists the
short letters and long names it may be given, in four kinds: no value,
value (attached or next word), value attached only, optional value. `_parse()`
(line 475) reads the words the way `getopt_long` does and refuses:

- an option that is not in the table ("option '--zzz' is not on the allowed
  list for grep …"). `_ANY` is gone; every program has a table.
- an abbreviated long option. getopt accepts any unambiguous prefix; the
  table accepts the full name only. An abbreviation of a forbidden name
  still gets that name's specific reason (`--out` is `--output`).
- a value written as a separate word that starts with `-` (`take_value`,
  line 494), unless the option is one that needs such values (`grep -e`,
  `head -n -5`, `journalctl --since -6h`, `date -d`, `ps --sort`). If a
  table were ever wrong about an option, the "value" would otherwise be an
  option nobody checked.
- an option after the first operand, for `uniq` always and for `grep` when
  `-r` is given (`opts_first`, `opts_first_with`, line 582). GNU programs
  reorder their arguments; with `POSIXLY_CORRECT` set they do not, and then
  `uniq in -c` writes a file named `-c` and `grep -r x a -f /home/pi`
  searches `/home/pi`. With options first, both readings agree.

For `grep` (line 673) the operand split is now exact: with `-r`, the first
operand is the pattern unless `-e` or `-f` was given, there must be at least
one root, and every root passes the recursive-read floor. `--exclude-dir zzz
^` is read as grep reads it: `zzz` is the option's value, `^` the pattern, no
root, refused.

Two rows the tester tagged F2 are allowed and should be:
`grep -r --color never BEGIN` and `grep -r --colour never BEGIN`. `--color`
takes a value only as `--color=WHEN`, so grep reads `never` as the pattern
and `BEGIN` as the file, which is how the classifier now reads it. Run in
the sandbox against the fake home: exit 2, "No such file or directory", no
secret printed. They are in `FIX1_ALLOWED` with that comment.

The subcommand programs keep their shape and get closed tables after the
subcommand: `systemctl` (line 789), every `git` subcommand (`_GIT_RULES`,
line 999), `ps` with its three option syntaxes (line 708), `ip` (no option
after the object, line 917).

For `git`, a long option's value is accepted only attached (`--date=short`,
not `--date short`): git takes some of them either way and some only
attached, and the attached form reads the same in both. The refusal says
"write --date=VALUE".

Tests: the 15 F2 rows of `E2E_BYPASSES` (the other two are the `--color`
rows above); "F2 class" rows of `FIX1_REFUSED`;
`test_every_program_refuses_an_unknown_option` (every program in `_RULES`
and every git subcommand); `test_rule_tables_are_consistent` (no option
listed twice, or both allowed and forbidden).

### F3. Path rules on every path-like argument

Class: `git diff A B` is `git diff --no-index A B` when git is not in a
repository or one path is outside it. It then compares any two paths,
directories included, and prints every file. The recursion rule looked only
at grep.

Change, `_check_git()` (line 1086):

- Every operand of `git diff` must pass `_recursive_root_refusal()` (line
  357), the same floor as a `grep -r` root: at least three components deep
  once resolved, no `..`, not under `/proc`, `/sys` or `/dev`. It is resolved
  against every directory git could be in: the login home, the directory
  left by the `-C` options, and `--work-tree` (line 1159).
- `--work-tree` itself must pass the floor (line 1124), because git reads
  working files under it by the names in the index.
- `--no-index` stays refused by name.

The tester's suggested rule (refuse absolute, `~`, `.` and `..` operands)
would not have closed it. `git -C / diff mnt home` uses two relative names
and passes that rule; in the sandbox it printed the fake key. It is refused
now ("path operand 'mnt' is too broad"). So is `git -C / -C home diff x/y
pi`.

What is still allowed is what `grep -r` is allowed: `git diff a b` in the
login home compares `~/a` and `~/b`, and `git diff /usr/local/games
/etc/ssl/private` passes the depth floor exactly as `grep -r ''
/etc/ssl/private` does. That is the floor Drew ruled acceptable for grep.

The audit log's forms are unchanged: `git --git-dir=… --work-tree=… diff
package-lock.json`, `… diff -- /home/pi/MagicMirror/package-lock.json`,
`git -C … diff --stat`.

Beyond git, "every path-like argument" now also means a second set of path
rules, applied to every argument of every program that reads file contents
(`_special_path_refusal()`, line 320; see F5): no `..`, no device, no
re-rooting `/proc` link.

Tests: the 17 F3 rows of `E2E_BYPASSES`; "F3 class" rows of `FIX1_REFUSED`.

### F4 and F5. Commands that never return

Class: a read-only command that does not end holds a core, a connection or
the disk, and killing the local `ssh` does not stop it on the host.

Three changes.

1. **No following, repeating, watching or waiting option is in any table.**
   With closed tables this is a property of the tables rather than a list of
   bad flags, and `test_dangerous_options_are_in_no_allowed_table` pins it.
   Named refusals with a specific reason: `ss -E`/`--events`,
   `free -c`/`--count` (and `-s`), `tail -f`/`-F`/`--follow`/`--retry`,
   `journalctl -f`, `systemctl --wait`, `grep -D`/`--devices`, `file -s`.
   `pm2` (line 814) is spelled out: `list`, `ls` and `status` take no words
   at all (so no `--watch`, no `--no-daemon`); `logs` requires `--nostream`
   and accepts only `--lines N` with N a number of one to four digits,
   `--err`, `--out`, `--raw` and one process name. `--lines --nostream` is
   refused because the value is not a number, which is the commander
   behaviour F4 used.

2. **Device files and their relatives are refused by name** for every
   program that reads contents (line 320): anything under `/dev` except
   `/dev/null`; `/proc` paths with a `root`, `cwd`, `fd`, `kmsg`, `kcore`,
   `mem`, `pagemap` or `map_files` component; `/sys/kernel/debug` and
   `/sys/kernel/tracing`. Any path with a `..` component is refused too,
   because with a symlink on the way the rule above cannot tell where it
   leads: `/var/run/../dev/zero` is `/dev/zero` on Debian (`/var/run` is a
   link to `/run`), and it read zeros in the sandbox. Every piece of an
   argument is checked (`--file=/dev/zero`, `-f/dev/zero`, `HEAD:/dev/x`).
   Programs that print names and metadata only (`ls`, `du`, `stat`, `df`,
   `lsblk`, `readlink`, `realpath`, `dirname`, `basename`) and the inert
   ones (`echo`, `printf`, `true`) are exempt, so `ls -la /dev` and
   `lsblk /dev/mmcblk0` work.

3. **The host stops the command itself.** This is a name rule, and a device
   node or a FIFO can sit anywhere, so the rule cannot be complete. The
   guarantee is the wrapper. `ops.remote_command()` (line 1468) turns the
   classified command into

   ```
   timeout -k 2 25 bash -c '<command, single-quoted>'
   ```

   and `ops_exec` sends that (`tool_service/main.py:952`, with `ssh -n` so
   the remote stdin is empty whatever ours is). GNU `timeout` puts itself
   and the command in one process group, sends TERM to the group at 25 s
   and KILL 2 s later. Both fit inside the unchanged 30 s local wait.
   Exit 124 (stopped) and 137 (killed) become a 504 with the cause
   (`main.py:962`); the audit line records the exit code. The audit call,
   the 30 s wait and the response shape for every other case are unchanged.

   `bash -c` has a second effect: the line is parsed by bash, the shell the
   lexer models and is tested against, whatever the account's login shell
   is. That closes the build report's residual risk 5.

   The tester's claim was confirmed first, with a real `sshd` inside the
   sandbox: without the wrapper, a silent command is still running after
   the `ssh` client is killed. With the wrapper it is gone when the limit
   passes, connected or not. Output under "How it was checked".

Tests: the F4 and F5 rows of `E2E_BYPASSES` (4 and 37);
"F4 and F5 class" rows of `FIX1_REFUSED`;
`test_remote_timeout_kills_the_whole_pipeline`,
`test_remote_timeout_kills_a_process_that_ignores_term`,
`test_remote_command_carries_the_exact_string`,
`test_remote_limits_fit_inside_the_local_wait`,
`test_ops_exec_remote_timeout_is_a_504_and_is_audited`,
`test_ops_exec_local_timeout_is_still_a_504`.

### F6. No option can name a write location

Class: `npm ls --logs-dir=<path>` and `--cache <path>` make npm create and
write a directory of the caller's choosing. More generally, an unchecked
option tail can name a file to write, a program to run or a host to contact.

Change: closed tables cover the getopt programs. npm and pm2 use their own
option parsers (nopt, commander), so their allowed forms are matched word by
word. `npm` (line 873): after `ls`, `list`, `view`, `outdated`, `root`,
`prefix` or `why`, only `--depth N`, `--json`, `--long`/`-l`,
`--parseable`/`-p`, `--all`/`-a`, `--global`/`-g`. Operands must look like a
registry package name (`name`, `@scope/name`, optional `@version`); a path,
a URL, a git address and the `user/repo` GitHub shorthand are refused, since
`npm view` on those fetches or clones. So `--registry`, `--proxy`,
`--userconfig`, `--prefix`, `--script-shell` and the rest are gone with
`--cache` and `--logs-dir`.

Not confirmed by running: there is no npm (and no pm2) on this machine and
I did not install one. The fix does not depend on whether those two options
write; nothing outside the six listed is accepted.

Tests: the 7 F6 rows of `E2E_BYPASSES`; "F6 class" rows of `FIX1_REFUSED`.

## Siblings looked for, and what was found

For each class I went through every program in `_RULES`.

**Options that write, or name where to write.** Found and refused:
`sort -T`/`--temporary-directory` (names the directory for temporary
files) and `--random-source`; `zgrep -f`/`--file` (zgrep is a shell script
and copies a non-regular pattern file to a temp file); `df --sync` (calls
`sync()`); `git blame --contents`. Already refused and kept: `sort -o`,
`--compress-program`, `--files0-from`; `uniq`'s second operand; `file -C`;
`git --output`; the `journalctl` maintenance options; `ss -D`, `-K`;
`sensors -s`; `date -s`; `hostname` operands. `git branch -l <name>` now
needs `--list`: before git 2.20 `-l` meant `--create-reflog` and the
operand created a branch (tester's observation 6; the Pi's git version is
unknown).

**Options that follow, repeat, watch or wait.** Found and refused beyond the
named ones: `systemctl --wait` (blocks while the system is starting),
`tail -s`/`--pid` (meaningful only with `-f`), `pm2 list --no-daemon`,
`pm2 --no-daemon`, `free --count`, `grep -D read` (would read FIFOs under a
recursive root).

**Options that read a list of files the classifier cannot see.** `wc
--files0-from`, `du --files0-from`, `file -f`, `date -f`, `ss -F`,
`sensors -c`, `git blame -S`: refused. (`grep -f` and `--exclude-from` stay;
the file they name is under the path rules and holds patterns, not paths.)

**Options that run a program or reach another host.** `git --textconv`,
`cat-file --textconv`/`--filters`, `--ext-diff`, `-c`; `systemctl -H`/`-M`;
`journalctl -M`; `ss -N`; npm's `--script-shell`, `--registry`, `--proxy`
and the others: in no table.

**Paths that never end or lead elsewhere.** `/proc/<pid>/root`, `cwd`,
`fd/*`; `/dev/stdin`, `/dev/fd/*`; `/proc/kmsg`, `/proc/kcore`;
`/sys/kernel/tracing/trace_pipe` (the tester listed the `debug` one); `..`
after a symlink (`/var/run/..`, `/proc/self/cwd/../..`); repeated slashes
and `/./`; values attached to options.

**Reads of trees without naming files.** `git diff` with relative names
under `-C`; `git --work-tree=<broad dir>`; recursive grep with options
after operands. `ls -R` and `du` walk trees but print names only.

**Classifier and shell disagreeing.** Tab; DEL; a non-string command;
`POSIXLY_CORRECT`; the login shell not being bash.

## The deny list as it now stands

`DENIED_PATH_PATTERNS`, one tuple, line 101. Every argument of every
program is lowercased, split on `/`, `=`, `:` and `,`, and each piece is
matched:

| Pattern | Matches | Since |
| --- | --- | --- |
| `.ssh` | the directory and everything under it | M3 |
| `id_*` | `id_rsa`, `id_ed25519`, … | M3 |
| `.env`, `.env.*`, `*.env` | dotenv files | M3 |
| `shadow*`, `gshadow*` | `/etc/shadow` and backups | M3 |
| `*.key`, `*_key`, `*.pem`, `*.p12`, `*.pfx` | key files | M3 |
| `*token*`, `.netrc`, `.git-credentials` | token files | M3 |
| `environ` | `/proc/<pid>/environ` | M3 |
| `.bash_history` | shell history | fix 1 (Drew) |
| `.npmrc` | npm auth tokens | fix 1 (Drew) |
| `wpa_supplicant*.conf` | `wpa_supplicant.conf`, `wpa_supplicant-wlan0.conf` | fix 1 (Drew) |
| `system-connections`, `*.nmconnection` | NetworkManager connection files and their directory | fix 1 (Drew) |
| `dump.pm2*` | `~/.pm2/dump.pm2` and `.bak` | fix 1 (Drew) |

Per Drew's ruling the list is not strict:

- `MagicMirror/config/config.js` is readable (pinned in `FIX1_ALLOWED`).
- `grep -r` is allowed inside the floor, so `grep -r '' /home/pi/MagicMirror`
  prints `MagicMirror/.env`, and `grep -r '' /home/pi/.pm2` prints
  `dump.pm2`. The list refuses a denied file when it is named, not when a
  deep recursive read reaches it. I did not change that; it is the ruling as
  I read it. If the four additions should hold against recursion too, say so
  and the floor needs a different shape.
- The pattern for Wi-Fi is the `.conf` file, not the word `wpa_supplicant`,
  so `systemctl status wpa_supplicant` works.
- `pm2 describe` is restored and prints one process's environment
  differences. `pm2 jlist`, `prettylist` and `env` (every variable of every
  process, the same data as `dump.pm2`) stay refused, as do `env`,
  `printenv` and `docker`.

## Restored

| Form | Why it is safe |
| --- | --- |
| `pm2 describe <name>`, and the aliases `show`, `info` | Exactly one name or id matching `[A-Za-z0-9][A-Za-z0-9._@-]*`, no options. It returns and writes nothing. |
| Globs `*` and `?` in path operands of `ls`, `du`, `stat`, `df`, `lsblk`, `readlink`, `realpath`, `dirname`, `basename` | See below. |
| `2>&1` and `2>/dev/null` | See below. |
| `systemctl --failed`, `--type=…`, `--state=…` before the subcommand | Value-less or attached, so nothing can pose as the subcommand. |
| `ps axo pid,etime,cmd` | `o` takes the next word as its format; only a BSD `e` flag prints environments. |
| `uniq -f 1 x` | The table knows `-f` takes a value. |

**Globs: restored in part.** A glob is expanded on the host into names the
classifier never sees, so it is allowed only where that cannot matter: in a
path operand of a program that prints names and sizes and never contents.
There a match can reveal a file's name or size, not what is in it. Three
conditions (`_check_glob_operand()`, line 385): the word starts with `/`,
`./` or `~/`, so no match can be read as an option; no component that holds
a glob starts with `.`, so a glob does not walk into hidden directories
(bash's `*` does not match a leading dot); and the literal parts still go
through the deny list (`ls ~/.ssh/*` is refused). `[`…`]` and braces stay
refused. Globs stay refused for every program that reads contents (`cat
/var/log/*.log`), because there the match could be a denied file, and for
`uniq`, where two matches would be an input and an output. `du -sh *` is
refused with "must start with /, ./ or ~/"; `du -sh ./*` works.

**`2>&1`: allowed, with `2>/dev/null`.** The redirection grammar the
classifier accepts is two exact words (`_STDERR_WORDS`, line 177), each only
when it stands alone: a word boundary before it, and a space, a pipe or the
end of the line after it. Neither names a file. Every other `<` or `>` is
refused as before, so `2>&1x`, `2>& 1`, `2>&2`, `2>&1-`, `1>&2`, `22>&1`,
`x2>&1`, `'x'2>&1`, `\2>&1`, `2>/dev/nullx`, `2>/dev/null/x`, `2> /dev/null`,
`2>/tmp/x`, `2>>/dev/null`, `>&2` and `&>/dev/null` are all refused (rows in
`FIX1_REFUSED`). The claim that the grammar is closed is tested against
bash: in the random-line test, 5,215 of the 20,000 compared lines used one
of the two words, bash passed each command exactly the lexer's words, and
no line created a file in the working directory. These two suffixes were
two of the eight refused forms in the audit log, and the tester's "big one".

## Still refused that a user might want

- Tab characters, and anything outside ASCII (a grep pattern with an
  accented letter).
- Any path with `..`, for programs that read contents: `cat ../x`,
  `cat /usr/bin/../../etc/os-release` (allowed by the M3 build; moved to
  the refused table).
- Device paths as arguments of programs that are not names-only: `grep
  /dev/root /proc/mounts` (write `grep dev/root`), `journalctl /dev/sda`,
  `systemctl status /dev/mmcblk0p2`, `pgrep -f /dev/`. Also `cat /dev/stdin`.
- Abbreviated long options (`grep --rec`), and for git, a long option's
  value as a separate word (`git log --since "2 weeks ago"`; write
  `--since=…`).
- Options after the pattern in a recursive grep (`grep x dir -r`).
- A separate value that starts with `-` where the table does not expect one
  (`stat -c -x`).
- `git branch -l <pattern>` (write `--list`); `git log --show-signature`,
  `--help`, `--stdin`; `git log -O`; `git blame --contents`.
- Globs for content readers (`cat /var/log/*.log`, `grep x /var/log/*`),
  globs that do not start with `/`, `./` or `~/`, and `[a-z]` classes.
- Every other redirection: `2>/tmp/x`, `>`, `<`, `&>`.
- `pm2 logs` options other than the five, more than 9999 lines, `pm2 list`
  with any option; `pm2 jlist`, `prettylist`, `env`.
- npm options other than the six; `npm view` of a path, URL or git address.
- `free -c 1`, `vmstat 1 3`, `iostat -x 1 2` (bounded samplers are still
  repeat options), `df --sync`, `file -s`, `file -f`, `date -f`, `sort -T`,
  `zgrep -r`/`-f` and all zgrep long options, `tail -s`/`--pid`.
- Unchanged from the build: `find`, `sed`, `awk`, `top`, `dmesg`, `curl`,
  `cd … &&`, `$HOME`, the deny list on non-paths (`grep token`), recursive
  grep above the floor (`grep -rn x /var/log`), `ip addr sh`, `ip route get`,
  `env`, `printenv`, `docker`.

## How it was checked

**Sandbox.** `bwrap --unshare-all` on this machine: no network (its own
loopback only), `/home` replaced by a tmpfs with a fake `/home/pi` from the
scratch directory (fake `.ssh/id_rsa`, `.ssh/authorized_keys`, `.env`,
`MagicMirror/.env`, `MagicMirror/config/config.js`, MagicMirror a real git
repository), `/mnt` empty. Each command was run as the login shell would
run it, through `ops.remote_command()`.

One thing the next tester should know: this machine's default `timeout`,
`sort`, `uniq`, `cat` and the rest are **uutils coreutils 0.2.2**, not GNU.
uutils `timeout -k 2 N …` exits 125 where GNU exits 124, and with a command
that ignores TERM it took 10 s to return where GNU takes 3. The Pi runs GNU
coreutils. GNU 9.5 is installed here as `gnutimeout` (and `gnu*` for the
rest); the sandbox puts those first in `PATH`, and the two wrapper tests in
the suite do the same (they skip if no GNU `timeout` exists).

Reproduction of the findings (before = classifier at `185050d`; "ran" is
the real effect in the sandbox, under a 3 s wrapper):

```
### F1: 'uniq /etc/hostname \xa0'
  before: allowed   after: refused: contains a character outside printable ASCII (U+00A0 at position 20): the remote shell does not split words where it looks like it should; use plain ASCII
  then `ls -b /home/pi`: 'MagicMirror\n\\302\\240\n'
### F1: 'git -C /home/pi/MagicMirror branch \xa0'
  before: allowed   after: refused: contains a character outside printable ASCII (U+00A0 at position 36): …
  then `git -C /home/pi/MagicMirror branch | cat -A`: '* main$\n  M-BM- $\n'
### F2: 'grep -r --exclude-dir zzz ^'
  before: allowed   after: refused: grep: recursive search with no directory searches the home directory; name a directory
  ran 0.3s under timeout 3; output lines 51; secret printed: True
### F2: 'grep -rh --binary-files text -A 60 OPENSSH'
  before: allowed   after: refused: grep: recursive search with no directory searches the home directory; name a directory
  ran 0.3s under timeout 3; output lines 5; secret printed: True
### F2: 'grep -r --color never BEGIN'
  before: allowed   after: ALLOWED
  ran 0.3s under timeout 3; output lines 3; secret printed: False
   | rc=2
### F3: 'git diff /mnt /home/pi'
  before: allowed   after: refused: git diff: path operand '/mnt' is too broad (it can reach secrets without naming them); search a deeper directory or name the files (outside a repository git diff compares any two paths, directories included)
  ran 0.3s under timeout 3; output lines 124; secret printed: True
### F3: 'git -C /home/pi/MagicMirror diff /mnt /home/pi'
  before: allowed   after: refused: git diff: path operand '/mnt' is too broad …
  ran 0.3s under timeout 3; output lines 124; secret printed: True
### F3-sibling: 'git -C / diff mnt home'
  before: allowed   after: refused: git diff: path operand 'mnt' is too broad …
  ran 0.3s under timeout 3; output lines 124; secret printed: True
### F5: 'ss -E'
  before: allowed   after: refused: ss: -E prints sockets as they close, forever, and never returns
  ran 3.3s under timeout 3; output lines 2; secret printed: False
   | rc=124
   | survivors:
### F5: 'free -c 999999'
  before: allowed   after: refused: free: -c repeats the report; one report only
  ran 3.3s under timeout 3; output lines 11; secret printed: False
   | rc=124
   | survivors:
### F5: 'wc -l /dev/zero'
  before: allowed   after: refused: wc: argument '/dev/zero' is a device file: reading one can block forever or never end (only /dev/null is allowed)
  ran 3.3s under timeout 3; output lines 2; secret printed: False
   | rc=124
   | survivors:
### F5-sibling: 'cat /var/run/../dev/zero'
  before: allowed   after: refused: cat: argument '/var/run/../dev/zero' contains '..': where such a path leads cannot be checked (a symlink on the way changes it); name the path directly
### F5-sibling: 'cat /proc/self/root/dev/zero'
  before: allowed   after: refused: cat: argument '/proc/self/root/dev/zero' is a /proc entry ('root') that blocks, never ends, or is a link to another part of the file system
### F5-sibling: 'cat /proc/self/cwd/../../dev/zero'
  before: allowed   after: refused: cat: argument '/proc/self/cwd/../../dev/zero' contains '..': …
```

(Trimmed: repeated reason text, and the harness's own `tr` in the survivor
list. The three `cat` siblings and `cat /dev/zero` read zeros until the
harness's 4,000-byte cap closed the pipe.) F4 and F6 were not run: no pm2
and no npm here.

**The timeout, through a real sshd.** Inside the same kind of sandbox: a
throwaway `sshd` on the sandbox's own 127.0.0.1:2222 with a key made for
the run, the real `ssh` client, a FIFO as the file that never returns (a
read the name rule cannot see), and for C the real `/ops/exec` route through
`TestClient` with a registry entry that points at the sandbox sshd. Nothing
left the namespace.

```
smoke: … remote-shell-ok timeout (GNU coreutils) 9.5
--- A. no wrapper (the code before this fix): silent command, local ssh killed at 2 s
A: remote processes 2 s after the client died: [28 cat /tmp/sshd/fifo;]
--- B. with the wrapper (remote limit 3 s): local ssh killed at 1 s, before the limit
B: sent: timeout -k 2 3 bash -c 'cat /tmp/sshd/fifo | cat'
B: remote processes right after the client died (1 s): [45 cat /tmp/sshd/fifo;]
B: remote processes at 4.5 s (limit 3 s passed): []
--- C. the real route: POST /ops/exec through ops_exec, real ssh, real limits (25 s remote, 30 s local)
C: allowed command -> 200 exit=0 output='Failed to add the host to the list of known hosts (/data/mm_known_hosts).\r\nCo (0.1s)
C: silent never-returning read -> 504 {'detail': 'command ran past 25s on sbx and was stopped there (exit 124)'} (25.1s)
C: remote processes after the 504: []
C: refused command -> 422 refused: cat: argument '/dev/zero' is a device file: reading one can block forever or neve (0.0s)
C: audit log:
    {"ts": 1794845745.335, "host": "sbx", "command": "echo 'a  b' 2>&1 | wc -c", "allowed": true, "exit_code": 0, "out_len": 149, "note": ""}
    {"ts": 1794845770.468, "host": "sbx", "command": "cat /tmp/sshd/fifo | cat", "allowed": true, "exit_code": 124, "out_len": 147, "note": ""}
    {"ts": 1794845770.975, "host": "sbx", "command": "cat /dev/zero", "allowed": false, "exit_code": null, "out_len": 0, "note": "cat: argument '/dev/zero' is a device file: reading one can block forever or never end (only /dev/null i
sshd stopped
```

A confirms the tester: the remote process outlives the killed client. B
shows the wrapper does not depend on the connection. C is the shipped code
path end to end. (The known-hosts warning in C is the route's fixed
`/data/mm_known_hosts` path, which does not exist in the sandbox.)

**New tests fail on the old code.** With `ops.py` and `main.py` put back to
`185050d` and the new test file kept: `337 failed, 679 passed`. With the fix:
all pass.

## Tests

`./scripts/run_tests.sh`, baseline taken first on `185050d`:

```
1520 passed, 2 skipped, 18 xfailed, 3 warnings in 11.76s
```

Final run on `120ad12`:

```
=========== 2098 passed, 2 skipped, 18 xfailed, 3 warnings in 15.79s ===========
```

`tests/.venv/bin/python -m pytest tests/test_ops_exec.py -q`:

```
1022 passed in 4.62s
```

The ops file went from 444 tests to 1022 (2098 − 1520 = 578 added; no other
test file changed). The three warnings are the existing Pydantic
deprecations.

Random lines against bash, at full size
(`OPS_LEX_FUZZ=20000 … -k test_lexer_agrees_with_bash_on_random_lines -s`;
the suite runs 300 with a fixed seed):

```
lexer vs bash: 20000 lines compared, 5215 with a stderr word, 0 differences, 0 files created
1 passed, 1021 deselected in 54.81s
```

What was added to `tests/test_ops_exec.py`:

- `E2E_BYPASSES` (110 rows): every row the tester's table tags F1 to F6 in
  a must-refuse group, verbatim, plus the forms that appear only in the
  report's text; each asserts its reason. Count pinned.
- `FIX1_REFUSED` (166 rows): siblings by class, the glob rules, the
  redirection near misses, the deny-list additions.
- `FIX1_ALLOWED` (48 rows) and `E2E_KEEP_ALLOWED` (174 rows: the tester's
  "should stay allowed" group, minus rows already in a table).
- Table checks: every program refuses an unknown option; tables are
  consistent; dangerous options are in no table.
- The lexer against bash; the wrapper carries the exact string; the timeout
  kills a pipeline and a process that ignores TERM; the route sends what it
  classified and maps exit 124 and 137 to 504.

Existing rows changed, four: `cat /usr/bin/../../etc/os-release` moved from
allowed to refused (`..`); `grep --rec x /home` keeps its refusal with a new
reason (no abbreviations); `ls 2>/dev/null` and `ls 2>&1` moved from refused
to allowed (replaced in the refused table by `ls 2>/tmp/err`).
`test_ops_exec_runs_readonly` now expects the wrapped command and `ssh -n`.

## Dev path

`docker compose --profile dev up -d --build tool_service-dev
orchestrator-dev` (dry run first). Run twice: once after the main change,
once after a last small one (options before operands for recursive grep).
Each time only `kronk-tool_service-dev-1` was recreated; `orchestrator-dev`'s
image was unchanged and its container kept running. Thirteen other
containers, HA and Music Assistant among them, have the same ids and
creation times as before the first rebuild.

Deployed code is the committed code:

```
repo:      d106e4640f26fe0ce0e2e462cf2846d7 ops.py  ff4fa94979903f76b67cbbdd62fe49d0 main.py
d106e4640f26fe0ce0e2e462cf2846d7  /app/ops.py
ff4fa94979903f76b67cbbdd62fe49d0  /app/main.py
```

The classifier called inside the dev container (first rebuild):

```
ALLOWED 'uptime'
refused 'uniq /etc/hostname \xa0'  -> contains a character outside printable ASCII (U+00A0 at position 20): the remote shell does not spli
refused 'grep -r --exclude-dir zzz ^'  -> grep: recursive search with no directory searches the home directory; name a directory
refused 'git diff /mnt /home/pi'  -> git diff: path operand '/mnt' is too broad (it can reach secrets without naming them); search a deep
refused 'pm2 logs --lines --nostream'  -> pm2 logs: --lines takes a number up to 9999 (without one, pm2 swallows the next option as the count)
refused 'pm2 list --watch'  -> pm2 list: no options or operands are allowed (--watch redraws forever and never returns)
refused 'ss -E'  -> ss: -E prints sockets as they close, forever, and never returns
refused 'free -c 999999'  -> free: -c repeats the report; one report only
refused 'sort /dev/urandom'  -> sort: argument '/dev/urandom' is a device file: reading one can block forever or never end (only /de
refused 'npm ls --logs-dir=/home/pi/x'  -> npm ls: option '--logs-dir=/home/pi/x' is not on the allowed list (--depth N, --json, --long, --pars
ALLOWED 'cat /home/pi/MagicMirror/config/config.js'
ALLOWED 'pm2 describe mm'
ALLOWED 'ls /var/log/*.log'
ALLOWED 'systemctl status x 2>&1 | head -20'
refused 'cat ~/.bash_history'  -> cat: argument '~/.bash_history' matches the secrets deny list ('.bash_history'); secrets must not re
wrapper: timeout -k 2 25 bash -c 'uptime -p'
```

After the second rebuild: `grep -r x a -f /home/pi` →
`(False, "grep: put options before the operands (got '-f' after 'x')")`.

The dev route, as the brief expected (no `ops/hosts.json`, none created):

```
{"host":"magicmirror","command":"uptime"}{"detail":"unknown host 'magicmirror' — known hosts: (none configured)"} -> HTTP 404
{"host":"magicmirror","command":"cat /dev/zero"}{"detail":"unknown host 'magicmirror' — known hosts: (none configured)"} -> HTTP 404
{"status":"ok","music_dry_run":true} -> HTTP 200
```

The dev container has no `MM_SSH_*`, no `/keys`, no `/data/mm_known_hosts`,
and its processes are `uvicorn`, `sh`, `cat`.

## Voice replay

`python3 scripts/voice_replay.py --label m3-fix1`, pipeline `kronk-dev`,
dry run on, corpus `5d5093a959f3ff42` (122 rows; the baseline had 114, the
M4b rows came later). Run after each rebuild.

Second run, on the final build
(`docs/bench/voice_replay_2026-11-16_160828_m3-fix1.json`):

```
voice_replay: {'pass': 98, 'fail': 1, 'xfail': 20, 'skip': 3}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
```

First run (`…_160044_m3-fix1.json`):

```
voice_replay: {'pass': 98, 'xfail': 19, 'fail': 1, 'skip': 3, 'xpass': 1}; executed 53, checked read-only 66; players unchanged (17 checked)
```

Against `voice_replay_2026-11-16_012824_m10-baseline.json`, by status and
method over the 114 shared rows:

| Run | Rows that differ |
| --- | --- |
| first | 1: `eval-shuffle-it`, xfail → xpass (a named flapper) |
| second | 2: `inc-0930-search-deep-house-ytm`, fail → pass (a named flapper); `plan-orbital-station-youtube`, pass → **fail** |

`plan-orbital-station-youtube` is not on the brief's list of flappers, so I
looked. "Start an orbital radio station on YouTube Music.": routed
`home/music` as expected, then the home agent made no tool call and said "I
can't directly stream from YouTube Music, but I can search for and play an
Orbital radio station for you." (trace `25b6341bab8a7bb39771aab70052d57c`).
The same row passed in the first run eight minutes earlier. `orchestrator-dev`
was not rebuilt between the two (or at all today since 09:23); the only
change between the runs is in `/ops/exec`'s classifier, which no corpus row
reaches. In the 29 replay files of 2026-11-16 in `docs/bench/` that hold
the row (before the reruns below), it failed three times: `m1-e2e`,
`m1b-post` and this one. Three single-row reruns on
the same build passed (`…m3-fix1-rerun1` to `rerun3`). It is the "on YouTube
Music" refusal the earlier items recorded, on a third row. The eight rows
that are not in the baseline have the same status as in the latest earlier
run (`…150919_m4b-e2e2.json`).

The corpus's seven `m4b-…update…` rows call `/magicmirror/update` on
**dev**, by design of the M4b replay. Dev has no key: both requests in dev's
log are `502`, and nothing was sent to the Pi. Production `tool_service` saw
0 `/ops/exec` and 0 `/magicmirror/update` requests in the last six hours.

## Not done, and why

- **Nothing was run on the Pi.** So these are still unchecked there: that
  `timeout` and `bash` are on the `pi` account's `PATH` (stock Raspberry Pi
  OS has both; if `timeout` were missing every command would fail loudly
  with exit 127), the git version, and whether `pm2` or `npm` are installed
  at all. The first real `remote_exec` after promotion is the check;
  `uptime` is enough.
- **F4 and F6 were not run** (no pm2, no npm here). The refusals do not
  depend on what those options do.
- **Recursion still reaches denied names** in a deep directory (`grep -r ''
  /home/pi/MagicMirror` prints `.env`). Left as Drew ruled; flagged above.
- **Programs that write their own state.** Not an option naming a write
  location, so outside F6 as stated, but not strictly read-only either:
  `git status`, `git diff` and `git describe --dirty` refresh `.git/index`;
  every `npm` run writes a debug log under `~/.npm/_logs`, and `npm view` /
  `outdated` write the npm cache; `pm2 list` starts a pm2 daemon if none is
  running and creates `~/.pm2`; `sort` on a large file spills to `/tmp`.
  These were true before M3. If read-only should exclude them, npm and pm2
  should leave the allowlist; that is Drew's call.
- **Output size.** `ops_exec` reads the whole output into memory before
  cutting it to 8,000 characters. A large read (`ls -R /`, a big log) is
  now bounded by 25 s of output rather than 30; not capped by size.
- **Host configuration that runs code** (repository hooks and
  `core.fsmonitor`, `.npmrc`, `BASH_ENV`, `GREP_OPTIONS` on an old grep):
  unchanged from the build report's residual risk 6. All need write access
  on the host first.
- **Symlinks, device nodes and FIFOs that are already on the host** in
  places the name rules do not cover. The timeout bounds what they can do.
- **No `docs/features/ops-exec.md`.** The rule set lives in `ops.py`'s
  docstring and tables, this report, and a status note in
  `docs/plans/MAGICMIRROR_PLAN.md`. Still a follow-up if Drew wants one.
- **The orchestrator's tool description** was not changed. It does not
  mention any rule that changed, and I was told not to touch routing.

## Departures from the brief

1. `2>/dev/null` is allowed along with `2>&1`. Same grammar argument, same
   test, and it is the form the audit log actually shows.
2. Globs are restored only for names-only programs. "Plain globs if safe":
   for programs that read contents I could not make them safe without the
   deny list seeing the expansion.
3. `pm2 show` and `pm2 info` are restored with `describe`; they are aliases.
4. The `..` rule is new and refuses one form the build allowed. It is what
   makes the device rule a rule about paths rather than about strings.
5. `ops_exec` changed (the wrapper, `ssh -n`, the 124/137 mapping), although
   the criteria say the classifier is the only gate change. The brief asked
   for the remote kill, and there is no way to get it from the classifier.
6. `ops/hosts.example.json`'s comment and `docs/plans/MAGICMIRROR_PLAN.md`
   now say a managed host needs `bash` and GNU `timeout`. `ROADMAP.md`'s
   mirror line notes the stderr words.

## Guard rails

- No command to the MagicMirror Pi or any managed host; no call to
  production's `/ops/exec` or `/magicmirror/update`. `data/ops_audit.log`
  was read only: 87 lines, md5 `a84455d0a6427ecc0a7e57a5ace8f635`, as the
  tester recorded.
- Production containers, HA and Music Assistant untouched; only
  `kronk-tool_service-dev-1` was recreated. No `down -v`, no
  `--remove-orphans`, no `pipeline_bench.sh`, no LAN scan. No sound: replay
  dry run on, 17 players unchanged in every run.
- `.env` and `.env.dev` not read or edited. No `ops/hosts.json` created
  (`ops/` holds only the example). `data/dev/ops_audit.log` unchanged at 31
  lines (dev answers 404 before the classifier, so nothing was appended).
- Scratch directory only, outside the repo: the fake home, the sandbox
  scripts, a directory of links to this machine's `gnu*` coreutils
  binaries, and the tester's table parsed to JSON. The sandbox sshd's keys
  lived in the sandbox's tmpfs and went with it. No background job is
  running; no sshd was left behind.
- Commits on `refactor` only. No push, no tag.
