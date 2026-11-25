# Claude Code settings (sanitized copy)

`settings.example.json` is a sanitized copy of the operator's
`~/.claude/settings.json`. It is kept so that the Claude Code setup this
repo's agent workflow depends on is recorded and reproducible.

Sanitized on 2026-11-25. The source held no secrets. One change was made:
the home directory in the hook command was replaced with `$HOME`, and the
quoting changed from single to double quotes so that `$HOME` expands.

## How to use it

Compare it with `~/.claude/settings.json`, or merge the keys you need into
that file. Claude Code does not read the file from this location. Changing
it here changes nothing until the live file is changed too.

The `SessionStart` hook calls `~/.claude/hooks/herdr-agent-state.sh`. That
script is not part of this copy.

## Settings

| Key | What it does | Why it is set |
|---|---|---|
| `permissions.defaultMode` = `auto` | Sets the permission mode a session starts in. | Purpose not recorded. |
| `permissions.skipDangerousModePermissionPrompt` = `true` | Skips the confirmation prompt shown when a session starts with permission checks bypassed. | Purpose not recorded. |
| `model` = `fable` | Sets the default model for new sessions. | Purpose not recorded. |
| `hooks.SessionStart` (matcher `*`) | Runs `herdr-agent-state.sh session` at every session start, with a 10 second timeout. | Purpose not recorded. The script name suggests it reports the session to herdr, the terminal multiplexer the agent workflow runs in (`docs/sprint/WORKFLOW.md`). |
| `effortLevel` = `xhigh` | Sets the default reasoning effort. | Purpose not recorded. |
| `modelSettings` | Sets an effort level per model: `claude-fable-5-1` high, `claude-opus-5-5` low, `claude-fable-5-2` medium. | Purpose not recorded. |
| `skipDangerousModePermissionPrompt` = `true` (top level) | Same setting as the one under `permissions`. It appears in both places in the live file. | Purpose not recorded. |
| `switchModelsOnFlag` = `false` | Purpose not recorded. | Purpose not recorded. |
| `env` | Sets environment variables for every session. The rows below list them. | See each variable. |
| `env.CLAUDE_CODE_RETRY_WATCHDOG` = `1` | Turns on the retry watchdog. It keeps retrying API responses 429 and 529 past the normal retry limit. | On 2026-11-24 a coordinator session's turn, started automatically when a background agent finished, hit an Anthropic API 529 (overloaded). It exhausted its retries and sat idle for about eight hours. |
| `env.CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS` = `1800000` | Sets how long the watchdog keeps retrying one request: 1,800,000 ms, which is 30 minutes. | Same incident. Thirty minutes per request is the limit chosen. |

Both watchdog variables take effect on the next session start. When this
was written (2026-11-25) the watchdog had not yet been seen to work.

## Keeping this current

When a situation leads to a new or changed Claude Code setting:

1. Update the live file, `~/.claude/settings.json`.
2. Update this copy and its table in the same change.
3. Record the date and the reason in the table row.

Never copy the live file over this one without sanitizing it first.
Replace secrets, tokens, email addresses, personal names, home LAN
addresses and the home directory path with placeholders.
