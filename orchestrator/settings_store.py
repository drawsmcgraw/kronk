"""The live config directory: what may be written, and the one function that
writes it (sprint M21 step 2; docs/features/config-directory.md).

The /settings editor writes only through write_file(). It refuses any name
that is not exactly one of EDITABLE's keys: there is no path joining,
normalising or decoding of a name from a request, so `../x`, `/etc/passwd`,
`prompts/../tuning.yaml`, `tuning.yaml/` and a NUL byte are all simply "not
a config file". Inside the directory it works on directory file
descriptors opened with O_NOFOLLOW, so a symlink in place of `prompts/` or
of the file itself is refused, and a swap between the check and the write
cannot redirect it.

The write is atomic: a new file in the same directory (O_EXCL, mode 0644),
written, flushed and fsynced, then renamed over the old one and the
directory fsynced. A reader (a reload in either service) sees the whole
old file or the whole new one, never a part. On any failure the temp file
is removed and the old file is untouched.
"""
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

import config_files
import config_loader
import prompt_defaults


@dataclass(frozen=True)
class FileInfo:
    owner: str          # the service that reads it: "orchestrator" | "tool_service"
    kind: str           # "prompt" | "yaml"
    description: str    # one line for the page


def _prompt_description(name: str) -> str:
    if name == "coordinator":
        return "System prompt of the coordinator (answers directly or delegates to a specialist)"
    return f"System prompt of the {name} agent"


def _editable() -> dict[str, FileInfo]:
    files = {f"{config_loader.PROMPTS_DIR}/{n}.md": FileInfo("orchestrator", "prompt", _prompt_description(n))
             for n in prompt_defaults.PROMPTS}
    files[config_loader.TUNING_FILE] = FileInfo(
        "orchestrator", "yaml", "Agent-loop budgets, tool timeouts, weather-context age, the messy-transcript "
        "gate and the model-lock overtakes")
    files[config_loader.LEAD_IN_FILE] = FileInfo(
        "orchestrator", "yaml", 'The words of the spoken lead-in ("ask Kronk to ...") that sends a request '
        "straight to the coordinator")
    files[config_loader.VOICE_FILE] = FileInfo(
        "orchestrator", "yaml", "The speech mount: the spoken-reply line, the stream hold and the voice deadline")
    for name in sorted(config_loader.TOOL_SERVICE_FILES):
        files[name] = FileInfo(
            "tool_service", "yaml", "Music: the verify window, the resolver's thresholds, the sentence parser's "
            "words and the decision order's policy")
    return files


# Every file of the live directory, and nothing else (criterion 10).
EDITABLE: dict[str, FileInfo] = _editable()


class StoreError(Exception):
    """A write that was refused or failed; the message names the cause."""


def check_name(rel) -> FileInfo:
    """The FileInfo of rel, which must be exactly one of EDITABLE's names."""
    if not isinstance(rel, str) or rel not in EDITABLE:
        raise StoreError(f"not a config file: {config_files.quote(rel)}; the files are "
                         f"{', '.join(sorted(EDITABLE))}")
    return EDITABLE[rel]


def max_bytes(rel: str) -> int:
    info = check_name(rel)
    return config_files.MAX_PROMPT_BYTES if info.kind == "prompt" else config_files.MAX_YAML_BYTES


def encode(rel: str, text) -> bytes:
    """The bytes write_file() would write for text: LF line ends, UTF-8,
    within the file's size limit. Raises StoreError."""
    if not isinstance(text, str):
        raise StoreError("the file text must be a string")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError as e:
        raise StoreError(f"{rel}: the text is not valid Unicode ({e.reason} at character {e.start})")
    limit = max_bytes(rel)
    if len(data) > limit:
        raise StoreError(f"{rel}: too large: {len(data)} bytes; the limit is {limit}")
    return data


def _open_dir(base: Path, parts: list[str]) -> int:
    """A descriptor of base/parts[0]/..., every component a real directory."""
    try:
        fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as e:
        raise StoreError(f"the config directory {base} cannot be opened: {e.strerror}")
    try:
        for part in parts:
            try:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except OSError as e:
                raise StoreError(f"{part}/ in the config directory is not a directory "
                                 f"(a symlink is refused): {e.strerror}")
            os.close(fd)
            fd = nfd
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_bytes(base: Path, rel: str) -> bytes | None:
    """The current bytes of rel (None when it does not exist), read the way
    write_file() writes: no symlink in the way. Raises StoreError."""
    check_name(rel)
    *parents, name = rel.split("/")
    dfd = _open_dir(Path(base), parents)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise StoreError(f"{rel} cannot be read (a symlink is refused): {e.strerror}")
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise StoreError(f"{rel} is not a regular file")
            limit = max_bytes(rel)
            chunks, size = [], 0
            while size <= limit:
                chunk = os.read(fd, limit + 1 - size)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size > limit:
                raise StoreError(f"{rel}: too large: more than {limit} bytes")
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def write_file(base: Path, rel: str, data: bytes) -> None:
    """Atomically replace base/rel with data (from encode()). Raises
    StoreError; on any failure the old file is as it was."""
    check_name(rel)
    if not isinstance(data, bytes):
        raise StoreError("write_file takes the encoded bytes (settings_store.encode)")
    if len(data) > max_bytes(rel):
        raise StoreError(f"{rel}: too large: {len(data)} bytes; the limit is {max_bytes(rel)}")
    *parents, name = rel.split("/")
    dfd = _open_dir(Path(base), parents)
    tmp = f".{name}.tmp-{secrets.token_hex(6)}"
    try:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode):
                raise StoreError(f"{rel} is not a regular file (a symlink is refused); not replaced")
        except FileNotFoundError:
            pass
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o644, dir_fd=dfd)
        except OSError as e:
            raise StoreError(f"{rel}: cannot create a file in the config directory: {e.strerror}")
        try:
            os.fchmod(fd, 0o644)
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        except OSError as e:
            os.close(fd)
            raise StoreError(f"{rel}: write failed: {e.strerror}")
        os.close(fd)
        try:
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except OSError as e:
            raise StoreError(f"{rel}: could not put the new file in place: {e.strerror}")
        tmp = None
        try:
            os.fsync(dfd)
        except OSError:
            pass            # the rename is done; a directory fsync is best effort on some filesystems
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
        os.close(dfd)
