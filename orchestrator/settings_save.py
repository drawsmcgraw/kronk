"""The /settings save (sprint M21 step 2, criteria 7, 8, 10, 12, 15, 24).

One path for a save, a revert and a candidate arriving from production
("apply to dev first"):

  1. one save at a time (an asyncio lock, and flock on .git/ for any other
     process);
  2. editing must be on (hash set, git usable, directory writable);
  3. the base: the editor's version (git blob id of the bytes it loaded)
     must be the file on disk now, else 409 with what changed meanwhile;
  4. encode (LF, UTF-8, size limit) and validate with the loader's own
     schema (the orchestrator's, or tool_service's /config/validate):
     invalid -> 422 with file and line, nothing written;
  5. the corpus check for music.yaml and lead_in.yaml (settings_corpus):
     a row that newly fails blocks the save unless overridden;
  6. write atomically (settings_store.write_file);
  7. reload the service that reads the file, and confirm the digest it
     now has in effect is the one just written;
  8. reload the other service, so both report fresh status;
  9. one git commit of that file, naming the file, the keys and who;
 10. an audit line.

Which wins on failure: the last committed state, the file on disk and the
values in effect are kept equal. If the owner's reload refuses the file or
cannot be reached (step 7), or the digest differs, or the commit fails
(step 9), the previous bytes are written back, the owner is reloaded again
and the commit is not made: the save did not happen, and the page says
which step failed. If writing the previous bytes back fails too, the
answer says exactly that the file on disk holds the new text, uncommitted
and not in effect, and the audit line records it. The other service (step
8) does not read the file; its reload failing is reported as a warning and
does not undo the save.
"""
import asyncio
import difflib
import fcntl
import hashlib
import logging
import os
from contextlib import contextmanager
from pathlib import Path

import config_files
import config_loader
import settings_audit
import settings_git
import settings_store

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
MAX_DIFF_LINES = 400


class SaveRefused(Exception):
    """A save that did not happen. status is the HTTP status; body the JSON."""

    def __init__(self, status: int, message: str, **body):
        self.status = status
        self.body = {"ok": False, "message": message, "detail": message, **body}
        super().__init__(message)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def unified(a: str, b: str, from_name: str, to_name: str) -> str:
    lines = list(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                      fromfile=from_name, tofile=to_name, n=2))
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + [f"... ({len(lines) - MAX_DIFF_LINES} more lines)\n"]
    return "".join(lines) or "(no difference)\n"


def changed_keys(rel: str, old: str | None, new: str) -> list[str]:
    """The keys a new text changes against the old one (YAML), or a line
    for a prompt."""
    info = settings_store.check_name(rel)
    if info.kind == "prompt":
        if old is None:
            return ["prompt text (new file)"]
        return [f"prompt text ({len(old)} -> {len(new)} chars)"] if old != new else []
    try:
        new_data, _ = config_loader.parse_yaml(new, rel)
    except config_loader.ConfigInvalid:
        return ["(does not parse)"]
    try:
        old_data, _ = config_loader.parse_yaml(old or "", rel)
    except config_loader.ConfigInvalid:
        return ["(the previous version did not parse)"]
    a, b = settings_git._flat(old_data), settings_git._flat(new_data)
    keys = sorted(k for k in set(a) | set(b) if a.get(k, KeyError) != b.get(k, KeyError))
    if not keys and old != new:
        return ["comments or layout only"]
    return keys


def message(rel: str, keys: list[str], who: str, overrides: list[str], extra: list[str] | None = None) -> str:
    subject = f"settings: {rel}: {', '.join(keys) or 'no key changed'}"
    if len(subject) > 100:
        subject = subject[:99] + "…"
    body = [f"File: {rel}", f"Keys: {', '.join(keys) or '(none)'}", f"By: {who}"]
    if overrides:
        body.append(f"Override: voice corpus rows {', '.join(overrides)} confirmed by the editor "
                    "(they newly fail with this change)")
    body += extra or []
    return subject + "\n\n" + "\n".join(body) + "\n"


@contextmanager
def _flock(base: Path):
    p = Path(base) / ".git" / "kronk-settings.lock"
    fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ── the two services ─────────────────────────────────────────────────────────

def _ts_client(timeout: float):
    import settings_api
    return settings_api.ts_client(timeout)


async def validate(rel: str, text: str) -> list[dict]:
    """[] when the candidate loads; else its problems (file, line, message)."""
    info = settings_store.check_name(rel)
    if info.owner == "orchestrator":
        try:
            config_loader.check_candidate(rel, text)
            return []
        except config_loader.ConfigInvalid as e:
            return [p.as_dict() for p in e.problems]
    try:
        async with _ts_client(15) as c:
            r = await c.post("/config/validate", json={"name": rel, "text": text})
        body = r.json()
    except Exception as e:
        return [{"file": f"config/{rel}", "line": None,
                 "message": f"tool_service could not validate it ({config_files.safe(e, 120)}); nothing written"}]
    return [] if body.get("ok") else body.get("errors") or [{"file": f"config/{rel}", "line": None,
                                                            "message": "tool_service refused it"}]


async def reload_service(owner: str) -> dict:
    """{"ok": bool, "changed"/"errors": ..., "digests": {...}} for one service."""
    if owner == "orchestrator":
        result = config_loader.reload()
        return {**result, "digests": dict(config_loader.latest().digests)}
    try:
        async with _ts_client(15) as c:
            r = await c.post("/config/reload")
            result = r.json()
            st = (await c.get("/config/status")).json()
        return {**result, "digests": st.get("files") or {}}
    except Exception as e:
        return {"ok": False, "errors": [{"file": None, "line": None,
                                         "message": f"tool_service did not answer the reload: {config_files.safe(e, 120)}"}],
                "digests": {}}


# ── the save ─────────────────────────────────────────────────────────────────

def editing_problem(base: Path) -> str | None:
    import settings_api
    st = settings_api.editing_status(base)
    return None if st["enabled"] else st["reason"]


async def save(rel: str, text, base_blob: str | None, who: str, client: str, *, overrides=(),
               event: str = "save", extra: list[str] | None = None, corpus=True) -> dict:
    """Run the whole path (module docstring). The answer dict on success;
    SaveRefused otherwise."""
    try:
        info = settings_store.check_name(rel)
    except settings_store.StoreError as e:
        raise SaveRefused(404, str(e))
    base = config_loader.config_dir()
    overrides = sorted({str(o) for o in overrides or ()})
    async with _lock:
        problem = editing_problem(base)
        if problem:
            raise SaveRefused(403, problem)
        with _flock(base):
            return await _save_locked(base, rel, info, text, base_blob, who, client, overrides, event,
                                      extra, corpus)


async def _save_locked(base, rel, info, text, base_blob, who, client, overrides, event, extra, corpus):
    def refuse(status, msg, reason, **body):
        settings_audit.record(f"{event}_refused", client=client, file=rel, reason=reason,
                              overrides=overrides or None)
        raise SaveRefused(status, msg, **body)

    try:
        data = settings_store.encode(rel, text)
    except settings_store.StoreError as e:
        refuse(422, f"not saved: {e}", "invalid", errors=[{"file": f"config/{rel}", "line": None, "message": str(e)}])
    new_text = data.decode("utf-8")
    try:
        old = settings_store.read_bytes(base, rel)
    except settings_store.StoreError as e:
        refuse(409, f"not saved: {e}", "unreadable")
    old_text = old.decode("utf-8", "replace").replace("\r\n", "\n") if old is not None else None
    current_blob = settings_git.blob_sha(old) if old is not None else settings_git.blob_sha(b"")

    # 3. The base.
    if base_blob != current_blob:
        their = None
        try:
            base_bytes = settings_git.run(base, "cat-file", "blob", base_blob) if base_blob else None
            their = unified(base_bytes.decode("utf-8", "replace"), old_text or "", f"{rel} (your base)",
                            f"{rel} (now)")
        except (settings_git.GitError, AttributeError):
            pass
        last = (settings_git.file_log(base, rel, limit=1) or [None])[0]
        refuse(409, f"not saved: {rel} changed since you opened it; nothing was merged or overwritten. "
                    "Reload it, apply your change again, then save.", "conflict",
               conflict={"base": base_blob, "current": current_blob, "their_change": their,
                         "yours_vs_current": unified(old_text or "", new_text, f"{rel} (now)", f"{rel} (yours)"),
                         "who": (last or {}).get("who"), "subject": (last or {}).get("subject"),
                         "uncommitted": settings_git.is_dirty(base, rel)})

    # 4. Validate with the loader's schema.
    problems = await validate(rel, new_text)
    if problems:
        refuse(422, f"not saved: {rel} does not validate (nothing was written)", "invalid", errors=problems)
    keys = changed_keys(rel, old_text, new_text)
    if old is not None and data == old:
        return {"ok": True, "changed": False, "message": f"no change: {rel} already holds this text",
                "blob": current_blob, "keys": [], "commit": None}

    # 5. The corpus check (music policy, lead-in).
    corpus_result = None
    if corpus:
        import settings_corpus
        if settings_corpus.applies(rel):
            corpus_result = await settings_corpus.check(rel, new_text)
            if corpus_result.get("error"):
                refuse(503, f"not saved: the voice corpus check could not run: {corpus_result['error']}",
                       "corpus_unavailable", corpus=corpus_result)
            blocking = [r["id"] for r in corpus_result["newly_failing"] if r["id"] not in overrides]
            if blocking:
                refuse(409, f"not saved: {len(blocking)} voice corpus rows newly fail with this change "
                            f"({', '.join(blocking)}). Confirm each to override, or change the text.",
                       "corpus", corpus=corpus_result, blocking=blocking)
            overrides = [o for o in overrides if o in {r["id"] for r in corpus_result["newly_failing"]}]
    else:
        overrides = []

    # 6. Write.
    try:
        settings_store.write_file(base, rel, data)
    except settings_store.StoreError as e:
        settings_audit.record(f"{event}_failed", client=client, file=rel, keys=keys, reason=f"write: {e}")
        raise SaveRefused(500, f"not saved: {e}; the file is as it was")

    async def undo(step: str, why: str, status: int = 500, **body):
        restored = True
        try:
            if old is None:
                os.unlink(Path(base) / rel)
            else:
                settings_store.write_file(base, rel, old)
        except (OSError, settings_store.StoreError) as e:
            restored = False
            why += f"; and writing the previous file back failed: {e}"
        try:
            settings_git.run(base, "reset", "-q", "--", rel)
        except settings_git.GitError:
            pass
        again = await reload_service(info.owner)
        if restored:
            msg = (f"not saved: {step} failed: {why}. The previous {rel} is back on disk"
                   f"{' and in effect' if again.get('ok') else ''}; nothing was committed.")
        else:
            msg = (f"NOT SAVED and NOT CONSISTENT: {step} failed: {why}. The file on disk holds your text, "
                   f"which is not committed and not in effect. Restore it by hand: git -C <live dir> checkout -- {rel}")
        settings_audit.record(f"{event}_failed", client=client, file=rel, keys=keys,
                              reason=f"{step}: {why}", outcome="restored" if restored else "inconsistent")
        raise SaveRefused(status, msg, step=step, restored=restored, **body)

    # 7. Reload the owner; confirm what is in effect.
    result = await reload_service(info.owner)
    if not result.get("ok"):
        await undo(f"the {info.owner} reload", "; ".join(
            f"{e.get('file') or ''}{':' + str(e['line']) if e.get('line') else ''}: {e.get('message')}"
            for e in result.get("errors") or []) or "refused", 422, errors=result.get("errors"))
    in_effect = (result.get("digests") or {}).get(rel)
    if in_effect != digest(new_text):
        await undo("the check of the value in effect",
                   f"{info.owner} has {in_effect} in effect for {rel}, not {digest(new_text)}")

    # 8. The other service.
    warnings = []
    other = "tool_service" if info.owner == "orchestrator" else "orchestrator"
    other_result = await reload_service(other)
    if not other_result.get("ok"):
        warnings.append(f"{other} (which does not read {rel}) did not reload cleanly: " + "; ".join(
            str(e.get("message")) for e in other_result.get("errors") or []) + ". The save stands.")

    # 9. Commit.
    msg = message(rel, keys, who, overrides, extra)
    try:
        commit = settings_git.commit_file(base, rel, msg)
    except settings_git.GitError as e:
        await undo("the git commit", str(e))
    settings_git.forget_health()

    # 10. Audit.
    settings_audit.record(event, client=client, file=rel, keys=keys, commit=commit, overrides=overrides or None,
                          warnings=warnings or None)
    logger.info("settings: %s %s by %s: %s (commit %s)", event, rel, client, ", ".join(keys), commit[:7])
    return {"ok": True, "changed": True, "commit": commit, "keys": keys, "blob": settings_git.blob_sha(data),
            "warnings": warnings, "corpus": corpus_result, "overrides": overrides,
            "message": f"saved {rel} ({', '.join(keys)}); in effect in {info.owner}; commit {commit[:7]}"}


async def check(rel: str, text) -> dict:
    """Validation, the keys it changes and the corpus check, writing nothing."""
    settings_store.check_name(rel)
    try:
        data = settings_store.encode(rel, text)
    except settings_store.StoreError as e:
        return {"ok": False, "errors": [{"file": f"config/{rel}", "line": None, "message": str(e)}]}
    new_text = data.decode("utf-8")
    problems = await validate(rel, new_text)
    if problems:
        return {"ok": False, "errors": problems}
    base = config_loader.config_dir()
    old = settings_store.read_bytes(base, rel)
    out = {"ok": True, "errors": [], "changed_keys": changed_keys(
        rel, old.decode("utf-8", "replace") if old is not None else None, new_text)}
    import settings_corpus
    if settings_corpus.applies(rel):
        out["corpus"] = await settings_corpus.check(rel, new_text)
    return out
