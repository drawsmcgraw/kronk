# M26 pass criteria: nothing depends on the Ollama install

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

## Done and undone by the coordinator before this item

- A copy of Ollama's `rocm` directory was made at
  `/home/drew/services/rocm-7.2/lib` and then deleted on Drew's order
  (above). Nothing used it.

## Plumbing, decided by the coordinator

- **The libraries come from the ROCm image, pinned by digest**, not from
  Ollama and not from the network at run time: the image is already
  local. A script in the repo (`scripts/build_rocm_libs.sh`) extracts them
  from a throwaway container run with `--network none`, into a directory
  given on the command line, and writes a manifest beside them: the
  image's digest, the ROCm version, and the sha256 of every file. Run
  again on the same image it produces the same manifest.
- **What is extracted is what the programs need, found, not guessed:** the
  closure of `ldd` for Whisper's `libctranslate2.so.4` (with its
  `runtime-libs`) and for Talkie's `llama-server` and `libggml-hip.so`,
  resolved inside the image, plus the data directories those libraries
  open at run time (`rocblas/library`; `hipblaslt/library` if the image
  has one and a library asks for it). System libraries the host already
  provides at the same soname are left to the host unless a program
  fails without the image's copy; the report says which way each went and
  why.
- **The directory is `%h/services/rocm-7.2/lib`**: beside
  `%h/services/wyoming-whisper/`, named for the ROCm release, so a later
  release sits beside it and a unit names the one it was built against.
- **This changes the binaries the three programs run against** (same
  version, ROCm's own build in place of Ollama's). It is done one unit at
  a time, dev first (tenet 9).
- The build writes the script and changes the unit files in `systemd/`,
  and runs the script only into its scratch directory. The coordinator
  runs it for the real directory, installs the units and restarts, in
  this order, checking each before the next: `wyoming-whisper-dev`,
  `llama-talkie`, then `wyoming-whisper` (production speech recognition: a
  restart of about ten seconds, when no satellite is listening). Rollback
  for any of them is the old unit file, which still works while Ollama's
  directory exists; that is why the delete waits for this item's test.
- `scripts/model_bench.py` is deleted: it can only talk to Ollama.

## Scope

1. `scripts/build_rocm_libs.sh` and its manifest format.
   The three unit files use `%h/services/rocm-7.2/lib` (Talkie's unit
   spells the home path as it does today) in place of
   `/usr/local/lib/ollama/rocm`, each with a comment naming the script.
2. No file in the repo outside `docs/` history names
   `/usr/local/lib/ollama`, `/usr/share/ollama` or port 11434 as something
   in use.
3. `README.md` (the llama.cpp units section, the Whisper units section)
   and `docs/features/voice-pipeline.md` where it describes the Whisper
   units' libraries.

Out of scope: any change to what the units run or to their flags;
rebuilding CTranslate2 or Talkie's server; a newer ROCm release; the
Ollama removal itself (Drew's sudo).

## Criteria

1. A test fails if any file under `systemd/` names `ollama`, and it fails
   on the tree before this item.
2. Each changed unit passes `systemd-analyze --user verify`. `ExecStart`
   and every `Environment` line other than `LD_LIBRARY_PATH` are byte for
   byte as before.
3. `scripts/build_rocm_libs.sh`: refuses an image whose digest is not the
   pinned one (and says how to pin a new one on purpose); runs its
   container with `--network none`; writes only inside the directory it
   is given; two runs into two scratch directories give byte-identical
   manifests; every file in the directory is in the manifest and matches
   it. Tests for the manifest check and the digest refusal run offline
   (no docker in the test suite).
4. Offline proof that the extracted set is enough, in scratch: for each
   of the three programs, `ldd` with the new `LD_LIBRARY_PATH` resolves
   everything, with no path under `/usr/local/lib/ollama` and no "not
   found"; and every library the running processes map from Ollama's
   directory today has its counterpart either in the extracted set (from
   the image) or on the host (named).
5. A load test in scratch that touches no running unit: Whisper's
   CTranslate2 imported and Talkie's `llama-server --version` (or
   `--list-devices`) run with the new path and with `/usr/local/lib/ollama`
   absent from `LD_LIBRARY_PATH`. No model is loaded on the GPU by either.
6. `grep -rn "usr/local/lib/ollama\|usr/share/ollama\|11434"` over the
   repo outside `docs/` finds nothing.
7. Docs changed as scope 3 says, including how `rocm-7.2/lib` is rebuilt
   if it is lost and how a new ROCm release would be pinned.
8. `./scripts/run_tests.sh` passes.
9. Nothing is installed, restarted or stopped by the build or the test.
   The test agent, after the coordinator's installs and restarts, checks
   on the host: the real directory matches its manifest; no process maps
   any file under `/usr/local/lib/ollama` (every `/proc/<pid>/maps` the
   agent can read); each of the three units maps the ROCm libraries from
   the new directory, is active, and has `NRestarts=0` since its restart;
   dev Whisper transcribes the M13 bench's synthetic clips with the same
   text as before the change (the coordinator records the before run),
   sent straight to the dev Wyoming port, not through Home Assistant;
   GPU busy is 0 % with all three idle for two minutes; Talkie answers
   one request on the dev route.

Reports: `docs/sprint/M26-build.md`, `docs/sprint/M26-e2e.md`.
