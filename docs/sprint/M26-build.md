# M26 build report: nothing depends on the Ollama install

Builder: Opus 5.5, 2026-11-24. Criteria: `docs/sprint/M26-criteria.md` at
`3006ef9`. Nothing was installed, restarted, stopped or started; nothing
was written under `~/services`, `~/pai`, `~/.config/systemd` or
`/usr/local`; no audio was sent to 10300 or 10301; no docker pull or
build. The only GPU contact was the two device enumerations of criterion 5.

`$S` below is this session's scratch directory
(`/tmp/claude-1000/-home-drew-git-repos-drawsmcgraw-kronk/744643f2-afc2-4235-8d4a-ea8a81bcee6d/scratchpad`,
a tmpfs). Both extracted directories in it were deleted at the end.

## What changed

| File | Change |
| --- | --- |
| `scripts/build_rocm_libs.sh` | New. `build <dir>`, `verify <dir>`, `manifest <libdir>`, `list`. The fixed list (ten sonames as soname link plus versioned file, and `rocblas/library`), the image by digest, the tar-stream extraction, the manifest. Header: purpose, usage, where the libraries come from, why `hipblaslt/library` and the four system libraries are not taken, how it extracts, the manifest format, what a pruned image needs, how to pin a new image. |
| `systemd/wyoming-whisper.service` | `LD_LIBRARY_PATH`: `/usr/local/lib/…/rocm` replaced by `%h/services/rocm-7.2/lib`; a three-line comment above it naming the script and the verify command. |
| `systemd/wyoming-whisper-dev.service` | The same. |
| `systemd/llama-talkie.service` | `LD_LIBRARY_PATH`: the same replacement, spelled `/home/drew/services/rocm-7.2/lib` as the unit spells its other paths; the header comment's three lines on Ollama's directory replaced by five naming the new directory and the script. |
| `scripts/model_bench.py` | Deleted (`git rm`). |
| `tests/test_m26_rocm_libs.py` | New, 28 tests: criterion 1, and criterion 3 offline (fake `docker` on `PATH`). |
| `README.md` | llama.cpp section: Talkie's `ini` block and its sentence point at `rocm-7.2`. Whisper units: one paragraph on their two library directories. New section "ROCm runtime libraries (`~/services/rocm-7.2/`)". "Building llama.cpp for gfx1151": one sentence tying the build image to the runtime libraries. |
| `docs/features/voice-pipeline.md` | Key design decisions: the CTranslate2 bullet names the build image and script; a new bullet on the Whisper units' libraries. |

Not touched: `ROADMAP.md` (the coordinator's), M25's files,
`~/services/wyoming-whisper/runtime-libs`.

## The script

```text
scripts/build_rocm_libs.sh build <dir> [--image <ref>]   extract into <dir>/lib, write <dir>/MANIFEST, then verify
scripts/build_rocm_libs.sh verify <dir>                  exit 1 on a missing, extra or changed entry or another image
scripts/build_rocm_libs.sh manifest <libdir>             print the manifest a directory would have (no docker)
scripts/build_rocm_libs.sh list                          the 21 members taken from the image
```

Order of checks in `build`, all before docker except the last three:
`--image` must equal `rocm/dev-ubuntu-24.04@sha256:86e11093…683a` (a tag,
another digest or another repo: exit 2, with how to pin on purpose); not
root (exit 2: the files must be the caller's); the directory absent or
empty (exit 2); `docker image inspect` gives that digest as the local
image's id, else exit 3 with the pull-by-digest line (it never pulls); the
image's `/opt/rocm-7.2.0/.info/version` is `7.2.0`; then the extraction.

Every container run is
`docker run --rm --network none --pull never --read-only --cap-drop ALL --security-opt no-new-privileges --entrypoint <cat|tar> <image@digest> …`,
with no `-v` or `--mount`. The archive is
`tar -C /opt/rocm-7.2.0/lib -cf - <21 members>` on stdout, unpacked by the
host's GNU tar 1.35 with `-x -p --no-same-owner` under `umask 022` (the
image's modes; the caller as owner; leading `/` and `..` members dropped by
tar). After unpacking, `check_extracted` refuses (exit 1, no manifest
written) unless: each versioned file is a regular file, each soname link
points at its versioned file by bare name, the top level is exactly the 20
names plus `rocblas`, `rocblas/` holds only `library`,
`TensileLibrary_lazy_gfx1151.dat` is present, there is no `hipblaslt`
directory and no link with a `/` in its target.

Manifest format (`<dir>/MANIFEST`, beside `lib/`):

```text
# ROCm runtime libraries for Whisper (CTranslate2) and Talkie (llama-server).
# Written by scripts/build_rocm_libs.sh; check with: scripts/build_rocm_libs.sh verify <dir>
image	rocm/dev-ubuntu-24.04@sha256:86e11093b4a7ec2a79b1b6701d10e840a6994f21c7e05929b51eb9be361c683a
rocm-version	7.2.0
source	/opt/rocm-7.2.0/lib
omitted	hipblaslt/library	left out on purpose: the units ran without it before M26
totals	files=1803 links=10 dirs=2 bytes=1944240155
entries
<path> TAB <f|l|d> TAB <mode> TAB <size, files only> TAB <sha256 | link target | ->
…
```

Entries sorted under `LC_ALL=C`, paths relative to `lib/`. No time, host,
user or destination. `verify` regenerates the whole text (header from the
script's pinned constants, body from the directory) and compares; on a
difference it lists `missing:`, `extra:`, `changed:` per path and
`header:` lines (another image, other totals), and exits 1. A directory
moved to another path still verifies.

## Criteria

### 1. A test fails if any file under `systemd/` names ollama — met

`tests/test_m26_rocm_libs.py::test_no_unit_file_names_the_ollama_install`
reads every file in `systemd/` and fails on any line containing the word
(case-folded). The word is built as `"oll" + "ama"`, and the file holds
neither Ollama path nor the port, so criterion 6's grep does not find it.
A second test (`test_units_load_the_extracted_directory_in_the_old_order`,
three cases) pins each unit's `LD_LIBRARY_PATH` list and checks the
unit's comments name the script.

On the tree before this item (`git archive 3006ef9 systemd` into
`$S/before-tree`, the test file copied beside it, the test venv's pytest
run there): both tests fail. Its first lines:

```text
E       AssertionError: unit files name the Ollama install:
E         llama-talkie.service:8: # /usr/local/lib/ollama/rocm (LD_LIBRARY_PATH below), as both Whisper units
E         llama-talkie.service:9: # do: removing Ollama must leave that directory.
E         llama-talkie.service:22: Environment="LD_LIBRARY_PATH=/usr/local/lib/ollama/rocm:/home/drew/pai/pai_workspace/llama-cpp/talkie-gfx1151-rocm7"
E         wyoming-whisper-dev.service:42: Environment=LD_LIBRARY_PATH=%h/services/wyoming-whisper/runtime-libs:/usr/local/lib/ollama/rocm
E         wyoming-whisper.service:10: Environment=LD_LIBRARY_PATH=%h/services/wyoming-whisper/runtime-libs:/usr/local/lib/ollama/rocm
```

On this tree they pass, and `grep -rni ollama systemd/` finds nothing.

### 2. `systemd-analyze --user verify`; only the one path changed — met

`systemd-analyze --user verify systemd/<unit>` exits 0 with no output for
each of the three, before (copies from `3006ef9`) and after.

By diff (`git diff systemd/`): in each unit the one `LD_LIBRARY_PATH` line
changed and comment lines were added or replaced; nothing else. Checked by
a script over the non-comment lines with continuations joined:

| Unit | Non-comment lines other than `LD_LIBRARY_PATH`, identical | `ExecStart` + other `Environment` lines among them | `LD_LIBRARY_PATH` before → after |
| --- | --- | --- | --- |
| `wyoming-whisper` | 15 of 15 | 3 | `runtime-libs`, **Ollama's** → `runtime-libs`, **`%h/services/rocm-7.2/lib`** |
| `wyoming-whisper-dev` | 20 of 20 | 6 | the same |
| `llama-talkie` | 12 of 12 | 2 | **Ollama's**, `talkie-gfx1151-rocm7` → **`/home/drew/services/rocm-7.2/lib`**, `talkie-gfx1151-rocm7` |

Same number of directories, same order, one position changed in each. No
new `Environment` line. The installed copies under
`~/.config/systemd/user/` are byte-identical (`cmp`) to `3006ef9`'s
`systemd/` files today, so the backups of step a are the old units.

### 3. The script — met

- **By digest, refuses any other**: shown above; offline tests for a tag,
  another digest of the same repo and the same digest under another repo
  (exit 2, the message names `IMAGE_DIGEST` and "pin it on purpose", the
  fake docker is never called, no directory created). A local image whose
  id is not the digest, and an image reporting ROCm `7.2.1`, are refused
  with exit 3.
- **`--network none`, nothing writable mounted, tar stream**: the offline
  build test logs every argv the script gives `docker` and asserts:
  `image inspect <image@digest>`, then two `run`s, each with
  `--network none`, `--rm`, `--read-only`, `--pull`, the image by digest,
  and none of `-v`, `--volume`, `--mount`, `--volumes-from`; the `tar`
  run's arguments are `-C /opt/rocm-7.2.0/lib -cf -` followed by exactly
  `list`'s output. Every extracted file is owned by the caller.
- **Refuses a non-empty directory**: exit 2 before docker; the stray file
  is left alone (offline test, and live on a scratch directory).
- **Two runs, byte-identical manifests** (live, into `$S/rocm-a` and
  `$S/rocm-b`, about 41 s each):

  ```text
  $ cmp rocm-a/MANIFEST rocm-b/MANIFEST && echo IDENTICAL
  IDENTICAL
  200d6829ce387f787b5e7cc2093edd4a30ae0c8698954769a904f195e9b7b539  rocm-a/MANIFEST
  200d6829ce387f787b5e7cc2093edd4a30ae0c8698954769a904f195e9b7b539  rocm-b/MANIFEST
  ```

  Offline: two equal trees at different depths give equal manifests.
- **Verify fails on a missing, extra or changed file** (live on
  `$S/rocm-b`: one byte appended to `libroctx64.so.4.1.70200`,
  `librocblas.so.5` removed, `extra.so` added, a `.dat` set to mode 600):

  ```text
  changed: libroctx64.so.4.1.70200
  changed: rocblas/library/TensileLibrary_lazy_gfx1151.dat
  extra:   extra.so
  header:  manifest says "totals	files=1803 links=10 dirs=2 bytes=1944240155", directory/script give "totals	files=1804 links=9 dirs=2 bytes=1944240157"
  missing: librocblas.so.5
  build_rocm_libs: …/rocm-b/lib does NOT match …/rocm-b/MANIFEST
  exit 1
  ```

  Offline: missing file, extra file, changed content, changed mode,
  changed link target, an extra `hipblaslt` directory, a manifest naming
  another image, no manifest: each exits 1 with its line.
- **The manifest's content** (`$S/rocm-a/MANIFEST`): 1,823 lines, 309,417
  bytes, sha256 `200d6829ce387f787b5e7cc2093edd4a30ae0c8698954769a904f195e9b7b539`.
  Totals `files=1803 links=10 dirs=2 bytes=1944240155`:
  - ten versioned libraries (1,273,384,440 bytes) and their ten soname
    links, each link's target its versioned file;
  - 1,793 files under `rocblas/library` (670,855,715 bytes), 96 of them
    gfx1151, including
    `rocblas/library/TensileLibrary_lazy_gfx1151.dat	f	644	24186	f05386049d4ce405ecb2af894d974ec53328111e6699d4fb42f42bc09ee0d9e9`;
  - two directories, `rocblas` and `rocblas/library`, mode 755;
  - nothing under `hipblaslt/` (`grep -c '^hipblaslt/'` gives 0; the word
    appears once, in the `omitted` header line);
  - modes 644 for files, 777 for links; no `/tmp`, user, host or date.
  The bare `.so` development links of the image are not taken (nothing
  asks for them).

  Versioned files, image against Ollama's copy (first 12 hex of sha256):

  | File | Image | Ollama's |
  | --- | --- | --- |
  | `libamd_comgr.so.3.0.0` | `20ef37fb99fc` | `4260c9a038f0` |
  | `libamdhip64.so.7.2.70200` | `3e75825df52d` | `710c769a7b58` |
  | `libhipblas.so.3.2.70200` | `4a8375b529ba` | `496d17880942` |
  | `libhipblaslt.so.1.2.70200` | `d70e6a341fbc` | `e54c27f8ebd8` |
  | `libhsa-runtime64.so.1.18.70200` | `5a7697364ebc` | `d0f8e860872a` |
  | `librocblas.so.5.2.70200` | `eb3a962769bb` | `32cc93511c16` |
  | `librocprofiler-register.so.0.6.0` | `1a23a2b5a62d` | `91327c010a72` |
  | `librocroller.so.1.0.0` | `340a5003a26e` | `d40a1e15ff74` |
  | `librocsolver.so.0.7.70200` | `d0c39287591c` | `4255ca9bdc0a` |
  | `libroctx64.so.4.1.70200` | `22bbc6946fdf` | `264bd4cb1734` |

- **Offline tests, no docker in the suite**: the parsing and format of the
  manifest, verify, the digest refusal, the non-empty refusal, and a full
  `build` against a fake `docker` (a Python script on `PATH` that answers
  `image inspect`, `cat` and `tar` from a small look-alike of
  `/opt/rocm-7.2.0/lib` with the host's tar). Two build tests feed a wrong
  archive (a soname link pointing at the wrong file; the lazy `.dat`
  missing) and see exit 1 with no manifest.

### 4. The set is enough — met (in scratch)

Host `ldd` (`$S/crit4-ldd.txt`, 114 lines), with
`LD_LIBRARY_PATH=~/services/wyoming-whisper/runtime-libs:$S/rocm-a/lib`
for `runtime-libs/libctranslate2.so.4` and the venv's
`ctranslate2/_ext.cpython-312-x86_64-linux-gnu.so`, and
`LD_LIBRARY_PATH=$S/rocm-a/lib:…/talkie-gfx1151-rocm7` for Talkie's
`llama-server` and `libggml-hip.so`: `grep -ciE 'not found|ollama'` gives
**0**. Each of the ten ROCm sonames resolves to `$S/rocm-a/lib` in all
four (`libamd_comgr.so.3`, `libamdhip64.so.7`, `libhipblas.so.3`,
`libhipblaslt.so.1`, `libhsa-runtime64.so.1`, `librocblas.so.5`,
`librocprofiler-register.so.0`, `librocroller.so.1`,
`librocsolver.so.0`, `libroctx64.so.4`: 4 hits each); `libctranslate2`,
`libhiprand`, `librocrand`, `libomp`, `libopenblas` resolve to
`runtime-libs`; `libdrm.so.2`, `libdrm_amdgpu.so.1`, `libelf.so.1`,
`libnuma.so.1` to `/lib/x86_64-linux-gnu` (4 hits each).

The 14 files each running unit maps from Ollama's directory today (read
from `/proc/<pid>/maps` of pids 2809, 1962004 and 2288893 at the start of
the build: the same 14 in all three) and their counterparts:

| Mapped today from Ollama's directory | Counterpart |
| --- | --- |
| `libamd_comgr.so.3.0.0` | extracted, `lib/libamd_comgr.so.3.0.0` |
| `libamdhip64.so.7.2.70200` | extracted |
| `libhipblas.so.3.2.70200` | extracted |
| `libhipblaslt.so.1.2.70200` | extracted |
| `libhsa-runtime64.so.1.18.70200` | extracted |
| `librocblas.so.5.2.70200` | extracted |
| `librocprofiler-register.so.0.6.0` | extracted |
| `librocroller.so.1.0.0` | extracted |
| `librocsolver.so.0.7.70200` | extracted |
| `libroctx64.so.4.1.70200` | extracted |
| `libdrm.so.2.4.0` | host `/usr/lib/x86_64-linux-gnu/libdrm.so.2.125.0`, package `libdrm2` 2.4.125-1ubuntu0.1 |
| `libdrm_amdgpu.so.1.0.0` | host `libdrm_amdgpu.so.1.125.0`, `libdrm-amdgpu1` 2.4.125-1ubuntu0.1 |
| `libelf-0.190.so` | host `libelf-0.193.so`, `libelf1t64` 0.193-1 |
| `libnuma.so.1.0.0` | host `libnuma.so.1.0.0`, `libnuma1` 2.0.19-1 |

### 5. The runtime starts — met (in scratch)

Both commands ran under `env -i HOME=… PATH=/usr/bin:/bin` plus the unit's
own `Environment` lines, with `$S/rocm-a/lib` in place of Ollama's path.
No model was loaded; each process exited by itself.

**Whisper**, `~/services/wyoming-whisper/.venv/bin/python -I
$S/code/ct2_devices.py` (calls `ctranslate2.get_cuda_device_count()`,
then reads its own `/proc/self/maps`), with production's three lines
(`HSA_OVERRIDE_GFX_VERSION=11.5.1`, `ROCR_VISIBLE_DEVICES=0`,
`LD_LIBRARY_PATH=…/runtime-libs:$S/rocm-a/lib`):

```text
get_cuda_device_count() = 1
mapped: /home/drew/services/wyoming-whisper/runtime-libs/libctranslate2.so.4
mapped: /home/drew/services/wyoming-whisper/runtime-libs/libhiprand.so.1
mapped: /home/drew/services/wyoming-whisper/runtime-libs/libomp.so
mapped: /home/drew/services/wyoming-whisper/runtime-libs/libopenblas.so.0
mapped: /home/drew/services/wyoming-whisper/runtime-libs/librocrand.so.1
mapped: $S/rocm-a/lib/libamd_comgr.so.3.0.0
mapped: $S/rocm-a/lib/libamdhip64.so.7.2.70200
mapped: $S/rocm-a/lib/libhipblas.so.3.2.70200
mapped: $S/rocm-a/lib/libhipblaslt.so.1.2.70200
mapped: $S/rocm-a/lib/libhsa-runtime64.so.1.18.70200
mapped: $S/rocm-a/lib/librocblas.so.5.2.70200
mapped: $S/rocm-a/lib/librocprofiler-register.so.0.6.0
mapped: $S/rocm-a/lib/librocroller.so.1.0.0
mapped: $S/rocm-a/lib/librocsolver.so.0.7.70200
mapped: $S/rocm-a/lib/libroctx64.so.4.1.70200
mapped: /usr/lib/x86_64-linux-gnu/libdrm.so.2.125.0
mapped: /usr/lib/x86_64-linux-gnu/libdrm_amdgpu.so.1.125.0
mapped: /usr/lib/x86_64-linux-gnu/libelf-0.193.so
mapped: /usr/lib/x86_64-linux-gnu/libnuma.so.1.0.0
mapped under ollama: 0
exit 0
```

(Also mapped, not in the filter's interest: the venv's `_ext` module and
numpy's own OpenBLAS.) With the dev unit's lines (the same three plus
`HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`): `get_cuda_device_count()
= 1`, the same ten from `$S/rocm-a/lib`, `mapped under ollama: 0`, exit 0.

**Talkie**, `…/talkie-gfx1151-rocm7/llama-server --list-devices` with
`HSA_OVERRIDE_GFX_VERSION=11.5.1` and
`LD_LIBRARY_PATH=$S/rocm-a/lib:…/talkie-gfx1151-rocm7`, under
`LD_DEBUG=libs LD_DEBUG_OUTPUT=$S/talkie-lddebug`:

```text
ggml_cuda_init: found 1 ROCm devices (Total VRAM: 104000 MiB):
  Device 0: Radeon 8060S Graphics, gfx1151 (0x1151), VMM: no, Wave Size: 32, VRAM: 104000 MiB
Available devices:
  ROCm0: Radeon 8060S Graphics (104000 MiB, 41551 MiB free)
exit 0
```

`calling init:` lines of the loader trace: the ten sonames from
`$S/rocm-a/lib` (`libamd_comgr.so.3` … `libroctx64.so.4`), `libdrm.so.2`,
`libdrm_amdgpu.so.1`, `libelf.so.1`, `libnuma.so.1` from
`/lib/x86_64-linux-gnu`, the seven ggml/llama libraries from
`talkie-gfx1151-rocm7`; `grep -c ollama` over the trace: **0**.

After both: no leftover process (`pgrep`), and over ten seconds Talkie's
running server used 0 CPU ticks and `gpu_busy_percent` read 0 ten times.
No running unit was touched.

### 6. The grep — met

```text
$ grep -rn -e '/usr/local/lib/ollama' -e '/usr/share/ollama' -e '11434' \
    --exclude-dir=.git --exclude-dir=docs --exclude=ROADMAP.md \
    --exclude='Kronk evaluation findings and implementation plan.md' .
$ echo $?
1
```

(Run over the whole working tree, gitignored files and `tests/.venv`
included.) The word "ollama" remains outside `docs/` only where it names
the HA integration or the shim (`pipeline_bench.sh`'s `ollama` transport,
`voice_replay.py`'s conversation-entity check, `upgrade_rules.toml`'s
"Ollama conversation integration", the orchestrator's shim): nothing of
the Ollama install.

### 7. Docs — met

`README.md`, the new section "ROCm runtime libraries
(`~/services/rocm-7.2/`)": where the libraries come from and that
CTranslate2 and Talkie's server were built in the same image; what the
script takes (the ten sonames, `rocblas/library`, 1.9 GB) and how
(digest, no network, nothing mounted, tar stream, manifest); verify and
rebuild commands (build into a new directory, swap, restart one unit at a
time, dev first); `hipblaslt/library` left out on purpose and why; the
four system libraries are the host's; the image is local only and a
pruned image needs a pull by the digest, given in full; how a new ROCm
release is pinned; no `ROCM_PATH`/`HIP_PATH`/Tensile variable. The
llama.cpp section's Talkie block and the Whisper units section point at
it. `docs/features/voice-pipeline.md` says the same for the Whisper
units in its design-decisions list. No ROADMAP edit.

### 8. The suite — met

Before the build (`./scripts/run_tests.sh -q -x` at `3006ef9`):

```text
7436 passed, 3 skipped, 54 xfailed, 6 warnings in 99.48s (0:01:39)
```

After:

```text
7464 passed, 3 skipped, 54 xfailed, 6 warnings in 101.23s (0:01:41)
```

28 new tests, all in `tests/test_m26_rocm_libs.py`; they open no socket
and run no docker.

### 9. Nothing installed, restarted or stopped — met

No `systemctl` other than `show -p MainPID` and `list-timers`; the three
units' main pids were 2809, 1962004 and 2288893 at the start and are the
same at the end. Nothing written under `~/services`, `~/pai`,
`~/.config/systemd`, `/usr/local` or `~/kronk-m13-bench`.

### 10. After the switch — not this build's

The test agent's, after the coordinator's switch.

## What I did not do

- Create `~/services/rocm-7.2`, install a unit, restart anything, run the
  bench, or change `ROADMAP.md`.
- Take `hipblaslt/library`, the image's `libdrm`/`libelf`/`libnuma`, or
  the bare `.so` development links.
- Touch `runtime-libs`. Its `libhiprand.so.1`, `librocrand.so.1` and
  `libomp.so` are the image's `libhiprand.so.1.1.70200`,
  `librocrand.so.1.1.70200` and `llvm/lib/libomp.so` by sha256 (first 16
  hex: `f0f414143486c2ed`, `ee519df1a98def4b`, `1d5268c2fbbd502e` on both
  sides), as the review said. They are root-owned (a bind mount wrote
  them).
- Add a row limit to `scripts/stt_prompt_bench.py`. The 25-row run needs
  a sub-sheet; the command to cut one is in step a below.

## Found that the criteria did not foresee

1. **`scripts/stt_prompt_bench.py run` has no row limit and sends the
   whole sheet**: 754 rows (714 speech + 40 non-speech) for `labels.csv`,
   and `compare --same` lists every differing row, non-speech included.
   For runs that are exactly the 714 and the 25 speech rows, cut two
   sheets with absolute audio paths (step a). Checked: the bench's own
   `read_sheet` loads 714 and 25 rows from them, every audio file exists,
   variants `clean`, `noise10`, `room`. The 25 are every 28th speech row
   (spread across sentences and the three variants).
2. **The bench reports p50/p95 per variant**, not over the whole run
   (`summarize` groups by `variant`). The pass rule's "p50 and p95 within
   20 %" needs the coordinator to say which; the per-variant table prints
   with `compare A B`, and an overall figure is one read of `rows[*].latency_s`
   in the two JSON files.
3. **The host's default `find` is bfs and coreutils are uutils** (memory
   note on uutils). The script uses `find -printf '%P\t%y\t%m\t%s\n'`
   (bfs supports it), `sha256sum`, `readlink`, `sort`; checked on this
   host. The session umask is 002; the script sets 022 so the `rocblas`
   directory, which the archive does not carry, is 755 on every run.
4. **Scratch is a tmpfs** here; two 1.9 GB extractions are 3.9 GB of RAM
   until deleted. They were deleted.
5. The real directory, built from the same image on another filesystem,
   should give the same manifest: sizes, modes, hashes and link targets do
   not depend on the filesystem, and directory sizes are not recorded. So
   `sha256sum ~/services/rocm-7.2/MANIFEST` should read
   `200d6829…b539`; a different value is worth a look before step c.

## Commands for the coordinator, steps a to e

Run from the repo root. `B` is a scratch directory of the coordinator's
choosing for sheets and results; `D=2026-11-24` or the day of the switch.

**a. Before** (after 04:05 on the day; the dev prompt timer last ran
04:02:43 today and next runs 04:00 tomorrow):

```bash
mkdir -p ~/backups/m26-units-2026-11-24
cp -p ~/.config/systemd/user/wyoming-whisper.service \
      ~/.config/systemd/user/wyoming-whisper-dev.service \
      ~/.config/systemd/user/llama-talkie.service ~/backups/m26-units-2026-11-24/
for u in wyoming-whisper wyoming-whisper-dev llama-talkie; do
  git show 3006ef9:systemd/$u.service | cmp - ~/backups/m26-units-2026-11-24/$u.service && echo "$u backup = 3006ef9"
done
sha256sum ~/services/wyoming-whisper/dev-prompt.env | tee $B/dev-prompt.before.sha256

# the two speech-only sheets (absolute audio paths; the audio is read only)
mkdir -p $B
python3 - "$B" <<'PY'
import csv, sys
from pathlib import Path
src = Path.home() / "kronk-m13-bench/synthetic/labels.csv"
out = Path(sys.argv[1])
rows = list(csv.DictReader(open(src, newline="", encoding="utf-8-sig")))
speech = [r for r in rows if r["correct_text"].strip() and not r["variant"].startswith("hazard-")]
for r in speech:
    r["audio_file"] = str(src.parent / r["audio_file"])
for name, sel in (("speech-714.csv", speech), ("speech-25.csv", speech[::28][:25])):
    with open(out / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(sel)
    print(name, len(sel))
PY
sha256sum $B/speech-714.csv $B/speech-25.csv
# expected: bb01769a1cd97ee6724f4dc05bf792e41a666568f5fabb11fb8c03885422c93a  speech-714.csv
#           8f0b564430e43bf7d2464d688a1aff6b0300dfb6e9d38268f8183a9f24c8fcbf  speech-25.csv

# before, dev (loopback 10301; about 714 transcriptions)
tests/.venv/bin/python scripts/stt_prompt_bench.py run --sheet $B/speech-714.csv \
    --port 10301 --label m26-dev-before --out $B/m26-dev-before.json \
    --prompt-file ~/services/wyoming-whisper/dev-prompt.env

# before, production (10300): first the idle check, then 25 rows
HA_TOKEN=$(sed -n 's/^HA_TOKEN=//p' .env) && curl -s -H "Authorization: Bearer $HA_TOKEN" \
    http://localhost:8123/api/states | python3 -c 'import json,sys; [print(s["entity_id"], s["state"]) for s in json.load(sys.stdin) if s["entity_id"].startswith("assist_satellite.")]'
docker logs --since 90s kronk-orchestrator-1 2>&1 | grep -E '"event": "request' || echo "no production request in 90 s"
tests/.venv/bin/python scripts/stt_prompt_bench.py run --sheet $B/speech-25.csv \
    --port 10300 --label m26-prod-before --out $B/m26-prod-before.json

# journal lines, both Whisper units, this boot
for u in wyoming-whisper wyoming-whisper-dev; do
  echo "$u $(journalctl --user -u $u -b --no-pager | grep -ciE 'rocblas|hipblaslt|tensile')"
done
```

**b. The directory:**

```bash
scripts/build_rocm_libs.sh build ~/services/rocm-7.2
scripts/build_rocm_libs.sh verify ~/services/rocm-7.2
sha256sum ~/services/rocm-7.2/MANIFEST        # expected 200d6829ce387f787b5e7cc2093edd4a30ae0c8698954769a904f195e9b7b539
find ~/services/rocm-7.2 ! -user drew | wc -l # expected 0
```

**c. Dev Whisper** (Hey Jarvis STT is down for the restart, about ten
seconds):

```bash
cp systemd/wyoming-whisper-dev.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart wyoming-whisper-dev.service
systemctl --user show -p ActiveState,NRestarts,MainPID wyoming-whisper-dev
ss -tln | grep 127.0.0.1:10301
grep -o '/home/drew/services/rocm-7.2/lib/[^ ]*\|/usr/local/lib/ollama[^ ]*' \
    /proc/$(systemctl --user show -p MainPID --value wyoming-whisper-dev)/maps | sort -u
sha256sum ~/services/wyoming-whisper/dev-prompt.env    # must equal $B/dev-prompt.before.sha256
tests/.venv/bin/python scripts/stt_prompt_bench.py run --sheet $B/speech-714.csv \
    --port 10301 --label m26-dev-after --out $B/m26-dev-after.json \
    --prompt-file ~/services/wyoming-whisper/dev-prompt.env
tests/.venv/bin/python scripts/stt_prompt_bench.py compare $B/m26-dev-before.json $B/m26-dev-after.json \
    --same $B/m26-dev-before.json $B/m26-dev-after.json
journalctl --user -u wyoming-whisper-dev --since "-30 min" --no-pager | grep -ciE 'rocblas|hipblaslt|tensile'
```

Rollback, dev:

```bash
cp ~/backups/m26-units-2026-11-24/wyoming-whisper-dev.service ~/.config/systemd/user/ && \
  systemctl --user daemon-reload && systemctl --user restart wyoming-whisper-dev.service
```

**d. Talkie:**

```bash
cp systemd/llama-talkie.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart llama-talkie.service
systemctl --user show -p ActiveState,NRestarts,MainPID llama-talkie
curl -s 127.0.0.1:11441/health
grep -o '/home/drew/services/rocm-7.2/lib/[^ ]*\|/usr/local/lib/ollama[^ ]*' \
    /proc/$(systemctl --user show -p MainPID --value llama-talkie)/maps | sort -u
curl -s http://localhost/dev/api/chat -H 'Content-Type: application/json' \
    -d '{"model":"kronk","stream":false,"messages":[{"role":"user","content":"ask talkie what he makes of the wireless"}]}'
# GPU busy, idle, two minutes, a reading every 20 s (seven readings)
for i in $(seq 0 6); do date +%T; cat /sys/class/drm/card1/device/gpu_busy_percent; [ $i -lt 6 ] && timeout 20 tail -f /dev/null; done
```

The GPU reading includes the two Vulkan model servers; a non-zero reading
while either is answering a request is not Talkie's. Check their journals
for a request at that time before calling it the idle spin.

Rollback, Talkie:

```bash
cp ~/backups/m26-units-2026-11-24/llama-talkie.service ~/.config/systemd/user/ && \
  systemctl --user daemon-reload && systemctl --user restart llama-talkie.service
```

**e. Production Whisper** (only after c and d pass; all satellites idle,
no production request in 90 s, as in step a):

```bash
cp systemd/wyoming-whisper.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart wyoming-whisper.service
for i in $(seq 1 60); do ss -tln | grep -q ':10300 ' && { echo "listening after ${i} s"; break; }; timeout 1 tail -f /dev/null; done
systemctl --user show -p ActiveState,NRestarts,MainPID wyoming-whisper
grep -o '/home/drew/services/rocm-7.2/lib/[^ ]*\|/usr/local/lib/ollama[^ ]*' \
    /proc/$(systemctl --user show -p MainPID --value wyoming-whisper)/maps | sort -u
# idle check as in step a, then:
tests/.venv/bin/python scripts/stt_prompt_bench.py run --sheet $B/speech-25.csv \
    --port 10300 --label m26-prod-after --out $B/m26-prod-after.json
tests/.venv/bin/python scripts/stt_prompt_bench.py compare $B/m26-prod-before.json $B/m26-prod-after.json \
    --same $B/m26-prod-before.json $B/m26-prod-after.json
journalctl --user -u wyoming-whisper --since "-30 min" --no-pager | grep -ciE 'rocblas|hipblaslt|tensile'
```

Rollback, production (at once if not listening in 60 s or the run fails;
the unit has no start limit, so a failing start loops every 5 s until
this runs):

```bash
cp ~/backups/m26-units-2026-11-24/wyoming-whisper.service ~/.config/systemd/user/ && \
  systemctl --user daemon-reload && systemctl --user restart wyoming-whisper.service
```

Each rollback puts back the unit that loads Ollama's directory, which
works only while `/usr/local/lib/ollama/rocm` exists.

## Questions

None blocking. One for the coordinator's pass rule: per-variant or
overall p50/p95 (finding 2).
