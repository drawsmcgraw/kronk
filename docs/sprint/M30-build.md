# M30 step A build

Guard rails: none broken. Nothing installed, no `daemon-reload`, no
restart, Whisper not started by hand. Read-only looks outside the repo:
the installed unit, the model cache's `refs/main` and `snapshots/`, the
venv's package versions and `faster_whisper/utils.py`. Two throwaway git
worktrees of past commits in scratch, to place a test failure; removed
(`git worktree list` shows the main tree only).

## What changed

| File | Change |
|---|---|
| `systemd/wyoming-whisper.service` | `Environment=HF_HUB_OFFLINE=1`, `Environment=HF_HUB_DISABLE_TELEMETRY=1` after `LD_LIBRARY_PATH`, and an eleven-line comment above them: why (wyoming-faster-whisper 3.1.0 does not pass `--local-files-only` to faster-whisper; an online load asks the Hub and rewrites `refs/main`), the pin (`mobiuslabsgmbh/faster-whisper-large-v3-turbo` at `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`), and that a by-hand faster-whisper upgrade can rename the cache directory and fail the start. Nothing else: no start limit, port unchanged, no prompt. |
| `docs/features/voice-pipeline.md` | The gotcha "the dev unit sets `HF_HUB_OFFLINE=1` … Production still does at each load" now says both units set it (production since M30), and names the upgrade risk. |
| `tests/test_m30_whisper_offline.py` | New, 9 tests: both units carry the two lines; production's comment carries the reason, the repository and the revision, and the upgrade risk; production and dev name the same `--model`, `--data-dir`, `--download-dir`, `--device`, `--compute-type`; production keeps `tcp://0.0.0.0:10300`, no `StartLimit*`, no prompt. |

Docs checked and left alone: `README.md` ("Whisper (STT) units") says
nothing about the Hub and stays true. `docs/VOICE_SETUP.md:293` ("First
start downloaded the Whisper `small` model from HuggingFace") is the
build journal's record of 2026-05 and was true when written.

The revision on disk was checked, not copied from the review:
`models/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/refs/main`
reads `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`, the only directory under
`snapshots/`. Installed versions: wyoming-faster-whisper 3.1.0,
faster-whisper 1.2.1, huggingface_hub 1.16.1, as the review says.
`faster_whisper/utils.py` maps `large-v3-turbo` to
`mobiuslabsgmbh/faster-whisper-large-v3-turbo`.

## A1

`diff ~/.config/systemd/user/wyoming-whisper.service systemd/wyoming-whisper.service`,
exit 1, the whole output:

```
13a14,26
> # HF_HUB_OFFLINE (work item M30): the installed wyoming-faster-whisper 3.1.0
> # does not pass --local-files-only to faster-whisper, which would otherwise ask
> # the Hugging Face Hub for updates at each model load, and rewrite the cache's
> # refs/main. Offline, the model loads from the cache only, which is the pin:
> # mobiuslabsgmbh/faster-whisper-large-v3-turbo at revision
> # 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf, under --data-dir below.
> # faster-whisper maps "large-v3-turbo" to that repository name; an upgrade of
> # faster-whisper by hand can change the name, and so the cache directory it
> # looks for, and the start then fails. The upgrade runs never change the venv
> # (scripts/upgrade_rules.toml: wyoming-faster-whisper is report only), so only
> # a by-hand upgrade can do that.
> Environment=HF_HUB_OFFLINE=1
> Environment=HF_HUB_DISABLE_TELEMETRY=1
```

Additions only; the installed unit equalled the repo's before the edit.

`systemd-analyze --user verify` on a copy in scratch
(`…/scratchpad/m30a/wyoming-whisper.service`): no output, exit 0.

## Anything else

1. **The criteria's "the venv is in no upgrade rule" is not so.**
   `scripts/upgrade_rules.toml` has a `wyoming-faster-whisper` component,
   `kind = "host-venv"`, `venv = "~/services/wyoming-whisper/.venv"`,
   `minor = "none"`: reported by the quarterly check, never applied. The
   conclusion stands (only a by-hand upgrade changes the venv), so the
   comment says that instead of "no rule". Also: the rule watches
   wyoming-faster-whisper only; faster-whisper itself, which owns the
   model map, has no line, so a report would not flag the map moving.
2. **`./scripts/run_tests.sh` has one failure, not from this change.**
   `tests/test_m33_work_state.py::test_the_real_import_at_the_build_start`
   fails at `assert s["notes"] == []`:
   `'M33: under way at 4eee8fd and not in the seed; imported active'`.
   It passes at `eba627a` and fails from `f3b4d4c` ("M33 promoted") on,
   on clean worktrees of each commit. That commit removed
   `"state": "active"` from M33 in `docs/sprint/M33-seed.json`; the test
   pins the import at `4eee8fd` but reads the seed from the working tree.
   Left alone: M33's test and the seed are the coordinator's. An escape
   by the promotion commit.

## Tests

`./scripts/run_tests.sh`, last lines:

```
=========================== short test summary info ============================
FAILED tests/test_m33_work_state.py::test_the_real_import_at_the_build_start
= 1 failed, 7967 passed, 3 skipped, 54 xfailed, 6 warnings in 159.41s (0:02:39) =
```

The new file alone: `9 passed`; with `tests/test_m26_rocm_libs.py`,
which also reads both units: `60 passed`.
