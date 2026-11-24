# M26 design review: nothing depends on the Ollama install

Reviewer: Fable 5.2, 2026-11-24. Reviewed: `docs/sprint/M26-criteria.md`
at `5ec5dd4`. Nothing was built, restarted or written outside this file.
Measurements were read from `/proc`, `readelf`, `nm`, `sha256sum` on the
host and from a `--network none --rm` container on the local image, with
nothing mounted.

## The main question

**Will the image's libraries run speech recognition and Talkie correctly
on this GPU?** Nothing I measured says they will not, and nothing that can
be measured without running a kernel on the GPU says they will. What was
established:

- The link closure is complete. The ROCm files the three programs need
  from `/opt/rocm-7.2.0/lib` are exactly the ten the units map today (same
  sonames, same versions, same `DT_NEEDED` lists as Ollama's builds). No
  eleventh file is needed.
- The image's libraries need at most `GLIBC_2.38` and `GLIBCXX_3.4.32`.
  The host (Ubuntu 25.10) has glibc 2.42 and `libstdc++.so.6.0.34`.
- Every symbol the image's `libhsa-runtime64` imports from `libdrm`,
  `libelf` and `libnuma` (3, 20 and 7 symbols; `ELFUTILS_1.0`/`1.6`,
  `libnuma_1.1`/`1.2`) is exported by the host's copies.
- Both trees carry gfx1151 kernels: 96 gfx1151 files in `rocblas/library`
  on each side, and `TensileLibrary_lazy_gfx1151.dat` in the image.
- No unit sets `ROCM_PATH`, `HIP_PATH`, `ROCBLAS_TENSILE_LIBPATH` or
  `HIPBLASLT_TENSILE_LIBPATH` (read from `/proc/<pid>/environ`), and
  `/opt/rocm` does not exist on the host, so nothing resolves through a
  ROCm tree today except by the library's own location.

**How would we know before production is switched?** Only by the dev
Whisper unit transcribing on the new libraries. It is the same binary,
venv, model and compute type as production and differs only in the
prompt, so it is a real canary. The criteria as written do not use it as
one: the transcription check sits in criterion 9, which the test agent
runs after all three restarts (points 1 and 2).

## Points

| # | Point | Grade |
| --- | --- | --- |
| 1 | The dev transcription check runs after production is already switched. It must be a gate between the dev restart and the next one. | Must change |
| 2 | Production gets no transcription after its own restart: only active, maps, `NRestarts`. A model load runs no matrix kernel. | Must change |
| 3 | The transcription criterion cannot be checked as worded: "same text" holds for speech rows only, the dev prompt can change between the two runs, the agent guard rail forbids STT runs, and a pass rule for a differing row is missing. | Must change |
| 4 | `hipblaslt/library` is left to a rule `ldd` cannot apply. The image has it (4.5 GB); Ollama's directory has none. Extracting it adds a second variable. | Must change |
| 5 | A fact is incomplete: all 1,793 files of `rocblas/library` differ by sha256 too, not only the ten libraries. The kernels the GPU executes change. | Must change (the text) |
| 6 | The extraction method is not stated. A bind-mounted output directory gives root-owned files and makes "writes only inside the directory" unprovable. | Must change |
| 7 | Criterion 6 fails on files the item should not edit (`ROADMAP.md`, the root evaluation document). | Must change |
| 8 | The four system libraries: decide now that the host's are used. Ollama's copies are neither the image's nor the host's. | Should change |
| 9 | Simpler design: a fixed list of ten sonames and one data directory in the script, in place of an `ldd` closure computed inside the image. | Should change |
| 10 | Criterion 5's load test should enumerate the GPU device, not only load libraries. | Should change |
| 11 | Manifest: say what it records (symlinks, modes), where it sits, and what it must not contain. | Should change |
| 12 | `/proc/<pid>/maps` cannot show where the kernel data was read from. Close this at the removal, by renaming Ollama's directory before deleting it. | Should change |
| 13 | Rollback: keep the installed unit files, set a time bound for production, note the missing start limit. | Should change |
| 14 | `runtime-libs` already holds three files from the image, by hash. Leave it; say so. | Note |
| 15 | Size of the extracted set: 1.9 GB. | Note |
| 16 | Run-time `dlopen` names and environment variables in the image's libraries. | Note |
| 17 | The nightly prompt timer restarts the dev unit. | Note |
| 18 | Nothing in `scripts/upgrade_rules.toml` tracks ROCm or the image; the image is the only local source. | Note |
| 19 | The image should be run by digest, not by tag. | Note |
| 20 | Facts checked and found right. | Note |

## Must change

**1. The gate is in the wrong place.** The plumbing section says the
coordinator restarts "in this order, checking each before the next", but
does not say what the check is. The only check that runs a kernel is in
criterion 9, done by the test agent "after the coordinator's installs and
restarts", that is, with production already on the new libraries. Word
the order as steps with their own gates: (a) record the before runs
(point 3); (b) install and restart `wyoming-whisper-dev`; gate: active,
port 10301 listening, the bench's speech rows against the before run,
latency, no rocBLAS or hipBLASLt line in its journal; (c) install and
restart `llama-talkie`; gate: one answer on the dev route, GPU busy 0 %
for two minutes (its known failure is the idle spin of 2026-06-09, so
this check belongs here, not at the end); (d) only then production. The
coordinator may run the bench: it is a run of an existing script, not
code. The test agent afterwards re-reads the results and repeats the
checks, as criterion 9 says.

**2. Production is switched blind.** After its restart the criteria check
that `wyoming-whisper` is active, maps the new files and has not
restarted. The model loads at start, so that proves allocation on the
GPU and nothing about a transcription. The first proof would be a
household utterance. Add: a before and an after run of a subset of the
speech clips on port 10300, sent straight to the port, with the idle
check M13's test agent used (all three satellites `idle`, no production
request without its `request_complete` in the last 90 s). M13-e2e ran 105
speech rows on 10300 this way; 25 rows is enough here. Same pass rule as
point 3.

**3. The transcription criterion, reworded.** Measured or read:

- The clips exist: `~/kronk-m13-bench/synthetic/` holds `labels.csv` and
  754 audio files. `scripts/stt_prompt_bench.py run --sheet … --port
  10301` sends files to a loopback Wyoming port and nowhere else, records
  text and time, and `compare --same A B` lists rows whose text differs.
  No Home Assistant, no Piper, no household audio. Do not re-render: use
  the existing files for both runs.
- "Same text" is not true of the whole set even with nothing changed. M13
  found 714 of 714 speech rows repeat exactly, and 4 of 40 non-speech
  rows gave 2 or 3 texts in 3 runs on production (temperature fallback).
  On the dev port the 40 gave one text each, but the rule should not lean
  on that.
- The dev prompt is rebuilt nightly at 04:00 and the unit restarted when
  it changes (`kronk-stt-prompt.timer`; last restart 2026-11-24 04:02:43).
  A before run from yesterday compares two prompts.
- The agents' guard rail reads "No STT or wake-stage run (audio capture
  is on)". The capture is in Home Assistant's pipeline, which this path
  does not touch, but the test agent's brief must carry the exemption in
  words or the agent will refuse.
- The kernels are a different build (point 5). A different float16 result
  in the last bit can move a borderline row. The criteria should say now
  what happens then.

Wording: "The coordinator records, on the day of the switch and after
04:05, a before run of the 714 speech rows of
`~/kronk-m13-bench/synthetic/labels.csv` on 10301 and of 25 of them on
10300, and the sha256 of `dev-prompt.env`. After each Whisper restart the
same rows are run again with the same `dev-prompt.env` hash. Pass: every
speech row has the same text. If any differ: the rows are listed, and the
item passes only on the coordinator's ruling that word error rate and
command-form errors are not worse; production is not switched before that
ruling. p50 and p95 latency are within 20 % of the before run (tenet 12).
Non-speech rows are not compared. These runs go straight to the Wyoming
port and are exempt from the brief's 'no STT run' rail; nothing goes
through Home Assistant."

**4. `hipblaslt/library`.** The image has `/opt/rocm/lib/hipblaslt/library`:
2,932 files, 4.5 GB (95 gfx1151 files, 32 MB, plus three files for all
architectures, 5 MB). Ollama's `rocm` directory has no `hipblaslt`
directory at all, so all three units run today with `libhipblaslt.so.1`
loaded (it is a `DT_NEEDED` of `librocblas`) and its kernel data absent.
`librocblas` carries `ROCBLAS_USE_HIPBLASLT` and the strings "hipBlasLT
failed, falling back to tensile". The criteria's rule, "if the image has
one and a library asks for it", cannot be applied by `ldd`: the image has
one, and whether rocBLAS asks for it on gfx1151 is decided at run time. I
did not establish rocBLAS 5.2's default for gfx1151. Whichever it is,
extracting the directory can only change which GEMM path runs, on top of
the change of build. Decide: **do not extract it**; the layout matches
what runs today, and the change stays one variable (tenet 9). The
manifest and the docs say it is left out on purpose. The gate in point 1
compares the journals: neither Whisper unit logs a rocBLAS, hipBLASLt or
Tensile line today (0 in production's journal this boot, 0 in dev's last
400 lines); a new one after the switch is a finding.

**5. The kernels differ too.** The criteria say both sides "hold 1,793
files under `rocblas/library`" and that the ten libraries differ by
sha256. Measured: the 1,793 names are the same on both sides and **all
1,793 files differ by sha256**, the `.hsaco` kernel objects and the
`.dat` selection tables included. So the code the GPU executes for every
matrix product changes, not only the host-side libraries. This is why
points 1 to 3 are graded as they are. Say it in the findings so the build
and test agents weigh the transcription gate correctly.

**6. Extraction method.** Docker here is rootful (`/var/lib/docker`). The
existing `runtime-libs` files and `build/ctranslate2-install` are owned
by root because a container wrote them through a bind mount. Done that
way again, the build agent cannot delete its scratch directories, Drew
needs sudo to replace `rocm-7.2/lib`, and "writes only inside the
directory it is given" rests on the script inside the container. Word
criterion 3: "the script mounts nothing writable; the files leave the
container as a tar stream on stdout (`docker run --rm --network none
--entrypoint tar <image@digest> -C /opt/rocm-7.2.0/lib -cf - <list>`) and
are unpacked by the caller, so every file is owned by the caller and
cannot land outside the directory; the script refuses a directory that
exists and is not empty." With point 9 nothing of the host needs to be
mounted into the container at all.

**7. Criterion 6 cannot pass as written.** Today the grep outside `docs/`
hits `ROADMAP.md` lines 60, 73 and 75 (the M26 entry and the removal
entry, which must keep naming the paths until Drew deletes them) and
`Kronk evaluation findings and implementation plan.md` line 89 (a dated
findings table). Scope 2 says "as something in use", which a grep cannot
judge. Word it: "the grep over the repo, leaving out `docs/`,
`ROADMAP.md` and `Kronk evaluation findings and implementation plan.md`,
finds nothing", and have the build move the ROADMAP line as the docs
lifecycle requires. Criterion 1's test must not contain the path as a
literal the grep would find, or must be built from parts. Deleting
`scripts/model_bench.py` is clean: nothing in the repo imports or calls
it (`coordinator_model_bench.py` and `devops_model_bench.py` are other
files).

## Should change

**8. System libraries.** The criteria call the four "ordinary system
libraries" and leave them "to the host unless a program fails". Measured:
Ollama's copies are not Ubuntu's (`libdrm.so.2.4.0` 87,904 bytes dated
2023-10-11, `libelf-0.190.so` 109,000 bytes, `libnuma.so.1.0.0` 51,400
bytes; the image's are 88,664, 121,560 and 52,248 bytes with different
hashes). The host has libdrm 2.4.125, libelf 0.193 and libnuma 2.0.19;
the image's `libhsa-runtime64` was built against 2.4.122, 0.190 and
2.0.18, and every symbol it imports is present on the host (above).
`/usr/share/libdrm/amdgpu.ids` exists on the host. Decide in the criteria:
the host's four are used, and no copy from the image (24.04 builds on a
25.10 host would be the worse choice). State it as a known third change
riding on the same restart (the units move from Ollama's libdrm to the
host's), so a failure in `libhsa-runtime64` start-up is read with that in
mind. Point 10 tests it before any unit is touched.

**9. A simpler design.** The closure is small and now known. From
`readelf -d`: `libctranslate2.so.4` needs `libhipblas.so.3` and
`libamdhip64.so.7` from ROCm (the rest come from `runtime-libs`);
`libggml-hip.so.0` needs `libhipblas.so.3`, `librocblas.so.5` and
`libamdhip64.so.7`; `llama-server` itself needs no ROCm file. Followed
through the image, that is the ten files and nothing else. So the script
can hold the list: ten sonames (each as the versioned file plus its
soname symlink) and `rocblas/library`. No `ldd` inside the image, no
host binaries mounted into a container, no rule for data directories
that `ldd` cannot see in any case. "Found, not guessed" is then a test,
which criterion 4 already is: host `ldd` with the new path resolves
everything, and the set covers every file the running units map. A new
ROCm release edits the list and the digest together.

**10. Criterion 5 proves loading, not the runtime.** Importing
`ctranslate2` and `llama-server --version` run constructors only.
`ctranslate2.get_cuda_device_count()` and `llama-server --list-devices`
initialise HIP and the HSA runtime: they open `/dev/kfd`, read the
topology through the host's libdrm, libelf and libnuma, and apply
`HSA_OVERRIDE_GFX_VERSION`. Neither loads a model or allocates on the
GPU, and each exits in seconds. Require both, with each unit's own
`Environment` lines, expecting one device; and have the test read its own
`/proc/self/maps` (or `LD_DEBUG=libs`) to show the ten came from the
scratch directory. This is the cheapest evidence available before the
dev restart, and the only one for point 8.

**11. Manifest.** Byte-identical manifests from two runs are achievable,
on these conditions, which the criterion should list: entries sorted
under `LC_ALL=C`; relative paths; no timestamp, hostname, user or
destination path in the file; symlinks recorded with their targets (the
programs ask for `libamdhip64.so.7`, the file is
`libamdhip64.so.7.2.70200`, so ten links are part of the set); file modes
recorded. "Every file in the directory is in the manifest" needs a rule
for the manifest itself: put it beside `lib/`, as
`rocm-7.2/MANIFEST`, so `lib/` holds only what was extracted and the
`rocblas/library` lookup (relative to `librocblas`) sees the image's
layout unchanged. Add an offline check: the manifest lists
`rocblas/library/TensileLibrary_lazy_gfx1151.dat` and 1,793 files under
`rocblas/library`.

**12. Where the kernel data is read from.** None of the three processes
holds a mapping or an open file under `rocblas/library` (read from
`/proc/<pid>/maps` and `/proc/<pid>/fd`): rocBLAS reads the files and
closes them. So "no process maps any file under `/usr/local/lib/ollama`"
is silent about data. rocBLAS locates the directory relative to
`librocblas` itself (strings `/../rocblas/library`, `/library`), so data
follows the library; that is an inference, and the criterion should say
it is one. The proof belongs to the removal: the instruction to Drew
should be to rename `/usr/local/lib/ollama` aside, restart
`wyoming-whisper-dev`, run the bench rows, and only then delete (tenet
11). Put that sentence in the criteria's hand-off to Drew and in the
ROADMAP removal entry.

**13. Rollback.** Sound in principle: the installed unit files are
byte-identical to `systemd/` today (checked by `diff`), there are no
drop-ins, and the old path works while Ollama's directory exists.
Additions: copy the three installed unit files to a scratch directory
before the first install, so rollback does not depend on a git command
typed under pressure; `wyoming-whisper.service` has no start limit, so a
failing start loops every 5 s until someone stops it; give production a
bound (not listening on 10300 within 60 s, or the subset run fails: roll
back at once, rule later). Keep Talkie's search order (ROCm directory
first, then its own).

## Notes

**14.** `runtime-libs/libhiprand.so.1`, `librocrand.so.1` and `libomp.so`
have the same sha256 as the image's `libhiprand.so.1.1.70200`,
`librocrand.so.1.1.70200` and `llvm/lib/libomp.so`. They are already the
image's builds, copied by hand with no manifest. `libopenblas.so.0` is
not in the image (the build script installed it with apt) and not on the
host. Leave `runtime-libs` alone (the scope excludes changes to what the
units run), keep it first in the Whisper search path, and have the build
report record the three matches.

**15.** The ten files are 1.27 GB (`librocsolver` alone is 931 MB, needed
only because `libhipblas` links it) and `rocblas/library` is 644 MB:
1.9 GB in all. Ollama's directory is 2.5 GB because it also holds
Ollama's own `libggml-hip.so` (733 MB), which nothing of ours loads. With
`hipblaslt/library` it would be 6.4 GB. A gfx1151-only cut of the data
would be about 20 MB, and is not worth a filter that has to be right.

**16.** Names the image's libraries can open at run time, none of which
appears in today's maps: `libGL.so.1` and `libX11.so.6` (libamdhip64,
OpenCL interop), `libdtif.so` and whatever `HSA_TOOLS_LIB` names
(libhsa-runtime64). `libamd_comgr` and `librocroller` read `ROCM_PATH`,
`HIP_PATH` and `LLVM_PATH` for run-time compilation; neither program
links `libhiprtc`, so I take it that path is unused. `libhipblaslt` has
`/opt/rocm/lib/hipblaslt/library/…` compiled in as a fallback; it is
absent on the host today and stays absent. Criterion 2 already forbids a
new `Environment` line; keep it that way.

**17.** `kronk-stt-prompt.timer` runs at 04:00 and `try-restart`s
`wyoming-whisper-dev` when the prompt text changed. It will restart the
unit on whichever file is installed, which is harmless, but the
"`NRestarts=0` since its restart" check and the before and after runs
should not straddle it.

**18.** `scripts/upgrade_rules.toml` tracks llama.cpp builds (Talkie's is
reported as a hand build) and the Whisper venv's package, not ROCm and
not the image. The image is local only (29.3 GB by `docker images`,
created 2026-01-21). If it is pruned, rebuilding `rocm-7.2/lib` needs a
pull by digest of a ten-month-old image. The doc of criterion 7 should
say that, and that the same image is what CTranslate2 and Talkie's
server were built in.

**19.** The image's ID and its repo digest are the same value
(`sha256:86e11093…683a`). The script should run
`rocm/dev-ubuntu-24.04@sha256:…`, not the `7.2-complete` tag, so the
check and the run cannot name different images. A `6.4-complete` image
is also on the host.

**20.** Confirmed as the criteria state: the three units each map the
same 14 files from Ollama's directory; the ten ROCm files have the same
names and versions in the image and different hashes; the image's digest
and `/opt/rocm/.info/version` (7.2.0); both programs were built in that
image (`RUNPATH /opt/rocm-7.2.0/lib` in `libctranslate2.so.4`,
`libggml-hip.so.0` and `llama-server`); the two Whisper units and Talkie
are active with `NRestarts=0`.
