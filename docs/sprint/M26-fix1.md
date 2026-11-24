# M26 fix round 1: the script's two holes and three mutants

Fix agent, 2026-11-24. Scope: the last ruling in `M26-criteria.md` (S1, S2
and the three surviving mutants of N4 in `M26-e2e.md`). Two files changed:
`scripts/build_rocm_libs.sh` and `tests/test_m26_rocm_libs.py`.

## What changed

### S1: `build` resolves the path, then tests it

`cmd_build` now runs `realpath -m` on the path it was given and uses the
result for everything after: the emptiness test, `mkdir` and the
extraction. A symlink, `<dir>/new/..`, `<dir>/lib/..`, `<link>/.` and a
trailing slash all resolve to the real directory, which is then refused
if it is not empty. `realpath` resolves symlinks before `..` (physical),
so `<link>/new/..` lands on the link's target, not on the link's parent.

A path whose parent does not exist is refused (exit 2) before docker is
called; `mkdir -p` is gone, so the build creates at most the one directory
it was given, and `lib/` inside it. A `find` that cannot read the
directory is refused too: an unreadable directory used to read as empty.
Error messages name the given path and what it resolved to:

```text
build_rocm_libs: refusing …/demo/link: …/demo/real exists and is not empty (build into a new directory)
build_rocm_libs: refusing …/demo/real/new/..: …/demo/real exists and is not empty (build into a new directory)
build_rocm_libs: refusing …/demo/nope/rocm: its parent directory …/demo/nope does not exist (create it first)
```

A symlink to an empty directory is accepted and the target is filled
(the moved-to-another-disk case still builds; tested).

### S2: `manifest` and `verify` read the listing NUL-separated

`manifest_body` reads `find -printf '%P\0%y\0%m\0%s\0'` with
`read -d ''`, so a newline in a name can no longer split one entry into
two lines. A name holding whitespace or a control character is refused,
as before but now reliably, including a tab (the old message was
`unexpected file type 'b': a`). A link target is checked the same way;
it was printed raw before. The target is read as `$(readlink …; echo x)`
and the `x` stripped, so a target ending in a newline is not shortened by
the command substitution and seen as the real target (without that, a
link to `libroctx64.so.4.1.70200<newline>` passes as a link to the
library).

Every refusal exits non-zero and names the entry with `printf %q`:

```text
build_rocm_libs: refusing entry libroctx64.so.4: its link target $'libroctx64.so.4.1.70200\nlibroctx64.so.4.1.70200\tf\t664\t5\tf60ed864…' holds whitespace or a control character
build_rocm_libs: could not read …/demo/v/lib
rc=1
build_rocm_libs: refusing entry $'a\tb': its name holds whitespace or a control character
```

`manifest` now returns 1 when `manifest_body` fails
(`body=$(manifest_body "$1") || return 1`). Before, inside verify's
`got=$(manifest …) || die`, errexit was off and a failed body was
printed as a partial manifest.

Side effect, not ordered: N5's mode-000 case now prints one line from
`sha256sum` and `could not read <dir>/lib` (exit 1) instead of the
hundreds of `missing:` lines N5 describes. The rest of N5 is unchanged.

### The manifest format is unchanged

Same fields, same separators, same sort. Read only, after the change:

```text
$ scripts/build_rocm_libs.sh verify ~/services/rocm-7.2
build_rocm_libs: /home/drew/services/rocm-7.2/lib matches its manifest (files=1803 links=10 dirs=2 bytes=1944240155)
rc=0
$ sha256sum ~/services/rocm-7.2/MANIFEST
200d6829ce387f787b5e7cc2093edd4a30ae0c8698954769a904f195e9b7b539  /home/drew/services/rocm-7.2/MANIFEST
```

The verify took 8.1 s.

## Tests (23 new, all offline)

All of them run the script with a fake `docker` on PATH (the existing
`_fake_docker`), or no docker at all. None calls the real docker or opens
a socket.

- `test_build_refuses_a_non_empty_directory_however_named`, 7 cases:
  `link`, `link/`, `link/.`, `real/`, `real/new/..`, `link/new/..`,
  `real/lib/..`. Each exits 2, says "not empty" and names the resolved
  directory, makes no docker call, leaves the directory's bytes unchanged
  and creates no `new`.
- `test_build_refuses_a_directory_whose_parent_does_not_exist`: exit 2,
  the message, no docker call, nothing created.
- `test_build_through_a_symlink_to_an_empty_directory_fills_the_target`.
- `test_build_sets_umask_022_whatever_the_callers` (N4, `umask 022`):
  build under umask 077; `<dir>`, `lib/` and `lib/rocblas` are 755, the
  manifest row reads `rocblas d 755`, `MANIFEST` is 644. The source's
  `rocblas/` is 711, which shows the directory's mode is not taken from
  the archive (`tar -cf - rocblas/library` does not carry `rocblas/`).
- `test_manifest_is_sorted_whatever_order_the_directory_lists` (N4, no
  sort): eight names created in shuffled order; it asserts first that the
  directory lists them unsorted (on tmpfs `find` returns newest first),
  then that the manifest rows are in byte order.
- `test_manifest_sorts_under_lc_all_c_whatever_the_callers_locale` (N4,
  sort outside `LC_ALL=C`): this host's `sort` is uutils 0.2.2, which
  ignores the locale (`en_US.UTF-8` sorts by byte), so on its own the
  mutant behaves the same as the real script. The test puts GNU `sort`
  (`/usr/bin/gnusort`, coreutils 9.5) first on PATH as `sort` and runs
  `manifest` with `LC_ALL=en_US.utf8`. It asserts first that this sort
  orders the names differently from C, then that the manifest is in C
  order. It skips with a reason if no GNU sort or UTF-8 locale exists; it
  ran here (0 skipped in this file).
- `test_verify_refuses_a_link_target_carrying_a_forged_line`: the test
  report's S2 attack (library deleted, soname link's target =
  `<file>\n<the deleted file's manifest line>`). Exit 1, no "matches",
  the refusal names `libroctx64.so.4`, at most three stderr lines.
- `test_verify_and_manifest_refuse_a_link_target_with_whitespace`, 5
  targets: trailing newline, newline, tab, space, carriage return.
  `verify` and `manifest` both exit non-zero and name the entry.
- `test_verify_and_manifest_refuse_a_name_with_whitespace`, 5 names:
  `a\nb`, `evil\n`, `a\tb`, `a b`, `rocblas/library/x\ny`. Both modes
  exit non-zero and show the name escaped (`$'a\tb'`).

The script at HEAD (before this round) against the new test file:
`17 failed, 34 passed`. The 17 are every S1 and S2 case except `real/`
and `real/lib/..`, which HEAD already refused. The three N4 tests pass on
HEAD, as they should: they test for mutants, and HEAD has the sort and
the umask.

## Mutants

Each one is a copy of the script with one change, run against the new
`tests/test_m26_rocm_libs.py` in a scratch tree (51 tests, baseline 51
passed). All 12 are killed:

| Mutant | Result | Killed by |
| --- | --- | --- |
| N4: `\| LC_ALL=C sort` → `\| sort` | 1 failed | `…sorts_under_lc_all_c…` |
| N4: `\| LC_ALL=C sort` → `\| cat` | 1 failed | `…sorted_whatever_order…` |
| N4: `umask 022` removed | 1 failed | `…umask_022…` |
| S1: no `realpath` (`dir=$given`) | 5 failed | `…however_named` |
| S1: `realpath -m -s` (logical `..`) | 4 failed | `…however_named` |
| S1: parent check off, `mkdir -p` back | 1 failed | `…parent_does_not_exist` |
| S1: emptiness check off | 8 failed | both non-empty tests |
| S2: HEAD's `manifest_body` | 11 failed | all three S2 tests |
| S2: link target check off | 6 failed | forged line, target whitespace |
| S2: plain `$(readlink)` (no `x`) | 1 failed | target with a trailing newline |
| S2: name check off | 5 failed | name whitespace |
| S2: `manifest` ignores a failed body | 11 failed | all three S2 tests |

`--no-same-owner` removed stays unkillable for a non-root caller (N4);
not attempted again.

## Suite

`./scripts/run_tests.sh`, before 7464 passed, 3 skipped, 54 xfailed:

```text
===== 7487 passed, 3 skipped, 54 xfailed, 6 warnings in 102.85s (0:01:42) ======
```

## What I did not do

- No `build` against `~/services` or anything under it; only the one
  read-only `verify` above. No real docker call: no run, pull or build.
  Everything ran against the fake.
- No unit restarted, stopped, started or installed; nothing written under
  `~/services`, `~/pai`, `~/.config/systemd`, `~/backups` or `/usr/local`.
- The list, the digest, the unit files and the docs are untouched. No doc
  line is made false: README's "the script refuses a non-empty one" is
  now true for every spelling of the path.
- N5's other points (a stray file beside `lib/`, a symlinked `lib/`, the
  manifest not being a signature) and N6, N7: not this round's.
- The root refusal is still untested (`unshare -r` is not permitted here,
  as the test report found).

Scratch: the mutant trees, the HEAD copy and the demo directories were
deleted. The mutant runner is left in the session's scratch directory
(`mutants.py`).
