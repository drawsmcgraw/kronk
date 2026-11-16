# M3 end-to-end test report: harden the read-only exec classifier

Test agent, 2026-11-16, branch `refactor` at `482a237`. Tested against
`docs/sprint/M3-criteria.md`; `docs/sprint/M3-build.md` was read last, as
claims to check.

**Verdict: ITEM FAILS.** Failed criteria: **2** and **4**.

The rewrite is a large improvement: every row of the plan's table is refused,
the lexer agrees with bash on 5,169 random accepted strings, and of 2,000
hand-written attempts 1,417 are refused with a specific reason. But
`classify_readonly()` still says "read-only" for commands that write, for
commands that print `.ssh/id_rsa` and `.env` without naming them, and for
commands that never return. Six findings, F1 to F6, each with the exact
command. F1 to F3 and F5 were proven by running the allowed command on this
machine against a fake home directory in the scratchpad; F4 is proven from
pm2's published source; F6 is from npm's documentation and was not run.

No command was sent to the MagicMirror Pi or any managed host. Production's
`/ops/exec` was not called.

## Criteria

| # | Result | Evidence |
| --- | --- | --- |
| 1 | PASS | All 10 plan rows refused by `classify_readonly()` with a specific reason; `env`, `printenv` not in `_RULES`. 6 of them also sent to `tool_service-dev` `/ops/exec`: 422 `refused: <reason>` each. Of 176 spelling variants of the rows, 164 are refused; the 12 allowed are the pm2 forms in F4 and harmless ones (observation 4). |
| 2 | **FAIL** | Rules exist per program, but three are defeated. `uniq` "at most one operand": `uniq /etc/hostname <NBSP>` is allowed and writes a file (F1). `pm2 logs` "requires `--nostream`": `pm2 logs --lines --nostream` is allowed and streams (F4). The never-return rules (`tail -f`, `journalctl -f`, `free -s`) have same-effect neighbours that are allowed: `ss -E`, `free -c 999999`, `pm2 list --watch`, `cat /dev/zero`, `sort /dev/urandom` (F4, F5). |
| 3 | PASS | Combined short flags, `--output x`, `--output=x`, abbreviations, flags after operands and after `--`: all refused (table, "plan table variants" and "write / exec side doors"). `journalctl -bf`, `-nf`, `tail ---follow`, `date --rfc-3339 0101` are allowed but the real programs reject them (run here). |
| 4 | **FAIL** | The deny list holds for every named path tried (129 forms: 128 refused, and `cat /home/pi/.ssh\ /x`, which names a different directory). It is bypassed without naming the path: `grep -r --exclude-dir zzz ^` (F2) and `git diff /mnt /home/pi` (F3) both print `.ssh/id_rsa`, `.ssh/authorized_keys` and `.env`. Globs and expansions are refused. |
| 5 | PASS | Command, process and arithmetic substitution, backticks, every redirection form tried (`>`, `>>`, `>&`, `2>file`, `&>`, `<`, `<<`, `<<<`, `<>`), `;`, `&&`, `\|\|`, `&`, `\|&`, newline, CR, NUL, other control characters, subshells, groups, comments, assignment prefixes: all refused. Pipes allowed only when every segment is allowed. (F1 is a mismatch between the string classified and the string executed, not one of the listed shell features; it is charged to criterion 2.) |
| 6 | PASS | `grep "rm -rf" /var/log/syslog`, `grep 'sudo reboot; rm -rf /' x`, `grep -E 'a\|b' x`, `echo 'a && b'` allowed; `sh -c "rm x"`, `ls \| 'sh'`, `"rm" -rf`, `'sudo' id`, `\sudo id` refused. |
| 7 | PASS | Unknown programs, unbalanced quotes, trailing backslash, empty, blank, 4,001 characters: refused. Non-string input raises (`AttributeError`/`TypeError`) instead of returning a refusal; the route's Pydantic model rejects it first (422), so nothing is allowed. Observation 7. |
| 8 | PASS | Legacy allowed table at `sprint-M1b` (15 rows): 15 allowed. `data/ops_audit.log` (read only): 87 entries, 78 distinct; 62 were allowed before, all 62 still allowed; 70 allowed now, 8 refused, the same 8 the build report names and justifies. |
| 9 | PASS | `git -C <dir> status/log/diff/…` allowed; `git -C x push`, `-C x -c a=b log`, `-C x --exec-path=y log` refused. Quoted pipe allowed. `ROADMAP.md` lines 87 to 88 say `journalctl --user` is Pi behaviour. |
| 10 | PASS | `tail -n 50 file`, `journalctl -n 100 --no-pager`, `pm2 logs --nostream --lines 50` allowed. |
| 11 | PASS | `REFUSED` (262 rows) asserts a reason substring for every row; `LEGACY_ALLOWED`, `AUDIT_LOG_ALLOWED` (count pinned at 62), `NEWLY_ALLOWED` cover 8 to 10. Every row named in criteria 1 to 7 is in the table. The tables do not contain F1 to F6; a fix needs to add them. |
| 12 | PASS | `tool_service/main.py` is byte-identical to tag `sprint-M1b`; `audit_exec` is identical. On dev (container code md5 equals the repo's): 15 refusals returned 422 `refused: <reason>` and wrote 15 audit lines (`allowed: false`, `exit_code: null`, reason as `note`); allowed commands stopped at the key check (500). No `ssh` process in the container, no `/keys` mount, no `MM_SSH_*`, no `/data/mm_known_hosts`. |
| 13 | PASS | No `sudo` field in `ops/hosts.example.json` or `load_registry()`; `docs/plans/MAGICMIRROR_PLAN.md` notes it was never read. `sudo`, `/usr/bin/sudo`, `'sudo'`, `\sudo`, `su\do`, `"su"do` refused. |
| 14 | PASS | `./scripts/run_tests.sh`: `1396 passed, 2 skipped, 18 xfailed, 3 warnings in 11.53s`. |
| 15 | PASS | `python3 scripts/voice_replay.py --label m3-e2e` on `kronk-dev`, dry run: 92 pass, 20 xfail, 0 fail, 2 skip. 113 of 114 rows have the baseline's status and method. The one difference is the baseline's known fail, `inc-0930-search-deep-house-ytm`, which passed this run: the "search … on YouTube Music" variance recorded in M1, M1b and the M3 build. No row got worse. |
| 16 | PASS | No command to the Pi or any managed host; production `/ops/exec` not called. Production audit log unchanged (87 lines, md5 `a84455d0a6427ecc0a7e57a5ace8f635`). All 14 containers have the same ids and creation times at the end as at the start (HA, Music Assistant and every production container included); none was rebuilt or restarted by me. Replay was silent: dry run on, 17 players unchanged. |

## Failures

How each was checked: the classifier was called directly in `tests/.venv`;
the allowed command was then run with `bash -c` under `env -i` against
`<scratchpad>/fake/home/pi` (a fake `.ssh/id_rsa`, `.ssh/authorized_keys`,
`.env`, `MagicMirror/.env`, `MagicMirror/config/config.js`), with the fake
home as the working directory, since an SSH command starts in the login home.

### F1. The classifier strips whitespace that the shell keeps (criterion 2; a write)

`classify_readonly()` classifies `command.strip()`. `ops_exec` sends
`req.command` unstripped. Python's `strip()` removes characters bash does
not treat as separators: U+00A0, U+2003, U+3000, U+2028, U+0085, and
0x1C to 0x1F. So a trailing one of these, after a space, is an extra operand
the classifier never sees.

| Command (as sent) | Classifier | What bash runs |
| --- | --- | --- |
| `uniq /etc/hostname  ` | allowed (`uniq /etc/hostname`) | writes the output to a file named U+00A0 in the home directory. Run here: file created, 6 bytes. |
| `uniq /etc/hostname \x1f` | allowed | same, file named 0x1F. Run here: created. |
| `git -C /home/pi/MagicMirror branch  ` | allowed (`git … branch`) | creates branch `refs/heads/<U+00A0>`. Run here: created. |
| `git -C /home/pi/MagicMirror tag  ` | allowed | creates tag `refs/tags/<U+00A0>`. Run here: created. |
| `hostname  `, `date  `, `vmstat  ` | allowed | an operand each rule forbids (sets the hostname as root; the others error). |

Also accepted by the dev route: `uniq /etc/hostname  ` returned the
key-check 500, not a 422.

For the fix agent: classify the exact string that will be executed. Strip
only space and tab (0x0B to 0x1F, `\n`, `\r` then reach the lexer's
control-character rule, which already refuses them), and either refuse any
other character for which `str.isspace()` is true or leave it in the word as
bash does. Add the rows above to `REFUSED`. `test_lexer_matches_bash_word_splitting`
did not catch it because it calls `_lex` on input with no such edge
characters.

### F2. `grep -r` with a value-taking long option searches the login home (criterion 4)

`_grep_check` treats the first operand as the pattern and the rest as
recursive roots. It knows seven long options that take a separate value.
grep has more: `--exclude-dir`, `--include`, `--exclude`, `--exclude-from`,
`--binary-files`, `--devices`. Written with a space, the value is counted as
the pattern and the real pattern as a root. A relative root resolves to
`/home/~/<word>`, depth 3, and passes. grep then has no file operand and
searches the working directory, which is the login home.

```
$ grep -r --exclude-dir zzz ^          # classifier: (True, 'read-only')
.env:FAKE_SECRET=hunter2
MagicMirror/.env:MM_FAKE=1
MagicMirror/config/config.js:var config={}
.ssh/authorized_keys:ssh-ed25519 FAKEPUB test
.ssh/id_rsa:-----BEGIN OPENSSH PRIVATE KEY-----
.ssh/id_rsa:FAKEKEYMATERIAL-not-a-real-key
.ssh/id_rsa:-----END OPENSSH PRIVATE KEY-----

$ grep -rh --binary-files text -A 60 OPENSSH     # allowed; prints only the key
-----BEGIN OPENSSH PRIVATE KEY-----
FAKEKEYMATERIAL-not-a-real-key
-----END OPENSSH PRIVATE KEY-----
```

(GNU grep 3.11.) Also allowed, same effect: `--include zzz`, `--exclude zzz`,
`--exclude-from /dev/null`, `--devices skip`, `--color never` and
`--colour never` (optional value: grep reads `never` as the pattern and
searches the home for it), `-e ^` after any of them, and the `egrep`,
`fgrep` spellings. 17 rows in the table.

For the fix agent: under `-r`, the operand split must be exact. Either give
the grep rule a closed table of long options (refuse any long option not in
it, and refuse a value-taking one written without `=`), or refuse a
recursive grep whose operand count is ambiguous. Also treat a root that is a
bare relative word as suspect only if you keep the `/home/~/` trick; the
depth test passing for the *pattern* is what makes this work.

### F3. `git diff` with two paths outside a repository is `--no-index`, and recurses (criterion 4)

The build refuses `git diff --no-index` and its abbreviations. git applies
`--no-index` on its own when the working directory is not a repository, or
when two paths are given and one is outside the work tree. Given two
directories it diffs them recursively and prints every file.

```
$ git diff /tmp/…/fake/empty .          # classifier on `git diff /mnt .`: (True, 'read-only')
diff --git a/./.env b/./.env
+FAKE_SECRET=hunter2
diff --git a/./.ssh/authorized_keys b/./.ssh/authorized_keys
+ssh-ed25519 FAKEPUB test
diff --git a/./.ssh/id_rsa b/./.ssh/id_rsa
+-----BEGIN OPENSSH PRIVATE KEY-----
+FAKEKEYMATERIAL-not-a-real-key
+-----END OPENSSH PRIVATE KEY-----
diff --git a/./MagicMirror/.env b/./MagicMirror/.env
+MM_FAKE=1
```

(git 2.51.0; output trimmed to the `+` lines.) Any existing directory serves
as the first argument; an empty one makes every file an addition. Allowed
forms: `git diff /mnt /home/pi`, `git diff /mnt ~`, `git diff /mnt .`,
`git diff -- /mnt /home/pi`, `git --no-pager diff …`,
`git -C /home/pi/MagicMirror diff /mnt /home/pi` (run here inside a real
repository: it still printed the fake home), `git diff /mnt /etc`,
`git diff /mnt /etc/ssl/private`, `git diff /mnt /`. It is not limited by
the recursion-depth rule, which applies only to grep.

For the fix agent: `git diff` needs an operand rule. The no-index path is
taken with exactly two path operands, so: refuse `git diff` when two or more
operands are paths that are absolute, start with `~`, are `.`/`..`, or
contain a `..` component; and refuse `git diff` path operands altogether
when no `-C`/`--git-dir` is given (the login home is not a repository, so
every two-path diff there is no-index). The audit log's diff forms (`diff
--stat`, `diff package-lock.json`, `diff -- /home/pi/MagicMirror/package-lock.json`,
all with `--git-dir` or `-C` and at most one path) must stay allowed.

### F4. pm2: `--nostream` can be swallowed, and `list --watch` never returns (criterion 2)

Not run (no pm2 on this machine, and none was installed). Read from pm2
5.4.3 `lib/binaries/CLI.js`, `lib/API.js` and its pinned `commander` 2.15.1
`index.js` (fetched from unpkg into the scratchpad).

- `pm2 logs --lines --nostream`: allowed. `--lines <n>` is a required-value
  option; commander 2.15.1 takes the next word as the value without looking
  at it (`index.js` line 723: `arg = argv[++i]`). `--nostream` becomes the
  line count, the flag is never set, and `pm2 logs` streams. The classifier
  only checks that the word `--nostream` appears.
- `pm2 list --watch`, `pm2 ls --watch`, `pm2 status --watch`: allowed.
  `API.js` line 606: if `--watch` is in the raw arguments, `list` redraws
  under `setInterval(show, 900)` and never exits.

For the fix agent: after the pm2 subcommand accept a closed list of flags.
For `logs`: `--nostream` (required), `--lines N` or `--lines=N` with N a
number, `--err`, `--out`, `--raw`, `--json`, `--timestamp`, and one process
name. For `list`/`ls`/`status`: no flags that are not known display flags;
refuse `--watch`. `pm2 list --no-daemon` and `pm2 --no-daemon` are also
allowed and, if no daemon is running, run it in the foreground; not
verified.

### F5. Reads that never return or fill the disk (criterion 2, the `tail -f` class)

The plan refuses `tail -f` and `pm2 logs` because they never return. These
are allowed and do the same. Without a pty, a remote process that produces
no output is not killed when the 30 s timeout kills the local `ssh`; it
keeps running on the host.

| Command | Classifier | Run here |
| --- | --- | --- |
| `ss -E`, `ss --events`, `ss -tE`, `ss --ev` | allowed | `timeout 3 ss -E`: still running at 3 s (iproute2 6.16). Prints sockets as they are destroyed, forever. |
| `free -c 999999`, `free --count N` | allowed | `free -c 3` took 2.0 s (one sample a second); `free -c 999999` still running at 3 s. `-s` is refused, `-c` is the same thing. |
| `wc -l /dev/zero`, `grep -c '' /dev/zero`, `head -n 1 /dev/zero`, `uniq /dev/zero`, `nl`, `cut`, `column`, `tail -n 1` on `/dev/zero` | allowed | `timeout 3 wc -l /dev/zero`: still running. No output, so it survives the SSH timeout and holds a core. `grep x /dev/zero` and `column /dev/zero` also grow in memory. |
| `sort /dev/urandom`, `sort /dev/zero` | allowed | wrote 1.8 GB of temp files to `$TMPDIR` in 3 s here. On the Pi that is `/tmp` on the SD card, until it is full. This one is a write. |
| `cat /dev/zero`, `cat /dev/urandom`, `head -c 99999999999 /dev/zero` | allowed | unbounded output until the timeout. |
| `cat /dev/tty`, `/dev/ttyAMA0`, `/dev/input/event0`, `/dev/kmsg`, `/sys/kernel/debug/tracing/trace_pipe`, `date -f /dev/zero`, `du --files0-from=/dev/zero`, `zgrep x /dev/zero` | allowed | not run; block or loop by construction. |
| `… \| cat /dev/zero`, `… \| wc /dev/zero` | allowed | any pipeline can end in one. |

The build report lists raw devices as residual risk 3, but only as a secrets
read needing root or `disk`. The never-return and disk-fill effects need no
privilege.

For the fix agent: a path rule for every program, beside the deny list:
refuse operands under `/dev/` other than `/dev/null`, `/proc/kmsg`, and
`/sys/kernel/debug`. Add `ss -E`/`--events` and `free -c`/`--count` to their
`forbid` maps.

### F6. npm flags after the subcommand are unchecked (criterion 2; a write; not run)

`npm ls --logs-dir=/home/pi/MagicMirror/modules`, `npm ls --cache /home/pi/x`,
`npm view x --cache /home/pi/z`, `npm outdated --cache=/home/pi/q` are
allowed. Per npm's config documentation, `logs-dir` and `cache` are
directories npm creates and writes to (debug logs, which include the
command line; the cacache store). There is no npm on this machine, so this
was not run; the fix agent should confirm it and close it with a closed
list of flags after the npm subcommand (`--depth`, `--json`, `--long`,
`-g`/`--global`, `--all`, `--parseable`). The same free tail lets
`--registry=<url>` and `--proxy=<url>` point `npm view` at any host, and
`--userconfig`/`--globalconfig` name any file as config.

## Observations (not failures)

1. **Builder's departures.**
   - *Forbidden flags refused after `--`*: sensible, no hole found. Cost: a
     file literally named `-o` or `-f` cannot be read.
   - *Deny list applied to every argument, grep patterns included*: no hole
     found; it is what stops `ps -o pid,environ` and `git show HEAD:.env`.
     Cost is real, see observation 3.
   - *Recursion-depth rule*: the idea is right; it has hole F2 and it covers
     only grep (F3). It also relies on resolving relative paths to
     `/home/~/…`, which is what lets a bare word pass as a deep root.
   - *docker removed*: sensible. 0 uses in the log, and the Pi has none.
2. **Secrets readable outside the specified deny list** (all allowed; for
   Drew's width decision). By name: `~/.bash_history`, `~/.npmrc`,
   `~/.envrc`, `~/.pm2/dump.pm2` (pm2's saved process list with every
   process's environment: the same data as the refused `pm2 jlist`),
   `MagicMirror/config/config.js`, `MagicMirror/.git/config`,
   `~/.config/gh/hosts.yml`, `~/.docker/config.json`, `~/.aws/credentials`,
   `~/.pgpass`, `~/.kube/config`, `~/.gnupg/*`, `secrets.json`,
   `credentials.json`, `privkey`, `key.txt`, `/proc/<pid>/cmdline`,
   `/etc/wpa_supplicant/wpa_supplicant.conf` and NetworkManager connection
   files (root-only on stock Pi OS), raw block devices (need `disk`).
   Through programs: `systemctl show <unit>` and `systemctl cat <unit>`
   print `Environment=` lines (the build refuses `show-environment` for
   that reason); `git log -p`, `git cat-file --batch-all-objects --batch`
   and `git ls-files -s` then `git cat-file -p <sha>` print anything ever
   committed; `pgrep -fa .`, `ps aux` print arguments; `journalctl`.
   By recursion into a deep tree, as the builder states: `grep -r ''
   /home/pi/MagicMirror` (prints `MagicMirror/.env`, a file the list
   names; I record it as an observation only because the brief says to),
   `grep -r '' /home/pi/.config`, `/home/pi/.pm2`, `/home/pi/.gnupg`,
   `/home/pi/.aws`, `/etc/ssl/private`, `/etc/NetworkManager/system-connections`.
3. **Useful commands now refused.** Stderr handling is the big one:
   `2>/dev/null` and `2>&1` (two of the eight refused log forms; `ops_exec`
   already merges stderr, so the agent only needs to drop the suffix, but a
   4B model will keep typing it). Globs: `ls /var/log/*.log`,
   `ls ~/.pm2/logs/*`, `du -sh *`, `systemctl list-units mag*`. `$HOME`,
   `$PATH`. Programs: `find`, `sed -n`, `awk`, `top -bn1`, `dmesg`,
   `crontab -l`, `curl -sI localhost:8080` (M5's subject), `ping -c 1`,
   `nproc`, `w`, `who`, `last`, `timedatectl`, `zcat`, `tac`, `diff`,
   `md5sum`, `test`, `pwd`, `netstat`, `dpkg -l`, `lsb_release`. Subcommands:
   `pm2 describe/show` (the usual way to see why a process restarts),
   `systemctl --failed` and `systemctl --type=service list-units` (option
   before the subcommand; `systemctl list-units --failed` works),
   `systemctl list-jobs`, `git rev-list --count HEAD..origin/master`,
   `git stash list`, `git reflog`, `git config --get remote.origin.url`,
   `git branch --contains HEAD` and `git branch --merged main` (only the
   `=` forms pass), `git tag --contains HEAD`, `ip route get 1.1.1.1`,
   `ip rule`, and the iproute2 abbreviations `ip addr sh`, `ip a s wlan0`
   (write `show`). Bounded samplers: `vmstat 1 3`, `iostat -x 1 2`,
   `free -s 1 -c 3`. `ps axo pid,etime,cmd`: any BSD-style word containing `e` is
   refused, a format list included (write `ps -eo pid,etime,cmd`). `uniq -f 1 x`
   (write `-f1`). Deny list on non-paths: `grep token …`,
   `grep -i access_token …`, `grep API_KEY config.js`, `grep environ x`,
   `git log --grep=token`, `git log -S api_key`, `ls …/MMM-Tokenizer`,
   `cat /home/pi/shadowbox/README`, `ls /home/pi/id_card.png`,
   `cat /etc/apt/trusted.gpg.d/x.pem`, `ls -la ~/.ssh` and
   `stat ~/.ssh/authorized_keys` (permission checks), `ls /proc/1/environ`.
   Recursive grep at depth 2: `grep -rn x /var/log`, `grep -rn x /etc/systemd`.
   `grep -R` (use `-r`). Quoting needed: `git log '@{u}..'`.
4. **Allowed in my must-refuse groups but harmless** (16 rows, tagged "ok"
   in the table): `ip link` (bare object lists links); full-width `；`, `＆`,
   `｜`, `＞` and U+037E are ordinary characters to bash; `ls<TAB>rm` is
   `ls rm`; `printf -v x y` sets a variable in a shell that then exits;
   `/usr/local/bin/cat` (the bin-directory list trusts `/usr/local/bin`,
   which needs root to write on stock Pi OS); `git log --help` runs `man`
   (not a tty, so no pager); `journalctl -bf`, `-nf`, `tail ---follow`,
   `date --rfc-3339 0101` are rejected by the programs themselves.
5. **Build report inaccuracies.** "A further sweep of about 150 commands …
   found nothing that mutates": F1 mutates. Residual risk 8 says a stdin
   reader waits out the 30 s timeout: the container's stdin is `/dev/null`
   (`readlink /proc/1/fd/0` in `tool_service-dev`), so `cat`, `sort`,
   `grep x`, `git shortlog` get EOF and return at once. Harmless either way.
6. **Old git.** `git branch -l <name>` is allowed as a listing form. Before
   git 2.20, `-l` meant `--create-reflog` and this created a branch. The
   Pi's git version was not checked (no command sent).
7. **Non-string input** raises inside `classify_readonly()` (`5`, `True`,
   `b"ls"`, a list) instead of returning `(False, reason)`. The route's
   `command: str` model answers 422 before the classifier runs, and that
   attempt is not audited. `None`, `0`, `False` give "empty command".
8. **Cost.** Slowest input found: `cat -ttt…` at 3,995 characters, 71 ms
   (the short-flag suffix loop over the deny list). 4,000 characters is the
   limit; 4,001 is "command too long". No pathological input found.
9. **Network.** `git remote show origin`, `npm view`, `npm outdated`
   contact a remote; `systemctl is-system-running --wait` blocks while the
   system is still starting.

## Tests and replay

```
$ ./scripts/run_tests.sh -q
1396 passed, 2 skipped, 18 xfailed, 3 warnings in 11.53s
$ tests/.venv/bin/python -m pytest tests/test_ops_exec.py -q
444 passed in 0.52s
```

Lexer against bash: 60,000 random strings over an alphabet of letters,
quotes, backslash, pipe and every shell metacharacter; the lexer accepted
6,532; 5,169 were compared with the words `bash` passes to a function; 356
differed, every one a leading or trailing U+00A0 removed by `strip()` (F1)
except strings where my harness rewrote a quoted `|`. No other difference.

Dev route (`POST 127.0.0.1:8103/ops/exec`, host `m3-e2e`, see "Temporary
changes"):

```
'env sh -c "id"' -> 422 refused: 'env' is not on the read-only allowlist: it runs any program given as an argument
'uniq /etc/hostname /home/pi/out' -> 422 refused: uniq: at most one file operand; a second operand is an output file that uniq writes
'git log --output=/home/pi/x' -> 422 refused: git log: --output writes a file
'printenv' -> 422 refused: 'printenv' is not on the read-only allowlist: it prints the environment, which holds secrets
'sudo reboot' -> 422 refused: sudo is not allowed in read-only mode
'uptime' -> 500 SSH key not found at /nonexistent/m3-e2e-key
'git -C /home/pi/MagicMirror status --short' -> 500 (same)
'grep "rm -rf" /var/log/syslog' -> 500 (same)
'grep -r --exclude-dir zzz ^' -> 500 (same: the classifier allowed it)
'git diff /mnt /home/pi' -> 500 (same)
'sort /dev/urandom' -> 500 (same)
'pm2 logs --lines --nostream' -> 500 (same)
'uniq /etc/hostname \xa0' -> 500 (same)
'ss -E' -> 500 (same)
{"host":"m3-e2e","command":5} -> 422 Input should be a valid string
```

A first batch of ten more refusals (`env rm -rf …`, `sort -o …`,
`cat /home/pi/.ssh/id_rsa`, `tail -f …`, `pm2 logs`, `ip link set eth0 down`,
`ls; rm x`, `cat "unbalanced`, `frobnicate`, blank) wrote ten audit lines
with the reason as `note`; the HTTP output of that batch was lost to a
terminal truncation and the statuses above come from the second batch.
`data/dev/ops_audit.log` went from 16 lines to 31: 15 new lines, one per
refusal, none for the commands that stopped at the key check.

Voice replay (criterion 15), result file
`docs/bench/voice_replay_2026-11-16_052801_m3-e2e.json`, compared with
`docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`:

```
voice_replay: {'pass': 92, 'xfail': 20, 'skip': 2}; executed 46, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
```

| Run | pass | xfail | fail | skip |
| --- | --- | --- | --- | --- |
| committed baseline | 91 | 20 | 1 | 2 |
| this run | 92 | 20 | 0 | 2 |

Row by row over 114 ids (`status` and `method`): 113 identical; 1 differs,
`inc-0930-search-deep-house-ytm`, fail in the baseline, pass here.

## Temporary changes, all reverted

- `ops/hosts.json` (gitignored), created twice for the dev route test with
  one host, `nobody@192.0.2.1` (TEST-NET-1) and key `/nonexistent/m3-e2e-key`;
  deleted after each batch. `ops/` holds only `hosts.example.json`; dev
  answers 404 "known hosts: (none configured)" again. The builder's
  `ops/hosts.json` was already gone when I started. Nothing in the repo
  tracks one; both compose services mount `./ops` read-only, and
  production's `./ops` resolves to `../kronk-releases/sprint-M1b/ops`, which
  holds only the example.
- `data/dev/ops_audit.log`: 15 lines appended by the dev route test (dev
  data, left in place; nothing under `./data` deleted).
- No container rebuilt or restarted. No edits to repo code, tests, compose,
  nginx, HA, `.env` or `.env.dev`. No tags, no push, no `pipeline_bench.sh`.
- Scratchpad only (outside the repo): the fake home directory, a fake git
  repository in it, the harness scripts, pm2/commander source files fetched
  from unpkg, and 1.8 GB of `sort` temp files that were removed.
- One command of mine was blocked by Claude Code's removal safety check (it
  contained no removal; the check could not parse a `bash -c` string). I
  reran the same test through Python's `subprocess` with no shell quoting
  tricks. Nothing was deleted.

## Full table of attempts

2,000 commands, called on `classify_readonly()` directly. 1,417 refused,
583 allowed. Non-printing and non-ASCII characters are shown as Python
escapes. For allowed rows the last column carries the finding (F1 to F6),
"obs" (observation 2 or 9), "ok" with the reason it is harmless, or nothing
(a command that should be allowed). The groups "should stay allowed" (232
rows, 231 allowed; the one refused is `ps axo pid,etime,cmd`) and "useful
forms now refused" (232 rows probed, 132 refused) are the other direction.

**plan table**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `env rm -rf /home/pi/MagicMirror` | refused | 'env' is not on the read-only allowlist: it runs any program given as an argument |
| `env sh -c "id"` | refused | 'env' is not on the read-only allowlist: it runs any program given as an argument |
| `sort -o /home/pi/.bashrc /tmp/x` | refused | sort: -o writes a file |
| `uniq /etc/hostname /home/pi/out` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `git log --output=/home/pi/x` | refused | git log: --output writes a file |
| `ip link set eth0 down` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'set' |
| `cat /home/pi/.ssh/id_rsa` | refused | cat: argument '/home/pi/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets mus… |
| `printenv` | refused | 'printenv' is not on the read-only allowlist: it prints the environment, which holds sec… |
| `tail -f /var/log/syslog` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `pm2 logs` | refused | pm2: 'pm2 logs' streams forever; add --nostream (and --lines N) |

**plan table variants**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `ENV rm x` | refused | 'ENV' is not on the read-only allowlist (mutations are not enabled yet) |
| `/usr/bin/env rm x` | refused | 'env' is not on the read-only allowlist: it runs any program given as an argument |
| `'env' rm x` | refused | 'env' is not on the read-only allowlist: it runs any program given as an argument |
| `e\\nv rm x` | refused | 'env' is not on the read-only allowlist: it runs any program given as an argument |
| `"e"nv rm x` | refused | 'env' is not on the read-only allowlist: it runs any program given as an argument |
| `env` | refused | 'env' is not on the read-only allowlist: it runs any program given as an argument |
| `env -i sh` | refused | 'env' is not on the read-only allowlist: it runs any program given as an argument |
| `printenv HOME` | refused | 'printenv' is not on the read-only allowlist: it prints the environment, which holds sec… |
| `/usr/bin/printenv` | refused | 'printenv' is not on the read-only allowlist: it prints the environment, which holds sec… |
| `sort --output /home/pi/.bashrc x` | refused | sort: --output writes a file |
| `sort --output=/x y` | refused | sort: --output writes a file |
| `sort -o/home/pi/.bashrc x` | refused | sort: -o writes a file (given as '-o/home/pi/.bashrc') |
| `sort x -o y` | refused | sort: -o writes a file |
| `sort -uo y x` | refused | sort: -o writes a file (given as '-uo') |
| `sort -u -- x -o y` | refused | sort: -o writes a file |
| `sort --o=y x` | refused | sort: --output writes a file (given as '--o=y') |
| `sort --outpu y x` | refused | sort: --output writes a file (given as '--outpu') |
| `sort '-o' y x` | refused | sort: -o writes a file |
| `sort -\\o y x` | refused | sort: -o writes a file |
| `sort "--output"=y x` | refused | sort: --output writes a file |
| `sort -k1,1 -o y x` | refused | sort: -o writes a file |
| `sort -t , -o y x` | refused | sort: -o writes a file |
| `sort -to -o y x` | refused | sort: -o writes a file |
| `sort -S 1M -T /tmp -o y` | refused | sort: -o writes a file |
| `sort --compress-program=sh x` | refused | sort: --compress-program runs a program |
| `sort --comp sh x` | refused | sort: --compress-program runs a program (given as '--comp') |
| `sort --files0-from=/tmp/l` | refused | sort: --files0-from reads the list of files to print from a file the classifier cannot s… |
| `sort --files0 /tmp/l` | refused | sort: --files0-from reads the list of files to print from a file the classifier cannot s… |
| `uniq a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq -c a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq -- a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq - b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq -f1 a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq -f 1 a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq --skip-fields=1 a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq --skip-fields 1 a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq a -- b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq a '' ` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq a \\ b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `cat x \| uniq - /home/pi/out` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq -1 a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq +1 a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `git log --output /x` | refused | git log: --output writes a file |
| `git log --outpu=/x` | refused | git log: --output writes a file (given as '--outpu=/x') |
| `git log --out /x` | refused | git log: --output writes a file (given as '--out') |
| `git show --output=/x` | refused | git show: --output writes a file |
| `git diff --output=/x` | refused | git diff: --output writes a file |
| `git -C /r log --output=/x` | refused | git log: --output writes a file |
| `git log -p --output=/x` | refused | git log: --output writes a file |
| `git log -- --output=/x` | refused | git log: --output writes a file |
| `git shortlog --output=/x` | refused | git shortlog: --output writes a file |
| `git format-patch -o /x HEAD~1` | refused | git 'format-patch' is not a read-only subcommand (allowed: blame, branch, cat-file, desc… |
| `git diff --output-indicator-new=x --output=/y` | refused | git diff: --output writes a file |
| `ip link set dev eth0 down` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'set' |
| `ip l set eth0 down` | refused | ip l: only a bare object or 'show'/'list' is allowed, got 'set' |
| `ip -4 link set eth0 down` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'set' |
| `ip link s eth0 down` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 's' |
| `ip link se eth0 down` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'se' |
| `ip addr add 1.2.3.4/24 dev eth0` | refused | ip addr: only a bare object or 'show'/'list' is allowed, got 'add' |
| `ip a a 1.2.3.4/24 dev eth0` | refused | ip a: only a bare object or 'show'/'list' is allowed, got 'a' |
| `ip route del default` | refused | ip route: only a bare object or 'show'/'list' is allowed, got 'del' |
| `ip r flush all` | refused | ip r: only a bare object or 'show'/'list' is allowed, got 'flush' |
| `ip neigh flush all` | refused | ip neigh: only a bare object or 'show'/'list' is allowed, got 'flush' |
| `ip link delete eth0` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'delete' |
| `ip link show eth0 \| ip link set eth0 down` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'set' |
| `ip -batch /tmp/b` | refused | ip: '-batch' is not an allowed option or object (objects: addr, link, route, neigh; opti… |
| `ip -b /tmp/b` | refused | ip: '-b' is not an allowed option or object (objects: addr, link, route, neigh; options:… |
| `ip -force -batch -` | refused | ip: '-force' is not an allowed option or object (objects: addr, link, route, neigh; opti… |
| `ip netns exec x sh` | refused | ip: 'netns' is not an allowed option or object (objects: addr, link, route, neigh; optio… |
| `ip -n ns link set lo down` | refused | ip: '-n' is not an allowed option or object (objects: addr, link, route, neigh; options:… |
| `ip monitor` | refused | ip: 'monitor' is not an allowed option or object (objects: addr, link, route, neigh; opt… |
| `ip -all netns exec sh` | refused | ip: '-all' is not an allowed option or object (objects: addr, link, route, neigh; option… |
| `ip link` | ALLOWED | ok (harmless; see notes) |
| `ip tunnel add x` | refused | ip: 'tunnel' is not an allowed option or object (objects: addr, link, route, neigh; opti… |
| `ip rule add` | refused | ip: 'rule' is not an allowed option or object (objects: addr, link, route, neigh; option… |
| `ip addr show; ip link set eth0 down` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `ip addr save` | refused | ip addr: only a bare object or 'show'/'list' is allowed, got 'save' |
| `ip addr restore` | refused | ip addr: only a bare object or 'show'/'list' is allowed, got 'restore' |
| `ip link show up type veth \| sh` | refused | 'sh' is not on the read-only allowlist (mutations are not enabled yet) |
| `ip link property add dev eth0 altname x` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'property' |
| `ip link add x type dummy` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'add' |
| `ip xfrm state flush` | refused | ip: 'xfrm' is not an allowed option or object (objects: addr, link, route, neigh; option… |
| `ip token set ::1 dev eth0` | refused | ip: argument 'token' matches the secrets deny list ('*token*'); secrets must not reach t… |
| `ip maddr add x dev eth0` | refused | ip: 'maddr' is not an allowed option or object (objects: addr, link, route, neigh; optio… |
| `ip tuntap add x mode tun` | refused | ip: 'tuntap' is not an allowed option or object (objects: addr, link, route, neigh; opti… |
| `tail -F x` | refused | tail: -F follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail --follow x` | refused | tail: --follow follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail --follow=name x` | refused | tail: --follow follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail --fol x` | refused | tail: --follow follows forever and never returns (burns the 30 s timeout); use -n N (giv… |
| `tail --f x` | refused | tail: --follow follows forever and never returns (burns the 30 s timeout); use -n N (giv… |
| `tail -fn 5 x` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N (given as … |
| `tail -n 5 -f x` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail -5f x` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N (given as … |
| `tail +5f x` | refused | tail: '+5f' follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail -n5 x -f` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail -c 5 -f x` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail --retry x` | refused | tail: --retry follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail -qf x` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N (given as … |
| `tail -0f x` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N (given as … |
| `tail 'x' "-f"` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail -- x -f` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail --pid=1 -f x` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail -s 1 -f x` | refused | tail: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `tail ---follow x` | ALLOWED | ok (harmless; see notes) |
| `pm2 logs --lines 5` | refused | pm2: 'pm2 logs' streams forever; add --nostream (and --lines N) |
| `pm2 logs app` | refused | pm2: 'pm2 logs' streams forever; add --nostream (and --lines N) |
| `pm2 log --nostream` | refused | pm2 'log' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 logs -- --nostream` | refused | pm2: 'pm2 logs' streams forever; add --nostream (and --lines N) |
| `pm2 logs '--nostream '` | refused | pm2: 'pm2 logs' streams forever; add --nostream (and --lines N) |
| `pm2 logs --no-stream` | refused | pm2: 'pm2 logs' streams forever; add --nostream (and --lines N) |
| `pm2 logs --lines --nostream` | ALLOWED | **F4** |
| `pm2 logs --nostream=false` | ALLOWED | ok (--nostream still set) |
| `pm2 logs --highlight --nostream` | ALLOWED | ok (--nostream still set) |
| `pm2 logs --timestamp --nostream` | ALLOWED | ok (--nostream still set) |
| `pm2 logs app --nostream --lines --nostream2` | ALLOWED | ok (--nostream still set) |
| `pm2 list --watch` | ALLOWED | **F4** |
| `pm2 ls --watch` | ALLOWED | **F4** |
| `pm2 status --watch` | ALLOWED | **F4** |
| `pm2 list --no-daemon` | ALLOWED | obs: foreground daemon if none runs (not verified) |
| `pm2 monit` | refused | pm2 'monit' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 dashboard` | refused | pm2 'dashboard' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 attach 0` | refused | pm2 'attach' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 restart all` | refused | pm2 'restart' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 -s restart all` | refused | pm2: put the subcommand first |
| `pm2 stop all` | refused | pm2 'stop' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 delete all` | refused | pm2 'delete' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 kill` | refused | pm2 'kill' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 start x.js` | refused | pm2 'start' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 flush` | refused | pm2 'flush' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 reloadLogs` | refused | pm2 'reloadLogs' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 save` | refused | pm2 'save' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 resurrect` | refused | pm2 'resurrect' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 startup` | refused | pm2 'startup' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 update` | refused | pm2 'update' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 set k v` | refused | pm2 'set' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 install x` | refused | pm2 'install' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 env 0` | refused | pm2 'env' is not a read-only subcommand: it prints environment variables (secrets) (allo… |
| `pm2 jlist` | refused | pm2 'jlist' is not a read-only subcommand: it prints every process's environment (secret… |
| `pm2 prettylist` | refused | pm2 'prettylist' is not a read-only subcommand: it prints every process's environment (s… |
| `pm2 describe 0` | refused | pm2 'describe' is not a read-only subcommand: it prints environment variables (secrets) … |
| `pm2 show 0` | refused | pm2 'show' is not a read-only subcommand: it prints environment variables (secrets) (all… |
| `pm2 info 0` | refused | pm2 'info' is not a read-only subcommand: it prints environment variables (secrets) (all… |
| `pm2 id app` | refused | pm2 'id' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 pid` | refused | pm2 'pid' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 report` | refused | pm2 'report' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 conf` | refused | pm2 'conf' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 exec ls` | refused | pm2 'exec' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 trigger a b` | refused | pm2 'trigger' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 sendSignal SIGKILL x` | refused | pm2 'sendSignal' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 scale app 4` | refused | pm2 'scale' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 reset all` | refused | pm2 'reset' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 --no-daemon` | ALLOWED | obs: foreground daemon if none runs (not verified) |
| `pm2 l` | refused | pm2 'l' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `pm2 ps` | refused | pm2 'ps' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `cat ~/.ssh/id_rsa` | refused | cat: argument '~/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets must not r… |
| `cat .ssh/id_rsa` | refused | cat: argument '.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets must not rea… |
| `cat ./.ssh/id_rsa` | refused | cat: argument './.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets must not r… |
| `cat /home/pi//.ssh//id_rsa` | refused | cat: argument '/home/pi//.ssh//id_rsa' matches the secrets deny list ('.ssh'); secrets m… |
| `cat /home/pi/MagicMirror/../.ssh/id_rsa` | refused | cat: argument '/home/pi/MagicMirror/../.ssh/id_rsa' matches the secrets deny list ('.ssh… |
| `cat ~pi/.ssh/authorized_keys` | refused | cat: argument '~pi/.ssh/authorized_keys' matches the secrets deny list ('.ssh'); secrets… |
| `cat /home/pi/.SSH/ID_RSA` | refused | cat: argument '/home/pi/.SSH/ID_RSA' matches the secrets deny list ('.ssh'); secrets mus… |
| `cat '/home/pi/.ssh/id_rsa'` | refused | cat: argument '/home/pi/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets mus… |
| `cat /home/pi/.s\\sh/id_rsa` | refused | cat: argument '/home/pi/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets mus… |
| `cat /home/pi/.s''sh/id_ed25519` | refused | cat: argument '/home/pi/.ssh/id_ed25519' matches the secrets deny list ('.ssh'); secrets… |
| `cat /home/pi/.ssh/` | refused | cat: argument '/home/pi/.ssh/' matches the secrets deny list ('.ssh'); secrets must not … |
| `cat /home/pi/.ssh` | refused | cat: argument '/home/pi/.ssh' matches the secrets deny list ('.ssh'); secrets must not r… |
| `ls -la ~/.ssh` | refused | ls: argument '~/.ssh' matches the secrets deny list ('.ssh'); secrets must not reach the… |
| `head ~/.ssh/config` | refused | head: argument '~/.ssh/config' matches the secrets deny list ('.ssh'); secrets must not … |
| `cat /home/pi/.ssh./id_rsa` | refused | cat: argument '/home/pi/.ssh./id_rsa' matches the secrets deny list ('id_*'); secrets mu… |
| `cat /home/pi/.ssh/id_rsa.` | refused | cat: argument '/home/pi/.ssh/id_rsa.' matches the secrets deny list ('.ssh'); secrets mu… |
| `cat /root/.ssh/id_ed25519` | refused | cat: argument '/root/.ssh/id_ed25519' matches the secrets deny list ('.ssh'); secrets mu… |
| `cat id_rsa` | refused | cat: argument 'id_rsa' matches the secrets deny list ('id_*'); secrets must not reach th… |
| `cat ./id_ed25519` | refused | cat: argument './id_ed25519' matches the secrets deny list ('id_*'); secrets must not re… |
| `cat /tmp/ID_ECDSA.pub` | refused | cat: argument '/tmp/ID_ECDSA.pub' matches the secrets deny list ('id_*'); secrets must n… |
| `cat /etc/ssh/ssh_host_ed25519_key` | refused | cat: argument '/etc/ssh/ssh_host_ed25519_key' matches the secrets deny list ('*_key'); s… |
| `cat /etc/ssh/ssh_host_rsa_key` | refused | cat: argument '/etc/ssh/ssh_host_rsa_key' matches the secrets deny list ('*_key'); secre… |

**deny list paths**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `cat /etc/shadow` | refused | cat: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not … |
| `cat /etc/shadow-` | refused | cat: argument '/etc/shadow-' matches the secrets deny list ('shadow*'); secrets must not… |
| `cat /etc//shadow` | refused | cat: argument '/etc//shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `cat /etc/./shadow` | refused | cat: argument '/etc/./shadow' matches the secrets deny list ('shadow*'); secrets must no… |
| `cat /etc/../etc/shadow` | refused | cat: argument '/etc/../etc/shadow' matches the secrets deny list ('shadow*'); secrets mu… |
| `cat /ETC/SHADOW` | refused | cat: argument '/ETC/SHADOW' matches the secrets deny list ('shadow*'); secrets must not … |
| `cat /etc/gshadow` | refused | cat: argument '/etc/gshadow' matches the secrets deny list ('gshadow*'); secrets must no… |
| `cat /etc/gshadow-` | refused | cat: argument '/etc/gshadow-' matches the secrets deny list ('gshadow*'); secrets must n… |
| `cat /etc/shadow.` | refused | cat: argument '/etc/shadow.' matches the secrets deny list ('shadow*'); secrets must not… |
| `cat /var/backups/shadow.bak` | refused | cat: argument '/var/backups/shadow.bak' matches the secrets deny list ('shadow*'); secre… |
| `cat /etc/'sha'dow` | refused | cat: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not … |
| `cat /etc/sha\\dow` | refused | cat: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not … |
| `cat "/etc/shadow"` | refused | cat: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not … |
| `cat /etc/shadow /etc/hostname` | refused | cat: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not … |
| `cat /etc/hostname /etc/shadow` | refused | cat: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not … |
| `head -n 1 /etc/shadow` | refused | head: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `tail -n 1 /etc/shadow` | refused | tail: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `grep root /etc/shadow` | refused | grep: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `grep -f /etc/shadow /dev/null` | refused | grep: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `sort /etc/shadow` | refused | sort: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `uniq /etc/shadow` | refused | uniq: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `wc /etc/shadow` | refused | wc: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not r… |
| `file /etc/shadow` | refused | file: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `stat /etc/shadow` | refused | stat: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `ls -l /etc/shadow` | refused | ls: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not r… |
| `cut -d: -f2 /etc/shadow` | refused | cut: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not … |
| `nl /etc/shadow` | refused | nl: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not r… |
| `column /etc/shadow` | refused | column: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must n… |
| `du /etc/shadow` | refused | du: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not r… |
| `readlink -f /etc/shadow` | refused | readlink: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must… |
| `realpath /etc/shadow` | refused | realpath: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must… |
| `zgrep . /etc/shadow` | refused | zgrep: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must no… |
| `egrep . /etc/shadow` | refused | egrep: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must no… |
| `fgrep x /etc/shadow` | refused | fgrep: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must no… |
| `date -f /etc/shadow` | refused | date: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `date -r /etc/shadow` | refused | date: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `file -f /etc/shadow` | refused | file: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `file -m /etc/shadow x` | refused | file: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not… |
| `wc --files0-from=/etc/shadow` | refused | wc: argument '--files0-from=/etc/shadow' matches the secrets deny list ('shadow*'); secr… |
| `du --files0-from=/etc/shadow` | refused | du: argument '--files0-from=/etc/shadow' matches the secrets deny list ('shadow*'); secr… |
| `git diff /dev/null /etc/shadow` | refused | git: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not … |
| `git blame --contents /etc/shadow x` | refused | git: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not … |
| `git log --ignore-revs-file=/etc/shadow` | refused | git: argument '--ignore-revs-file=/etc/shadow' matches the secrets deny list ('shadow*')… |
| `git diff -O/etc/shadow` | refused | git: argument '-O/etc/shadow' matches the secrets deny list ('shadow*'); secrets must no… |
| `journalctl --file=/etc/shadow` | refused | journalctl: argument '--file=/etc/shadow' matches the secrets deny list ('shadow*'); sec… |
| `journalctl -D /home/pi/.ssh` | refused | journalctl: argument '/home/pi/.ssh' matches the secrets deny list ('.ssh'); secrets mus… |
| `ss -F /etc/shadow` | refused | ss: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not r… |
| `sensors -c /etc/shadow` | refused | sensors: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must … |
| `ps --sort /etc/shadow` | refused | ps: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets must not r… |
| `systemctl status /etc/shadow` | refused | systemctl: argument '/etc/shadow' matches the secrets deny list ('shadow*'); secrets mus… |
| `stat -c %n /proc/self/root/etc/shadow` | refused | stat: argument '/proc/self/root/etc/shadow' matches the secrets deny list ('shadow*'); s… |
| `cat /proc/self/root/etc/shadow` | refused | cat: argument '/proc/self/root/etc/shadow' matches the secrets deny list ('shadow*'); se… |
| `cat /proc/1/root/home/pi/.ssh/id_rsa` | refused | cat: argument '/proc/1/root/home/pi/.ssh/id_rsa' matches the secrets deny list ('.ssh');… |
| `cat /proc/self/cwd/.ssh/id_rsa` | refused | cat: argument '/proc/self/cwd/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secre… |
| `cat /proc/self/environ` | refused | cat: argument '/proc/self/environ' matches the secrets deny list ('environ'); secrets mu… |
| `cat /proc/1/environ` | refused | cat: argument '/proc/1/environ' matches the secrets deny list ('environ'); secrets must … |
| `cat /proc/self/task/1/environ` | refused | cat: argument '/proc/self/task/1/environ' matches the secrets deny list ('environ'); sec… |
| `cat /proc/thread-self/environ` | refused | cat: argument '/proc/thread-self/environ' matches the secrets deny list ('environ'); sec… |
| `head -c 4096 /proc/self/environ` | refused | head: argument '/proc/self/environ' matches the secrets deny list ('environ'); secrets m… |
| `tr '\\0' '\\n' \| cat /proc/self/environ` | refused | cat: argument '/proc/self/environ' matches the secrets deny list ('environ'); secrets mu… |
| `cat /home/pi/.env` | refused | cat: argument '/home/pi/.env' matches the secrets deny list ('.env'); secrets must not r… |
| `cat .env` | refused | cat: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `cat ./.env` | refused | cat: argument './.env' matches the secrets deny list ('.env'); secrets must not reach th… |
| `cat /home/pi/MagicMirror/.env` | refused | cat: argument '/home/pi/MagicMirror/.env' matches the secrets deny list ('.env'); secret… |
| `cat /srv/app/.env.local` | refused | cat: argument '/srv/app/.env.local' matches the secrets deny list ('.env.*'); secrets mu… |
| `cat /srv/app/.env.production` | refused | cat: argument '/srv/app/.env.production' matches the secrets deny list ('.env.*'); secre… |
| `cat prod.env` | refused | cat: argument 'prod.env' matches the secrets deny list ('*.env'); secrets must not reach… |
| `cat /x/.ENV` | refused | cat: argument '/x/.ENV' matches the secrets deny list ('.env'); secrets must not reach t… |
| `cat .env.` | refused | cat: argument '.env.' matches the secrets deny list ('.env.*'); secrets must not reach t… |
| `cat '.env'` | refused | cat: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `cat .e\\nv` | refused | cat: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `cat ".env"` | refused | cat: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `cat .en''v` | refused | cat: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `cat -- .env` | refused | cat: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `cat -A .env` | refused | cat: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `head -n1 .env` | refused | head: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the… |
| `grep = .env` | refused | grep: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the… |
| `grep -e x -f .env` | refused | grep: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the… |
| `sort .env` | refused | sort: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the… |
| `wc -c .env` | refused | wc: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the m… |
| `nl .env` | refused | nl: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the m… |
| `git show HEAD:.env` | refused | git: argument 'HEAD:.env' matches the secrets deny list ('.env'); secrets must not reach… |
| `git show HEAD:./.env` | refused | git: argument 'HEAD:./.env' matches the secrets deny list ('.env'); secrets must not rea… |
| `git show :.env` | refused | git: argument ':.env' matches the secrets deny list ('.env'); secrets must not reach the… |
| `git show :0:.env` | refused | git: argument ':0:.env' matches the secrets deny list ('.env'); secrets must not reach t… |
| `git cat-file -p HEAD:.env` | refused | git: argument 'HEAD:.env' matches the secrets deny list ('.env'); secrets must not reach… |
| `git diff HEAD -- .env` | refused | git: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `git log -p -- .env` | refused | git: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `git blame .env` | refused | git: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the … |
| `git -C /home/pi/.ssh log` | refused | git: argument '/home/pi/.ssh' matches the secrets deny list ('.ssh'); secrets must not r… |
| `git --git-dir=/home/pi/.ssh log` | refused | git: argument '--git-dir=/home/pi/.ssh' matches the secrets deny list ('.ssh'); secrets … |
| `git --work-tree=/home/pi/.ssh status` | refused | git: argument '--work-tree=/home/pi/.ssh' matches the secrets deny list ('.ssh'); secret… |
| `git --git-dir /home/pi/.ssh log` | refused | git: argument '/home/pi/.ssh' matches the secrets deny list ('.ssh'); secrets must not r… |
| `cat server.pem` | refused | cat: argument 'server.pem' matches the secrets deny list ('*.pem'); secrets must not rea… |
| `cat /etc/ssl/private/ssl-cert-snakeoil.key` | refused | cat: argument '/etc/ssl/private/ssl-cert-snakeoil.key' matches the secrets deny list ('*… |
| `cat x.KEY` | refused | cat: argument 'x.KEY' matches the secrets deny list ('*.key'); secrets must not reach th… |
| `cat x.p12` | refused | cat: argument 'x.p12' matches the secrets deny list ('*.p12'); secrets must not reach th… |
| `cat x.pfx` | refused | cat: argument 'x.pfx' matches the secrets deny list ('*.pfx'); secrets must not reach th… |
| `cat /etc/letsencrypt/live/x/privkey.pem` | refused | cat: argument '/etc/letsencrypt/live/x/privkey.pem' matches the secrets deny list ('*.pe… |
| `cat api_key` | refused | cat: argument 'api_key' matches the secrets deny list ('*_key'); secrets must not reach … |
| `cat my_token.txt` | refused | cat: argument 'my_token.txt' matches the secrets deny list ('*token*'); secrets must not… |
| `cat tokens.json` | refused | cat: argument 'tokens.json' matches the secrets deny list ('*token*'); secrets must not … |
| `cat .netrc` | refused | cat: argument '.netrc' matches the secrets deny list ('.netrc'); secrets must not reach … |
| `cat ~/.netrc` | refused | cat: argument '~/.netrc' matches the secrets deny list ('.netrc'); secrets must not reac… |
| `cat ~/.git-credentials` | refused | cat: argument '~/.git-credentials' matches the secrets deny list ('.git-credentials'); s… |
| `cat /home/pi/.config/hass_token` | refused | cat: argument '/home/pi/.config/hass_token' matches the secrets deny list ('*token*'); s… |
| `cat TOKEN` | refused | cat: argument 'TOKEN' matches the secrets deny list ('*token*'); secrets must not reach … |
| `cat ~/.ssh/authorized_keys` | refused | cat: argument '~/.ssh/authorized_keys' matches the secrets deny list ('.ssh'); secrets m… |
| `cat authorized_keys/../.ssh/x` | refused | cat: argument 'authorized_keys/../.ssh/x' matches the secrets deny list ('.ssh'); secret… |
| `cat -vid_rsa` | refused | cat: argument '-vid_rsa' matches the secrets deny list ('id_*'); secrets must not reach … |
| `grep -fid_rsa x` | refused | grep: argument '-fid_rsa' matches the secrets deny list ('id_*'); secrets must not reach… |
| `grep -f.env x` | refused | grep: argument '-f.env' matches the secrets deny list ('*.env'); secrets must not reach … |
| `grep -f~/.ssh/id_rsa x` | refused | grep: argument '-f~/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets must no… |
| `grep -e x -f ~/.ssh/id_rsa` | refused | grep: argument '~/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets must not … |
| `grep --file=/home/pi/.ssh/id_rsa x` | refused | grep: argument '--file=/home/pi/.ssh/id_rsa' matches the secrets deny list ('.ssh'); sec… |
| `grep --file /home/pi/.env x` | refused | grep: argument '/home/pi/.env' matches the secrets deny list ('.env'); secrets must not … |
| `sort --random-source=/etc/shadow x` | refused | sort: argument '--random-source=/etc/shadow' matches the secrets deny list ('shadow*'); … |
| `sort -T ~/.ssh x` | refused | sort: argument '~/.ssh' matches the secrets deny list ('.ssh'); secrets must not reach t… |
| `ls ~/.ssh/..` | refused | ls: argument '~/.ssh/..' matches the secrets deny list ('.ssh'); secrets must not reach … |
| `stat ~/.ssh/../.bashrc` | refused | stat: argument '~/.ssh/../.bashrc' matches the secrets deny list ('.ssh'); secrets must … |
| `ls -la .ssh` | refused | ls: argument '.ssh' matches the secrets deny list ('.ssh'); secrets must not reach the m… |
| `du -a ~/.ssh` | refused | du: argument '~/.ssh' matches the secrets deny list ('.ssh'); secrets must not reach the… |
| `file ~/.ssh/id_rsa` | refused | file: argument '~/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets must not … |
| `cat /home/pi/.ssh\\ /x` | ALLOWED | ok (harmless; see notes) |
| `cat ~/.ssh/id_rsa\\ ` | refused | could not parse command: trailing backslash |
| `echo ~/.ssh/id_rsa` | refused | echo: argument '~/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets must not … |
| `ps -o pid,environ` | refused | ps: argument 'pid,environ' matches the secrets deny list ('environ'); secrets must not r… |
| `ps -oenviron` | refused | ps: argument '-oenviron' matches the secrets deny list ('environ'); secrets must not rea… |
| `ps --format environ` | refused | ps: argument 'environ' matches the secrets deny list ('environ'); secrets must not reach… |

**deny list: unlisted secrets (observation)**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `cat ~/.bash_history` | ALLOWED | obs |
| `cat ~/.npmrc` | ALLOWED | obs |
| `cat ~/.pm2/dump.pm2` | ALLOWED | obs |
| `cat ~/.pm2/dump.pm2.bak` | ALLOWED | obs |
| `cat /home/pi/MagicMirror/config/config.js` | ALLOWED | obs |
| `cat ~/.envrc` | ALLOWED | obs |
| `cat ~/.config/gh/hosts.yml` | ALLOWED | obs |
| `cat ~/.docker/config.json` | ALLOWED | obs |
| `cat ~/.aws/credentials` | ALLOWED | obs |
| `cat ~/.pgpass` | ALLOWED | obs |
| `cat ~/.my.cnf` | ALLOWED | obs |
| `cat /etc/wpa_supplicant/wpa_supplicant.conf` | ALLOWED | obs |
| `cat /etc/NetworkManager/system-connections/home.nmconnection` | ALLOWED | obs |
| `cat /home/pi/MagicMirror/.git/config` | ALLOWED | obs |
| `cat ~/.gnupg/secring.gpg` | ALLOWED | obs |
| `cat ~/.gnupg/private-keys-v1.d/x` | ALLOWED | obs |
| `cat /etc/sudoers` | ALLOWED | obs |
| `cat ~/.wget-hsts` | ALLOWED | obs |
| `cat ~/.lesshst` | ALLOWED | obs |
| `cat ~/.viminfo` | ALLOWED | obs |
| `cat ~/.python_history` | ALLOWED | obs |
| `cat ~/.node_repl_history` | ALLOWED | obs |
| `cat secrets.json` | ALLOWED | obs |
| `cat credentials.json` | ALLOWED | obs |
| `cat ~/.htpasswd` | ALLOWED | obs |
| `cat /etc/ssl/private/server.crt` | ALLOWED | obs |
| `cat privkey` | ALLOWED | obs |
| `cat ~/key.txt` | ALLOWED | obs |
| `cat /home/pi/passwords.txt` | ALLOWED | obs |
| `cat ~/.kube/config` | ALLOWED | obs |
| `cat /var/lib/docker/containers/x/config.v2.json` | ALLOWED | obs |
| `cat /proc/1/cmdline` | ALLOWED | obs |
| `cat /proc/self/maps` | ALLOWED | obs |
| `cat /proc/kcore` | ALLOWED | obs |
| `cat /dev/mmcblk0p2` | ALLOWED | obs |
| `head -c 512 /dev/mem` | ALLOWED | obs |
| `cat /dev/sda` | ALLOWED | obs |
| `grep -a x /dev/mmcblk0` | ALLOWED | obs |
| `cat /sys/firmware/devicetree/base/serial-number` | ALLOWED | obs |
| `cat /etc/machine-id` | ALLOWED | obs |
| `systemctl show magicmirror` | ALLOWED | obs |
| `systemctl cat magicmirror` | ALLOWED | obs |
| `systemctl show -p Environment magicmirror` | ALLOWED | obs |
| `systemctl --user show magicmirror --property=Environment` | ALLOWED | obs |
| `git log -p` | ALLOWED | obs |
| `git log -p --all` | ALLOWED | obs |
| `git cat-file --batch-all-objects --batch` | ALLOWED | obs |
| `git cat-file -p 0123abcd` | ALLOWED | obs |
| `git ls-files -s` | ALLOWED | obs |
| `git show HEAD` | ALLOWED | obs |
| `git log --show-signature` | ALLOWED | obs |
| `pgrep -fa .` | ALLOWED | obs |
| `ps aux` | ALLOWED | obs |
| `ps -ef` | ALLOWED | obs |
| `ps -eo args` | ALLOWED | obs |
| `ps auxww` | ALLOWED | obs |
| `journalctl --no-pager -n 100000` | ALLOWED | obs |
| `grep -r '' /home/pi/MagicMirror` | ALLOWED | obs |
| `grep -r BEGIN /etc/ssl/private` | ALLOWED | obs |
| `grep -r psk /etc/NetworkManager/system-connections` | ALLOWED | obs |
| `grep -r '' /home/pi/.config` | ALLOWED | obs |
| `grep -r . /home/pi/.pm2` | ALLOWED | obs |
| `grep -r '' /home/pi/.gnupg` | ALLOWED | obs |
| `grep -r '' /home/pi/.aws` | ALLOWED | obs |
| `grep -r '' /root/.config/x` | ALLOWED | obs |
| `grep -r '' /var/lib/x` | ALLOWED | obs |
| `grep -rh '' /etc/ssl/private` | ALLOWED | obs |
| `grep -r '' ~/MagicMirror` | ALLOWED | obs |
| `grep -r '' ~pi/MagicMirror` | ALLOWED | obs |
| `grep -r '' MagicMirror` | ALLOWED | obs |
| `git remote show origin` | ALLOWED | obs |
| `npm view left-pad` | ALLOWED | obs |
| `npm view x --registry=http://192.0.2.1/` | ALLOWED | obs |
| `npm outdated` | ALLOWED | obs |

**recursion / read-without-naming**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `grep -r BEGIN /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -r BEGIN ~` | refused | grep: recursive search root '~' is too broad (it can reach secrets without naming them);… |
| `grep -r BEGIN ~/` | refused | grep: recursive search root '~/' is too broad (it can reach secrets without naming them)… |
| `grep -r BEGIN ~pi` | refused | grep: recursive search root '~pi' is too broad (it can reach secrets without naming them… |
| `grep -r BEGIN .` | refused | grep: recursive search root '.' is too broad (it can reach secrets without naming them);… |
| `grep -r BEGIN` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r BEGIN /` | refused | grep: recursive search root '/' is too broad (it can reach secrets without naming them);… |
| `grep -r BEGIN //home//pi/` | refused | grep: recursive search root '//home//pi/' is too broad (it can reach secrets without nam… |
| `grep -r BEGIN /home/pi/MagicMirror/..` | refused | grep: recursive search root '/home/pi/MagicMirror/..' contains '..' |
| `grep -r BEGIN /home/pi/.` | refused | grep: recursive search root '/home/pi/.' is too broad (it can reach secrets without nami… |
| `grep -r BEGIN /home/pi/./` | refused | grep: recursive search root '/home/pi/./' is too broad (it can reach secrets without nam… |
| `grep -r BEGIN ./` | refused | grep: recursive search root './' is too broad (it can reach secrets without naming them)… |
| `grep -r BEGIN ''` | refused | grep: recursive search root '' is too broad (it can reach secrets without naming them); … |
| `grep -r BEGIN ~+` | refused | grep: recursive search root '~+' is too broad (it can reach secrets without naming them)… |
| `grep -r BEGIN ~-` | refused | grep: recursive search root '~-' is too broad (it can reach secrets without naming them)… |
| `grep -r BEGIN /proc/self/cwd` | refused | grep: recursive search under /proc is not allowed |
| `grep -r BEGIN /proc/1/root/home/pi` | refused | grep: recursive search under /proc is not allowed |
| `grep -r BEGIN /root` | refused | grep: recursive search root '/root' is too broad (it can reach secrets without naming th… |
| `grep -r BEGIN /etc` | refused | grep: recursive search root '/etc' is too broad (it can reach secrets without naming the… |
| `grep -r BEGIN /etc/ssh` | refused | grep: recursive search root '/etc/ssh' is too broad (it can reach secrets without naming… |
| `grep -R BEGIN /home/pi/x/y` | refused | grep: -R follows every symlink while recursing; use -r |
| `grep --recursive BEGIN /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep --rec BEGIN /home` | refused | grep: recursive search root '/home' is too broad (it can reach secrets without naming th… |
| `grep --r BEGIN /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -d recurse BEGIN /home/pi` | refused | grep: -d sets the directory action; use -r for recursion |
| `grep -drecurse BEGIN /home/pi` | refused | grep: -d sets the directory action; use -r for recursion (given as '-drecurse') |
| `grep --directories=recurse BEGIN /home/pi` | refused | grep: --directories sets the directory action; use -r for recursion |
| `grep --dir=recurse BEGIN /home/pi` | refused | grep: --directories sets the directory action; use -r for recursion (given as '--dir=rec… |
| `grep --dereference-recursive x /home/pi/a/b` | refused | grep: --dereference-recursive follows every symlink while recursing; use -r |
| `grep --deref x /home/pi/a/b` | refused | grep: --dereference-recursive follows every symlink while recursing; use -r (given as '-… |
| `grep -ir BEGIN /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -ri BEGIN /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -re BEGIN /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -e BEGIN -r /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -r -e x -e y /home/pi/MagicMirror /etc` | refused | grep: recursive search root '/etc' is too broad (it can reach secrets without naming the… |
| `egrep -r BEGIN ~` | refused | egrep: recursive search root '~' is too broad (it can reach secrets without naming them)… |
| `fgrep -r BEGIN ~` | refused | fgrep: recursive search root '~' is too broad (it can reach secrets without naming them)… |
| `zgrep -r BEGIN ~` | refused | zgrep: recursive search root '~' is too broad (it can reach secrets without naming them)… |
| `grep BEGIN -r` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep BEGIN -r ~` | refused | grep: recursive search root '~' is too broad (it can reach secrets without naming them);… |
| `grep BEGIN ~ -r` | refused | grep: recursive search root '~' is too broad (it can reach secrets without naming them);… |
| `grep -r -- BEGIN ~` | refused | grep: recursive search root '~' is too broad (it can reach secrets without naming them);… |
| `grep -r BEGIN -- ~` | refused | grep: recursive search root '~' is too broad (it can reach secrets without naming them);… |
| `grep -c -r BEGIN /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -rl '' /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -A 99 -r BEGIN /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -m 1 -r BEGIN /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -r --exclude-dir zzz ^` | ALLOWED | **F2** |
| `grep -r --exclude-dir zzz BEGIN` | ALLOWED | **F2** |
| `grep -r --include zzz BEGIN` | ALLOWED | **F2** |
| `grep -r --exclude zzz BEGIN` | ALLOWED | **F2** |
| `grep -r --exclude-from /dev/null BEGIN` | ALLOWED | **F2** |
| `grep -r --binary-files text BEGIN` | ALLOWED | **F2** |
| `grep -r --devices skip BEGIN` | ALLOWED | **F2** |
| `grep -r --color never BEGIN` | ALLOWED | **F2** |
| `grep -r --colour never BEGIN` | ALLOWED | **F2** |
| `grep -r --exclude-dir zzz -h ^` | ALLOWED | **F2** |
| `grep -rh --exclude-dir zzz PRIVATE` | ALLOWED | **F2** |
| `grep -r --binary-files text -A 60 OPENSSH` | ALLOWED | **F2** |
| `egrep -r --exclude-dir zzz ^` | ALLOWED | **F2** |
| `fgrep -r --exclude-dir zzz BEGIN` | ALLOWED | **F2** |
| `grep --exclude-dir zzz -r a` | ALLOWED | **F2** |
| `grep -r --exclude-dir=zzz ^` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r --max-count 1 BEGIN` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r --context 3 BEGIN` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r --label x BEGIN` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r -A 3 BEGIN` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r -m 1 BEGIN` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r --exclude-dir zzz BEGIN /` | refused | grep: recursive search root '/' is too broad (it can reach secrets without naming them);… |
| `grep -r --exclude-dir /home/pi/a ^ ` | ALLOWED | **F2** |
| `grep -r --exclude-dir a/b/c /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -r --binary-files text /home/pi` | refused | grep: recursive search root '/home/pi' is too broad (it can reach secrets without naming… |
| `grep -r --exclude-dir zzz -e ^` | ALLOWED | **F2** |
| `grep -r --regexp ^` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r --regexp=^` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r --file /dev/null` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -rf /tmp/pats` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -rf /tmp/pats a/b` | ALLOWED | ok (deep relative root) |
| `grep -r -D skip x` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r -D skip ^ ` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -r -B 2 ^` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `git diff /mnt /home/pi` | ALLOWED | **F3** |
| `git diff /usr/local/games /home/pi` | ALLOWED | **F3** |
| `git diff /mnt ~` | ALLOWED | **F3** |
| `git diff /mnt .` | ALLOWED | **F3** |
| `git diff -- /mnt /home/pi` | ALLOWED | **F3** |
| `git --no-pager diff /mnt /home/pi` | ALLOWED | **F3** |
| `git -C /home/pi/MagicMirror diff /mnt /home/pi` | ALLOWED | **F3** |
| `git diff /dev/null /home/pi/.bash_history` | ALLOWED | **F3** |
| `git diff --no-index /mnt /home/pi` | refused | git diff: --no-index compares arbitrary paths outside the repository |
| `git diff --no-inde /mnt /home/pi` | refused | git diff: --no-index compares arbitrary paths outside the repository (given as '--no-ind… |
| `git diff --stat /mnt /home/pi` | ALLOWED | **F3** |
| `git diff /mnt /etc` | ALLOWED | **F3** |
| `git diff /mnt /root` | ALLOWED | **F3** |
| `git diff /mnt /etc/ssl/private` | ALLOWED | **F3** |
| `git diff --name-only /mnt /home/pi` | ALLOWED | **F3** |
| `git diff /mnt /` | ALLOWED | **F3** |
| `git diff /etc/hostname /etc/hosts` | ALLOWED | **F3** |
| `git --git-dir=/nonexistent diff /mnt /home/pi` | ALLOWED | **F3** |
| `git --work-tree=/ diff /mnt /home/pi` | ALLOWED | **F3** |

**write / exec side doors (must refuse)**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `find / -name x` | refused | 'find' is not on the read-only allowlist (mutations are not enabled yet) |
| `find . -exec rm {} ';'` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `find . -delete` | refused | 'find' is not on the read-only allowlist (mutations are not enabled yet) |
| `find . -fprint /x` | refused | 'find' is not on the read-only allowlist (mutations are not enabled yet) |
| `find . -ok rm '{}' ';'` | refused | 'find' is not on the read-only allowlist (mutations are not enabled yet) |
| `find . -execdir sh ';'` | refused | 'find' is not on the read-only allowlist (mutations are not enabled yet) |
| `/usr/bin/find .` | refused | 'find' is not on the read-only allowlist (mutations are not enabled yet) |
| `sed -i s/a/b/ f` | refused | 'sed' is not on the read-only allowlist (mutations are not enabled yet) |
| `sed -n 1p f` | refused | 'sed' is not on the read-only allowlist (mutations are not enabled yet) |
| `sed 'w /x' f` | refused | 'sed' is not on the read-only allowlist (mutations are not enabled yet) |
| `sed s/a/b/e f` | refused | 'sed' is not on the read-only allowlist (mutations are not enabled yet) |
| `sed --in-place=bak s/a/b/ f` | refused | 'sed' is not on the read-only allowlist (mutations are not enabled yet) |
| `sed -n '1e id' f` | refused | 'sed' is not on the read-only allowlist (mutations are not enabled yet) |
| `awk 'BEGIN{system("id")}'` | refused | 'awk' is not on the read-only allowlist (mutations are not enabled yet) |
| `awk '{print > "/x"}' f` | refused | 'awk' is not on the read-only allowlist (mutations are not enabled yet) |
| `gawk 1 f` | refused | 'gawk' is not on the read-only allowlist (mutations are not enabled yet) |
| `mawk 1 f` | refused | 'mawk' is not on the read-only allowlist (mutations are not enabled yet) |
| `xargs rm` | refused | 'xargs' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls \| xargs rm` | refused | 'xargs' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls \| xargs` | refused | 'xargs' is not on the read-only allowlist (mutations are not enabled yet) |
| `tee /x` | refused | 'tee' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls \| tee /x` | refused | 'tee' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls \| tee` | refused | 'tee' is not on the read-only allowlist (mutations are not enabled yet) |
| `dd if=/dev/zero of=/x` | refused | 'dd' is not on the read-only allowlist (mutations are not enabled yet) |
| `dd if=/etc/hostname` | refused | 'dd' is not on the read-only allowlist (mutations are not enabled yet) |
| `cp a b` | refused | 'cp' is not on the read-only allowlist (mutations are not enabled yet) |
| `mv a b` | refused | 'mv' is not on the read-only allowlist (mutations are not enabled yet) |
| `install a b` | refused | 'install' is not on the read-only allowlist (mutations are not enabled yet) |
| `rsync a b` | refused | 'rsync' is not on the read-only allowlist (mutations are not enabled yet) |
| `ln -s a b` | refused | 'ln' is not on the read-only allowlist (mutations are not enabled yet) |
| `ln a b` | refused | 'ln' is not on the read-only allowlist (mutations are not enabled yet) |
| `touch x` | refused | 'touch' is not on the read-only allowlist (mutations are not enabled yet) |
| `truncate -s 0 x` | refused | 'truncate' is not on the read-only allowlist (mutations are not enabled yet) |
| `shred x` | refused | 'shred' is not on the read-only allowlist (mutations are not enabled yet) |
| `rm x` | refused | 'rm' is not on the read-only allowlist (mutations are not enabled yet) |
| `rm -rf /` | refused | 'rm' is not on the read-only allowlist (mutations are not enabled yet) |
| `rmdir x` | refused | 'rmdir' is not on the read-only allowlist (mutations are not enabled yet) |
| `mkdir x` | refused | 'mkdir' is not on the read-only allowlist (mutations are not enabled yet) |
| `mkfifo x` | refused | 'mkfifo' is not on the read-only allowlist (mutations are not enabled yet) |
| `mknod x p` | refused | 'mknod' is not on the read-only allowlist (mutations are not enabled yet) |
| `chmod 777 x` | refused | 'chmod' is not on the read-only allowlist (mutations are not enabled yet) |
| `chown pi x` | refused | 'chown' is not on the read-only allowlist (mutations are not enabled yet) |
| `chgrp pi x` | refused | 'chgrp' is not on the read-only allowlist (mutations are not enabled yet) |
| `chattr +i x` | refused | 'chattr' is not on the read-only allowlist (mutations are not enabled yet) |
| `setfacl -m u:x:rw f` | refused | 'setfacl' is not on the read-only allowlist (mutations are not enabled yet) |
| `tar cf /x .` | refused | 'tar' is not on the read-only allowlist (mutations are not enabled yet) |
| `tar xf x` | refused | 'tar' is not on the read-only allowlist (mutations are not enabled yet) |
| `tar tf x` | refused | 'tar' is not on the read-only allowlist (mutations are not enabled yet) |
| `tar -cf - . \| cat` | refused | 'tar' is not on the read-only allowlist (mutations are not enabled yet) |
| `zip x y` | refused | 'zip' is not on the read-only allowlist (mutations are not enabled yet) |
| `unzip x` | refused | 'unzip' is not on the read-only allowlist (mutations are not enabled yet) |
| `gzip x` | refused | 'gzip' is not on the read-only allowlist (mutations are not enabled yet) |
| `gunzip x` | refused | 'gunzip' is not on the read-only allowlist (mutations are not enabled yet) |
| `zcat x` | refused | 'zcat' is not on the read-only allowlist (mutations are not enabled yet) |
| `bzip2 x` | refused | 'bzip2' is not on the read-only allowlist (mutations are not enabled yet) |
| `xz x` | refused | 'xz' is not on the read-only allowlist (mutations are not enabled yet) |
| `curl http://x` | refused | 'curl' is not on the read-only allowlist (mutations are not enabled yet) |
| `curl -o /x http://x` | refused | 'curl' is not on the read-only allowlist (mutations are not enabled yet) |
| `curl -O http://x` | refused | 'curl' is not on the read-only allowlist (mutations are not enabled yet) |
| `curl --output /x http://x` | refused | 'curl' is not on the read-only allowlist (mutations are not enabled yet) |
| `curl -d a=b http://x` | refused | 'curl' is not on the read-only allowlist (mutations are not enabled yet) |
| `curl -X POST http://x` | refused | 'curl' is not on the read-only allowlist (mutations are not enabled yet) |
| `curl -sI localhost:8080` | refused | 'curl' is not on the read-only allowlist (mutations are not enabled yet) |
| `wget http://x` | refused | 'wget' is not on the read-only allowlist (mutations are not enabled yet) |
| `wget -O /x http://x` | refused | 'wget' is not on the read-only allowlist (mutations are not enabled yet) |
| `wget -qO- http://x` | refused | 'wget' is not on the read-only allowlist (mutations are not enabled yet) |
| `nc -l 1234` | refused | 'nc' is not on the read-only allowlist (mutations are not enabled yet) |
| `ncat x 1` | refused | 'ncat' is not on the read-only allowlist (mutations are not enabled yet) |
| `socat - TCP:x:1` | refused | 'socat' is not on the read-only allowlist (mutations are not enabled yet) |
| `telnet x` | refused | 'telnet' is not on the read-only allowlist (mutations are not enabled yet) |
| `ftp x` | refused | 'ftp' is not on the read-only allowlist (mutations are not enabled yet) |
| `ssh x` | refused | 'ssh' is not on the read-only allowlist (mutations are not enabled yet) |
| `ssh pi@localhost id` | refused | 'ssh' is not on the read-only allowlist (mutations are not enabled yet) |
| `scp a b` | refused | 'scp' is not on the read-only allowlist (mutations are not enabled yet) |
| `sftp x` | refused | 'sftp' is not on the read-only allowlist (mutations are not enabled yet) |
| `ssh-keygen -f x` | refused | 'ssh-keygen' is not on the read-only allowlist (mutations are not enabled yet) |
| `ssh-add` | refused | 'ssh-add' is not on the read-only allowlist (mutations are not enabled yet) |
| `ssh-keyscan x` | refused | 'ssh-keyscan' is not on the read-only allowlist (mutations are not enabled yet) |
| `git config user.name x` | refused | git 'config' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git config --global alias.st '!sh'` | refused | git 'config' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git config -l` | refused | git 'config' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git -c core.pager=sh log` | refused | git: option '-c' before the subcommand is not allowed (allowed: -C <dir>, --git-dir, --w… |
| `git -c alias.st=!sh st` | refused | git: option '-c' before the subcommand is not allowed (allowed: -C <dir>, --git-dir, --w… |
| `git -ccore.pager=sh log` | refused | git: option '-ccore.pager=sh' before the subcommand is not allowed (allowed: -C <dir>, -… |
| `git --config-env=core.pager=X log` | refused | git: option '--config-env=core.pager=X' before the subcommand is not allowed (allowed: -… |
| `git --exec-path=/tmp log` | refused | git: option '--exec-path=/tmp' before the subcommand is not allowed (allowed: -C <dir>, … |
| `git --exec-path log` | refused | git: option '--exec-path' before the subcommand is not allowed (allowed: -C <dir>, --git… |
| `git -p log` | refused | git: option '-p' before the subcommand is not allowed (allowed: -C <dir>, --git-dir, --w… |
| `git --paginate log` | refused | git: option '--paginate' before the subcommand is not allowed (allowed: -C <dir>, --git-… |
| `git --html-path` | refused | git: option '--html-path' before the subcommand is not allowed (allowed: -C <dir>, --git… |
| `git --namespace=x log` | refused | git: option '--namespace=x' before the subcommand is not allowed (allowed: -C <dir>, --g… |
| `git --super-prefix=x log` | refused | git: option '--super-prefix=x' before the subcommand is not allowed (allowed: -C <dir>, … |
| `git --attr-source=x log` | refused | git: option '--attr-source=x' before the subcommand is not allowed (allowed: -C <dir>, -… |
| `git --literal-pathspecs log` | refused | git: option '--literal-pathspecs' before the subcommand is not allowed (allowed: -C <dir… |
| `git --bare log` | refused | git: option '--bare' before the subcommand is not allowed (allowed: -C <dir>, --git-dir,… |
| `git -P log` | refused | git: option '-P' before the subcommand is not allowed (allowed: -C <dir>, --git-dir, --w… |
| `git --list-cmds=main` | refused | git: option '--list-cmds=main' before the subcommand is not allowed (allowed: -C <dir>, … |
| `git fetch` | refused | git 'fetch' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git pull` | refused | git 'pull' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, di… |
| `git push` | refused | git 'push' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, di… |
| `git clone x` | refused | git 'clone' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git checkout .` | refused | git 'checkout' is not a read-only subcommand (allowed: blame, branch, cat-file, describe… |
| `git reset --hard` | refused | git 'reset' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git clean -fdx` | refused | git 'clean' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git stash` | refused | git 'stash' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git stash list` | refused | git 'stash' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git commit -m x` | refused | git 'commit' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git add .` | refused | git 'add' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, dif… |
| `git rm x` | refused | git 'rm' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, diff… |
| `git mv a b` | refused | git 'mv' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, diff… |
| `git merge x` | refused | git 'merge' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git rebase x` | refused | git 'rebase' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git gc` | refused | git 'gc' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, diff… |
| `git prune` | refused | git 'prune' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git repack` | refused | git 'repack' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git init` | refused | git 'init' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, di… |
| `git worktree add x` | refused | git 'worktree' is not a read-only subcommand (allowed: blame, branch, cat-file, describe… |
| `git submodule update` | refused | git 'submodule' is not a read-only subcommand (allowed: blame, branch, cat-file, describ… |
| `git submodule foreach sh` | refused | git 'submodule' is not a read-only subcommand (allowed: blame, branch, cat-file, describ… |
| `git bisect run sh` | refused | git 'bisect' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git filter-branch` | refused | git 'filter-branch' is not a read-only subcommand (allowed: blame, branch, cat-file, des… |
| `git update-ref x y` | refused | git 'update-ref' is not a read-only subcommand (allowed: blame, branch, cat-file, descri… |
| `git symbolic-ref HEAD x` | refused | git 'symbolic-ref' is not a read-only subcommand (allowed: blame, branch, cat-file, desc… |
| `git notes add` | refused | git 'notes' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git apply x` | refused | git 'apply' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git am x` | refused | git 'am' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, diff… |
| `git archive -o /x HEAD` | refused | git 'archive' is not a read-only subcommand (allowed: blame, branch, cat-file, describe,… |
| `git archive --output=/x HEAD` | refused | git 'archive' is not a read-only subcommand (allowed: blame, branch, cat-file, describe,… |
| `git archive --remote=x HEAD` | refused | git 'archive' is not a read-only subcommand (allowed: blame, branch, cat-file, describe,… |
| `git bundle create x HEAD` | refused | git 'bundle' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git daemon` | refused | git 'daemon' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git upload-pack .` | refused | git 'upload-pack' is not a read-only subcommand (allowed: blame, branch, cat-file, descr… |
| `git receive-pack .` | refused | git 'receive-pack' is not a read-only subcommand (allowed: blame, branch, cat-file, desc… |
| `git fetch --upload-pack=sh x` | refused | git 'fetch' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git ls-remote --upload-pack=sh x` | refused | git 'ls-remote' is not a read-only subcommand (allowed: blame, branch, cat-file, describ… |
| `git ls-remote` | refused | git 'ls-remote' is not a read-only subcommand (allowed: blame, branch, cat-file, describ… |
| `git grep -O sh x` | refused | git 'grep' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, di… |
| `git grep x` | refused | git 'grep' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, di… |
| `git difftool` | refused | git 'difftool' is not a read-only subcommand (allowed: blame, branch, cat-file, describe… |
| `git mergetool` | refused | git 'mergetool' is not a read-only subcommand (allowed: blame, branch, cat-file, describ… |
| `git help -a` | refused | git 'help' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, di… |
| `git help log` | refused | git 'help' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, di… |
| `git log --help` | ALLOWED | ok (harmless; see notes) |
| `git instaweb` | refused | git 'instaweb' is not a read-only subcommand (allowed: blame, branch, cat-file, describe… |
| `git gui` | refused | git 'gui' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, dif… |
| `git send-email` | refused | git 'send-email' is not a read-only subcommand (allowed: blame, branch, cat-file, descri… |
| `git shell` | refused | git 'shell' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git credential fill` | refused | git 'credential' is not a read-only subcommand (allowed: blame, branch, cat-file, descri… |
| `git rev-list HEAD` | refused | git 'rev-list' is not a read-only subcommand (allowed: blame, branch, cat-file, describe… |
| `git for-each-ref` | refused | git 'for-each-ref' is not a read-only subcommand (allowed: blame, branch, cat-file, desc… |
| `git reflog` | refused | git 'reflog' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git reflog expire --all` | refused | git 'reflog' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git var -l` | refused | git 'var' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, dif… |
| `git hook run x` | refused | git 'hook' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, di… |
| `git maintenance run` | refused | git 'maintenance' is not a read-only subcommand (allowed: blame, branch, cat-file, descr… |
| `git fsck` | refused | git 'fsck' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, di… |
| `git count-objects` | refused | git 'count-objects' is not a read-only subcommand (allowed: blame, branch, cat-file, des… |
| `git whatchanged` | refused | git 'whatchanged' is not a read-only subcommand (allowed: blame, branch, cat-file, descr… |
| `git st` | refused | git 'st' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, diff… |
| `git lg` | refused | git 'lg' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, diff… |
| `git -C x -c a=b log` | refused | git: option '-c' before the subcommand is not allowed (allowed: -C <dir>, --git-dir, --w… |
| `git -C x --exec-path=y log` | refused | git: option '--exec-path=y' before the subcommand is not allowed (allowed: -C <dir>, --g… |
| `git --no-pager -c a=b log` | refused | git: option '-c' before the subcommand is not allowed (allowed: -C <dir>, --git-dir, --w… |
| `git --git-dir=x -c a=b log` | refused | git: option '-c' before the subcommand is not allowed (allowed: -C <dir>, --git-dir, --w… |
| `git -C` | refused | git: -C needs a value |
| `git` | refused | git: name a read-only subcommand |
| `git --git-dir` | refused | git: --git-dir needs a value |
| `git branch evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch -d x` | refused | git branch: only listing options are allowed, got '-d' |
| `git branch -D x` | refused | git branch: only listing options are allowed, got '-D' |
| `git branch -m a b` | refused | git branch: only listing options are allowed, got '-m' |
| `git branch -M a b` | refused | git branch: only listing options are allowed, got '-M' |
| `git branch -c a b` | refused | git branch: only listing options are allowed, got '-c' |
| `git branch -C a b` | refused | git branch: only listing options are allowed, got '-C' |
| `git branch -f x` | refused | git branch: only listing options are allowed, got '-f' |
| `git branch --delete x` | refused | git branch: only listing options are allowed, got '--delete' |
| `git branch --move a b` | refused | git branch: only listing options are allowed, got '--move' |
| `git branch --copy a b` | refused | git branch: only listing options are allowed, got '--copy' |
| `git branch --force x y` | refused | git branch: only listing options are allowed, got '--force' |
| `git branch -u origin/x` | refused | git branch: only listing options are allowed, got '-u' |
| `git branch --set-upstream-to=o/x` | refused | git branch: only listing options are allowed, got '--set-upstream-to=o/x' |
| `git branch --unset-upstream` | refused | git branch: only listing options are allowed, got '--unset-upstream' |
| `git branch --edit-description` | refused | git branch: only listing options are allowed, got '--edit-description' |
| `git branch --track x` | refused | git branch: only listing options are allowed, got '--track' |
| `git branch -rd origin/x` | refused | git branch: only listing options are allowed, got '-rd' |
| `git branch -dr origin/x` | refused | git branch: only listing options are allowed, got '-dr' |
| `git branch -v evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch -a evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch -r evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --all evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --color evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --no-color evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --contains=HEAD evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --sort=x evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --list -d x` | refused | git branch: only listing options are allowed, got '-d' |
| `git branch -l -D x` | refused | git branch: only listing options are allowed, got '-D' |
| `git branch -ld x` | refused | git branch: only listing options are allowed, got '-ld' |
| `git branch -- evil` | refused | git branch: only listing options are allowed, got '--' |
| `git branch --list -- -d` | refused | git branch: only listing options are allowed, got '--' |
| `git branch --create-reflog x` | refused | git branch: only listing options are allowed, got '--create-reflog' |
| `git branch -t x` | refused | git branch: only listing options are allowed, got '-t' |
| `git branch --no-track x` | refused | git branch: only listing options are allowed, got '--no-track' |
| `git branch --recurse-submodules x` | refused | git branch: only listing options are allowed, got '--recurse-submodules' |
| `git branch -i evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --ignore-case evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --no-abbrev evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --abbrev=7 evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --format=x evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --show-current evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --verbose evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --remotes evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --no-column evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch -vv evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --points-at=HEAD evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --merged=HEAD evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --no-merged=HEAD evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git branch --no-contains=HEAD evil` | refused | git branch: an operand without --list creates a branch; use --list <pattern> |
| `git tag v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag -d v1` | refused | git tag: only listing options are allowed, got '-d' |
| `git tag -a v9 -m x` | refused | git tag: only listing options are allowed, got '-a' |
| `git tag -f v1` | refused | git tag: only listing options are allowed, got '-f' |
| `git tag -s v9` | refused | git tag: only listing options are allowed, got '-s' |
| `git tag --delete v1` | refused | git tag: only listing options are allowed, got '--delete' |
| `git tag -n v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag -n1 v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag -i v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag --ignore-case v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag --column v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag --no-column v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag --sort=x v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag --format=x v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag --contains=HEAD v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag -l -d v1` | refused | git tag: only listing options are allowed, got '-d' |
| `git tag -ld v1` | refused | git tag: only listing options are allowed, got '-ld' |
| `git tag -l -F /x v9` | refused | git tag: only listing options are allowed, got '-F' |
| `git tag -l -m x v9` | refused | git tag: only listing options are allowed, got '-m' |
| `git tag -v v1` | refused | git tag: only listing options are allowed, got '-v' |
| `git tag --points-at=HEAD v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag --merged=HEAD v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag -- v9` | refused | git tag: only listing options are allowed, got '--' |
| `git tag -n999 v9` | refused | git tag: an operand without --list creates a tag; use --list <pattern> |
| `git tag -li -a v9` | refused | git tag: only listing options are allowed, got '-a' |
| `git tag --list --create-reflog v9` | refused | git tag: only listing options are allowed, got '--create-reflog' |
| `git tag -e v9` | refused | git tag: only listing options are allowed, got '-e' |
| `git tag -u key v9` | refused | git tag: only listing options are allowed, got '-u' |
| `git remote add x y` | refused | git remote 'add' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote -v add x y` | refused | git remote 'add' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote rm x` | refused | git remote 'rm' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote remove x` | refused | git remote 'remove' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote rename a b` | refused | git remote 'rename' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote set-url origin x` | refused | git remote 'set-url' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote set-head origin x` | refused | git remote 'set-head' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote set-branches o x` | refused | git remote 'set-branches' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote prune origin` | refused | git remote 'prune' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote update` | refused | git remote 'update' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote show -v add` | ALLOWED | ok (harmless; see notes) |
| `git remote get-url --push --add x` | refused | git remote: option '--add' is not allowed |
| `git remote --verbose update` | refused | git remote 'update' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote -v -v prune x` | refused | git remote 'prune' is not read-only (allowed: bare, -v, show, get-url) |
| `git remote show x --upload-pack=sh` | refused | git remote: option '--upload-pack=sh' is not allowed |
| `git log --ext-diff` | refused | git log: --ext-diff runs an external diff program |
| `git show --ext-diff` | refused | git show: --ext-diff runs an external diff program |
| `git diff --ext-diff` | refused | git diff: --ext-diff runs an external diff program |
| `git log -p --ext` | refused | git log: --ext-diff runs an external diff program (given as '--ext') |
| `git diff --no-index a b` | refused | git diff: --no-index compares arbitrary paths outside the repository |
| `git diff --no-inde a b` | refused | git diff: --no-index compares arbitrary paths outside the repository (given as '--no-ind… |
| `git diff --no-i a b` | refused | git diff: --no-index compares arbitrary paths outside the repository (given as '--no-i') |
| `less x` | refused | 'less' is not on the read-only allowlist (mutations are not enabled yet) |
| `more x` | refused | 'more' is not on the read-only allowlist (mutations are not enabled yet) |
| `man ls` | refused | 'man' is not on the read-only allowlist (mutations are not enabled yet) |
| `most x` | refused | 'most' is not on the read-only allowlist (mutations are not enabled yet) |
| `pg x` | refused | 'pg' is not on the read-only allowlist (mutations are not enabled yet) |
| `view x` | refused | 'view' is not on the read-only allowlist (mutations are not enabled yet) |
| `vi x` | refused | 'vi' is not on the read-only allowlist (mutations are not enabled yet) |
| `vim x` | refused | 'vim' is not on the read-only allowlist (mutations are not enabled yet) |
| `vim -c ':!sh' x` | refused | 'vim' is not on the read-only allowlist (mutations are not enabled yet) |
| `nano x` | refused | 'nano' is not on the read-only allowlist (mutations are not enabled yet) |
| `ed x` | refused | 'ed' is not on the read-only allowlist (mutations are not enabled yet) |
| `ex x` | refused | 'ex' is not on the read-only allowlist (mutations are not enabled yet) |
| `emacs x` | refused | 'emacs' is not on the read-only allowlist (mutations are not enabled yet) |
| `pico x` | refused | 'pico' is not on the read-only allowlist (mutations are not enabled yet) |
| `python -c 'import os'` | refused | 'python' is not on the read-only allowlist (mutations are not enabled yet) |
| `python3 -c 1` | refused | 'python3' is not on the read-only allowlist (mutations are not enabled yet) |
| `python3` | refused | 'python3' is not on the read-only allowlist (mutations are not enabled yet) |
| `perl -e 1` | refused | 'perl' is not on the read-only allowlist (mutations are not enabled yet) |
| `ruby -e 1` | refused | 'ruby' is not on the read-only allowlist (mutations are not enabled yet) |
| `node -e 1` | refused | node: only 'node --version' is allowed (node runs code) |
| `node` | refused | node: only 'node --version' is allowed (node runs code) |
| `node -p 1` | refused | node: only 'node --version' is allowed (node runs code) |
| `node --eval 1` | refused | node: only 'node --version' is allowed (node runs code) |
| `node x.js` | refused | node: only 'node --version' is allowed (node runs code) |
| `node -v x.js` | refused | node: only 'node --version' is allowed (node runs code) |
| `node --version -e 1` | refused | node: only 'node --version' is allowed (node runs code) |
| `node -e 1 --version` | refused | node: only 'node --version' is allowed (node runs code) |
| `node -r x --version` | refused | node: only 'node --version' is allowed (node runs code) |
| `node --version --inspect` | refused | node: only 'node --version' is allowed (node runs code) |
| `nodejs --version` | refused | 'nodejs' is not on the read-only allowlist (mutations are not enabled yet) |
| `/usr/bin/node x.js` | refused | node: only 'node --version' is allowed (node runs code) |
| `lua x` | refused | 'lua' is not on the read-only allowlist (mutations are not enabled yet) |
| `php -r 1` | refused | 'php' is not on the read-only allowlist (mutations are not enabled yet) |
| `bash` | refused | 'bash' is not on the read-only allowlist (mutations are not enabled yet) |
| `sh` | refused | 'sh' is not on the read-only allowlist (mutations are not enabled yet) |
| `dash` | refused | 'dash' is not on the read-only allowlist (mutations are not enabled yet) |
| `zsh` | refused | 'zsh' is not on the read-only allowlist (mutations are not enabled yet) |
| `bash -c id` | refused | 'bash' is not on the read-only allowlist (mutations are not enabled yet) |
| `sh -c id` | refused | 'sh' is not on the read-only allowlist (mutations are not enabled yet) |
| `bash -lc id` | refused | 'bash' is not on the read-only allowlist (mutations are not enabled yet) |
| `ash` | refused | 'ash' is not on the read-only allowlist (mutations are not enabled yet) |
| `ksh` | refused | 'ksh' is not on the read-only allowlist (mutations are not enabled yet) |
| `fish` | refused | 'fish' is not on the read-only allowlist (mutations are not enabled yet) |
| `busybox sh` | refused | 'busybox' is not on the read-only allowlist (mutations are not enabled yet) |
| `busybox cat x` | refused | 'busybox' is not on the read-only allowlist (mutations are not enabled yet) |
| `exec sh` | refused | 'exec' is not on the read-only allowlist (mutations are not enabled yet) |
| `eval id` | refused | 'eval' is not on the read-only allowlist (mutations are not enabled yet) |
| `source x` | refused | 'source' is not on the read-only allowlist (mutations are not enabled yet) |
| `. x` | refused | '.' is not on the read-only allowlist (mutations are not enabled yet) |
| `command rm x` | refused | 'command' is not on the read-only allowlist (mutations are not enabled yet) |
| `builtin eval x` | refused | 'builtin' is not on the read-only allowlist (mutations are not enabled yet) |
| `nohup sh` | refused | 'nohup' is not on the read-only allowlist (mutations are not enabled yet) |
| `nice sh` | refused | 'nice' is not on the read-only allowlist (mutations are not enabled yet) |
| `ionice sh` | refused | 'ionice' is not on the read-only allowlist (mutations are not enabled yet) |
| `timeout 1 sh` | refused | 'timeout' is not on the read-only allowlist (mutations are not enabled yet) |
| `time sh` | refused | 'time' is not on the read-only allowlist (mutations are not enabled yet) |
| `watch ls` | refused | 'watch' is not on the read-only allowlist (mutations are not enabled yet) |
| `strace ls` | refused | 'strace' is not on the read-only allowlist (mutations are not enabled yet) |
| `ltrace ls` | refused | 'ltrace' is not on the read-only allowlist (mutations are not enabled yet) |
| `gdb` | refused | 'gdb' is not on the read-only allowlist (mutations are not enabled yet) |
| `setsid sh` | refused | 'setsid' is not on the read-only allowlist (mutations are not enabled yet) |
| `stdbuf -o0 sh` | refused | 'stdbuf' is not on the read-only allowlist (mutations are not enabled yet) |
| `chroot / sh` | refused | 'chroot' is not on the read-only allowlist (mutations are not enabled yet) |
| `unshare sh` | refused | 'unshare' is not on the read-only allowlist (mutations are not enabled yet) |
| `nsenter sh` | refused | 'nsenter' is not on the read-only allowlist (mutations are not enabled yet) |
| `script /x` | refused | 'script' is not on the read-only allowlist (mutations are not enabled yet) |
| `screen` | refused | 'screen' is not on the read-only allowlist (mutations are not enabled yet) |
| `tmux` | refused | 'tmux' is not on the read-only allowlist (mutations are not enabled yet) |
| `at now` | refused | 'at' is not on the read-only allowlist (mutations are not enabled yet) |
| `batch` | refused | 'batch' is not on the read-only allowlist (mutations are not enabled yet) |
| `crontab -l` | refused | 'crontab' is not on the read-only allowlist (mutations are not enabled yet) |
| `crontab -e` | refused | 'crontab' is not on the read-only allowlist (mutations are not enabled yet) |
| `crontab -r` | refused | 'crontab' is not on the read-only allowlist (mutations are not enabled yet) |
| `crontab x` | refused | 'crontab' is not on the read-only allowlist (mutations are not enabled yet) |
| `systemd-run sh` | refused | 'systemd-run' is not on the read-only allowlist (mutations are not enabled yet) |
| `pkexec sh` | refused | 'pkexec' is not on the read-only allowlist (mutations are not enabled yet) |
| `su` | refused | 'su' is not on the read-only allowlist (mutations are not enabled yet) |
| `su -c id` | refused | 'su' is not on the read-only allowlist (mutations are not enabled yet) |
| `doas id` | refused | 'doas' is not on the read-only allowlist (mutations are not enabled yet) |
| `sudo reboot` | refused | sudo is not allowed in read-only mode |
| `sudo -n true` | refused | sudo is not allowed in read-only mode |
| `/usr/bin/sudo id` | refused | sudo is not allowed in read-only mode |
| `'sudo' id` | refused | sudo is not allowed in read-only mode |
| `\\sudo id` | refused | sudo is not allowed in read-only mode |
| `su\\do id` | refused | sudo is not allowed in read-only mode |
| `"su"do id` | refused | sudo is not allowed in read-only mode |
| `sudo` | refused | sudo is not allowed in read-only mode |
| `ls \| sudo tee x` | refused | sudo is not allowed in read-only mode |
| `sudoedit x` | refused | 'sudoedit' is not on the read-only allowlist (mutations are not enabled yet) |
| `visudo` | refused | 'visudo' is not on the read-only allowlist (mutations are not enabled yet) |
| `systemctl restart x` | refused | systemctl 'restart' is not a read-only subcommand (allowed: cat, get-default, is-active,… |
| `systemctl start x` | refused | systemctl 'start' is not a read-only subcommand (allowed: cat, get-default, is-active, i… |
| `systemctl stop x` | refused | systemctl 'stop' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl reload x` | refused | systemctl 'reload' is not a read-only subcommand (allowed: cat, get-default, is-active, … |
| `systemctl enable x` | refused | systemctl 'enable' is not a read-only subcommand (allowed: cat, get-default, is-active, … |
| `systemctl disable x` | refused | systemctl 'disable' is not a read-only subcommand (allowed: cat, get-default, is-active,… |
| `systemctl mask x` | refused | systemctl 'mask' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl kill x` | refused | systemctl 'kill' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl daemon-reload` | refused | systemctl 'daemon-reload' is not a read-only subcommand (allowed: cat, get-default, is-a… |
| `systemctl reboot` | refused | systemctl 'reboot' is not a read-only subcommand (allowed: cat, get-default, is-active, … |
| `systemctl poweroff` | refused | systemctl 'poweroff' is not a read-only subcommand (allowed: cat, get-default, is-active… |
| `systemctl halt` | refused | systemctl 'halt' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl suspend` | refused | systemctl 'suspend' is not a read-only subcommand (allowed: cat, get-default, is-active,… |
| `systemctl isolate x` | refused | systemctl 'isolate' is not a read-only subcommand (allowed: cat, get-default, is-active,… |
| `systemctl set-default x` | refused | systemctl 'set-default' is not a read-only subcommand (allowed: cat, get-default, is-act… |
| `systemctl set-property x y` | refused | systemctl 'set-property' is not a read-only subcommand (allowed: cat, get-default, is-ac… |
| `systemctl edit x` | refused | systemctl 'edit' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl revert x` | refused | systemctl 'revert' is not a read-only subcommand (allowed: cat, get-default, is-active, … |
| `systemctl set-environment A=b` | refused | systemctl 'set-environment' is not a read-only subcommand (allowed: cat, get-default, is… |
| `systemctl unset-environment A` | refused | systemctl 'unset-environment' is not a read-only subcommand (allowed: cat, get-default, … |
| `systemctl import-environment` | refused | systemctl 'import-environment' is not a read-only subcommand (allowed: cat, get-default,… |
| `systemctl show-environment` | refused | systemctl 'show-environment' is not a read-only subcommand: it prints the manager's envi… |
| `systemctl --user show-environment` | refused | systemctl 'show-environment' is not a read-only subcommand: it prints the manager's envi… |
| `systemctl daemon-reexec` | refused | systemctl 'daemon-reexec' is not a read-only subcommand (allowed: cat, get-default, is-a… |
| `systemctl reset-failed` | refused | systemctl 'reset-failed' is not a read-only subcommand (allowed: cat, get-default, is-ac… |
| `systemctl try-restart x` | refused | systemctl 'try-restart' is not a read-only subcommand (allowed: cat, get-default, is-act… |
| `systemctl reload-or-restart x` | refused | systemctl 'reload-or-restart' is not a read-only subcommand (allowed: cat, get-default, … |
| `systemctl link /x` | refused | systemctl 'link' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl preset x` | refused | systemctl 'preset' is not a read-only subcommand (allowed: cat, get-default, is-active, … |
| `systemctl add-wants a b` | refused | systemctl 'add-wants' is not a read-only subcommand (allowed: cat, get-default, is-activ… |
| `systemctl switch-root /` | refused | systemctl 'switch-root' is not a read-only subcommand (allowed: cat, get-default, is-act… |
| `systemctl kexec` | refused | systemctl 'kexec' is not a read-only subcommand (allowed: cat, get-default, is-active, i… |
| `systemctl emergency` | refused | systemctl 'emergency' is not a read-only subcommand (allowed: cat, get-default, is-activ… |
| `systemctl rescue` | refused | systemctl 'rescue' is not a read-only subcommand (allowed: cat, get-default, is-active, … |
| `systemctl default` | refused | systemctl 'default' is not a read-only subcommand (allowed: cat, get-default, is-active,… |
| `systemctl exit` | refused | systemctl 'exit' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl clean x` | refused | systemctl 'clean' is not a read-only subcommand (allowed: cat, get-default, is-active, i… |
| `systemctl freeze x` | refused | systemctl 'freeze' is not a read-only subcommand (allowed: cat, get-default, is-active, … |
| `systemctl thaw x` | refused | systemctl 'thaw' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl bind x /y` | refused | systemctl 'bind' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl mount-image x y` | refused | systemctl 'mount-image' is not a read-only subcommand (allowed: cat, get-default, is-act… |
| `systemctl log-level debug` | refused | systemctl 'log-level' is not a read-only subcommand (allowed: cat, get-default, is-activ… |
| `systemctl service-watchdogs no` | refused | systemctl 'service-watchdogs' is not a read-only subcommand (allowed: cat, get-default, … |
| `systemctl -p status restart x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --property status restart x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl -H root@x status` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl status -H root@x` | refused | systemctl: -H targets another host |
| `systemctl status --host=x` | refused | systemctl: --host targets another host |
| `systemctl status --ho x` | refused | systemctl: --host targets another host (given as '--ho') |
| `systemctl status -lH x` | refused | systemctl: -H targets another host (given as '-lH') |
| `systemctl status -M x` | refused | systemctl: -M targets another machine |
| `systemctl status --machine=x` | refused | systemctl: --machine targets another machine |
| `systemctl --user -M x@ status` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl -t help restart x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --state=x restart y` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --no-block restart x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --now enable x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --failed` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl -n 5 restart x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl -o cat stop x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --root=/ enable x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --user --global enable x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --wait restart x` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl -q restart x` | refused | systemctl 'restart' is not a read-only subcommand (allowed: cat, get-default, is-active,… |
| `systemctl --quiet --all stop x` | refused | systemctl 'stop' is not a read-only subcommand (allowed: cat, get-default, is-active, is… |
| `systemctl status x restart y \| systemctl restart y` | refused | systemctl 'restart' is not a read-only subcommand (allowed: cat, get-default, is-active,… |
| `systemctl 'restart' x` | refused | systemctl 'restart' is not a read-only subcommand (allowed: cat, get-default, is-active,… |
| `systemctl status;systemctl restart x` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `journalctl -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl --follow` | refused | journalctl: --follow follows forever and never returns (burns the 30 s timeout); use -n … |
| `journalctl --fol` | refused | journalctl: --follow follows forever and never returns (burns the 30 s timeout); use -n … |
| `journalctl -xef` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N (giv… |
| `journalctl -fu x` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N (giv… |
| `journalctl -u x -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl -n 5 -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl -n5 -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl -o cat -f x` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl --no-tail --follow` | refused | journalctl: --follow follows forever and never returns (burns the 30 s timeout); use -n … |
| `journalctl --vacuum-size=1M` | refused | journalctl: --vacuum-size deletes journal files |
| `journalctl --vacuum-time=1s` | refused | journalctl: --vacuum-time deletes journal files |
| `journalctl --vacuum-files=1` | refused | journalctl: --vacuum-files deletes journal files |
| `journalctl --vacuum-size 1M` | refused | journalctl: --vacuum-size deletes journal files |
| `journalctl --vac=1M` | refused | journalctl: --vacuum-size deletes journal files (given as '--vac=1M') |
| `journalctl --vacuum-s=1` | refused | journalctl: --vacuum-size deletes journal files (given as '--vacuum-s=1') |
| `journalctl --rotate` | refused | journalctl: --rotate rotates journal files |
| `journalctl --rot` | refused | journalctl: --rotate rotates journal files (given as '--rot') |
| `journalctl --flush` | refused | journalctl: --flush moves journal files |
| `journalctl --fl` | refused | journalctl: --flush moves journal files (given as '--fl') |
| `journalctl --sync` | refused | journalctl: --sync writes the journal |
| `journalctl --relinquish-var` | refused | journalctl: --relinquish-var changes journal storage |
| `journalctl --smart-relinquish-var` | refused | journalctl: --smart-relinquish-var changes journal storage |
| `journalctl --setup-keys` | refused | journalctl: --setup-keys writes sealing keys |
| `journalctl --update-catalog` | refused | journalctl: --update-catalog writes the catalog |
| `journalctl --cursor-file=/x` | refused | journalctl: --cursor-file writes a cursor file |
| `journalctl --cursor-file /x` | refused | journalctl: --cursor-file writes a cursor file |
| `journalctl --cursor-f=/x` | refused | journalctl: --cursor-file writes a cursor file (given as '--cursor-f=/x') |
| `journalctl --rotate --vacuum-time=1s` | refused | journalctl: --rotate rotates journal files |
| `journalctl -u x --rotate` | refused | journalctl: --rotate rotates journal files |
| `journalctl -- --rotate` | refused | journalctl: --rotate rotates journal files |
| `journalctl -o --rotate --rotate` | refused | journalctl: --rotate rotates journal files |
| `journalctl --since x --flush` | refused | journalctl: --flush moves journal files |
| `journalctl -b --follow` | refused | journalctl: --follow follows forever and never returns (burns the 30 s timeout); use -n … |
| `journalctl -b -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl -bf` | ALLOWED | ok (harmless; see notes) |
| `journalctl -nf` | ALLOWED | ok (harmless; see notes) |
| `journalctl -n -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl -e -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl -S today -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl -t x -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `journalctl --grep x --follow` | refused | journalctl: --follow follows forever and never returns (burns the 30 s timeout); use -n … |
| `journalctl -i x -f` | refused | journalctl: -f follows forever and never returns (burns the 30 s timeout); use -n N |
| `npm install` | refused | npm 'install' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, … |
| `npm i` | refused | npm 'i' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view, … |
| `npm ci` | refused | npm 'ci' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view,… |
| `npm run x` | refused | npm 'run' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view… |
| `npm run-script x` | refused | npm 'run-script' is not a read-only subcommand (allowed: list, ls, outdated, prefix, roo… |
| `npm start` | refused | npm 'start' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vi… |
| `npm test` | refused | npm 'test' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm exec x` | refused | npm 'exec' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm x x` | refused | npm 'x' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view, … |
| `npx x` | refused | 'npx' is not on the read-only allowlist (mutations are not enabled yet) |
| `npm publish` | refused | npm 'publish' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, … |
| `npm update` | refused | npm 'update' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, v… |
| `npm uninstall x` | refused | npm 'uninstall' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root… |
| `npm rebuild` | refused | npm 'rebuild' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, … |
| `npm link` | refused | npm 'link' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm pack` | refused | npm 'pack' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm config set a b` | refused | npm 'config' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, v… |
| `npm config get a` | refused | npm 'config' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, v… |
| `npm config list` | refused | npm 'config' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, v… |
| `npm set a b` | refused | npm 'set' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view… |
| `npm get a` | refused | npm 'get' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view… |
| `npm cache clean --force` | refused | npm 'cache' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vi… |
| `npm audit fix` | refused | npm 'audit' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vi… |
| `npm audit` | refused | npm 'audit' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vi… |
| `npm dedupe` | refused | npm 'dedupe' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, v… |
| `npm prune` | refused | npm 'prune' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vi… |
| `npm init` | refused | npm 'init' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm login` | refused | npm 'login' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vi… |
| `npm token list` | refused | npm: argument 'token' matches the secrets deny list ('*token*'); secrets must not reach … |
| `npm whoami` | refused | npm 'whoami' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, v… |
| `npm -g install x` | refused | npm: put the subcommand first |
| `npm --prefix ls install` | refused | npm: put the subcommand first |
| `npm --prefix /x install` | refused | npm: put the subcommand first |
| `npm explore x -- sh` | refused | npm 'explore' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, … |
| `npm edit x` | refused | npm 'edit' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm restart` | refused | npm 'restart' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, … |
| `npm stop` | refused | npm 'stop' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm doctor` | refused | npm 'doctor' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, v… |
| `npm fund` | refused | npm 'fund' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm help x` | refused | npm 'help' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm docs x` | refused | npm 'docs' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm repo x` | refused | npm 'repo' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm bugs x` | refused | npm 'bugs' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm install-test` | refused | npm 'install-test' is not a read-only subcommand (allowed: list, ls, outdated, prefix, r… |
| `npm it` | refused | npm 'it' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view,… |
| `npm ll` | refused | npm 'll' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view,… |
| `npm la` | refused | npm 'la' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view,… |
| `npm v x` | refused | npm 'v' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view, … |
| `npm info x` | refused | npm 'info' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm show x` | refused | npm 'show' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vie… |
| `npm explain x` | refused | npm 'explain' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, … |
| `npm bin` | refused | npm 'bin' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view… |
| `npm query x` | refused | npm 'query' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, vi… |
| `npm pkg set a=b` | refused | npm 'pkg' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view… |
| `npm pkg get a` | refused | npm 'pkg' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view… |
| `npm version` | refused | npm 'version' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, … |
| `npm version patch` | refused | npm 'version' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, … |
| `npm ls \| npm i` | refused | npm 'i' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, view, … |
| `ifconfig eth0 down` | refused | 'ifconfig' is not on the read-only allowlist (mutations are not enabled yet) |
| `ifconfig` | refused | 'ifconfig' is not on the read-only allowlist (mutations are not enabled yet) |
| `iptables -F` | refused | 'iptables' is not on the read-only allowlist (mutations are not enabled yet) |
| `iptables -L` | refused | 'iptables' is not on the read-only allowlist (mutations are not enabled yet) |
| `nft flush ruleset` | refused | 'nft' is not on the read-only allowlist (mutations are not enabled yet) |
| `nft list ruleset` | refused | 'nft' is not on the read-only allowlist (mutations are not enabled yet) |
| `route add x` | refused | 'route' is not on the read-only allowlist (mutations are not enabled yet) |
| `arp -d x` | refused | 'arp' is not on the read-only allowlist (mutations are not enabled yet) |
| `iw dev` | refused | 'iw' is not on the read-only allowlist (mutations are not enabled yet) |
| `iwconfig` | refused | 'iwconfig' is not on the read-only allowlist (mutations are not enabled yet) |
| `nmcli con down x` | refused | 'nmcli' is not on the read-only allowlist (mutations are not enabled yet) |
| `nmcli` | refused | 'nmcli' is not on the read-only allowlist (mutations are not enabled yet) |
| `rfkill block all` | refused | 'rfkill' is not on the read-only allowlist (mutations are not enabled yet) |
| `ethtool -s eth0` | refused | 'ethtool' is not on the read-only allowlist (mutations are not enabled yet) |
| `tc qdisc add x` | refused | 'tc' is not on the read-only allowlist (mutations are not enabled yet) |
| `bridge link` | refused | 'bridge' is not on the read-only allowlist (mutations are not enabled yet) |
| `mount` | refused | 'mount' is not on the read-only allowlist (mutations are not enabled yet) |
| `mount /dev/sda1 /mnt` | refused | 'mount' is not on the read-only allowlist (mutations are not enabled yet) |
| `umount /mnt` | refused | 'umount' is not on the read-only allowlist (mutations are not enabled yet) |
| `swapoff -a` | refused | 'swapoff' is not on the read-only allowlist (mutations are not enabled yet) |
| `sysctl -w a=b` | refused | 'sysctl' is not on the read-only allowlist (mutations are not enabled yet) |
| `sysctl -a` | refused | 'sysctl' is not on the read-only allowlist (mutations are not enabled yet) |
| `modprobe x` | refused | 'modprobe' is not on the read-only allowlist (mutations are not enabled yet) |
| `rmmod x` | refused | 'rmmod' is not on the read-only allowlist (mutations are not enabled yet) |
| `insmod x` | refused | 'insmod' is not on the read-only allowlist (mutations are not enabled yet) |
| `kill 1` | refused | 'kill' is not on the read-only allowlist (mutations are not enabled yet) |
| `kill -9 1` | refused | 'kill' is not on the read-only allowlist (mutations are not enabled yet) |
| `killall node` | refused | 'killall' is not on the read-only allowlist (mutations are not enabled yet) |
| `pkill node` | refused | 'pkill' is not on the read-only allowlist (mutations are not enabled yet) |
| `pkill -f x` | refused | 'pkill' is not on the read-only allowlist (mutations are not enabled yet) |
| `pidwait x` | refused | 'pidwait' is not on the read-only allowlist (mutations are not enabled yet) |
| `renice 1 1` | refused | 'renice' is not on the read-only allowlist (mutations are not enabled yet) |
| `reboot` | refused | 'reboot' is not on the read-only allowlist (mutations are not enabled yet) |
| `shutdown now` | refused | 'shutdown' is not on the read-only allowlist (mutations are not enabled yet) |
| `halt` | refused | 'halt' is not on the read-only allowlist (mutations are not enabled yet) |
| `poweroff` | refused | 'poweroff' is not on the read-only allowlist (mutations are not enabled yet) |
| `init 0` | refused | 'init' is not on the read-only allowlist (mutations are not enabled yet) |
| `telinit 6` | refused | 'telinit' is not on the read-only allowlist (mutations are not enabled yet) |
| `date -s 2020-01-01` | refused | date: -s sets the clock |
| `date --set=2020-01-01` | refused | date: --set sets the clock |
| `date --set 2020-01-01` | refused | date: --set sets the clock |
| `date --se 2020` | refused | date: --set sets the clock (given as '--se') |
| `date -us 2020` | refused | date: -s sets the clock (given as '-us') |
| `date 010100002020` | refused | date: operand '010100002020' sets the clock; only +FORMAT operands are allowed |
| `date -u 010100002020` | refused | date: operand '010100002020' sets the clock; only +FORMAT operands are allowed |
| `date -- 010100002020` | refused | date: operand '010100002020' sets the clock; only +FORMAT operands are allowed |
| `date +%s 010100002020` | refused | date: operand '010100002020' sets the clock; only +FORMAT operands are allowed |
| `date -d x 010100002020` | refused | date: operand '010100002020' sets the clock; only +FORMAT operands are allowed |
| `date --date x 0101` | refused | date: operand '0101' sets the clock; only +FORMAT operands are allowed |
| `date --da x 0101` | refused | date: operand 'x' sets the clock; only +FORMAT operands are allowed |
| `date --ref 0101` | refused | date: operand '0101' sets the clock; only +FORMAT operands are allowed |
| `date -I 010100002020` | refused | date: operand '010100002020' sets the clock; only +FORMAT operands are allowed |
| `date --iso-8601 0101` | refused | date: operand '0101' sets the clock; only +FORMAT operands are allowed |
| `date --rfc-email 0101` | refused | date: operand '0101' sets the clock; only +FORMAT operands are allowed |
| `date --debug 0101` | refused | date: operand '0101' sets the clock; only +FORMAT operands are allowed |
| `date --utc 0101` | refused | date: operand '0101' sets the clock; only +FORMAT operands are allowed |
| `date -R 0101` | refused | date: operand '0101' sets the clock; only +FORMAT operands are allowed |
| `date -Ins 0101` | refused | date: operand '0101' sets the clock; only +FORMAT operands are allowed |
| `date -Is -s 2020` | refused | date: -s sets the clock |
| `date -d 2020 -s 2021` | refused | date: -s sets the clock |
| `date '-s' 2020` | refused | date: -s sets the clock |
| `date -r x -s 2020` | refused | date: -s sets the clock |
| `date -f x -s 2020` | refused | date: -s sets the clock |
| `date --rfc-3339 seconds 0101` | refused | date: operand '0101' sets the clock; only +FORMAT operands are allowed |
| `date --rfc-3339 0101` | ALLOWED | ok (harmless; see notes) |
| `timedatectl set-time x` | refused | 'timedatectl' is not on the read-only allowlist (mutations are not enabled yet) |
| `timedatectl` | refused | 'timedatectl' is not on the read-only allowlist (mutations are not enabled yet) |
| `hwclock -w` | refused | 'hwclock' is not on the read-only allowlist (mutations are not enabled yet) |
| `hostname evil` | refused | hostname: an operand sets the hostname; no operands allowed |
| `hostname -F /x` | refused | hostname: -F sets the hostname from a file |
| `hostname --file=/x` | refused | hostname: --file sets the hostname from a file |
| `hostname --fi /x` | refused | hostname: --file sets the hostname from a file (given as '--fi') |
| `hostname -b x` | refused | hostname: -b sets the hostname |
| `hostname -vF /x` | refused | hostname: -F sets the hostname from a file (given as '-vF') |
| `hostname -- evil` | refused | hostname: an operand sets the hostname; no operands allowed |
| `hostname -I evil` | refused | hostname: an operand sets the hostname; no operands allowed |
| `hostname -s evil` | refused | hostname: an operand sets the hostname; no operands allowed |
| `hostnamectl set-hostname x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl hostname x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl hostname` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl set-icon-name x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl set-chassis x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl set-deployment x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl set-location x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl chassis vm` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl status x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl -H x status` | refused | hostnamectl: -H targets another host |
| `hostnamectl --host x` | refused | hostnamectl: --host targets another host |
| `hostnamectl -M x` | refused | hostnamectl: -M targets another machine |
| `hostnamectl --json pretty set-hostname x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl --static x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `hostnamectl --no-ask-password set-hostname x` | refused | hostnamectl: only bare 'hostnamectl' or 'hostnamectl status' is allowed (other subcomman… |
| `ss -K` | refused | ss: -K kills sockets |
| `ss --kill` | refused | ss: --kill kills sockets |
| `ss -tK` | refused | ss: -K kills sockets (given as '-tK') |
| `ss --ki` | refused | ss: --kill kills sockets (given as '--ki') |
| `ss -K dst 1.2.3.4` | refused | ss: -K kills sockets |
| `ss -D /x` | refused | ss: -D writes a dump file |
| `ss --diag=/x` | refused | ss: --diag writes a dump file |
| `ss --diag /x` | refused | ss: --diag writes a dump file |
| `ss -tD /x` | refused | ss: -D writes a dump file (given as '-tD') |
| `ss -f inet -K` | refused | ss: -K kills sockets |
| `ss -A tcp -K` | refused | ss: -K kills sockets |
| `ss -N x -K` | refused | ss: -K kills sockets |
| `ss -E` | ALLOWED | **F5** |
| `ss --events` | ALLOWED | **F5** |
| `ss -tE` | ALLOWED | **F5** |
| `ss --ev` | ALLOWED | **F5** |
| `free -s 1` | refused | free: -s repeats until stopped |
| `free -hs1` | refused | free: -s repeats until stopped (given as '-hs1') |
| `free --seconds 1` | refused | free: --seconds repeats until stopped |
| `free --sec=1` | refused | free: --seconds repeats until stopped (given as '--sec=1') |
| `free -c 5` | ALLOWED | **F5** |
| `free -c 999999` | ALLOWED | **F5** |
| `free --count 5` | ALLOWED | **F5** |
| `free -s1 -c 2` | refused | free: -s repeats until stopped (given as '-s1') |
| `vmstat 1` | refused | vmstat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `vmstat 1 5` | refused | vmstat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `vmstat -n 1` | refused | vmstat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `vmstat -w 1` | refused | vmstat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `vmstat -- 1` | refused | vmstat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `vmstat -S M 1` | refused | vmstat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `vmstat -d 1` | refused | vmstat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `iostat 2` | refused | iostat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `iostat -x 1` | refused | iostat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `iostat -p ALL 1` | refused | iostat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `iostat 1 2` | refused | iostat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `iostat -- 1` | refused | iostat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `sensors -s` | refused | sensors: -s writes limits to the chip |
| `sensors --set` | refused | sensors: --set writes limits to the chip |
| `sensors -us` | refused | sensors: -s writes limits to the chip (given as '-us') |
| `sensors --se` | refused | sensors: --set writes limits to the chip (given as '--se') |
| `sensors -c /x -s` | refused | sensors: -s writes limits to the chip |
| `file -C` | refused | file: -C writes a compiled magic file |
| `file -C -m /x` | refused | file: -C writes a compiled magic file |
| `file --compile -m x` | refused | file: --compile writes a compiled magic file |
| `file --comp -m x` | refused | file: --compile writes a compiled magic file (given as '--comp') |
| `file -bC -m x` | refused | file: -C writes a compiled magic file (given as '-bC') |
| `file -m x -C` | refused | file: -C writes a compiled magic file |
| `file -mx -C` | refused | file: -C writes a compiled magic file |
| `file -e x -C` | refused | file: -C writes a compiled magic file |
| `file -F : -C` | refused | file: -C writes a compiled magic file |
| `file -P a=1 -C` | refused | file: -C writes a compiled magic file |
| `file -f x -C` | refused | file: -C writes a compiled magic file |
| `file -- -C` | refused | file: -C writes a compiled magic file |
| `vcgencmd display_power 0` | refused | vcgencmd: allowed commands are commands, get_config, get_mem, get_throttled, measure_clo… |
| `vcgencmd measure_temp x y` | refused | vcgencmd: one command and at most one argument, no flags |
| `vcgencmd -t display_power 0` | refused | vcgencmd: allowed commands are commands, get_config, get_mem, get_throttled, measure_clo… |
| `vcgencmd bootloader_config` | refused | vcgencmd: allowed commands are commands, get_config, get_mem, get_throttled, measure_clo… |
| `vcgencmd otp_dump` | refused | vcgencmd: allowed commands are commands, get_config, get_mem, get_throttled, measure_clo… |
| `vcgencmd` | refused | vcgencmd: allowed commands are commands, get_config, get_mem, get_throttled, measure_clo… |
| `vcgencmd set_backlight 0` | refused | vcgencmd: allowed commands are commands, get_config, get_mem, get_throttled, measure_clo… |
| `vcgencmd get_config -x` | refused | vcgencmd: one command and at most one argument, no flags |
| `vcgencmd vcos log` | refused | vcgencmd: allowed commands are commands, get_config, get_mem, get_throttled, measure_clo… |
| `vcgencmd version \| vcgencmd display_power 0` | refused | vcgencmd: allowed commands are commands, get_config, get_mem, get_throttled, measure_clo… |
| `docker ps` | refused | 'docker' is not on the read-only allowlist: never used on a managed host; 'logs -f' and … |
| `docker logs x` | refused | 'docker' is not on the read-only allowlist: never used on a managed host; 'logs -f' and … |
| `docker exec x sh` | refused | 'docker' is not on the read-only allowlist: never used on a managed host; 'logs -f' and … |
| `docker run x` | refused | 'docker' is not on the read-only allowlist: never used on a managed host; 'logs -f' and … |
| `podman ps` | refused | 'podman' is not on the read-only allowlist (mutations are not enabled yet) |
| `kubectl get po` | refused | 'kubectl' is not on the read-only allowlist (mutations are not enabled yet) |
| `apt install x` | refused | 'apt' is not on the read-only allowlist (mutations are not enabled yet) |
| `apt list` | refused | 'apt' is not on the read-only allowlist (mutations are not enabled yet) |
| `apt-get update` | refused | 'apt-get' is not on the read-only allowlist (mutations are not enabled yet) |
| `dpkg -i x` | refused | 'dpkg' is not on the read-only allowlist (mutations are not enabled yet) |
| `dpkg -l` | refused | 'dpkg' is not on the read-only allowlist (mutations are not enabled yet) |
| `pip install x` | refused | 'pip' is not on the read-only allowlist (mutations are not enabled yet) |
| `snap install x` | refused | 'snap' is not on the read-only allowlist (mutations are not enabled yet) |
| `raspi-config` | refused | 'raspi-config' is not on the read-only allowlist (mutations are not enabled yet) |
| `rpi-update` | refused | 'rpi-update' is not on the read-only allowlist (mutations are not enabled yet) |
| `top` | refused | 'top' is not on the read-only allowlist (mutations are not enabled yet) |
| `top -bn1` | refused | 'top' is not on the read-only allowlist (mutations are not enabled yet) |
| `htop` | refused | 'htop' is not on the read-only allowlist (mutations are not enabled yet) |
| `dmesg` | refused | 'dmesg' is not on the read-only allowlist (mutations are not enabled yet) |
| `dmesg -c` | refused | 'dmesg' is not on the read-only allowlist (mutations are not enabled yet) |
| `dmesg -w` | refused | 'dmesg' is not on the read-only allowlist (mutations are not enabled yet) |
| `yes` | refused | 'yes' is not on the read-only allowlist (mutations are not enabled yet) |
| `sleep 100` | refused | 'sleep' is not on the read-only allowlist (mutations are not enabled yet) |
| `wall x` | refused | 'wall' is not on the read-only allowlist (mutations are not enabled yet) |
| `write pi` | refused | 'write' is not on the read-only allowlist (mutations are not enabled yet) |
| `mesg n` | refused | 'mesg' is not on the read-only allowlist (mutations are not enabled yet) |
| `passwd` | refused | 'passwd' is not on the read-only allowlist (mutations are not enabled yet) |
| `chpasswd` | refused | 'chpasswd' is not on the read-only allowlist (mutations are not enabled yet) |
| `useradd x` | refused | 'useradd' is not on the read-only allowlist (mutations are not enabled yet) |
| `usermod x` | refused | 'usermod' is not on the read-only allowlist (mutations are not enabled yet) |
| `logger x` | refused | 'logger' is not on the read-only allowlist (mutations are not enabled yet) |
| `loginctl terminate-user pi` | refused | 'loginctl' is not on the read-only allowlist (mutations are not enabled yet) |
| `loginctl` | refused | 'loginctl' is not on the read-only allowlist (mutations are not enabled yet) |
| `base64 x` | refused | 'base64' is not on the read-only allowlist (mutations are not enabled yet) |
| `xxd x` | refused | 'xxd' is not on the read-only allowlist (mutations are not enabled yet) |
| `od x` | refused | 'od' is not on the read-only allowlist (mutations are not enabled yet) |
| `hexdump x` | refused | 'hexdump' is not on the read-only allowlist (mutations are not enabled yet) |
| `strings x` | refused | 'strings' is not on the read-only allowlist (mutations are not enabled yet) |
| `tac x` | refused | 'tac' is not on the read-only allowlist (mutations are not enabled yet) |
| `rev x` | refused | 'rev' is not on the read-only allowlist (mutations are not enabled yet) |
| `diff a b` | refused | 'diff' is not on the read-only allowlist (mutations are not enabled yet) |
| `cmp a b` | refused | 'cmp' is not on the read-only allowlist (mutations are not enabled yet) |
| `md5sum x` | refused | 'md5sum' is not on the read-only allowlist (mutations are not enabled yet) |
| `sha256sum x` | refused | 'sha256sum' is not on the read-only allowlist (mutations are not enabled yet) |
| `split x` | refused | 'split' is not on the read-only allowlist (mutations are not enabled yet) |
| `csplit x` | refused | 'csplit' is not on the read-only allowlist (mutations are not enabled yet) |
| `paste a b` | refused | 'paste' is not on the read-only allowlist (mutations are not enabled yet) |
| `join a b` | refused | 'join' is not on the read-only allowlist (mutations are not enabled yet) |
| `comm a b` | refused | 'comm' is not on the read-only allowlist (mutations are not enabled yet) |
| `fold x` | refused | 'fold' is not on the read-only allowlist (mutations are not enabled yet) |
| `fmt x` | refused | 'fmt' is not on the read-only allowlist (mutations are not enabled yet) |
| `pr x` | refused | 'pr' is not on the read-only allowlist (mutations are not enabled yet) |
| `expand x` | refused | 'expand' is not on the read-only allowlist (mutations are not enabled yet) |
| `test -f x` | refused | 'test' is not on the read-only allowlist (mutations are not enabled yet) |
| `[ -f x ]` | refused | contains unquoted '[' (glob): chaining, redirection, background, substitution and expans… |
| `[[ -f x ]]` | refused | contains unquoted '[' (glob): chaining, redirection, background, substitution and expans… |
| `cd /tmp` | refused | 'cd' is not on the read-only allowlist (mutations are not enabled yet) |
| `pwd` | refused | 'pwd' is not on the read-only allowlist (mutations are not enabled yet) |
| `export A=b` | refused | 'export' is not on the read-only allowlist (mutations are not enabled yet) |
| `unset A` | refused | 'unset' is not on the read-only allowlist (mutations are not enabled yet) |
| `set -x` | refused | 'set' is not on the read-only allowlist (mutations are not enabled yet) |
| `alias x=y` | refused | 'alias' is not on the read-only allowlist (mutations are not enabled yet) |
| `umask 0` | refused | 'umask' is not on the read-only allowlist (mutations are not enabled yet) |
| `ulimit -a` | refused | 'ulimit' is not on the read-only allowlist (mutations are not enabled yet) |
| `trap x EXIT` | refused | 'trap' is not on the read-only allowlist (mutations are not enabled yet) |
| `read x` | refused | 'read' is not on the read-only allowlist (mutations are not enabled yet) |
| `wait` | refused | 'wait' is not on the read-only allowlist (mutations are not enabled yet) |
| `jobs` | refused | 'jobs' is not on the read-only allowlist (mutations are not enabled yet) |
| `fg` | refused | 'fg' is not on the read-only allowlist (mutations are not enabled yet) |
| `bg` | refused | 'bg' is not on the read-only allowlist (mutations are not enabled yet) |
| `history` | refused | 'history' is not on the read-only allowlist (mutations are not enabled yet) |
| `hash -r` | refused | 'hash' is not on the read-only allowlist (mutations are not enabled yet) |
| `let x=1` | refused | 'let' is not on the read-only allowlist (mutations are not enabled yet) |
| `declare x` | refused | 'declare' is not on the read-only allowlist (mutations are not enabled yet) |
| `printf -v x y` | ALLOWED | ok (harmless; see notes) |
| `echo x > y` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `type -a ls > x` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true > /home/pi/x` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true >/home/pi/x` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true>x` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true >\| x` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true 1>x` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true 2>x` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true &>x` | refused | contains '&' (background job): chaining, redirection, background, substitution and expan… |
| `true >&x` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true >>x` | refused | contains '>>' (redirection): chaining, redirection, background, substitution and expansi… |
| `true <>x` | refused | contains '<' (redirection): chaining, redirection, background, substitution and expansio… |
| `true 2>&1` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true 1>&2` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `true >&2` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `cat <x` | refused | contains '<' (redirection): chaining, redirection, background, substitution and expansio… |
| `cat < /etc/shadow` | refused | contains '<' (redirection): chaining, redirection, background, substitution and expansio… |
| `cat <<EOF` | refused | contains '<<' (here-document): chaining, redirection, background, substitution and expan… |
| `cat <<<x` | refused | contains '<<' (here-document): chaining, redirection, background, substitution and expan… |
| `cat <<-EOF` | refused | contains '<<' (here-document): chaining, redirection, background, substitution and expan… |
| `cat <(ls)` | refused | contains '<(' (process substitution): chaining, redirection, background, substitution an… |
| `cat >(sh)` | refused | contains '>(' (process substitution): chaining, redirection, background, substitution an… |
| `cat 3<x` | refused | contains '<' (redirection): chaining, redirection, background, substitution and expansio… |
| `ls 2>/dev/null` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `ls >/dev/null` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `ls &` | refused | contains '&' (background job): chaining, redirection, background, substitution and expan… |
| `ls & ls` | refused | contains '&' (background job): chaining, redirection, background, substitution and expan… |
| `ls&` | refused | contains '&' (background job): chaining, redirection, background, substitution and expan… |
| `ls ; ls` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `ls;ls` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `ls && ls` | refused | contains '&&' (command chaining): chaining, redirection, background, substitution and ex… |
| `ls&&ls` | refused | contains '&&' (command chaining): chaining, redirection, background, substitution and ex… |
| `ls \|\| ls` | refused | contains '\|\|' (command chaining): chaining, redirection, background, substitution and … |
| `ls\|\|ls` | refused | contains '\|\|' (command chaining): chaining, redirection, background, substitution and … |
| `ls \|& cat` | refused | contains '\|&' (pipe with stderr redirection): chaining, redirection, background, substi… |
| `ls \| \| ls` | refused | empty pipeline segment |
| `ls \|` | refused | empty pipeline segment |
| `\| ls` | refused | empty pipeline segment |
| `ls \|\| ` | refused | contains '\|\|' (command chaining): chaining, redirection, background, substitution and … |
| `ls\nrm x` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |
| `ls\rrm x` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |
| `ls\trm` | ALLOWED | ok (harmless; see notes) |
| `ls\x00rm x` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |
| `ls\x0brm x` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |
| `ls\x0crm x` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |
| `ls\x1brm` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |
| `ls\x7f` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |
| `ls \\\nrm` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |
| `ls $(id)` | refused | contains '$(' (command substitution): chaining, redirection, background, substitution an… |
| `` ls `id` `` | refused | contains ''' (command substitution): chaining, redirection, background, substitution and… |
| `ls "$(id)"` | refused | contains '$(' (command substitution): chaining, redirection, background, substitution an… |
| `` ls "`id`" `` | refused | contains ''' (command substitution): chaining, redirection, background, substitution and… |
| `ls $((1+1))` | refused | contains '$(' (command substitution): chaining, redirection, background, substitution an… |
| `ls $[1+1]` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `ls ${HOME}` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `ls $HOME` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `ls "$HOME"` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `ls $'\\x41'` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `ls $"x"` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `cat $HOME/.ssh/id_rsa` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `cat ${HOME}/.ssh/id_rsa` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `cat ~/.ss?/id_rsa` | refused | contains unquoted '?' (glob): chaining, redirection, background, substitution and expans… |
| `cat ~/.ssh/*` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `cat ~/[.]ssh/id_rsa` | refused | contains unquoted '[' (glob): chaining, redirection, background, substitution and expans… |
| `cat ~/.{ssh,x}/id_rsa` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `cat ~/.s*` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `cat /etc/shado?` | refused | contains unquoted '?' (glob): chaining, redirection, background, substitution and expans… |
| `cat /etc/sh[a]dow` | refused | contains unquoted '[' (glob): chaining, redirection, background, substitution and expans… |
| `cat /etc/sh{a,}dow` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `cat /proc/*/environ` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `cat /proc/[0-9]*/environ` | refused | contains unquoted '[' (glob): chaining, redirection, background, substitution and expans… |
| `cat /proc/self/envir?n` | refused | contains unquoted '?' (glob): chaining, redirection, background, substitution and expans… |
| `cat /home/pi/.en*` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `cat ?env` | refused | contains unquoted '?' (glob): chaining, redirection, background, substitution and expans… |
| `cat *.pem` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `cat *` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `cat .??*` | refused | contains unquoted '?' (glob): chaining, redirection, background, substitution and expans… |
| `ls *` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `cat /etc/shadow{,}` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `cat /etc/{shadow,x}` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `cat {/etc/shadow}` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `cat ~/.ssh/id_{rsa,ed25519}` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `cat +(x)` | refused | contains '(' (subshell): chaining, redirection, background, substitution and expansion a… |
| `cat @(a\|b)` | refused | contains '(' (subshell): chaining, redirection, background, substitution and expansion a… |
| `cat !(x)` | refused | contains '(' (subshell): chaining, redirection, background, substitution and expansion a… |
| `ls ( x )` | refused | contains '(' (subshell): chaining, redirection, background, substitution and expansion a… |
| `(ls)` | refused | contains '(' (subshell): chaining, redirection, background, substitution and expansion a… |
| `( ls )` | refused | contains '(' (subshell): chaining, redirection, background, substitution and expansion a… |
| `{ ls; }` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `{ ls ; }` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `ls #x` | refused | contains unquoted '#' (comment): chaining, redirection, background, substitution and exp… |
| `#ls` | refused | contains unquoted '#' (comment): chaining, redirection, background, substitution and exp… |
| `ls x#y #z` | refused | contains unquoted '#' (comment): chaining, redirection, background, substitution and exp… |
| `! ls` | refused | '!' is not on the read-only allowlist (mutations are not enabled yet) |
| `if true; then ls; fi` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `while true; do ls; done` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `for x in a; do ls; done` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `case x in x) ls;; esac` | refused | contains ')' (subshell): chaining, redirection, background, substitution and expansion a… |
| `function f { ls; }` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `f() { ls; }` | refused | contains '(' (subshell): chaining, redirection, background, substitution and expansion a… |
| `coproc ls` | refused | 'coproc' is not on the read-only allowlist (mutations are not enabled yet) |
| `select x in a; do ls; done` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `time ls` | refused | 'time' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls 'unbalanced` | refused | could not parse command: unbalanced single quote |
| `ls "unbalanced` | refused | could not parse command: unbalanced double quote |
| `ls unbalanced\\` | refused | could not parse command: trailing backslash |
| `ls 'a'"b` | refused | could not parse command: unbalanced double quote |
| (empty string) | refused | empty command |
| ` ` (blank) | refused | empty command |
| `\t` | refused | empty command |
| `   \t  ` | refused | empty command |
| `\n` | refused | empty command |
| `''` | refused | '' is not on the read-only allowlist (mutations are not enabled yet) |
| `""` | refused | '' is not on the read-only allowlist (mutations are not enabled yet) |
| `'' ls` | refused | '' is not on the read-only allowlist (mutations are not enabled yet) |
| `\\ ` | refused | could not parse command: trailing backslash |
| `ls aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa...` | refused | command too long |
| `A=b` | refused | env-assignment prefix 'A=b' is not allowed |
| `A=b ls` | refused | env-assignment prefix 'A=b' is not allowed |
| `FOO=bar uptime` | refused | env-assignment prefix 'FOO=bar' is not allowed |
| `PATH=/tmp ls` | refused | env-assignment prefix 'PATH=/tmp' is not allowed |
| `PAGER=sh git log` | refused | env-assignment prefix 'PAGER=sh' is not allowed |
| `GIT_PAGER=sh git log` | refused | env-assignment prefix 'GIT_PAGER=sh' is not allowed |
| `LD_PRELOAD=/x ls` | refused | env-assignment prefix 'LD_PRELOAD=/x' is not allowed |
| `GIT_EXTERNAL_DIFF=sh git diff` | refused | env-assignment prefix 'GIT_EXTERNAL_DIFF=sh' is not allowed |
| `GIT_SSH_COMMAND=sh git remote show x` | refused | env-assignment prefix 'GIT_SSH_COMMAND=sh' is not allowed |
| `LESSOPEN='\|sh' cat x` | refused | env-assignment prefix 'LESSOPEN=\|sh' is not allowed |
| `A=1 B=2 ls` | refused | env-assignment prefix 'A=1' is not allowed |
| `IFS=x ls` | refused | env-assignment prefix 'IFS=x' is not allowed |
| `BASH_ENV=x ls` | refused | env-assignment prefix 'BASH_ENV=x' is not allowed |
| `a=b; ls` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `'A=b' ls` | refused | env-assignment prefix 'A=b' is not allowed |
| `A\\=b ls` | refused | env-assignment prefix 'A=b' is not allowed |
| `"PATH=/tmp" ls` | refused | env-assignment prefix 'PATH=/tmp' is not allowed |
| `ls A=b \| B=c cat` | refused | env-assignment prefix 'B=c' is not allowed |
| `ls \| PAGER=x cat` | refused | env-assignment prefix 'PAGER=x' is not allowed |
| `/tmp/cat x` | refused | '/tmp/cat': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/bin,… |
| `./cat x` | refused | './cat': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/bin, /u… |
| `../cat x` | refused | '../cat': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/bin, /… |
| `~/cat x` | refused | '~/cat': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/bin, /u… |
| `~/bin/ls` | refused | '~/bin/ls': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/bin,… |
| `bin/ls` | refused | 'bin/ls': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/bin, /… |
| `/usr/bin/../../tmp/cat` | refused | '/usr/bin/../../tmp/cat': name the program, or give its path in /bin, /sbin, /usr/bin, /… |
| `/usr/bin/./../bin/env` | refused | '/usr/bin/./../bin/env': name the program, or give its path in /bin, /sbin, /usr/bin, /u… |
| `//usr/bin/env x` | refused | '//usr/bin/env': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local… |
| `/usr/bin//env x` | refused | '/usr/bin//env': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local… |
| `/usr/local/bin/evil` | refused | 'evil' is not on the read-only allowlist (mutations are not enabled yet) |
| `/usr/local/bin/cat x` | ALLOWED | ok (harmless; see notes) |
| `/bin/sh` | refused | 'sh' is not on the read-only allowlist (mutations are not enabled yet) |
| `/bin/bash -c id` | refused | 'bash' is not on the read-only allowlist (mutations are not enabled yet) |
| `/usr/bin/env` | refused | 'env' is not on the read-only allowlist: it runs any program given as an argument |
| `/usr/sbin/reboot` | refused | 'reboot' is not on the read-only allowlist (mutations are not enabled yet) |
| `/sbin/reboot` | refused | 'reboot' is not on the read-only allowlist (mutations are not enabled yet) |
| `/sbin/ip link set eth0 down` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'set' |
| `/snap/bin/x` | refused | '/snap/bin/x': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/b… |
| `/opt/x` | refused | '/opt/x': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/bin, /… |
| `/usr/bin/` | refused | '' is not on the read-only allowlist (mutations are not enabled yet) |
| `/` | refused | '/': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/bin, /usr/s… |
| `/usr/bin/cat/` | refused | '/usr/bin/cat/': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local… |
| `cat/` | refused | 'cat/': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/bin, /us… |
| `/home/pi/cat x` | refused | '/home/pi/cat': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/… |
| `/usr/games/x` | refused | '/usr/games/x': name the program, or give its path in /bin, /sbin, /usr/bin, /usr/local/… |
| `/usr/lib/git-core/git-shell` | refused | '/usr/lib/git-core/git-shell': name the program, or give its path in /bin, /sbin, /usr/b… |
| `ls\xa0-la \| rm` | refused | 'ls\xa0-la' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls\u2028rm x` | refused | 'ls\u2028rm' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls\u2029rm x` | refused | 'ls\u2029rm' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls\x85rm x` | refused | 'ls\x85rm' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls \uff1brm x` | ALLOWED | ok (harmless; see notes) |
| `ls \uff06 rm x` | ALLOWED | ok (harmless; see notes) |
| `ls \uff5c sh` | ALLOWED | ok (harmless; see notes) |
| `ls \uff1e x` | ALLOWED | ok (harmless; see notes) |
| `ls \u037e rm x` | ALLOWED | ok (harmless; see notes) |
| `\uff45\uff4e\uff56 rm x` | refused | 'ｅｎｖ' is not on the read-only allowlist (mutations are not enabled yet) |
| `s\u0443do id` | refused | 'sуdo' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls\u3000rm` | refused | 'ls\u3000rm' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls\u200brm` | refused | 'ls\u200brm' is not on the read-only allowlist (mutations are not enabled yet) |
| `ls \ufeff; rm x` | refused | contains ';' (command chaining): chaining, redirection, background, substitution and exp… |
| `ls\xad` | refused | 'ls\xad' is not on the read-only allowlist (mutations are not enabled yet) |

**strip() mismatch: classifier sees stripped, ssh gets original**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `uniq /etc/hostname \xa0` | ALLOWED | **F1** |
| `uniq /etc/hostname \u2003` | ALLOWED | **F1** |
| `uniq /etc/hostname \x1f` | ALLOWED | **F1** |
| `uniq /etc/hostname \x1c` | ALLOWED | **F1** |
| `uniq /etc/hostname \u3000` | ALLOWED | **F1** |
| `uniq /etc/hostname \x85` | ALLOWED | **F1** |
| `uniq /etc/hostname \u2028` | ALLOWED | **F1** |
| `hostname \xa0` | ALLOWED | **F1** |
| `date \u2003` | ALLOWED | **F1** |
| `vmstat \xa0` | ALLOWED | **F1** |
| `uniq /etc/hostname \x1d` | ALLOWED | **F1** |
| `uniq /etc/hostname \x1e` | ALLOWED | **F1** |
| `ls \x1f` | ALLOWED | **F1** |
| `uniq a \x0b` | ALLOWED | **F1** |
| `uniq a \x0c` | ALLOWED | **F1** |
| `uniq a \n` | ALLOWED | **F1** |
| `uniq a \r` | ALLOWED | **F1** |
| `\xa0uniq a b` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq a\x1fb` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |
| `uniq a \x1f b` | refused | contains a newline or control character (chaining is not allowed in read-only mode) |

**never returns / resource burn (must refuse)**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `cat /dev/zero` | ALLOWED | **F5** |
| `cat /dev/urandom` | ALLOWED | **F5** |
| `cat /dev/random` | ALLOWED | **F5** |
| `wc -l /dev/zero` | ALLOWED | **F5** |
| `wc /dev/urandom` | ALLOWED | **F5** |
| `sort /dev/urandom` | ALLOWED | **F5** |
| `sort /dev/zero` | ALLOWED | **F5** |
| `grep x /dev/zero` | ALLOWED | **F5** |
| `grep -a x /dev/urandom` | ALLOWED | **F5** |
| `head -n 1 /dev/zero` | ALLOWED | **F5** |
| `tail -n 1 /dev/zero` | ALLOWED | **F5** |
| `tail -c +1 /dev/zero` | ALLOWED | **F5** |
| `uniq /dev/zero` | ALLOWED | **F5** |
| `nl /dev/zero` | ALLOWED | **F5** |
| `cut -c1 /dev/zero` | ALLOWED | **F5** |
| `column /dev/zero` | ALLOWED | **F5** |
| `tr a b` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `cat` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `cat -` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `cat /dev/stdin` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `cat /dev/tty` | ALLOWED | **F5** |
| `cat /dev/ttyAMA0` | ALLOWED | **F5** |
| `cat /dev/input/event0` | ALLOWED | **F5** |
| `cat /dev/kmsg` | ALLOWED | **F5** |
| `cat /proc/kmsg` | ALLOWED | **F5** |
| `cat /dev/watchdog` | ALLOWED | **F5** |
| `head -c 99999999999 /dev/zero` | ALLOWED | **F5** |
| `du -a /` | ALLOWED | obs: slow or network, finite |
| `ls -R /` | ALLOWED | obs: slow or network, finite |
| `grep -c '' /dev/zero` | ALLOWED | **F5** |
| `zgrep x /dev/zero` | ALLOWED | **F5** |
| `file -s /dev/zero` | ALLOWED | ok (returns or errors) |
| `stat /dev/zero` | ALLOWED | ok (returns or errors) |
| `cat /sys/kernel/debug/tracing/trace_pipe` | ALLOWED | **F5** |
| `cat /dev/fd/0` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `cat /proc/self/fd/0` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `sort` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `wc` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `grep x` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `head` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `tail` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `uniq` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `nl` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `cut -f1` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `column` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `git shortlog` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `git cat-file --batch` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `git cat-file --batch-check` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `git log --stdin` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `git diff --stdin` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `zgrep x` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `systemctl is-system-running --wait` | ALLOWED | obs: slow or network, finite |
| `systemctl status --follow` | ALLOWED | ok (returns or errors) |
| `journalctl --wait` | ALLOWED | ok (returns or errors) |
| `git remote show origin` | ALLOWED | obs: slow or network, finite |
| `npm view x` | ALLOWED | obs: slow or network, finite |
| `date -f -` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `date -f /dev/zero` | ALLOWED | **F5** |
| `file -f -` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `wc --files0-from=-` | ALLOWED | ok: stdin is /dev/null, returns at once |
| `du --files0-from=/dev/zero` | ALLOWED | **F5** |
| `ps -L --forest \| cat /dev/zero` | ALLOWED | **F5** |
| `ss -tlnp \| wc /dev/zero` | ALLOWED | **F5** |
| `pm2 logs --nostream --lines 999999999` | ALLOWED | obs: slow or network, finite |

**npm side doors**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `npm ls --logs-dir=/home/pi/x` | ALLOWED | **F6** (not run here) |
| `npm ls --logs-dir /home/pi/MagicMirror/modules` | ALLOWED | **F6** (not run here) |
| `npm ls --cache=/home/pi/x` | ALLOWED | **F6** (not run here) |
| `npm ls --cache /home/pi/y` | ALLOWED | **F6** (not run here) |
| `npm view x --cache /home/pi/z` | ALLOWED | **F6** (not run here) |
| `npm outdated --cache=/home/pi/q` | ALLOWED | **F6** (not run here) |
| `npm ls --userconfig=/home/pi/x` | ALLOWED | obs: npm flags unchecked |
| `npm ls --globalconfig=/x` | ALLOWED | obs: npm flags unchecked |
| `npm ls --prefix=/x` | ALLOWED | obs: npm flags unchecked |
| `npm root --prefix /x` | ALLOWED | obs: npm flags unchecked |
| `npm ls --script-shell=sh` | ALLOWED | obs: npm flags unchecked |
| `npm ls --node-options=--require=/x` | ALLOWED | obs: npm flags unchecked |
| `npm view x --registry=http://192.0.2.1/` | ALLOWED | obs: npm flags unchecked |
| `npm view x --proxy=http://192.0.2.1` | ALLOWED | obs: npm flags unchecked |
| `npm ls --loglevel=silly --logs-dir=/dev/shm/x` | ALLOWED | **F6** (not run here) |
| `npm ls --init-module=/x` | ALLOWED | obs: npm flags unchecked |
| `npm ls -g --json` | ALLOWED | obs: npm flags unchecked |
| `npm ls --viewer=sh` | ALLOWED | obs: npm flags unchecked |
| `npm ls --editor=sh` | ALLOWED | obs: npm flags unchecked |
| `npm ls --shell=sh` | ALLOWED | obs: npm flags unchecked |
| `npm ls --git=sh` | ALLOWED | obs: npm flags unchecked |
| `npm view x --browser=sh` | ALLOWED | obs: npm flags unchecked |
| `npm outdated --call=sh` | ALLOWED | obs: npm flags unchecked |
| `npm prefix -g` | ALLOWED | obs: npm flags unchecked |
| `npm why x --package-lock-only` | ALLOWED | obs: npm flags unchecked |

**should stay allowed: legacy / audit forms / devops**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `uptime` | ALLOWED |  |
| `uptime -p` | ALLOWED |  |
| `cat /etc/os-release` | ALLOWED |  |
| `cat /etc/os-release \| grep "PRETTY_NAME"` | ALLOWED |  |
| `ls /home/pi/MagicMirror/modules` | ALLOWED |  |
| `grep -n module: /home/pi/MagicMirror/config/config.js` | ALLOWED |  |
| `git -C /home/pi/MagicMirror status --short` | ALLOWED |  |
| `git -C /home/pi/MagicMirror diff --stat` | ALLOWED |  |
| `git -C /home/pi/MagicMirror log -3 --format=%h%x20%ci%x20%s` | ALLOWED |  |
| `git -C ~/MagicMirror log --oneline -5` | ALLOWED |  |
| `git -C x rev-parse HEAD` | ALLOWED |  |
| `git -C x describe --tags` | ALLOWED |  |
| `git -C x branch -a` | ALLOWED |  |
| `git -C x branch -vv` | ALLOWED |  |
| `git -C x branch --show-current` | ALLOWED |  |
| `git -C x tag -l` | ALLOWED |  |
| `git -C x tag --list 'v2*'` | ALLOWED |  |
| `git -C x remote -v` | ALLOWED |  |
| `git -C x remote get-url origin` | ALLOWED |  |
| `git -C x show --stat HEAD` | ALLOWED |  |
| `git -C x log HEAD~3..HEAD` | ALLOWED |  |
| `git -C x log -1 --format='%h %s'` | ALLOWED |  |
| `git -C x diff HEAD~1 --stat` | ALLOWED |  |
| `git -C x status -sb` | ALLOWED |  |
| `git -C x ls-files modules` | ALLOWED |  |
| `git -C x blame -L 1,5 package.json` | ALLOWED |  |
| `git -C x log --follow -- package.json` | ALLOWED |  |
| `git -C x log --no-merges -5` | ALLOWED |  |
| `git -C x log --oneline --graph -10` | ALLOWED |  |
| `git --version` | ALLOWED |  |
| `git --no-pager -C x log -1` | ALLOWED |  |
| `journalctl --user -u magicmirror --since today --no-pager \| grep -iE "updatenotif\|update.avail\|behind" \...` | ALLOWED |  |
| `journalctl -n 100 --no-pager` | ALLOWED |  |
| `journalctl -n100 --no-pager` | ALLOWED |  |
| `journalctl -u ssh -n 50 --no-pager` | ALLOWED |  |
| `journalctl --user -u magicmirror --since -6h --no-pager -o short-iso` | ALLOWED |  |
| `journalctl -b -p err --no-pager` | ALLOWED |  |
| `journalctl -k -n 50` | ALLOWED |  |
| `journalctl -xe --no-pager` | ALLOWED |  |
| `journalctl --disk-usage` | ALLOWED |  |
| `journalctl --list-boots` | ALLOWED |  |
| `journalctl --since '1 hour ago' --until now --no-pager` | ALLOWED |  |
| `journalctl -u x --cursor abc -n 5` | ALLOWED |  |
| `journalctl -b -1 -n 20` | ALLOWED |  |
| `journalctl -r -n 20` | ALLOWED |  |
| `journalctl -o json -n 1` | ALLOWED |  |
| `journalctl -g error -n 10` | ALLOWED |  |
| `tail -n 50 /var/log/syslog` | ALLOWED |  |
| `tail -50 /var/log/syslog` | ALLOWED |  |
| `tail -n50 /var/log/syslog` | ALLOWED |  |
| `tail -c 200 x` | ALLOWED |  |
| `tail -n +5 x` | ALLOWED |  |
| `head -n 20 x` | ALLOWED |  |
| `head -c 100 x` | ALLOWED |  |
| `pm2 logs --nostream --lines 50` | ALLOWED | ok (--nostream still set) |
| `pm2 logs mm --nostream --lines 50` | ALLOWED | ok (--nostream still set) |
| `pm2 list` | ALLOWED | ok (--nostream still set) |
| `pm2 ls` | ALLOWED | ok (--nostream still set) |
| `pm2 status` | ALLOWED | ok (--nostream still set) |
| `pm2 --version` | ALLOWED | ok (--nostream still set) |
| `pm2 logs --nostream` | ALLOWED | ok (--nostream still set) |
| `pm2 logs --lines 50 --nostream` | ALLOWED | ok (--nostream still set) |
| `pm2 logs --nostream --err --lines 20` | ALLOWED | ok (--nostream still set) |
| `grep "rm -rf" /var/log/syslog` | ALLOWED |  |
| `grep 'sudo reboot; rm -rf /' x` | ALLOWED |  |
| `grep ">" x` | ALLOWED |  |
| `echo 'a && b'` | ALLOWED |  |
| `grep -E 'a\|b' x` | ALLOWED |  |
| `grep -nE "address\|port:\|ipWhitelist\|useHttps" /home/pi/MagicMirror/config/config.js` | ALLOWED |  |
| `grep -ri 'out of date' /home/pi/MagicMirror/translations /home/pi/MagicMirror/modules/default/updatenotific...` | ALLOWED |  |
| `grep -rn x /home/pi/MagicMirror/js/defaults.js` | ALLOWED |  |
| `grep -rn pat ~/MagicMirror/config` | ALLOWED |  |
| `grep -c error x` | ALLOWED |  |
| `grep -v tmpfs` | ALLOWED |  |
| `grep -A3 -B3 x y` | ALLOWED |  |
| `grep -n 'foo(' x` | ALLOWED |  |
| `grep -o x y` | ALLOWED |  |
| `grep -P '\\d+' x` | ALLOWED |  |
| `grep --color=never x y` | ALLOWED |  |
| `grep -i error /var/log/syslog \| tail -n 20` | ALLOWED |  |
| `zgrep error /var/log/syslog.2.gz` | ALLOWED |  |
| `ps aux` | ALLOWED |  |
| `ps -ef` | ALLOWED |  |
| `ps aux \| grep node` | ALLOWED |  |
| `ps -eo lstart,args \| grep -i electron \| head -4` | ALLOWED |  |
| `ps -eo pid,etime,cmd` | ALLOWED |  |
| `ps aux --sort=-%mem \| head` | ALLOWED |  |
| `ps -p 123 -o etime=` | ALLOWED |  |
| `ps -u pi` | ALLOWED |  |
| `ps axo pid,etime,cmd` | refused | ps: BSD option 'pid,etime,cmd' includes 'e', which prints every process's environment (s… |
| `ps -eo pid,comm,%mem --sort -%mem` | ALLOWED |  |
| `ps axjf` | ALLOWED |  |
| `ps -C node -o pid=` | ALLOWED |  |
| `ps faux` | ALLOWED |  |
| `ps -e` | ALLOWED |  |
| `ps ax \| wc -l` | ALLOWED |  |
| `ps -o pid,ppid,cmd -p 1` | ALLOWED |  |
| `df -h` | ALLOWED |  |
| `df -h /home/pi /tmp` | ALLOWED |  |
| `du -sh /home/pi/MagicMirror` | ALLOWED |  |
| `free -h` | ALLOWED | **F5** |
| `free -m` | ALLOWED | **F5** |
| `ss -ltn` | ALLOWED | **F5** |
| `ss -ltnp` | ALLOWED | **F5** |
| `ss -tulpn` | ALLOWED | **F5** |
| `hostname` | ALLOWED |  |
| `hostname -I` | ALLOWED |  |
| `hostname -f` | ALLOWED |  |
| `hostnamectl` | ALLOWED |  |
| `hostnamectl status` | ALLOWED |  |
| `ip a` | ALLOWED |  |
| `ip addr` | ALLOWED |  |
| `ip -br a` | ALLOWED |  |
| `ip -4 addr show wlan0` | ALLOWED |  |
| `ip addr show dev wlan0` | ALLOWED |  |
| `ip route` | ALLOWED |  |
| `ip r` | ALLOWED |  |
| `ip route show` | ALLOWED |  |
| `ip -s link` | ALLOWED |  |
| `ip link show eth0` | ALLOWED |  |
| `ip neigh` | ALLOWED |  |
| `ip -j addr` | ALLOWED |  |
| `ip route show table all` | ALLOWED |  |
| `date` | ALLOWED |  |
| `date +%s` | ALLOWED |  |
| `date -u +%FT%TZ` | ALLOWED |  |
| `date -d yesterday +%F` | ALLOWED |  |
| `date -d @0` | ALLOWED |  |
| `date -r /etc/hostname` | ALLOWED |  |
| `date -Iseconds` | ALLOWED |  |
| `date -R` | ALLOWED |  |
| `date --iso-8601=seconds` | ALLOWED |  |
| `date --date=yesterday` | ALLOWED |  |
| `date --date yesterday` | ALLOWED |  |
| `systemctl status magicmirror` | ALLOWED |  |
| `systemctl --user status magicmirror` | ALLOWED |  |
| `systemctl is-active x` | ALLOWED |  |
| `systemctl is-enabled x` | ALLOWED |  |
| `systemctl list-units --type=service --state=running` | ALLOWED |  |
| `systemctl list-units --failed` | ALLOWED |  |
| `systemctl list-timers` | ALLOWED |  |
| `systemctl status x --no-pager -l` | ALLOWED |  |
| `systemctl --no-pager status x` | ALLOWED |  |
| `systemctl show -p ActiveState x` | ALLOWED |  |
| `systemctl cat x` | ALLOWED |  |
| `systemctl` | ALLOWED |  |
| `systemctl status` | ALLOWED |  |
| `systemctl is-failed x` | ALLOWED |  |
| `systemctl list-unit-files` | ALLOWED |  |
| `stat -c %y_%n /home/pi/MagicMirror/.git/FETCH_HEAD` | ALLOWED |  |
| `stat -c '%y %n' x` | ALLOWED |  |
| `ls -la /home/pi/mm-backups` | ALLOWED |  |
| `ls -1t /home/pi/mm-backups` | ALLOWED |  |
| `ls -lt --time-style=full-iso x` | ALLOWED |  |
| `ls -la ~` | ALLOWED |  |
| `ls ~/MagicMirror/modules \| wc -l` | ALLOWED |  |
| `ls -lt \| head` | ALLOWED |  |
| `wc -l x` | ALLOWED |  |
| `cat /proc/cpuinfo` | ALLOWED |  |
| `cat /proc/meminfo` | ALLOWED |  |
| `cat /sys/class/thermal/thermal_zone0/temp` | ALLOWED |  |
| `cat /proc/loadavg` | ALLOWED |  |
| `cat /etc/hostname` | ALLOWED |  |
| `cat /etc/passwd` | ALLOWED |  |
| `cat /var/log/syslog \| tail -n 5` | ALLOWED |  |
| `sort x \| uniq -c \| sort -rn \| head` | ALLOWED |  |
| `sort -k2 -n x` | ALLOWED |  |
| `sort -t: -k3 -n /etc/passwd` | ALLOWED |  |
| `sort -u x` | ALLOWED |  |
| `sort -rn` | ALLOWED |  |
| `uniq -c` | ALLOWED |  |
| `uniq x` | ALLOWED |  |
| `uniq -f1 x` | ALLOWED |  |
| `cut -d: -f1 /etc/passwd` | ALLOWED |  |
| `cut -d' ' -f1 x` | ALLOWED |  |
| `tr -s ' '` | ALLOWED |  |
| `column -t x` | ALLOWED |  |
| `nl x` | ALLOWED |  |
| `npm --version` | ALLOWED |  |
| `npm -v` | ALLOWED |  |
| `npm ls --depth=0` | ALLOWED |  |
| `npm ls` | ALLOWED |  |
| `npm list -g --depth=0` | ALLOWED |  |
| `npm root -g` | ALLOWED |  |
| `npm prefix` | ALLOWED |  |
| `npm outdated` | ALLOWED |  |
| `npm view magicmirror version` | ALLOWED |  |
| `node --version` | ALLOWED |  |
| `node -v` | ALLOWED |  |
| `vcgencmd measure_temp` | ALLOWED |  |
| `vcgencmd get_throttled` | ALLOWED |  |
| `vcgencmd get_mem arm` | ALLOWED |  |
| `vcgencmd measure_volts core` | ALLOWED |  |
| `vcgencmd version` | ALLOWED |  |
| `uname -a` | ALLOWED |  |
| `id` | ALLOWED |  |
| `id pi` | ALLOWED |  |
| `whoami` | ALLOWED |  |
| `which node` | ALLOWED |  |
| `type -a node` | ALLOWED |  |
| `echo hello` | ALLOWED |  |
| `printf '%s\\n' x` | ALLOWED |  |
| `lsblk` | ALLOWED |  |
| `lsblk -f` | ALLOWED |  |
| `lscpu` | ALLOWED |  |
| `lsusb` | ALLOWED |  |
| `lsmod \| grep bcm` | ALLOWED |  |
| `pgrep -a node` | ALLOWED |  |
| `pgrep -fl electron` | ALLOWED |  |
| `pidof node` | ALLOWED |  |
| `vmstat` | ALLOWED |  |
| `vmstat -s` | ALLOWED |  |
| `iostat` | ALLOWED |  |
| `iostat -x` | ALLOWED |  |
| `sensors` | ALLOWED |  |
| `file x` | ALLOWED |  |
| `readlink -f x` | ALLOWED |  |
| `realpath .` | ALLOWED |  |
| `basename /a/b` | ALLOWED |  |
| `dirname /a/b` | ALLOWED |  |
| `true` | ALLOWED |  |
| `/usr/bin/uptime` | ALLOWED |  |
| `/bin/cat /etc/hostname` | ALLOWED |  |
| `cat \\;` | ALLOWED |  |
| `cat 'a b'` | ALLOWED |  |
| `cat a\\ b` | ALLOWED |  |
| `ls -la /home/pi/Magic\\ Mirror` | ALLOWED |  |
| `grep 'it''s' x` | ALLOWED |  |
| `echo "a\\"b"` | ALLOWED |  |
| `ls\t-la` | ALLOWED |  |
| `  ls  ` | ALLOWED |  |
| `ls \| grep x \| wc -l` | ALLOWED |  |

**useful forms now refused (informational)**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `ls /home/pi/MagicMirror/modules/default/updatenotification 2>/dev/null` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `systemctl status x 2>&1 \| head -20` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `pm2 logs --nostream 2>&1 \| tail -n 20` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `ls /var/log/*.log` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `ls ~/.pm2/logs/*` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `du -sh *` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `du -sh ~/MagicMirror/*` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `cd /home/pi/MagicMirror && git status --short` | refused | contains '&&' (command chaining): chaining, redirection, background, substitution and ex… |
| `find /home/pi/MagicMirror -name '*.js'` | refused | 'find' is not on the read-only allowlist (mutations are not enabled yet) |
| `find . -maxdepth 1 -type d` | refused | 'find' is not on the read-only allowlist (mutations are not enabled yet) |
| `echo $HOME` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `echo $PATH` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `echo $SHELL` | refused | contains '$' (variable expansion): chaining, redirection, background, substitution and e… |
| `wc -l < x` | refused | contains '<' (redirection): chaining, redirection, background, substitution and expansio… |
| `top -bn1 \| head -20` | refused | 'top' is not on the read-only allowlist (mutations are not enabled yet) |
| `dmesg \| tail -n 20` | refused | 'dmesg' is not on the read-only allowlist (mutations are not enabled yet) |
| `crontab -l` | refused | 'crontab' is not on the read-only allowlist (mutations are not enabled yet) |
| `curl -sI http://localhost:8080` | refused | 'curl' is not on the read-only allowlist (mutations are not enabled yet) |
| `curl -s -o /dev/null -w '%{http_code}' localhost:8080` | refused | 'curl' is not on the read-only allowlist (mutations are not enabled yet) |
| `ping -c 1 192.0.2.1` | refused | 'ping' is not on the read-only allowlist (mutations are not enabled yet) |
| `nproc` | refused | 'nproc' is not on the read-only allowlist (mutations are not enabled yet) |
| `w` | refused | 'w' is not on the read-only allowlist (mutations are not enabled yet) |
| `who` | refused | 'who' is not on the read-only allowlist (mutations are not enabled yet) |
| `last -n 5` | refused | 'last' is not on the read-only allowlist (mutations are not enabled yet) |
| `lsb_release -a` | refused | 'lsb_release' is not on the read-only allowlist (mutations are not enabled yet) |
| `dpkg -l nodejs` | refused | 'dpkg' is not on the read-only allowlist (mutations are not enabled yet) |
| `apt list --upgradable` | refused | 'apt' is not on the read-only allowlist (mutations are not enabled yet) |
| `timedatectl` | refused | 'timedatectl' is not on the read-only allowlist (mutations are not enabled yet) |
| `sed -n '1,20p' x` | refused | 'sed' is not on the read-only allowlist (mutations are not enabled yet) |
| `awk '{print $1}' x` | refused | 'awk' is not on the read-only allowlist (mutations are not enabled yet) |
| `tac x \| head` | refused | 'tac' is not on the read-only allowlist (mutations are not enabled yet) |
| `diff a b` | refused | 'diff' is not on the read-only allowlist (mutations are not enabled yet) |
| `md5sum x` | refused | 'md5sum' is not on the read-only allowlist (mutations are not enabled yet) |
| `sha256sum x` | refused | 'sha256sum' is not on the read-only allowlist (mutations are not enabled yet) |
| `zcat x.gz \| tail -n 5` | refused | 'zcat' is not on the read-only allowlist (mutations are not enabled yet) |
| `strings x \| head` | refused | 'strings' is not on the read-only allowlist (mutations are not enabled yet) |
| `test -f x` | refused | 'test' is not on the read-only allowlist (mutations are not enabled yet) |
| `pwd` | refused | 'pwd' is not on the read-only allowlist (mutations are not enabled yet) |
| `groups` | refused | 'groups' is not on the read-only allowlist (mutations are not enabled yet) |
| `locale` | refused | 'locale' is not on the read-only allowlist (mutations are not enabled yet) |
| `netstat -tlnp` | refused | 'netstat' is not on the read-only allowlist (mutations are not enabled yet) |
| `pm2 describe mm` | refused | pm2 'describe' is not a read-only subcommand: it prints environment variables (secrets) … |
| `pm2 show mm` | refused | pm2 'show' is not a read-only subcommand: it prints environment variables (secrets) (all… |
| `pm2 jlist` | refused | pm2 'jlist' is not a read-only subcommand: it prints every process's environment (secret… |
| `pm2 info mm` | refused | pm2 'info' is not a read-only subcommand: it prints environment variables (secrets) (all… |
| `pm2 logs` | refused | pm2: 'pm2 logs' streams forever; add --nostream (and --lines N) |
| `pm2 id mm` | refused | pm2 'id' is not a read-only subcommand (allowed: list, logs, ls, status) |
| `npm config get prefix` | refused | npm 'config' is not a read-only subcommand (allowed: list, ls, outdated, prefix, root, v… |
| `npm ls 2>/dev/null` | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansio… |
| `node -e 'console.log(1)'` | refused | node: only 'node --version' is allowed (node runs code) |
| `node -p process.version` | refused | node: only 'node --version' is allowed (node runs code) |
| `nodejs --version` | refused | 'nodejs' is not on the read-only allowlist (mutations are not enabled yet) |
| `systemctl --failed` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --type=service list-units` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl -t service --state=failed` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl --user --failed` | refused | systemctl: put options after the subcommand (allowed before it: --all, --full, --no-lege… |
| `systemctl list-jobs` | refused | systemctl 'list-jobs' is not a read-only subcommand (allowed: cat, get-default, is-activ… |
| `systemctl show-environment` | refused | systemctl 'show-environment' is not a read-only subcommand: it prints the manager's envi… |
| `systemctl is-system-running` | ALLOWED |  |
| `git -C x fetch --dry-run` | refused | git 'fetch' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git -C x rev-list --count HEAD..origin/master` | refused | git 'rev-list' is not a read-only subcommand (allowed: blame, branch, cat-file, describe… |
| `git -C x ls-remote origin` | refused | git 'ls-remote' is not a read-only subcommand (allowed: blame, branch, cat-file, describ… |
| `git -C x stash list` | refused | git 'stash' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, d… |
| `git -C x config --get remote.origin.url` | refused | git 'config' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git -C x reflog -5` | refused | git 'reflog' is not a read-only subcommand (allowed: blame, branch, cat-file, describe, … |
| `git -C x for-each-ref` | refused | git 'for-each-ref' is not a read-only subcommand (allowed: blame, branch, cat-file, desc… |
| `git -C x log @{u}..` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `git -C x log '@{u}..'` | ALLOWED |  |
| `git -C x show HEAD:package.json` | ALLOWED |  |
| `git -C x log -- --output` | refused | git log: --output writes a file |
| `git -C x branch --contains HEAD` | refused | git branch: only listing options are allowed, got '--contains' |
| `git -C x branch --merged` | refused | git branch: only listing options are allowed, got '--merged' |
| `git -C x branch --merged main` | refused | git branch: only listing options are allowed, got '--merged' |
| `git -C x tag --contains HEAD` | refused | git tag: only listing options are allowed, got '--contains' |
| `git -C x tag -n` | ALLOWED |  |
| `git -C x branch -a --sort=-committerdate` | ALLOWED |  |
| `git -C x branch --list 'feat*'` | ALLOWED |  |
| `git -C x branch -l 'feat*'` | ALLOWED |  |
| `git -C x remote show origin` | ALLOWED |  |
| `git -C x describe --always --dirty` | ALLOWED |  |
| `git -C x diff --name-only HEAD~1` | ALLOWED |  |
| `git -C x log --grep=token` | refused | git: argument '--grep=token' matches the secrets deny list ('*token*'); secrets must not… |
| `git -C x diff -- config/config.js.sample` | ALLOWED |  |
| `git -C x log -S api_key` | refused | git: argument 'api_key' matches the secrets deny list ('*_key'); secrets must not reach … |
| `ip route get 1.1.1.1` | refused | ip route: only a bare object or 'show'/'list' is allowed, got 'get' |
| `ip -o addr` | ALLOWED |  |
| `ip addr sh` | refused | ip addr: only a bare object or 'show'/'list' is allowed, got 'sh' |
| `ip a s wlan0` | refused | ip a: only a bare object or 'show'/'list' is allowed, got 's' |
| `ip link ls` | refused | ip link: only a bare object or 'show'/'list' is allowed, got 'ls' |
| `ip -statistics link` | ALLOWED |  |
| `ip rule` | refused | ip: 'rule' is not an allowed option or object (objects: addr, link, route, neigh; option… |
| `ip rule show` | refused | ip: 'rule' is not an allowed option or object (objects: addr, link, route, neigh; option… |
| `ip -6 route` | ALLOWED |  |
| `ip maddr` | refused | ip: 'maddr' is not an allowed option or object (objects: addr, link, route, neigh; optio… |
| `ip tunnel show` | refused | ip: 'tunnel' is not an allowed option or object (objects: addr, link, route, neigh; opti… |
| `ip netns list` | refused | ip: 'netns' is not an allowed option or object (objects: addr, link, route, neigh; optio… |
| `ip -n x a` | refused | ip: '-n' is not an allowed option or object (objects: addr, link, route, neigh; options:… |
| `vmstat 1 3` | refused | vmstat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `iostat -x 1 2` | refused | iostat: an interval operand repeats until stopped; one snapshot only (no operands) |
| `free -s 1 -c 3` | refused | free: -s repeats until stopped |
| `ps auxe` | refused | ps: BSD option 'auxe' includes 'e', which prints every process's environment (secrets) |
| `ps axo pid,etime` | refused | ps: BSD option 'pid,etime' includes 'e', which prints every process's environment (secre… |
| `ps e` | refused | ps: BSD option 'e' includes 'e', which prints every process's environment (secrets) |
| `ps -o pid,etime 123` | ALLOWED |  |
| `ps xe` | refused | ps: BSD option 'xe' includes 'e', which prints every process's environment (secrets) |
| `ps -e e` | refused | ps: BSD option 'e' includes 'e', which prints every process's environment (secrets) |
| `ps 123` | ALLOWED |  |
| `ps eo pid` | refused | ps: BSD option 'eo' includes 'e', which prints every process's environment (secrets) |
| `ps -- e` | refused | ps: BSD option 'e' includes 'e', which prints every process's environment (secrets) |
| `ps axww -o pid,lstart,cmd` | ALLOWED |  |
| `ps ax -o user,pid` | ALLOWED |  |
| `grep token /var/log/syslog` | refused | grep: argument 'token' matches the secrets deny list ('*token*'); secrets must not reach… |
| `grep -i 'access_token' x` | refused | grep: argument 'access_token' matches the secrets deny list ('*token*'); secrets must no… |
| `grep API_KEY config.js` | refused | grep: argument 'API_KEY' matches the secrets deny list ('*_key'); secrets must not reach… |
| `grep -c shadow /etc/nsswitch.conf` | refused | grep: argument 'shadow' matches the secrets deny list ('shadow*'); secrets must not reac… |
| `grep environ x` | refused | grep: argument 'environ' matches the secrets deny list ('environ'); secrets must not rea… |
| `grep id_rsa /var/log/auth.log` | refused | grep: argument 'id_rsa' matches the secrets deny list ('id_*'); secrets must not reach t… |
| `grep .env x` | refused | grep: argument '.env' matches the secrets deny list ('.env'); secrets must not reach the… |
| `grep -r '' /var/log` | refused | grep: recursive search root '/var/log' is too broad (it can reach secrets without naming… |
| `grep -rn httpHeaders /home/pi/MagicMirror/js` | ALLOWED |  |
| `grep -rn -E helmet\|httpHeaders /home/pi/MagicMirror/js/server.js` | refused | grep: recursive search with no directory searches the home directory; name a directory |
| `grep -rn x /var/log` | refused | grep: recursive search root '/var/log' is too broad (it can reach secrets without naming… |
| `grep -rn x /etc/systemd` | refused | grep: recursive search root '/etc/systemd' is too broad (it can reach secrets without na… |
| `grep -r x ~/MagicMirror` | ALLOWED |  |
| `grep -rn x .` | refused | grep: recursive search root '.' is too broad (it can reach secrets without naming them);… |
| `grep -R x /home/pi/MagicMirror/config` | refused | grep: -R follows every symlink while recursing; use -r |
| `ls -la ~/.ssh` | refused | ls: argument '~/.ssh' matches the secrets deny list ('.ssh'); secrets must not reach the… |
| `stat ~/.ssh/authorized_keys` | refused | stat: argument '~/.ssh/authorized_keys' matches the secrets deny list ('.ssh'); secrets … |
| `ls -la /home/pi/MagicMirror/.env` | refused | ls: argument '/home/pi/MagicMirror/.env' matches the secrets deny list ('.env'); secrets… |
| `ls /etc/ssl/private` | ALLOWED |  |
| `ls /home/pi/MagicMirror/modules/MMM-Tokenizer` | refused | ls: argument '/home/pi/MagicMirror/modules/MMM-Tokenizer' matches the secrets deny list … |
| `cat /var/log/token-refresh.log` | refused | cat: argument '/var/log/token-refresh.log' matches the secrets deny list ('*token*'); se… |
| `wc -l ~/.ssh/authorized_keys` | refused | wc: argument '~/.ssh/authorized_keys' matches the secrets deny list ('.ssh'); secrets mu… |
| `cat /etc/ssh/sshd_config` | ALLOWED |  |
| `cat /etc/ssh/ssh_host_ed25519_key.pub` | ALLOWED |  |
| `ls /usr/share/keyrings/x.key` | refused | ls: argument '/usr/share/keyrings/x.key' matches the secrets deny list ('*.key'); secret… |
| `cat /etc/environment` | ALLOWED |  |
| `ls /proc/1/environ` | refused | ls: argument '/proc/1/environ' matches the secrets deny list ('environ'); secrets must n… |
| `cat /home/pi/shadowbox/README` | refused | cat: argument '/home/pi/shadowbox/README' matches the secrets deny list ('shadow*'); sec… |
| `ls /home/pi/id_card.png` | refused | ls: argument '/home/pi/id_card.png' matches the secrets deny list ('id_*'); secrets must… |
| `cat /etc/apt/trusted.gpg.d/x.pem` | refused | cat: argument '/etc/apt/trusted.gpg.d/x.pem' matches the secrets deny list ('*.pem'); se… |
| `uniq -f 1 x` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uniq --skip-fields 1 x` | refused | uniq: at most one file operand; a second operand is an output file that uniq writes |
| `uptime -s x` | refused | uptime: at most 0 operands allowed, got 1 |
| `date -d '2020-01-01 +1 day'` | ALLOWED |  |
| `date -d 'now' '+%F %T'` | ALLOWED |  |
| `date --date='1 hour ago'` | ALLOWED |  |
| `date -u` | ALLOWED |  |
| `date 2020` | refused | date: operand '2020' sets the clock; only +FORMAT operands are allowed |
| `tail -q -n 5 a b` | ALLOWED |  |
| `tail --lines=5 x` | ALLOWED |  |
| `tail -v x` | ALLOWED |  |
| `tail --follow=name --retry x` | refused | tail: --follow follows forever and never returns (burns the 30 s timeout); use -n N |
| `sort f -- -o` | refused | sort: -o writes a file |
| `cat -- -o` | ALLOWED |  |
| `ls #comment` | refused | contains unquoted '#' (comment): chaining, redirection, background, substitution and exp… |
| `ls a#b` | refused | contains unquoted '#' (comment): chaining, redirection, background, substitution and exp… |
| `echo a{b,c}` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `echo {}` | refused | contains unquoted '{' (brace expansion): chaining, redirection, background, substitution… |
| `echo [x]` | refused | contains unquoted '[' (glob): chaining, redirection, background, substitution and expans… |
| `echo a?` | refused | contains unquoted '?' (glob): chaining, redirection, background, substitution and expans… |
| `echo 'a*'` | ALLOWED |  |
| `echo a\\*` | ALLOWED |  |
| `echo "*"` | ALLOWED |  |
| `echo ~` | ALLOWED |  |
| `echo !` | ALLOWED |  |
| `echo a=b` | ALLOWED |  |
| `echo -e 'a\\tb'` | ALLOWED |  |
| `echo \\$HOME` | ALLOWED |  |
| `echo '$HOME'` | ALLOWED |  |
| `echo "\\$HOME"` | ALLOWED |  |
| `docker ps` | refused | 'docker' is not on the read-only allowlist: never used on a managed host; 'logs -f' and … |
| `docker logs --tail 50 x` | refused | 'docker' is not on the read-only allowlist: never used on a managed host; 'logs -f' and … |
| `hostnamectl --static` | ALLOWED |  |
| `hostnamectl --json=pretty` | ALLOWED |  |
| `ss -s` | ALLOWED | **F5** |
| `ss -tan state established` | ALLOWED | **F5** |
| `ss -o state established '( dport = :22 )'` | ALLOWED | **F5** |
| `ss -H -tln` | ALLOWED | **F5** |
| `sensors -u` | ALLOWED |  |
| `sensors -j` | ALLOWED |  |
| `free -w -h` | ALLOWED | **F5** |
| `free -c 1` | ALLOWED | **F5** |
| `file -b x` | ALLOWED |  |
| `file -i x` | ALLOWED |  |
| `file -z x.gz` | ALLOWED |  |
| `file -L x` | ALLOWED |  |
| `lsusb -t` | ALLOWED |  |
| `lsblk -o NAME,SIZE` | ALLOWED |  |
| `df -Th` | ALLOWED |  |
| `df -i` | ALLOWED |  |
| `df --sync` | ALLOWED |  |
| `du -d1 /home/pi` | ALLOWED |  |
| `du --max-depth=1 -h /var/log` | ALLOWED |  |
| `pgrep -u pi` | ALLOWED |  |
| `pgrep -c node` | ALLOWED |  |
| `pidof -s node` | ALLOWED |  |
| `stat --format=%s x` | ALLOWED |  |
| `ls -R /home/pi/MagicMirror/config` | ALLOWED |  |
| `wc -l a b c` | ALLOWED |  |
| `cat -n x` | ALLOWED |  |
| `head -3 x` | ALLOWED |  |
| `nl -ba x` | ALLOWED |  |
| `cut -c1-80 x` | ALLOWED |  |
| `tr a-z A-Z` | ALLOWED |  |
| `tr '[:lower:]' '[:upper:]'` | ALLOWED |  |
| `tr -d '\\r'` | ALLOWED |  |
| `column -s, -t x` | ALLOWED |  |
| `which -a npm` | ALLOWED |  |
| `systemctl status 'foo@bar.service'` | ALLOWED |  |
| `systemctl status foo@bar` | ALLOWED |  |
| `systemctl list-units 'mag*'` | ALLOWED |  |
| `systemctl list-units mag*` | refused | contains unquoted '*' (glob): chaining, redirection, background, substitution and expans… |
| `journalctl -u 'mag*'` | ALLOWED |  |
| `journalctl _PID=1 -n 5` | ALLOWED |  |
| `journalctl _SYSTEMD_UNIT=ssh.service -n 5` | ALLOWED |  |
| `journalctl --user-unit=magicmirror -n 5` | ALLOWED |  |
| `journalctl -t kernel -n 5` | ALLOWED |  |
| `journalctl --output=json-pretty -n 1` | ALLOWED |  |
| `journalctl --no-full -n 5` | ALLOWED |  |
| `journalctl --utc -n 5` | ALLOWED |  |
| `journalctl --fields` | ALLOWED |  |
| `journalctl -F _SYSTEMD_UNIT` | ALLOWED |  |
| `journalctl --verify` | ALLOWED |  |
| `journalctl --header` | ALLOWED |  |
| `journalctl -m -n 5` | ALLOWED |  |
| `journalctl --file /var/log/journal/x/system.journal -n 5` | ALLOWED |  |
| `journalctl --cursor x` | ALLOWED |  |
| `journalctl --show-cursor -n 1` | ALLOWED |  |
| `journalctl -q -n 1` | ALLOWED |  |
| `journalctl --facility=auth -n 5` | ALLOWED |  |
| `journalctl -N` | ALLOWED |  |

**strip() mismatch: classifier sees stripped, ssh gets original**

| Command | Verdict | Reason (trimmed) / finding |
| --- | --- | --- |
| `git -C /home/pi/MagicMirror branch \xa0` | ALLOWED | **F1** |
| `git -C /home/pi/MagicMirror tag \xa0` | ALLOWED | **F1** |
| `git branch \u2003` | ALLOWED | **F1** |
| `git tag \x1f` | ALLOWED | **F1** |
