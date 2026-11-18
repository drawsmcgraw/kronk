# M18 coordinator fixes after the fourth test

Sprint coordinator, 2026-11-18 (UTC), branch `refactor`, after
`docs/sprint/M18-e2e4.md` passed M18 with no blocking finding. Three small
changes from its "must know" list, made directly instead of through a fourth
fix round. Nothing was upgraded.

| Finding | Change | Test |
| --- | --- | --- |
| Z1: a rollback of Home Assistant was abandoned, HA left stopped on the version that had just failed, when the rollback's own stop ended in a kill or its exit code could not be read | `scripts/upgrades.py` `_ha_steps(kill_fails=False)` from `ha_rollback`: the reading is noted (`Ctx.stop_note`), the old image is brought up all the same, and the outcome's detail says how the stop ended. The upgrade's own stop is unchanged: a kill there still fails it before the new container is created | `tests/test_m18_fix3.py::test_a_rollback_goes_on_to_the_old_image_when_its_own_stop_was_not_clean` (killed, unreadable); both fail on the code before |
| Z8: `m19-can-you-ask-kronk-to-turn-it-up` failed once in 22 replays (the 4B model answered without the tool call); in a monthly verify that rolls back a good upgrade | added to `replay.flappers` in `scripts/upgrade_rules.toml` with the reason | data only |
| Z5: with `~/.config/kronk/upgrade.env` missing, the two units failed to start with nothing reported | the drop-in is `EnvironmentFile=-%h/.config/kronk/upgrade.env`: the script then falls back to the rules file's branch and declines on it, which is reported | `test_the_units_carry_no_branch_and_the_drop_in_points_at_the_one_file` |

Not changed, logged for Drew in `PROMOTIONS.md`: Z2 (the month-end notice
can say "declined 7 times" when night 1 upgraded something), Z3 (a failed
`git rev-parse` right after a commit is read as the commit failing), Z4 (a
blocked record's texts name `--ignore-window`, which does nothing for a
block; the order that works is `ack --yes-leave-as-is`, then the fix by
hand), Z7 (a stale "HA is down" line on `/services` beside the newer
"started" line).

`./scripts/run_tests.sh -q -p no:cacheprovider`: `5423 passed, 3 skipped,
53 xfailed, 6 warnings`.
