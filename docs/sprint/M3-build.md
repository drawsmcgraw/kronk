# M3 build report: harden the read-only exec classifier

Build agent, 2026-11-16 04:50 to 05:40 UTC, branch `refactor`. Criteria:
`docs/sprint/M3-criteria.md`. Commits: `8a648e6` (classifier, tests,
example registry), `28a42e8` (ROADMAP and mirror plan notes), and the commit
that adds this report.

No command was sent to the MagicMirror Pi or any managed host. Production's
`/ops/exec` was not called. Refusals were tested against `tool_service-dev`
only, through a throwaway registry entry that points at a TEST-NET-1 address
with no key (details under "Dev path").

## What changed

`classify_readonly()` in `tool_service/ops.py` was a flat allowlist: any
`_SAFE` program passed with any arguments, a quote-blind substring check
looked for `;`, `&`, `>` and friends, `cmd.split("|")` cut pipes (including
pipes inside quotes), and a dual-use program's subcommand was the first
non-dash token. It is now four steps, each of which can refuse:

1. **Lex like bash.** A small lexer walks the line tracking single quotes,
   double quotes and backslashes, and produces the words bash would pass
   after quote removal (`test_lexer_matches_bash_word_splitting` checks it
   against `bash -c 'eval "set -- $1"'`). Outside single quotes it refuses
   every shell feature it does not model: `;`, `&`, `&&`, `||`, `|&`, `<`,
   `>`, `>>`, `<<`, `<(`, `>(`, `(`, `)`, `$` (so `$(...)`, `${...}`,
   `$VAR`, `$'...'`), backticks, unquoted `*`, `?`, `[` (globs), unquoted
   `{`, `}` (brace expansion), unquoted `#` (comment), newlines and other
   control characters. An unbalanced quote or a trailing backslash is
   "could not parse command". The only operator allowed is a single `|`.
2. **Name the program.** Each pipe segment's first word must be a key of
   `_RULES`, bare or as a path in `/bin`, `/usr/bin`, `/sbin`, `/usr/sbin`,
   `/usr/local/bin` (exact directory; `/usr/bin/../../tmp/cat` is refused).
   A first word containing `=` is an env-assignment prefix and refused.
   `sudo` gets its own reason. Removed programs get theirs.
3. **Deny list.** Every argument of every program is split into path
   components and matched against `DENIED_PATH_PATTERNS` (next section).
4. **Program rule.** Flags and operands, per program (table below).

`ops_exec` in `tool_service/main.py` is unchanged: same audit call with the
reason as `note`, same 30 s timeout, same 422 `refused: <reason>` shape.

### How flags are read

One scanner (`_scan`) serves every program that has flag rules:

- A forbidden short letter is found anywhere in a combined cluster
  (`-uo`, `-fn`, `-5f`), until the cluster reaches a letter that takes a
  value; the rest of the cluster is that value (`-to` is `-t o`, `-mC` is
  `-m C`). Value letters are listed only where the program's getopt string
  is known; an unlisted value letter can only make the scanner stricter.
- A long option is forbidden when its name is a prefix of a forbidden name,
  because getopt accepts any unambiguous abbreviation: `--out=x`, `--o x`,
  `--outp x` are all `--output`. `long_ok` lists the one exact name that is
  legitimately a prefix of a forbidden one (`journalctl --cursor` vs
  `--cursor-file`).
- Flags are found after operands too (`sort f -o out`), since GNU getopt
  permutes.
- `--` ends option parsing for counting operands: every word after it is an
  operand, as is a lone `-` (stdin). Forbidden spellings are still refused
  after `--` (`sort f -- -o out`). That is a deliberate over-refusal: a file
  named `-o` is never needed, and not every program honours `--`.
- Operand limits count every word that is not a flag. For programs where a
  limit matters (`uniq`, `hostname`, `hostnamectl`, `vmstat`, `iostat`,
  `uptime`) no flag is treated as taking a value, so a flag's value counts
  as an operand. That can only over-refuse (`uniq -f 1 f` is refused; write
  `uniq -f1 f`).

Programs with subcommands (`systemctl`, `git`, `npm`, `pm2`, `ip`) must put
the subcommand first, after an exact list of value-less global options.
Otherwise a flag's value can pose as the subcommand: under the old rule
`systemctl -p status restart magicmirror` read as `status`.

## Path deny list

`DENIED_PATH_PATTERNS`, one tuple in `tool_service/ops.py`. Each argument is
lowercased and split on `/`, `=`, `:` and `,`; each component is matched
with `fnmatch` against:

| Pattern | Matches | Why |
| --- | --- | --- |
| `.ssh` | the `.ssh` directory and anything under it | SSH keys, `authorized_keys` |
| `id_*` | `id_rsa`, `id_ed25519`, `id_rsa.pub`, wherever they are | SSH key files |
| `.env`, `.env.*`, `*.env` | `.env`, `.env.local`, `prod.env` | dotenv files |
| `shadow*`, `gshadow*` | `/etc/shadow`, `/etc/shadow-`, `/etc/gshadow` | password hashes |
| `*.key`, `*_key`, `*.pem`, `*.p12`, `*.pfx` | `server.key`, `ssh_host_ed25519_key`, `cert.pem` | key files |
| `*token*`, `.netrc`, `.git-credentials` | `hass_token.txt`, `tokens.json`, `~/.netrc` | token files |
| `environ` | `/proc/<pid>/environ` | a process environment: the same leak as `printenv` |

Because matching is per component and unanchored, `.ssh/id_rsa`,
`~/.ssh/id_rsa`, `~root/.ssh/x`, `/home/pi//.ssh//id_rsa`,
`/home/pi/MagicMirror/../.ssh/id_rsa`, `'.ssh'`, `.s\sh` and
`"/home/pi/.SSH/ID_RSA"` are all refused. Arguments that carry a path
after `=` or `:` are covered (`--file=…`, `--git-dir=…`, `HEAD:.env`). A
value attached to a short flag is also covered: for a word starting with a
single `-`, every suffix of the first component is tried (`-fid_rsa`,
`-f.env`, `-f~/.ssh/x`).

The list applies to every program, not only to file readers, and to every
argument, including grep patterns. So `grep token file` and `grep .env file`
are refused; the agent can rephrase (`grep tok.n`). That is the cost of a
rule simple enough to check.

Two related rules:

- `pid,environ` as a `ps -o` value hits `environ`; harmless over-refusal.
- **Recursive reads** (`grep -r` and the other grep names) can reach a
  denied file without naming it. Each recursive root, after `~` and relative
  paths are resolved against the login home, must be at least
  `RECURSIVE_ROOT_MIN_DEPTH = 3` components deep (`/home/pi/MagicMirror` is
  3; `/home/pi`, `~`, `.`, `/`, `/etc`, `/var/log` are refused), must not
  contain `..`, and must not be under `/proc`, `/sys` or `/dev`
  (`RECURSIVE_ROOT_NEVER`). `grep -R` (follows every symlink) and
  `-d`/`--directories` are refused outright. A recursive grep with no root
  searches the home directory and is refused.

Globs and expansions that could reach a denied path (`~/.ss?/id_rsa`,
`~/.ssh/*`, `[.]ssh`, `.{s,}sh`, `$HOME/.ssh`) are refused by the lexer
before the deny list is consulted, since an unquoted glob or `$` is refused
everywhere.

## Rule set as built

Refusal is the default: a program not in this table is refused with
"'<prog>' is not on the read-only allowlist (mutations are not enabled
yet)".

| Program | Allowed | Forbidden flags (any spelling) | Operands |
| --- | --- | --- | --- |
| `uptime` | any flag | none | none |
| `whoami`, `id`, `uname`, `true`, `echo`, `printf`, `which`, `type`, `lsblk`, `lscpu`, `lsusb`, `lsmod`, `df`, `pgrep`, `pidof` | any flag | none | any |
| `cat`, `head`, `wc`, `cut`, `tr`, `column`, `nl`, `ls`, `du`, `stat`, `readlink`, `realpath`, `dirname`, `basename` | any flag | none | any (deny list) |
| `grep`, `egrep`, `fgrep`, `zgrep` | any flag | `-R`, `--dereference-recursive`, `-d`, `--directories` | any; recursive roots per the rule above |
| `tail` | any flag | `-f`, `-F`, `--follow`, `--retry`; operands `+Nf` | any |
| `sort` | any flag | `-o`, `--output`, `--compress-program`, `--files0-from` | any |
| `uniq` | any flag | none | at most 1 (a second is an output file) |
| `file` | any flag | `-C`, `--compile` | any |
| `date` | any flag | `-s`, `--set` | only `+FORMAT` (an operand without `+` sets the clock) |
| `hostname` | any flag | `-F`, `--file`, `-b`, `--boot` | none (an operand sets the hostname) |
| `hostnamectl` | any flag | `-H`, `--host`, `-M`, `--machine` | none, or exactly `status` |
| `ss` | any flag | `-K`, `--kill`, `-D`, `--diag` | any (filter expression) |
| `free` | any flag | `-s`, `--seconds` | any |
| `sensors` | any flag | `-s`, `--set` | any |
| `vmstat`, `iostat` | any flag | none | none (an interval repeats until stopped) |
| `ps` | any flag | BSD option words containing `e` (`auxe`, `eww`: print every process's environment) | pids |
| `journalctl` | any flag | `-f`, `--follow`, `--vacuum-size`, `--vacuum-time`, `--vacuum-files`, `--rotate`, `--flush`, `--relinquish-var`, `--smart-relinquish-var`, `--sync`, `--setup-keys`, `--update-catalog`, `--cursor-file` | any (matches) |
| `node` | exactly `node --version` or `node -v` | everything else | none |
| `systemctl` | before the subcommand only `--user`, `--system`, `--no-pager`, `--full`, `-l`, `--all`, `-a`, `--plain`, `--no-legend`, `-q`, `--quiet`; subcommands `status`, `is-active`, `is-enabled`, `is-failed`, `is-system-running`, `show`, `cat`, `list-units`, `list-unit-files`, `list-timers`, `list-sockets`, `list-dependencies`, `get-default`, or none | after it: `-H`, `--host`, `-M`, `--machine`; subcommand `show-environment` (prints the manager environment) | units |
| `git` | before the subcommand only `-C <dir>`, `--git-dir[=]<dir>`, `--work-tree[=]<dir>`, `--no-pager`, `--no-optional-locks`, or a lone `--version`; subcommands `status`, `log`, `show`, `diff`, `rev-parse`, `describe`, `ls-files`, `shortlog`, `blame`, `cat-file`, `branch`, `tag`, `remote` | before it: everything else, notably `-c`, `--config-env`, `--exec-path`, `-p`/`--paginate`; after it: `--output`, `--ext-diff`, `--no-index` | free for the plain subcommands |
| `git branch` | listing flags only: letters `a r v l i`, `--list`, `--all`, `--remotes`, `--verbose`, `--show-current`, `--color`, `--no-color`, `--no-column`, `--ignore-case`, `--no-abbrev`, and `--contains=`, `--no-contains=`, `--merged=`, `--no-merged=`, `--points-at=`, `--sort=`, `--format=`, `--abbrev=`, `--color=` | any other flag (`-d`, `-D`, `-m`, `-c`, `--set-upstream-to`, …) | patterns only with `-l`/`--list` (otherwise an operand creates a branch) |
| `git tag` | letters `l i`, `-n[N]`, `--list`, `--ignore-case`, `--column`, `--no-column`, and `--contains=`, `--no-contains=`, `--merged=`, `--no-merged=`, `--points-at=`, `--sort=`, `--format=` | any other flag (`-d`, `-a`, `-m`, …) | patterns only with `-l`/`--list` |
| `git remote` | bare, `-v`/`--verbose`, `show [-n] <name>`, `get-url [--push] [--all] <name>` | any other flag | any other sub-subcommand (`add`, `set-url`, `prune`, …) |
| `npm` | subcommand first: `ls`, `list`, `view`, `outdated`, `root`, `prefix`, `why`; or flags only (`npm --version`) | none | after the subcommand, free |
| `pm2` | subcommand first: `list`, `ls`, `status`, `logs` (requires `--nostream` before any `--`); or flags only | `jlist`, `prettylist`, `show`, `describe`, `info`, `env` refused as subcommands (print environments) | free |
| `ip` | globals `-4 -6 -s -stats -statistics -d -details -j -json -p -pretty -o -oneline -c -color -br -brief -h -human -human-readable` (exact); objects `a addr address l link r ro route n neigh neighbor neighbour`; then nothing, or exactly `show`/`list` and its filter words | any other global (`-batch`, `-b`, `-force`, `-n`, …); abbreviations (`ip link s` is `set`) | after `show`/`list`, free |
| `vcgencmd` | `measure_temp`, `measure_clock`, `measure_volts`, `get_throttled`, `get_mem`, `get_config`, `version`, `commands` | any flag | at most one argument |

## Programs removed from the allowed set

| Program | Why |
| --- | --- |
| `env` | Runs any program given as an argument (`env rm -rf …`, `env sh -c …`). Plan step 1. |
| `printenv` | Prints the environment, which holds secrets, into the model context and traces. Plan step 1. |
| `docker` | Never used on a managed host (0 of 87 audit entries). `docker logs -f` and `docker stats` never return, and `docker inspect` prints container environments. Three special rules for a program nobody uses; re-add with rules when a host needs it. |

Also out, as subcommands of programs that stay: `systemctl
show-environment` (prints the manager environment), `pm2 jlist`,
`prettylist`, `show`, `describe`, `info` (each prints process environment
variables). `pm2` and `npm` are not used on the Pi per the audit log; they
stay for criteria 2 and 10.

Added: `node`, restricted to exactly `node --version` / `node -v`. The audit
log shows the agent asked for it once and was refused; the old plan listed
it as phase-A read-only.

Narrowed programs that were on `_SAFE` with any arguments and could mutate:
`date` (set the clock with `-s` or a bare operand), `hostname` (set it with
an operand or `-F`), `hostnamectl` (`set-hostname`, `hostname <x>`), `sort`
(`-o`, `--compress-program` runs a program), `uniq` (output operand), `ss`
(`-K` kills sockets, `-D` writes a file), `sensors` (`-s` writes chip
limits), `file` (`-C` writes a file), `vcgencmd` (`display_power 0` turns
the mirror's screen off; was any argument), `git branch`/`tag`/`remote`
(create, delete, rename, set-url were all allowed by the old first-token
check).

## Registry `sudo` field (criterion 13)

Removed. `ops_exec` never read it; it existed in the env-fallback entry in
`load_registry()` and in `ops/hosts.example.json` (plus the example's
comment "'sudo' is per-host"). Both are gone, and
`docs/plans/MAGICMIRROR_PLAN.md` notes the removal where it described the
field. `test_registry_env_fallback` and `test_example_registry_has_no_sudo_field`
pin it. `sudo`, bare or as `/usr/bin/sudo`, quoted or escaped, is still
refused with "sudo is not allowed in read-only mode".

## Command forms from the audit log

`data/ops_audit.log` (read, not written): 87 entries, 2026-07-11 to
2026-10-22, 78 distinct commands, all on host `magicmirror`. Before: 62
allowed, 16 refused. After: 70 allowed, 8 refused.

- All 62 previously allowed forms are still allowed (pinned in
  `AUDIT_LOG_ALLOWED` in `tests/test_ops_exec.py`).
- 8 previously refused forms are now allowed: the four `git -C` forms, the
  three quoted-pipe forms, and `node --version`.
- 8 stay refused, each for a real reason: `sudo reboot`; two `2>/dev/null`
  redirections; `find … | xargs grep …` (`find` has `-exec`/`-delete`,
  `xargs` runs programs); two `grep -nE a|b|c` with the pipe unquoted (a
  real pipe into a program named `port` / `httpHeaders`); `cd … && git …`
  and `cd …; git …` (chaining; `git -C` now does the job).

None of the forms the plan table lists as bypasses was ever used.

| # | Command (as logged) | n | Before | After | Reason if refused |
| --- | --- | --- | --- | --- | --- |
| 1 | `uptime -p` | 2 | allowed | allowed |  |
| 2 | `sudo reboot` | 1 | refused | refused | sudo is not allowed in read-only mode |
| 3 | `uptime` | 3 | allowed | allowed |  |
| 4 | `cat /etc/os-release \| grep "PRETTY_NAME"` | 1 | allowed | allowed |  |
| 5 | `cat /etc/os-release` | 1 | allowed | allowed |  |
| 6 | `ls /home/pi/MagicMirror/modules` | 2 | allowed | allowed |  |
| 7 | `grep -n module: /home/pi/MagicMirror/config/config.js` | 2 | allowed | allowed |  |
| 8 | `git -C /home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast status` | 1 | refused | **allowed** |  |
| 9 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git --work-tree=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast status` | 1 | allowed | allowed |  |
| 10 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git --work-tree=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast log -3 --format=%h_%ad_%s --date=short` | 1 | allowed | allowed |  |
| 11 | `journalctl --user -u magicmirror --since today --no-pager \| grep -iE "updatenotif\|update.avail\|behind" \| tail -20` | 1 | refused | **allowed** |  |
| 12 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP status` | 1 | allowed | allowed |  |
| 13 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-Sunpower-Monitor/.git --work-tree=/home/pi/MagicMirror/modules/MMM-Sunpower-Monitor status` | 1 | allowed | allowed |  |
| 14 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git branch -r` | 1 | allowed | allowed |  |
| 15 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git remote -v` | 1 | allowed | allowed |  |
| 16 | `journalctl --user -n3000 --no-pager \| grep -iE updatenotification \| tail -25` | 1 | allowed | allowed |  |
| 17 | `grep -ri "out of date" /home/pi/MagicMirror/translations /home/pi/MagicMirror/modules/default/updatenotification` | 1 | allowed | allowed |  |
| 18 | `grep -rn "UPDATE_NOTIFICATION" /home/pi/MagicMirror/translations/en.json` | 1 | allowed | allowed |  |
| 19 | `grep -rni "out.of.date\\|outdated" /home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/MMM-OpenWeatherMapForecast.js /home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/node_helper.js` | 1 | refused | **allowed** |  |
| 20 | `ls /home/pi/MagicMirror/modules/default` | 1 | allowed | allowed |  |
| 21 | `ls /home/pi/MagicMirror/modules/default/updatenotification 2>/dev/null` | 1 | refused | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansion are not allowed (read-only mode; use single quotes for a literal) |
| 22 | `git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror status` | 1 | allowed | allowed |  |
| 23 | `git --git-dir=/home/pi/MagicMirror/.git ls-files modules/default` | 1 | allowed | allowed |  |
| 24 | `git --git-dir=/home/pi/MagicMirror/.git ls-files \| grep -i updatenotification \| head -8` | 1 | allowed | allowed |  |
| 25 | `ls /home/pi/MagicMirror/node_modules/@magicmirror 2` | 1 | allowed | allowed |  |
| 26 | `cat /home/pi/MagicMirror/defaultmodules/updatenotification/git_helper.js` | 1 | allowed | allowed |  |
| 27 | `tail -n 80 /home/pi/MagicMirror/defaultmodules/updatenotification/git_helper.js` | 1 | allowed | allowed |  |
| 28 | `stat -c %y_%n /home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git/FETCH_HEAD` | 1 | allowed | allowed |  |
| 29 | `stat -c %y_%n /home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git/FETCH_HEAD` | 1 | allowed | allowed |  |
| 30 | `stat -c %y_%n /home/pi/MagicMirror/modules/MMM-Sunpower-Monitor/.git/FETCH_HEAD` | 1 | allowed | allowed |  |
| 31 | `stat -c %y_%n /home/pi/MagicMirror/.git/FETCH_HEAD` | 1 | allowed | allowed |  |
| 32 | `journalctl -n4000 --no-pager \| grep -i updatenotification \| tail -15` | 1 | allowed | allowed |  |
| 33 | `journalctl -n4000 --no-pager \| grep -i "update found" \| tail -10` | 1 | allowed | allowed |  |
| 34 | `ps -eo lstart,args \| grep -i electron \| head -4` | 1 | allowed | allowed |  |
| 35 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP diff --stat` | 1 | allowed | allowed |  |
| 36 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP diff package-lock.json \| head -25` | 1 | allowed | allowed |  |
| 37 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP log -1 --format=%h_%ad_%s --date=short` | 2 | allowed | allowed |  |
| 38 | `git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP status --short` | 1 | allowed | allowed |  |
| 39 | `journalctl -n300 --no-pager \| grep -i updatenotification \| tail -12` | 1 | allowed | allowed |  |
| 40 | `journalctl --since 12:47 --no-pager \| grep -ci "update found"` | 1 | allowed | allowed |  |
| 41 | `grep -r "location" /home/pi/MagicMirror/modules/default/weather/ 2>/dev/null \| head -5` | 1 | refused | refused | contains '>' (redirection): chaining, redirection, background, substitution and expansion are not allowed (read-only mode; use single quotes for a literal) |
| 42 | `cat /home/pi/MagicMirror/modules/default/weather/config.js` | 1 | allowed | allowed |  |
| 43 | `find /home/pi/MagicMirror -type f -name "*.js" \| xargs grep -l "weather" \| head -3` | 1 | refused | refused | 'find' is not on the read-only allowlist (mutations are not enabled yet) |
| 44 | `grep -nE "address\|port:\|ipWhitelist\|useHttps" /home/pi/MagicMirror/config/config.js` | 1 | refused | **allowed** |  |
| 45 | `ss -ltn` | 2 | allowed | allowed |  |
| 46 | `hostname -I` | 2 | allowed | allowed |  |
| 47 | `ss -ltnp` | 1 | allowed | allowed |  |
| 48 | `grep -nE address\|port\|ipWhitelist /home/pi/MagicMirror/config/config.js` | 1 | refused | refused | 'port' is not on the read-only allowlist (mutations are not enabled yet) |
| 49 | `cat /home/pi/MagicMirror/config/config.js` | 2 | allowed | allowed |  |
| 50 | `grep -rn -i referrer /home/pi/MagicMirror/modules/MMM-RAIN-MAP/MMM-RAIN-MAP.js` | 1 | allowed | allowed |  |
| 51 | `grep -rn -i referrerpolicy /home/pi/MagicMirror/index.html` | 1 | allowed | allowed |  |
| 52 | `grep -rn -E helmet\|httpHeaders\|referrerPolicy /home/pi/MagicMirror/js/server.js` | 1 | refused | refused | grep: recursive search with no directory searches the home directory; name a directory |
| 53 | `grep -rn httpHeaders /home/pi/MagicMirror/js/defaults.js` | 1 | allowed | allowed |  |
| 54 | `grep -n httpHeaders /home/pi/MagicMirror/config/config.js` | 1 | allowed | allowed |  |
| 55 | `git -C /home/pi/MagicMirror status --short` | 1 | refused | **allowed** |  |
| 56 | `git -C /home/pi/MagicMirror diff --stat` | 1 | refused | **allowed** |  |
| 57 | `git -C /home/pi/MagicMirror log -3 --format=%h%x20%ci%x20%s` | 1 | refused | **allowed** |  |
| 58 | `cd /home/pi/MagicMirror && git status --short` | 1 | refused | refused | contains '&&' (command chaining): chaining, redirection, background, substitution and expansion are not allowed (read-only mode; use single quotes for a literal) |
| 59 | `cd /home/pi/MagicMirror; git status --short` | 1 | refused | refused | contains ';' (command chaining): chaining, redirection, background, substitution and expansion are not allowed (read-only mode; use single quotes for a literal) |
| 60 | `git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror status --short` | 1 | allowed | allowed |  |
| 61 | `git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror diff --stat` | 1 | allowed | allowed |  |
| 62 | `git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror log -3 --format=%h_%ci_%s` | 1 | allowed | allowed |  |
| 63 | `stat -c %y_%U_%n /home/pi/MagicMirror/package-lock.json /home/pi/MagicMirror/package.json` | 1 | allowed | allowed |  |
| 64 | `git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror diff package-lock.json` | 1 | allowed | allowed |  |
| 65 | `git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror diff -- /home/pi/MagicMirror/package-lock.json` | 1 | allowed | allowed |  |
| 66 | `grep -n install-mm /home/pi/MagicMirror/package.json` | 1 | allowed | allowed |  |
| 67 | `npm --version` | 1 | allowed | allowed |  |
| 68 | `node --version` | 1 | refused | **allowed** |  |
| 69 | `ls -1t /home/pi/mm-backups` | 1 | allowed | allowed |  |
| 70 | `df -h /home/pi /tmp` | 1 | allowed | allowed |  |
| 71 | `ls -la /home/pi/mm-backups` | 1 | allowed | allowed |  |
| 72 | `du -sh /home/pi/MagicMirror` | 1 | allowed | allowed |  |
| 73 | `stat -c %y_%n /home/pi/MagicMirror/.git/FETCH_HEAD /home/pi/MagicMirror/.git/index /home/pi/MagicMirror/.git/objects /home/pi/MagicMirror/.git/ORIG_HEAD` | 1 | allowed | allowed |  |
| 74 | `ls -lt --time-style=full-iso /home/pi/MagicMirror/.git/objects` | 1 | allowed | allowed |  |
| 75 | `grep -n -i updatenotification -A6 /home/pi/MagicMirror/config/config.js` | 1 | allowed | allowed |  |
| 76 | `journalctl --user --since 12:25 --until 12:37 --no-pager -o short-iso` | 1 | allowed | allowed |  |
| 77 | `journalctl --user -u magicmirror --since 12:20 --until 12:40 --no-pager -o short-iso` | 1 | allowed | allowed |  |
| 78 | `journalctl --user -u magicmirror --since -6h --no-pager -o short-iso` | 1 | allowed | allowed |  |

## Bypass attempts

Every row below is in the refusal table of `tests/test_ops_exec.py` with its
reason asserted; the table has 262 rows. Grouped by technique, with the
verdict (all refused unless marked):

| Technique | Tried | Result |
| --- | --- | --- |
| The plan's table | all 10 rows | refused, each with a specific reason |
| Combined short flags | `sort -uo out f`, `tail -fn 5 f`, `tail -5f f`, `journalctl -fu x`, `date -us 2020`, `ss -tK`, `file -bC -m m`, `git branch -rd x` | refused; reason names the letter and the word (`given as '-uo'`) |
| Value letters | `sort -to f` (`-t o`), `file -mC f` (`-m C`), `date -Iseconds` | allowed: getopt reads these as values, and so does the scanner |
| Attached values | `sort -o/home/pi/out f`, `grep -f~/.ssh/id_rsa x`, `grep -fid_rsa x` | refused |
| Abbreviated long options | `sort --out=x`, `sort --o=x`, `sort --compress=sh`, `git log --out=x`, `git log --outp x`, `git show --ext=x`, `git diff --no-inde a b`, `tail --fol f`, `journalctl --vac=1M`, `date --se 2020`, `ss --ki`, `grep --rec x /home` | refused |
| `--output x` and `--output=x` | sort, git log, git show | refused |
| Flag after an operand | `sort f -o out`, `tail -n 5 -f f`, `journalctl -n 5 -f` | refused |
| `--` | `sort f -- -o out` refused (forbidden spellings stay forbidden); `uniq -- -c out` refused (two operands); `hostname -- evil` refused; `date -- 0101` refused; `pm2 logs -- --nostream` refused (`--nostream` after `--` is not a flag); `git log -- --output=x` refused (over-refusal) | as stated |
| Value posing as subcommand | `systemctl -p status restart x`, `npm --prefix ls install`, `npm -g install x`, `pm2 -s restart all` | refused: subcommand must come first |
| Subcommand abbreviation | `ip link s eth0 down` (`s` is `set` in iproute2) | refused: only exact `show`/`list` |
| git global options | `git -c core.pager=sh log`, `git -c alias.st=!sh st`, `git --config-env=…`, `git --exec-path=/tmp log`, `git -p log` | refused |
| git mutating listings | `git branch evil`, `-d`, `-D`, `-m`, `--set-upstream-to=…`; `git tag v9`, `-d`, `-a -m`; `git remote add`, `set-url`, `prune` | refused |
| git reading outside the repo | `git diff --no-index /etc/hostname /home/pi/x`, `git blame --contents ~/.ssh/id_rsa x`, `git show HEAD:.env`, `git -C /home/pi/.ssh log` | refused |
| ip | `-batch f`, `-force`, `netns exec x sh`, `addr add`, `route flush all`, `link set` | refused |
| Never-returning reads | `tail -f/-F/--follow/--retry/+5f`, `journalctl -f`, `pm2 logs`, `free -s 1`, `vmstat 1`, `iostat 2`, `… \| tail -f` | refused |
| Environment leaks | `printenv`, `env`, `/proc/self/environ`, `/proc/1234/environ`, `ps auxe`, `ps eww`, `systemctl show-environment`, `pm2 jlist` | refused |
| Relative, `~`, `..`, `//`, quoting, escapes, case | see "Path deny list" | refused |
| Globs and expansions | `~/.ss?/id_rsa`, `~/.ssh/*`, `[.]ssh`, `.{s,}sh`, `$HOME/…`, `${HOME}`, `"$HOME/x"`, `$'\x2essh'` | refused by the lexer |
| Recursion around the deny list | `grep -r BEGIN` with roots `/home/pi`, `~`, `~/`, `~pi`, `.`, none, `/`, `//home//pi/`, `…/..`, `/proc/1234/task`, `/sys/class/net`; `grep -re x /home/pi`; `grep -r -e x -e y /home/pi/MagicMirror /etc`; `--recursive`, `--rec`, `-R`, `--dereference-recursive`, `-d recurse`, `--directories=recurse`; `egrep -r` | refused |
| Command substitution | `$(…)`, `"$(…)"`, backticks bare and in double quotes | refused |
| Process substitution, here-docs | `<(ls)`, `>(sh)`, `<<EOF`, `<<<` | refused |
| Redirection | `>`, `>>`, `<` of a denied path, `2>/dev/null`, `2>&1` | refused |
| Separators and background | `;` (with and without spaces), `&&`, `\|\|`, `&`, `\|&`, newline, carriage return, NUL, `( … )`, `{ …; }`, `#` | refused |
| Assignment prefixes | `FOO=bar uptime`, `A=1 B=2 ls`, `PATH=/tmp ls` | refused |
| Quoting a program or flag | `"rm" -rf`, `'sudo' reboot`, `\sudo reboot`, `sort '-o' out f`, `tail "-f" f`, `ls \| 'sh'`, `sh -c "rm x"`, `bash -c 'id'` | refused |
| Pipes | `ps aux \| rm -rf /`, `\| sh`, `\| xargs rm`, `\| uniq - out`, unquoted `a\|b` pattern, `ls \|`, `\| ls`, `ls \| \| ls` | refused |
| Program paths | `/tmp/cat`, `./cat`, `/usr/bin/../../tmp/cat`, `/usr/bin/sudo`, `/usr/bin/env` | refused |
| Parse failures | unbalanced `'`, unbalanced `"`, trailing `\`, empty, blank, 4001 characters | refused |

Quote handling in the other direction (criterion 6), allowed: `grep "rm -rf"
/var/log/syslog`, `grep 'sudo reboot; rm -rf /' …`, `grep ">" …`, `echo 'a
&& b'`, `grep -E 'a|b' …`, `cat \;` (an escaped `;` is a file name, as bash
sees it).

A further sweep of about 150 commands outside the table found nothing that
mutates. What it did find is listed under "Residual risks": reads that the
current deny list does not cover.

## Tests

`./scripts/run_tests.sh`, final run on the committed code:

```
=========== 1396 passed, 2 skipped, 18 xfailed, 3 warnings in 11.67s ===========
```

Baseline before M3 was `992 passed, 2 skipped, 18 xfailed`. The ops test
file went from 40 tests to 444 (`tests/.venv/bin/python -m pytest
tests/test_ops_exec.py -q` → `444 passed in 0.53s`), so 992 − 40 + 444 =
1396; no other test changed. The three warnings are the existing Pydantic
deprecations in `orchestrator/main.py`.

What the file covers:

- `REFUSED`, 262 rows, `(command, reason substring)`; every row asserts the
  reason. Grouped A1 to A7 to match criteria 1 to 7.
- `LEGACY_ALLOWED` (the pre-M3 allowed table, 15 rows, unchanged),
  `AUDIT_LOG_ALLOWED` (the 62 allowed forms from the audit log, count
  pinned), `NEWLY_ALLOWED` (76 rows: the quirk fixes, bounded log reads,
  read-only forms of every program that got rules, quoted forbidden words).
- Every `DENIED_PATH_PATTERNS` entry refuses a concrete path; the list is a
  single tuple; `env`, `printenv`, `docker` are not in `_RULES`.
- The lexer's words equal bash's words for quoted input.
- Registry: the env fallback and `ops/hosts.example.json` carry no `sudo`.
- Route: a refused command returns 422 `refused: <reason>`, writes one audit
  line with `allowed: false`, `exit_code: null` and the reason as `note`, and
  never reaches `_run`.

## Dev path (criterion 12)

`docker compose --profile dev up -d --build --no-deps tool_service-dev
orchestrator-dev` (dry run first: only the two dev containers named). Only
`kronk-tool_service-dev-1` was recreated; `orchestrator-dev`'s image was
unchanged. Production container ids and start times before and after are
identical (14 containers checked).

Dev has no registry (`/ops/hosts.json` absent, no `MM_SSH_*`), so
`/ops/exec` answers 404 before the classifier runs. To reach the classifier
I wrote a throwaway `ops/hosts.json` (gitignored; mounted only into
`tool_service-dev`, production mounts `../kronk-releases/sprint-M1b/ops`)
with one host:

```json
{"m3-test": {"ssh_target": "nobody@192.0.2.1", "key": "/nonexistent/m3-test-key", ...}}
```

192.0.2.1 is TEST-NET-1 (never routed); the key does not exist, and
`ops_exec` checks the key before it builds the SSH command. The file was
deleted after the test; `/ops/exec` on dev answers 404 "known hosts: (none
configured)" again.

Results, 05:07 UTC, `POST 127.0.0.1:8103/ops/exec {"host": "m3-test", ...}`:

| Command | Status | Detail |
| --- | --- | --- |
| `env rm -rf /home/pi/MagicMirror` | 422 | `refused: 'env' is not on the read-only allowlist: it runs any program given as an argument` |
| `env sh -c "id"` | 422 | same |
| `sort -o /home/pi/.bashrc /tmp/x` | 422 | `refused: sort: -o writes a file` |
| `uniq /etc/hostname /home/pi/out` | 422 | `refused: uniq: at most one file operand; a second operand is an output file that uniq writes` |
| `git log --output=/home/pi/x` | 422 | `refused: git log: --output writes a file` |
| `ip link set eth0 down` | 422 | `refused: ip link: only a bare object or 'show'/'list' is allowed, got 'set'` |
| `cat /home/pi/.ssh/id_rsa` | 422 | `refused: cat: argument '/home/pi/.ssh/id_rsa' matches the secrets deny list ('.ssh'); secrets must not reach the model` |
| `printenv` | 422 | `refused: 'printenv' is not on the read-only allowlist: it prints the environment, which holds secrets` |
| `tail -f /var/log/syslog` | 422 | `refused: tail: -f follows forever and never returns (burns the 30 s timeout); use -n N` |
| `pm2 logs` | 422 | `refused: pm2: 'pm2 logs' streams forever; add --nostream (and --lines N)` |
| `sort -uo out f` | 422 | `refused: sort: -o writes a file (given as '-uo')` |
| `cat ~/.ss?/id_rsa` | 422 | `refused: contains unquoted '?' (glob): …` |
| `echo $(reboot)` | 422 | `refused: contains '$(' (command substitution): …` |
| `sh -c "rm x"` | 422 | `refused: 'sh' is not on the read-only allowlist (mutations are not enabled yet)` |
| `cat 'unbalanced` | 422 | `refused: could not parse command: unbalanced single quote` |
| `sudo reboot` | 422 | `refused: sudo is not allowed in read-only mode` |
| `grep "rm -rf" /var/log/syslog` | 500 | `SSH key not found at /nonexistent/m3-test-key` (classifier allowed it; stopped at the key check) |
| `git -C /home/pi/MagicMirror status --short` | 500 | same |
| `uptime` | 500 | same |

`data/dev/ops_audit.log` did not exist before and has 16 lines after, one
per refusal, each `allowed: false`, `exit_code: null`, with the reason as
`note`. The three allowed commands wrote no line: `ops_exec` audits after a
run, and these stopped at the key check, which is the unchanged behaviour.

No SSH: `tool_service-dev` has no `/keys` mount and no `MM_SSH_*`; its
process list after the test held two processes and no `ssh`;
`/data/mm_known_hosts` (written by the first SSH connection) does not exist;
Tempo has no `ssh.exec` span from any service in the last 24 hours.
Production's audit log is untouched: 87 lines, md5 `a84455d0…`, the value
M17's test recorded.

## Voice replay (criterion 15)

`python3 scripts/voice_replay.py --label m3-pre` before the dev rebuild
(dev on `b107cef`, pre-M3 code) and `--label m3-post` after it (dev
`tool_service` on `8a648e6`; the file records `28a42e8`, the HEAD at run
time). Pipeline `kronk-dev`, dry run on, corpus hash `6e4c8bd21eb979fc` in
all three files.

| Run | File | pass | xfail | fail | skip |
| --- | --- | --- | --- | --- | --- |
| committed baseline | `voice_replay_2026-11-16_012824_m10-baseline.json` | 91 | 20 | 1 | 2 |
| before | `voice_replay_2026-11-16_045926_m3-pre.json` | 90 | 20 | 2 | 2 |
| after | `voice_replay_2026-11-16_050809_m3-post.json` | 91 | 20 | 1 | 2 |

All runs: 46 executed, 66 checked read-only, 17 players unchanged. The
script exits 1 whenever a row fails; it did in all three, on the baseline's
known fail `inc-0930-search-deep-house-ytm`.

After matches the baseline row for row: every one of the 114 rows has the
same status and method. One row differed only in the before run, on pre-M3
code: `eval-search-youtube-music-for-some-deep-chill` ("Search YouTube Music
for some deep, chill house music and play that.") — no tool call; the
coordinator answered "I am unable to search YouTube Music for specific
genres…" (trace `f7c20f5e2bd0e1371945f550dd9dd529`). That is the "search …
on YouTube Music" variance M1 and M1b recorded, and it happened before any
M3 code was deployed. M3 changes only `/ops/exec`, which no corpus row
reaches.

## Guard rails

- No command to the MagicMirror Pi or any managed host. Production
  `/ops/exec` not called. The only `/ops/exec` calls went to
  `tool_service-dev` against a TEST-NET-1 host with no key.
- Production containers, HA and Music Assistant untouched: ids and start
  times of all 14 non-dev containers identical before and after.
- `data/ops_audit.log` read only (87 lines, md5 unchanged). `.env` and
  `.env.dev` not read or edited. No `down -v`, no `--remove-orphans`, no
  `pipeline_bench.sh`, no sound (dry run on, players unchanged).
- Files created outside git's tracking: `ops/hosts.json` (gitignored,
  deleted after the dev test), `data/dev/ops_audit.log` (16 refusal lines
  from the dev test, left in place as evidence; dev data).

## Deviations from the plan and criteria

1. **Forbidden flags after `--` are refused** (criterion 3, "a flag after
   `--` handled correctly"). For operand counting `--` is honoured; for
   forbidden spellings it is not, so `sort f -- -o out` and `git log --
   --output=x` are refused although the program would read them as file
   names. Chosen because not every program honours `--` and no legitimate
   read needs a file named `-o`.
2. **The deny list applies to every argument, grep patterns included.**
   `grep token f` and `grep .env f` are refused. Exempting the pattern
   operand needs an exact model of grep's value-taking options, and a
   mistake there would exempt a real file. Over-refusal instead.
3. **Deny list entries beyond the plan's wording.** The plan's list is
   ".ssh/, id_*, .env, shadow, key and token files". "Key and token files"
   is read as `*.key`, `*_key` (SSH host keys), `*.pem`, `*.p12`, `*.pfx`,
   `*token*`, `.netrc`, `.git-credentials`. `environ` is added because
   `/proc/<pid>/environ` is `printenv` by another name, and the plan removes
   `printenv` for that reason. Each is one line in the tuple.
4. **Recursive-read rule (not in the plan).** Criterion 4 asks that globs or
   expansions that could reach a denied path be refused; `grep -r` does the
   same thing without a glob. The depth rule is the narrowest rule that
   keeps the audit log's `grep -r` forms working.
5. **`docker` removed** (plan names only `env` and `printenv`). Unused, and
   it needed three special rules to be safe.
6. **Extra narrowing the plan did not list**: `systemctl show-environment`;
   `pm2 jlist/prettylist/show/describe/info/env`; `ps` BSD `e`; `date`
   operands; `hostname -F/-b`; `hostnamectl` set forms; `ss -K/-D`;
   `sensors -s`; `free -s`; `vmstat`/`iostat` intervals; `sort
   --compress-program` and `--files0-from`; `git` global options, `branch`,
   `tag`, `remote`, `--ext-diff`, `--no-index`; `vcgencmd` command list;
   program paths limited to system bin directories.
7. **`node --version` added** as an exact form (refused before; in the old
   plan's phase-A list; asked for once in the log).
8. **Criterion 12 needed a dev registry entry.** Dev has no hosts, so the
   classifier is unreachable there without one. The throwaway
   `ops/hosts.json` is described under "Dev path" and was removed.
9. **No ops doc under `docs/features/`.** The brief points there; none
   exists for ops exec (phase A shipped 2026-07-11 before the feature-doc
   convention). The rule set lives in this report and the module
   docstring; `docs/plans/MAGICMIRROR_PLAN.md` carries a status note.
   Writing `docs/features/ops-exec.md` is a follow-up if Drew wants one.
10. The plan's `uniq` limit ("one") is applied with naive operand counting:
    `uniq -f 1 f` is refused (write `-f1`). Correct for safety, slightly
    strict.

## Residual risks

What this classifier does not stop, most serious first. None mutates; all
are reads that reach the model and the traces.

1. **Recursive grep inside a deep tree reads everything in it.** `grep -r ''
   /home/pi/MagicMirror` is allowed and prints every file under the mirror,
   including any module `.env`, `config/config.js` and `.git/config` (which
   holds a token if a remote URL embeds one). The deny list cannot see files
   reached by recursion. Closing it means refusing `-r` (the audit log's
   five `-r` uses would need rewriting; four of them search a single file),
   or a deeper minimum.
2. **Secrets the deny list does not name.** Readable today, by name:
   `MagicMirror/config/config.js` (module API keys; read twice in the log),
   `~/.bash_history`, `~/.npmrc`, `~/.config/**` (e.g. `gh/hosts.yml`),
   `~/.docker/config.json`, `~/.aws/credentials`, Wi-Fi keys in
   `/etc/wpa_supplicant/wpa_supplicant.conf` and
   `/etc/NetworkManager/system-connections/*` (root-only on stock Pi OS),
   `/proc/<pid>/cmdline`, `Environment=` lines via `systemctl show` or
   `systemctl cat`, secrets in `journalctl` output, secrets in git history
   via `git log -p`, arguments in `ps aux`.
3. **Raw devices.** `cat /dev/mmcblk0p2`, `grep -a … /dev/mmcblk0p2`,
   `head /dev/mem`, `cat /proc/kcore` pass the classifier; they work only
   if the login user is root or in the `disk`/`kmem` groups. Not checked on
   the Pi (no command was sent). A prefix rule for `/dev/` (other than
   `/dev/null`) would close it; it is a different shape from the component
   patterns, so it is left for Drew.
4. **Existing symlinks on the Pi.** A symlink in an allowed path that points
   at `.ssh` is followed by `cat`. Creating one needs write access, which
   the classifier denies, so only a pre-existing link matters.
5. **Remote shell assumption.** The lexer models bash and POSIX sh. A zsh
   login shell would expand `=cmd` and extended-glob forms it does not
   refuse. The Pi's login shell for `pi` is bash by default; not checked.
6. **Configuration on the host still runs code.** `git status`/`diff` honour
   the repository's `core.fsmonitor`, hooks, textconv and pager settings;
   `pm2 list` starts a pm2 daemon if none runs; `npm` reads `.npmrc`. These
   need write access on the host to weaponise.
7. **Network reads.** `npm view`, `npm outdated` and `git remote show`
   contact the registry or the remote. Read-only, but their responses are
   untrusted text in the model's context (M4's subject).
8. **Commands that read stdin with no input** (`cat`, `sort`, bare `grep x`)
   wait for the 30 s timeout. Unchanged from before; a nuisance, not a
   hole.
9. **The key is still general-purpose.** This classifier is the only
   control between the model and a shell on the Pi
   (`docs/incidents/INVESTIGATION_2026-08-14_mm_banner.md`, tenet-10 note).
   Re-scoping the key remains Drew's decision.

## Left for Drew

- **Deny list width** (open question, plan "Still open"). As built: the
  plan's list plus the interpretations in deviation 3. Candidates, each a
  one-line addition to `DENIED_PATH_PATTERNS` unless noted:
  - `config.js` — blocks MagicMirror's config (module API keys), which the
    agent reads routinely to debug modules. The biggest trade-off.
  - `.bash_history`, `.npmrc`, `credentials`, `*.nmconnection`,
    `wpa_supplicant*`, `.docker`, `hosts.yml`.
  - `/dev/` raw devices: needs a prefix check, not a pattern (about five
    lines).
  - Recursion: keep `RECURSIVE_ROOT_MIN_DEPTH = 3`, raise it, or refuse
    `grep -r` altogether.
- Whether to write `docs/features/ops-exec.md`.
