"""The rebuild signal: one `repository_dispatch` to the portfolio repository when Project 1 changed.

The public site is rebuilt from Project 1 alone, by a workflow in `config.PORTFOLIO_REPO` that listens for
`repository_dispatch` with event type `projecthub-state-changed`. The drain calls `signal_rebuild` after a run;
this module decides whether a dispatch goes out.

Debounced: at most one dispatch every `DEBOUNCE_S` (ten minutes). A change inside the window is not lost: it
leaves the signal *owed*, and the next call after the window sends it, even from a run that wrote nothing.
The time of the last dispatch and the owed flag live in a table of the outbox database, so the debounce holds
across processes and restarts.

The payload names what changed in counts only (writes, and the drain run's time). No row text, holder or note
goes into it: the receiving workflow reads Project 1 itself.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from projecthub import config

EVENT_TYPE = "projecthub-state-changed"
DEBOUNCE_S = 600.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox_dispatch (
    event TEXT PRIMARY KEY,
    last_at REAL,
    owed INTEGER NOT NULL DEFAULT 0
);
"""


class Dispatcher(Protocol):
    def dispatch(
        self, repo: str, event_type: str, client_payload: dict[str, object]
    ) -> None: ...


@dataclass(frozen=True)
class Signal:
    sent: bool
    why: str


def _state(db: sqlite3.Connection, event: str) -> tuple[float | None, bool]:
    db.executescript(SCHEMA)
    row = db.execute(
        "SELECT last_at, owed FROM outbox_dispatch WHERE event=?", (event,)
    ).fetchone()
    if row is None:
        return None, False
    return (None if row[0] is None else float(row[0])), bool(row[1])


def _save(
    db: sqlite3.Connection, event: str, last_at: float | None, owed: bool
) -> None:
    db.execute(
        "INSERT INTO outbox_dispatch (event, last_at, owed) VALUES (?, ?, ?) "
        "ON CONFLICT (event) DO UPDATE SET last_at=excluded.last_at, owed=excluded.owed",
        (event, last_at, int(owed)),
    )


def signal_rebuild(
    client: Dispatcher | None,
    db: sqlite3.Connection,
    *,
    changed: int,
    repo: str = config.PORTFOLIO_REPO,
    event_type: str = EVENT_TYPE,
    debounce_s: float = DEBOUNCE_S,
    clock: Callable[[], float] = time.time,
) -> Signal:
    """Send one dispatch when `changed` > 0 or one is owed, unless one went out less than `debounce_s` ago.

    A failed dispatch (no client, no permission, no network) keeps the signal owed and is reported, never
    raised: the board writes it follows already went through, and the next run tries again.
    """
    now = clock()
    last_at, owed = _state(db, event_type)
    wanted = changed > 0 or owed
    if not wanted:
        return Signal(False, "nothing changed")
    if last_at is not None and now - last_at < debounce_s:
        _save(db, event_type, last_at, True)
        wait = int(debounce_s - (now - last_at))
        return Signal(
            False,
            f"debounced: the last dispatch was {int(now - last_at)}s ago; owed, due in {wait}s",
        )
    if client is None or not hasattr(client, "dispatch"):
        _save(db, event_type, last_at, True)
        return Signal(False, "no client can send repository_dispatch; owed")
    try:
        client.dispatch(
            repo,
            event_type,
            {
                "writes": changed,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            },
        )
    except Exception as error:  # noqa: BLE001 - reported, and owed until the next run
        _save(db, event_type, last_at, True)
        return Signal(False, f"dispatch to {repo} failed: {error}; owed")
    _save(db, event_type, now, False)
    return Signal(True, f"sent {event_type} to {repo}")
