"""What one chat request has done so far, and how it ended (sprint M9 step 4).

One `Run` per request, in a ContextVar. client_watch sets it before the
app runs (so the app's task inherits it); a pipeline started without it
(tests, an in-process caller) gets one from `ensure()`.

- `tools_sent`: tools.execute appends a tool's name before it calls
  tool_service. The deadline sentence reads it: once a tool that changes
  something has been sent, the reply never says the action did not happen.
- `client_gone`: set by client_watch when the client disconnects.
- `deadline_at`: the voice deadline (event-loop time), set by the deadline
  wrapper; `ended` says why a run was stopped early.
"""
from contextvars import ContextVar
from dataclasses import dataclass, field

DEADLINE = "deadline"
CLIENT_GONE = "client_gone"
CANCELLED = "cancelled"


@dataclass
class Run:
    tools_sent: list[str] = field(default_factory=list)
    client_gone: bool = False
    deadline_at: float | None = None
    ended: str | None = None


current: ContextVar[Run | None] = ContextVar("kronk_run", default=None)


def ensure() -> Run:
    run = current.get()
    if run is None:
        run = Run()
        current.set(run)
    return run


def note_tool_sent(name: str) -> None:
    run = current.get()
    if run is not None:
        run.tools_sent.append(name)
