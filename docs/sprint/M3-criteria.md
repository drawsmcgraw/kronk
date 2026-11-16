# M3 pass criteria: harden the read-only exec classifier

Written by the sprint coordinator on 2026-11-15, before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M3. Harden
the read-only exec classifier" and the M3 line of "Verified on Kronk,
2026-11-15"). The tester checks against this file, not against the build
report.

## Scope

In: `tool_service/ops.py` `classify_readonly()` and its tests; the three
roadmap quirks where the classifier is too strict (`git -C`, quoted pipes;
`journalctl --user` is Pi behaviour and is documented, not "fixed").

Out: the MagicMirror Pi (no command is sent to it by either agent in this
item); the mutation path (`mm_update`); `http_check` (M5); the devops agent's
tool list (M4).

Open with Drew: how wide the path deny list should be. Until he answers,
the list is the plan's: `.ssh/`, `id_*` key files, `.env` files, `shadow`,
and key and token files. It lives in one named constant so widening it is a
one-line change, and the build report lists exactly what it matches.

## Criteria

### A. What must now be refused

1. `env` and `printenv` are not in the allowed set. Every row of the
   plan's table is refused by `classify_readonly()` with a specific reason:
   `env rm -rf /home/pi/MagicMirror`; `env sh -c "id"`;
   `sort -o /home/pi/.bashrc /tmp/x`; `uniq /etc/hostname /home/pi/out`;
   `git log --output=/home/pi/x`; `ip link set eth0 down`;
   `cat /home/pi/.ssh/id_rsa`; `printenv`; `tail -f /var/log/syslog`;
   `pm2 logs`.
2. Per-program rules exist, not a flat allowlist: forbidden flags
   (`sort -o`/`--output`, `git --output`, `date -s`/`--set`,
   `tail -f`/`--follow`/`-F`, `journalctl -f`/`--follow`, `file -C`);
   positional limits (`uniq` at most one, `hostname` none); `ip` allows
   only a bare object or `show`/`list` after it; `pm2 logs` requires
   `--nostream`.
3. Flag spellings that reach the same effect are refused too: combined
   short flags (`sort -uo out`, `tail -fn 5`), `--output file` and
   `--output=file`, an abbreviated long option where the program accepts
   one (`--out=x`), and a flag after `--` handled correctly.
4. The path deny list applies to every program that reads files (`cat`,
   `head`, `tail`, `less`, `grep`, `sort`, `uniq`, `wc`, `file`, `stat`,
   `ls` where it reveals contents, and any other in the allowed set), in
   any argument position, including relative paths, `~`, `..` segments and
   repeated slashes. Globs or shell expansions that could reach a denied
   path are refused.
5. Shell features that defeat the classifier are refused unless a rule
   proves them safe: command substitution (`$(...)`, backticks), process
   substitution, redirection to files (`>`, `>>`, `<` of a denied path),
   `;`, `&&`, `||`, background `&`, here-docs, and variable assignment
   prefixes. A pipe is allowed only when every segment is itself allowed.
6. The quote-blind bugs are fixed in both directions: a forbidden word
   inside a quoted argument does not cause a refusal
   (`grep "rm -rf" /var/log/syslog` is allowed), and quoting does not hide
   a real one (`sh -c "rm x"` is refused; a pipe character inside quotes
   is not treated as a pipe, and a real pipe to a forbidden program is
   refused).
7. Refusal is the default: an unknown program, an unparseable command
   line (unbalanced quotes), or an empty command is refused.

### B. What must keep working

8. Every command form in the existing allowed table of
   `tests/test_ops_exec.py` still passes, and every read-only form
   actually used in `/data/ops_audit.log` (87 entries) is still allowed.
   The build report lists the distinct forms from the log and the
   classifier's verdict on each; any that is now refused is named and
   justified.
9. The two too-strict quirks are fixed: `git -C <dir> <read-only
   subcommand>` is allowed (and `git -C <dir> <mutating subcommand>` is
   refused), and a quoted pipe character in an argument is allowed. The
   `journalctl --user` note in `ROADMAP.md` is corrected to say it is Pi
   behaviour.
10. Bounded log reads work: `tail -n 50 file`, `journalctl -n 100
    --no-pager`, `pm2 logs --nostream --lines 50`.

### C. Tests and behaviour

11. `tests/test_ops_exec.py` has a refusal table covering every row of
    criteria 1 to 7 and an allowed table covering 8 to 10. Each refusal
    asserts the reason, not only the boolean.
12. The classifier is the only gate change: `ops_exec`'s audit logging,
    timeout and response shape are unchanged, and a refused command is
    still written to the audit log with its reason. Shown on the dev
    path: a refused command sent to `tool_service-dev`'s `/ops/exec`
    returns the refusal and appears in the dev audit log; nothing is sent
    over SSH (dev has no key; confirm no SSH process was started).
13. The dead `"sudo": true` registry field is either removed from
    `ops/hosts.example.json` and the docs, or documented as unused. The
    classifier still refuses `sudo`.
14. `./scripts/run_tests.sh` passes. Baseline before M3:
    `992 passed, 2 skipped, 18 xfailed`.
15. The M10 live replay on dev matches the committed baseline apart from
    known run-to-run variance.
16. No command was sent to the MagicMirror Pi or any managed host during
    build or test. Production containers, HA and Music Assistant untouched.

## Reports

Build: `docs/sprint/M3-build.md`. End to end: `docs/sprint/M3-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
