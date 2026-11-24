# M26 pass criteria: nothing depends on the Ollama install

Revised 2026-11-24 after the design review (`docs/sprint/M26-review.md`):
all 20 of its points are taken; where this file and the review disagree,
this file decides.

Source: Drew's orders of 2026-11-24: "Make sure no one depends on any of
the ollama stuff. Then let me know when we can actually delete it", and
on the first plan (copy the borrowed files): "copying the existing files
we borrowed from ollama feels lazy. We should have our own copy dedicated
to the work and not just a blind copy from an unrelated project".

Handoff line: Whisper and Talkie load ROCm libraries taken from the ROCm
7.2 image they were built in, by a script with a manifest, and nothing on
the host or in the repo points at Ollama.

## What the investigation found (coordinator, 2026-11-24)

- Three running units load libraries from `/usr/local/lib/ollama/rocm`
  through `LD_LIBRARY_PATH`: `wyoming-whisper` (production speech
  recognition), `wyoming-whisper-dev` and `llama-talkie`. Each maps the
  same 14 files (`libamdhip64`, `libhsa-runtime64`, `librocblas`,
  `libhipblas`, `libhipblaslt`, `librocsolver`, `libamd_comgr`,
  `librocroller`, `libroctx64`, `librocprofiler-register`, `libdrm`,
  `libdrm_amdgpu`, `libelf`, `libnuma`). No other process on the host maps
  anything under `/usr/local/lib/ollama`. The two GPU model servers are
  Vulkan builds and map none of it.
- Ollama was installed by its script, not by a package: the binary
  `/usr/local/bin/ollama`, the libraries `/usr/local/lib/ollama` (7.2 GB:
  `rocm` 2.5 GB, three CUDA sets, `vulkan`), the system unit
  `/etc/systemd/system/ollama.service` (enabled, active, port 11434 on
  every interface), the user `ollama`, and the models in
  `/usr/share/ollama` (241 GB).
- Home Assistant's Ollama integration points at Kronk's own shim
  (`http://localhost/voice` and `http://localhost/dev/voice`), not at
  Ollama.
- Three system-level unit files from April 2026 still name the Ollama
  library path: `/etc/systemd/system/llama-bonsai.service`,
  `llama-devstral.service`, `llama-coordinator.service`. All disabled and
  inactive. Root-owned; removing them is Drew's.
- In the repo: the `LD_LIBRARY_PATH` lines of
  `systemd/wyoming-whisper.service`, `systemd/wyoming-whisper-dev.service`
  and `systemd/llama-talkie.service` (and that unit's header comment);
  `scripts/model_bench.py` calls Ollama on port 11434 (an April bench
  script); README's llama.cpp section shows the Ollama path.

- **Ollama's libraries are not ROCm's own.** Whisper's CTranslate2
  (`~/services/wyoming-whisper/build/build-in-container.sh`) and Talkie's
  server (`pai_workspace/llama-cpp/Dockerfile.talkie-hip`) were both built
  inside `rocm/dev-ubuntu-24.04:7.2-complete`. That image is on this host
  (29.3 GB, digest
  `sha256:86e11093b4a7ec2a79b1b6701d10e840a6994f21c7e05929b51eb9be361c683a`,
  `/opt/rocm/.info/version` 7.2.0). Its `/opt/rocm/lib` holds the same ten
  ROCm files by name and version (`libamdhip64.so.7.2.70200` and the
  rest), and **every one has a different sha256 from Ollama's copy**: same
  version, a different build. So for months both programs have run against
  another project's rebuild of the libraries they were compiled with. Both
  hold 1,793 files under `rocblas/library`.
- The other four files the units map from Ollama's directory (`libdrm`,
  `libdrm_amdgpu`, `libelf`, `libnuma`) are ordinary system libraries; in
  the image they are Ubuntu 24.04's, under `/lib/x86_64-linux-gnu`.
- **The kernels differ too** (reviewer). The 1,793 names under
  `rocblas/library` are the same on both sides and all 1,793 files differ
  by sha256, the `.hsaco` kernel objects included. So the code the GPU
  runs for every matrix product changes, not only the host-side
  libraries. Both sides carry 96 gfx1151 files.
- The link closure is the ten files and nothing else (reviewer, from
  `readelf -d`): `libctranslate2.so.4` needs `libhipblas.so.3` and
  `libamdhip64.so.7`; Talkie's `libggml-hip.so.0` needs those and
  `librocblas.so.5`; the rest follow from them. The image's libraries need
  at most `GLIBC_2.38` and `GLIBCXX_3.4.32`; the host has glibc 2.42.
- Ollama's `libdrm`, `libelf` and `libnuma` are neither the image's nor
  the host's. The host has libdrm 2.4.125, libelf 0.193, libnuma 2.0.19,
  and exports every symbol the image's `libhsa-runtime64` imports.
- The image has `/opt/rocm/lib/hipblaslt/library` (2,932 files, 4.5 GB).
  Ollama's directory has no `hipblaslt` directory, so the units run today
  with `libhipblaslt.so.1` loaded and its kernel data absent.
- No unit sets `ROCM_PATH`, `HIP_PATH` or a Tensile path variable.
  rocBLAS reads its data files and closes them, so `/proc/<pid>/maps`
  cannot show where the kernel data came from; it locates the directory
  relative to `librocblas` itself (an inference from its strings).
- `~/services/wyoming-whisper/runtime-libs` already holds three files
  that are the image's by sha256 (`libhiprand`, `librocrand`, `libomp`).
  It is left alone.
- The clips exist: `~/kronk-m13-bench/synthetic/` holds `labels.csv` and
  754 audio files; `scripts/stt_prompt_bench.py run --sheet … --port N`
  sends files to a loopback Wyoming port and nowhere else. In M13, 714 of
  714 speech rows repeated exactly; some non-speech rows did not. The dev
  prompt is rebuilt at 04:00 and the dev unit restarted when it changes.
- Docker here is rootful: files written through a bind mount are root's.

## Done and undone by the coordinator before this item

- A copy of Ollama's `rocm` directory was made at
  `/home/drew/services/rocm-7.2/lib` and then deleted on Drew's order
  (above). Nothing used it.

## Plumbing, decided by the coordinator

- **The libraries come from the ROCm image, run by digest**
  (`rocm/dev-ubuntu-24.04@sha256:86e11093…683a`), not from Ollama and not
  from the network: the image is local. If it is ever pruned, a rebuild
  needs a pull by that digest; the docs say so.
- **The script holds the list**: the ten sonames (each as its versioned
  file plus its soname symlink) and `rocblas/library`. No `ldd` inside
  the image. That the list is enough is a test (criterion 4), not a
  computation. A new ROCm release edits the list and the digest together.
- **`hipblaslt/library` is not extracted.** The layout matches what runs
  today, so the change stays one variable. The manifest and the docs say
  it is left out on purpose.
- **`libdrm`, `libdrm_amdgpu`, `libelf` and `libnuma` are the host's.** No
  copy from the image. This is a second change riding on the same restart
  (the units move from Ollama's copies to the host's); criterion 5 tests
  it before any unit is touched.
- **Extraction mounts nothing writable.** The files leave the container
  as a tar stream on stdout (`docker run --rm --network none --entrypoint
  tar <image@digest> -C /opt/rocm-7.2.0/lib -cf - <list>`) and are
  unpacked by the caller, so every file is the caller's. The script
  refuses a directory that exists and is not empty.
- **Layout**: `%h/services/rocm-7.2/lib/` holds only what was extracted;
  the manifest is beside it, `%h/services/rocm-7.2/MANIFEST`.
- **The manifest** records the image digest, the ROCm version, and for
  every entry its relative path, mode, and sha256 or symlink target,
  sorted under `LC_ALL=C`. No timestamp, host name, user or destination
  path.
- **Search order in the units is unchanged**: Whisper `runtime-libs` first
  then the ROCm directory; Talkie the ROCm directory first then its own.
  No new `Environment` line.
- `scripts/model_bench.py` is deleted: it can only talk to Ollama, and
  nothing imports it.
- The build writes the script and changes the unit files in `systemd/`,
  and runs the script only into its scratch directory.

## The switch, by the coordinator, with a gate at each step

The coordinator runs existing scripts and installs; it writes no code.

a. Before: copy the three installed unit files to
   `~/backups/m26-units-2026-11-24/`. After 04:05 on the day of the
   switch, record the sha256 of `dev-prompt.env`, a run of the 714 speech
   rows of `~/kronk-m13-bench/synthetic/labels.csv` on port 10301, and a
   run of 25 of them on port 10300 (all satellites idle, no production
   voice request in flight). Count rocBLAS, hipBLASLt and Tensile lines in
   both Whisper journals.
b. Run `scripts/build_rocm_libs.sh` into `~/services/rocm-7.2/`; check
   the directory against its manifest.
c. Install and restart `wyoming-whisper-dev`. Gate: active, port 10301
   listening, the 714 rows again with the same `dev-prompt.env` hash,
   latency, journal lines.
d. Install and restart `llama-talkie`. Gate: one answer on the dev route;
   GPU busy 0 % at each of at least six readings over two minutes with it
   idle (its known failure is the idle spin of 2026-06-09).
e. Only then `wyoming-whisper` (production speech recognition; about ten
   seconds; all satellites idle). Gate: listening on 10300 within 60 s,
   then the 25 rows again. If it is not listening in 60 s or the run
   fails: the backed-up unit file goes back at once, and the ruling comes
   after.

**Pass rule for a transcription run:** every speech row has the same text
as the before run. If any differ, the rows are listed and the item passes
only on the coordinator's ruling that word error rate and command-form
errors are not worse; production is not switched before that ruling. p50
and p95 latency are within 20 % of the before run (tenet 12). Non-speech
rows are not compared. These runs go straight to a loopback Wyoming port
and are exempt from the briefs' "no STT run" rail; nothing goes through
Home Assistant and no household audio is used.

## Scope

1. `scripts/build_rocm_libs.sh`, its list and its manifest format.
2. The three unit files use `%h/services/rocm-7.2/lib` (Talkie's unit
   spells the home path as it does today) in place of
   `/usr/local/lib/ollama/rocm`, each with a comment naming the script.
3. No file in the repo names `/usr/local/lib/ollama`, `/usr/share/ollama`
   or port 11434, leaving out `docs/`, `ROADMAP.md` and `Kronk evaluation
   findings and implementation plan.md` (history and the open removal
   entry).
4. Docs: `README.md` (the llama.cpp units section, the Whisper units
   section), `docs/features/voice-pipeline.md` where it describes the
   Whisper units' libraries: where the libraries come from, how
   `rocm-7.2/` is rebuilt, that the image is local only and what a rebuild
   needs if it is pruned, that `hipblaslt/library` is left out on purpose,
   that CTranslate2 and Talkie's server were built in the same image, and
   how a new ROCm release would be pinned.

Out of scope: any change to what the units run or to their flags;
`runtime-libs`; rebuilding CTranslate2 or Talkie's server; a newer ROCm
release; a start limit for the Whisper unit; the Ollama removal itself
(Drew's sudo).

## Criteria

1. A test fails if any file under `systemd/` names `ollama`, and it fails
   on the tree before this item. The test does not carry the path as a
   literal that criterion 6's grep would find.
2. Each changed unit passes `systemd-analyze --user verify`. `ExecStart`
   and every `Environment` line other than `LD_LIBRARY_PATH` are byte for
   byte as before; the order of directories in `LD_LIBRARY_PATH` is as
   before with only the one path replaced.
3. The script: runs the image by digest and refuses any other (and says
   how to pin a new one on purpose); `--network none`; mounts nothing
   writable; extracts by tar stream; refuses a non-empty directory; two
   runs into two scratch directories give byte-identical manifests; a
   verify mode checks a directory against its manifest and fails on a
   missing, extra or changed file. The manifest lists
   `rocblas/library/TensileLibrary_lazy_gfx1151.dat`, 1,793 files under
   `rocblas/library`, ten versioned libraries and their ten symlinks, and
   nothing under `hipblaslt/`. The manifest parsing, the verify mode and
   the digest refusal are tested offline (no docker in the suite).
4. The set is enough, shown in scratch: for `libctranslate2.so.4` (with
   `runtime-libs` first), Talkie's `llama-server` and its
   `libggml-hip.so`, host `ldd` with the new `LD_LIBRARY_PATH` resolves
   everything with no path under Ollama's directory and no "not found";
   and each of the 14 files the running units map from Ollama's directory
   today has its counterpart in the extracted set (ten) or on the host
   (four, named with their versions).
5. The runtime starts, shown in scratch with each unit's own
   `Environment` lines and the scratch directory in place of Ollama's
   path: `ctranslate2.get_cuda_device_count()` from Whisper's venv
   returns 1, and Talkie's `llama-server --list-devices` lists one ROCm
   device. Each process's own `/proc/self/maps` (or `LD_DEBUG=libs`) shows
   the ten libraries came from the scratch directory and none from
   Ollama's. Neither loads a model or allocates on the GPU; no running
   unit is touched.
6. `grep -rn` for the two Ollama paths and for port 11434 over the repo,
   leaving out `docs/`, `ROADMAP.md` and `Kronk evaluation findings and
   implementation plan.md`, finds nothing.
7. Docs changed as scope 4 says.
8. `./scripts/run_tests.sh` passes (before: the count at the build's
   start, quoted in the report).
9. Nothing is installed, restarted or stopped by the build or the test.
10. After the coordinator's switch, the test agent checks on the host:
    `~/services/rocm-7.2/lib` verifies against its manifest and every file
    is owned by drew; each of the three units is active, maps the ten ROCm
    libraries from the new directory and the four system libraries from
    the host, and has `NRestarts=0` since its restart; no process the
    agent can read maps any file under Ollama's directory; the
    coordinator's before and after runs are re-read and the pass rule
    re-applied from the result files, and the agent repeats the 25-row run
    on 10301 itself; neither Whisper journal has a new rocBLAS, hipBLASLt
    or Tensile line; GPU busy is 0 % with all three idle for two minutes;
    Talkie answers one request on the dev route. The report says plainly
    that the maps check is silent about kernel data files and that their
    source is inferred from `librocblas`'s location.

## Hand-off to Drew, after the test passes

The removal is done in two steps (tenet 11), and the first is the proof
that nothing reads Ollama's directory, kernel data included:

1. `sudo mv /usr/local/lib/ollama /usr/local/lib/ollama.removed`; the
   coordinator restarts `wyoming-whisper-dev`, repeats the 25-row run, and
   checks Talkie answers.
2. Only then the deletes.

## Rulings and the switch as run (coordinator, 2026-11-24)

- Latency in the pass rule is the overall p50 and p95 of a run, computed
  from the result file's rows; the bench's per-variant table is shown
  beside it.
- Results, sheets and the unit backups are in
  `~/backups/m26-units-2026-11-24/` (`bench/` for the runs).
- Step b, 15:11 to 15:12 (first written here as 15:05, which was wrong:
  `M26-e2e.md` N2; it overlapped the first minute of the dev before run,
  so that run's first 150 rows are slower, p50 0.360 s against 0.322 s for
  the rest, and the after run's latency gain is overstated by that much):
  `~/services/rocm-7.2` built; verify passes; MANIFEST
  sha256 `200d6829…b539` as the build report predicted; 0 files not
  owned by drew.
- Step c, 15:16 (dev Whisper): listening after 4 s; the ten libraries from
  the new directory, the four system ones from the host, none from
  Ollama's. 714 rows: 712 texts the same, 2 differ, both with fewer word
  errors after (`m13-0022-noise10`, `m13-0102-room`). Word errors 499 →
  468, command-form errors 90 → 89, p50 0.327 → 0.323 s, p95 0.407 →
  0.381 s, 0 journal lines. **Ruled a pass**: nothing is worse.
- The switch was paused from about 15:20 to 15:33 for an unrelated
  incident (`docs/incidents/INVESTIGATION_2026-11-24_griz_album.md`,
  which rules today's changes out as its cause).
- Step d, 15:35 (Talkie): healthy after 2 s; same library sources; one
  answer on `/dev/api/chat`; GPU busy 0 at seven readings over two
  minutes.
- Step e, 15:38:30 (production Whisper; two satellites idle, one
  unavailable, no production request in 90 s): listening after 3 s; ten
  libraries from the new directory, none from Ollama's. 25 rows: 25 texts
  the same; p50 0.318 → 0.317 s, p95 0.365 → 0.364 s; 0 journal lines.
  GPU busy back to 0 within 25 s of the run. No process on the host maps
  any file under Ollama's directory. No memwatch notice.

- 2026-11-24, on the test (`M26-e2e.md`, PASSES, no blocker):
  - S1 (`build` writes into a non-empty directory reached through a
    symlink or named `<dir>/new/..`) and S2 (`verify` passes with a
    library missing when a symlink target holds a newline): **fix round
    1.** S1 is the one that matters: a later `build` could overwrite
    libraries three running units have loaded. With them: tests that kill
    the three surviving mutants of N4 (no sort, sort outside `LC_ALL=C`,
    no `umask 022`).
  - The hand-off's first step gains a Talkie restart, as the tester
    advises: after the rename the coordinator restarts
    `wyoming-whisper-dev` and `llama-talkie`, runs the 25 rows on 10301,
    and checks Talkie answers.
  - N1 (a production Whisper restart asks huggingface.co for the model's
    revision; the dev unit has `HF_HUB_OFFLINE=1`, production does not):
    older than this item and outside its scope; for Drew, as its own
    change, since it needs another production Whisper restart.
  - Left over: N5, N6, N7.

Reports: `docs/sprint/M26-review.md` (done), `docs/sprint/M26-build.md`,
`docs/sprint/M26-e2e.md`, `docs/sprint/M26-fix1.md`,
`docs/sprint/M26-e2e2.md`.
