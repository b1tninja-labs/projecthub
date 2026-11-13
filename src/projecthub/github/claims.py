"""Claims decided on GitHub Project 1: who works which item, until when, the way a team uses a Jira board.

The Project is the authority. A claim is four field values on the item, written in one request:

    Agent          "<holder> [<first 8 of the lease id>]"   (the claim token)
    Claimed        the UTC date of the claim                  (DATE, already on the Project)
    Status         In progress
    Claim expires  "YYYY-MM-DDTHH:MM:SSZ", UTC                (TEXT; see below)

plus Last activity. The local SQLite store keeps a mirror lease (the opaque lease id is still the capability
the other tools take) and the outbox for every later write, but it decides nothing.

Why TEXT and not DATE for Claim expires. A lease lives 30 minutes by default and at most four hours; a DATE
holds a day. A crashed agent's claim would then hold its row until midnight UTC, up to 24 hours, instead of
until its TTL ran out. ISO-8601 in UTC with a trailing Z sorts as text and parses with `fromisoformat`.

Why the token carries part of the lease id. Every agent session acts as the same GitHub account, and two of
them may well both call themselves "agent". A claim is won when the re-read shows *this* claim's token, and
that test must tell two sessions with one name apart.

The protocol (GitHub Projects has no compare-and-swap, so it is read, write, settle, re-read):

1. Refuse at once when there is no client (offline) or the rate budget is under its write floor.
2. Find the item in the cached board (one paginated read a minute at most); refuse HOLDER_BUSY when the
   caller's name already holds `max_live` unexpired claims on the Project.
3. Read the item fresh (one point). It is claimable when its Status is Backlog, Ready or In progress and it
   carries no live claim: no Agent, or a Claim expires that is missing or in the past. In progress is in that
   list on purpose: it is what a lapsed claim leaves behind, and taking over an expired claim is the point of
   an expiry. A claim never written by this protocol has no Claim expires, so it reads as lapsed.
4. Write the claim, at most `MAX_GAP_S` after that read (else read again first).
5. Wait `settle_s`, then read the item again. If Agent is not this claim's token, a later writer won: LEASE_HELD.
   Last writer wins, and every claimant sees the same last writer, as long as each one's read-to-write gap
   is shorter than the settle time. A write that itself took longer than the settle time cannot be sure it
   did not overwrite a claim that was already confirmed, so it withdraws (CLAIM_UNCERTAIN) instead of
   proceeding.
6. Only then record the mirror lease locally, patch the cached item, and tell the drain which Status it wrote,
   so a later release is not mistaken for a person's move.

What remains of the race is closed at the next heartbeat: `heartbeat` reads the cached item, and a claim
whose item names another live holder ends with LEASE_LOST. The drain refuses to write any queued claim field
onto an item whose Agent names a different claim.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol

from projecthub import config
from projecthub.github.boardcache import BoardCache
from projecthub.github.client import (
    Change,
    GhProjectsError,
    Item,
    MissingScope,
    NotFound,
    Outcome,
    Project,
    RateLimited,
)
from projecthub.github.ratebudget import BUDGET, BudgetLow, RateBudget
from projecthub.mcp.work import MAX_TTL, LeaseError, claim_token, row_key

CLAIM_EXPIRES = "Claim expires"
CLAIM_EXPIRES_TYPE = "TEXT"
CLAIMABLE = ("Backlog", "Ready", "In progress")
IN_PROGRESS = "In progress"
SETTLE_S = 5.0
MAX_GAP_S = 2.0
FRESH_READS = 3


class ClaimClient(Protocol):
    """The slice of `client.Projects` the claim protocol uses."""

    def project(self, owner: str, number: int) -> Project: ...

    def items(self, owner: str, number: int) -> list[Item]: ...

    def item(self, item_id: str) -> Item: ...

    def set_values(
        self, project: Project, changes: Sequence[Change]
    ) -> list[Outcome]: ...


class FieldMaker(Protocol):
    def project(self, owner: str, number: int) -> Project: ...

    def create_field(
        self,
        project_id: str,
        name: str,
        data_type: str,
        options: Sequence[dict[str, str]] = (),
        iteration: dict[str, object] | None = None,
    ) -> str: ...


# -- reading a claim off an item ----------------------------------------------------------------------


def iso(stamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


def parse_iso(text: object) -> float | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def agent_name(agent: object) -> str:
    """The holder's name inside a token: "agent-7 [3f2a91c0]" -> "agent-7". A bare name is returned as is."""
    text = str(agent or "").strip()
    head, sep, tail = text.rpartition(" [")
    return head if sep and tail.endswith("]") else text


def expires_of(item: Item) -> float | None:
    return parse_iso(item.values.get(CLAIM_EXPIRES))


def live_claim(item: Item, now: float) -> bool:
    """An Agent with a Claim expires still in the future. Anything less is no claim at all."""
    if not str(item.values.get("Agent") or "").strip():
        return False
    expires = expires_of(item)
    return expires is not None and expires > now


def claimable(item: Item, now: float) -> bool:
    return str(item.values.get("Status") or "") in CLAIMABLE and not live_claim(
        item, now
    )


def held_keys(
    cards: Sequence[Item], now: float, key: Callable[[Item], str]
) -> set[str]:
    """`key(card)` for every card under a live claim: what pickup must pass over."""
    return {key(card) for card in cards if live_claim(card, now)}


def ensure_claim_field(
    client: FieldMaker,
    owner: str = config.PROJECT_OWNER,
    number: int = config.PROJECT_NUMBER,
) -> tuple[Project, bool]:
    """Create the Claim expires field when the Project lacks it. Returns (project, created).

    A person runs this once, through `projecthub leases ensure-claim-field`; nothing calls it on its own.
    A field of that name with another type is refused rather than used, because every reader parses it.
    """
    project = client.project(owner, number)
    found = project.fields.get(CLAIM_EXPIRES)
    if found is not None:
        if found.data_type != CLAIM_EXPIRES_TYPE:
            raise GhProjectsError(
                f"{CLAIM_EXPIRES!r} exists as {found.data_type}; the claim protocol needs {CLAIM_EXPIRES_TYPE}"
            )
        return project, False
    client.create_field(project.id, CLAIM_EXPIRES, CLAIM_EXPIRES_TYPE)
    return client.project(owner, number), True


# -- the claimant -------------------------------------------------------------------------------------


class ProjectClaims:
    """Claims against the Project, with the local store as mirror and outbox. Takes the place of `Store` for
    every tool that starts or keeps a claim; the tools that only log go to the store directly."""

    def __init__(
        self,
        store: Any,
        client: ClaimClient | None,
        *,
        owner: str = config.PROJECT_OWNER,
        number: int = config.PROJECT_NUMBER,
        cache: BoardCache | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        settle_s: float | None = None,
        budget: RateBudget | None = None,
        offline_reason: str = "",
    ) -> None:
        self.store = store
        self.client = client
        self.owner = owner
        self.number = number
        self.clock = clock
        self.sleep = sleep
        self.settle_s = SETTLE_S if settle_s is None else settle_s
        self.budget = budget or BUDGET
        self.offline_reason = offline_reason
        self.cache = cache or BoardCache(
            getattr(store, "path", ":memory:"), clock=clock, budget=self.budget
        )
        self._project: Project | None = None

    # -- helpers -----------------------------------------------------------------------------------

    def _error(self, code: str, reason: str) -> Exception:
        return LeaseError(code, reason)

    def _offline(self, why: object) -> Exception:
        return self._error(
            "OFFLINE",
            f"claims are decided on {config.BOARD_URL} and it cannot be reached ({why}); "
            "no claim was made. Retry when the network and `gh auth status` are back",
        )

    def _guard(self, call: Callable[[], Any]) -> Any:
        """Map client failures onto the stable refusal codes the tools branch on."""
        try:
            return call()
        except BudgetLow as error:
            raise self._error("RATE_BUDGET_LOW", str(error)) from error
        except RateLimited as error:
            raise self._error("RATE_BUDGET_LOW", str(error)) from error
        except MissingScope as error:
            raise self._error("MISSING_SCOPE", str(error)) from error
        except NotFound as error:
            raise self._error("NO_ITEM", str(error)) from error
        except GhProjectsError as error:
            raise self._offline(error) from error

    def project(self) -> Project:
        if self._project is None:
            assert self.client is not None
            client = self.client
            self._project = self._guard(lambda: client.project(self.owner, self.number))
        assert self._project is not None
        return self._project

    def _pacing(self) -> Any:
        from projecthub.github import outbox

        return outbox.Budget(
            outbox.DrainState(self._state_path()), clock=self.clock, sleep=self.sleep
        )

    def _state_path(self) -> str:
        return str(getattr(self.store, "path", ":memory:"))

    def _record_status(self, item_id: str, value: str) -> None:
        from projecthub.github import outbox

        outbox.DrainState(self._state_path()).record(
            item_id, "Status", value, self.clock()
        )

    def find(self, project: str, row_id: str) -> Item:
        from projecthub.github.outbox import find_item

        assert self.client is not None
        client = self.client
        items = self._guard(lambda: self.cache.items(client, self.owner, self.number))
        found = find_item(items, project, row_id)
        if not found:
            raise self._error(
                "NO_ITEM", f"no item titled [{project}] {row_id} on {config.BOARD_URL}"
            )
        if len(found) > 1:
            raise self._error(
                "AMBIGUOUS", f"{len(found)} items are titled [{project}] {row_id}"
            )
        return found[0]

    def mine(self, holder: str, now: float) -> list[Item]:
        """Items whose live claim names `holder`, from the cached board. A claim this machine already ended
        (released, parked, blocked: the write is still in the outbox) does not count against the holder."""
        assert self.client is not None
        client = self.client
        items = self._guard(lambda: self.cache.items(client, self.owner, self.number))
        ended = getattr(self.store, "ended_here", None)
        return [
            i
            for i in items
            if live_claim(i, now)
            and agent_name(i.values.get("Agent")) == holder
            and not (ended is not None and ended(str(i.values.get("Agent"))))
        ]

    # -- claim -------------------------------------------------------------------------------------

    def acquire(
        self,
        project: str,
        row_id: str,
        holder: str = "agent",
        scope_files: list[str] | None = None,
        ttl: float = 30 * 60,
        max_live: int = 1,
        branch: str = "",
    ) -> Any:
        if self.client is None:
            raise self._offline(self.offline_reason or "no GitHub client")
        try:
            self.budget.require_write("claim")
        except BudgetLow as error:
            raise self._error("RATE_BUDGET_LOW", str(error)) from error
        client = self.client
        span = max(60.0, min(float(ttl), MAX_TTL))
        board = self.project()
        if CLAIM_EXPIRES not in board.fields:
            raise self._error(
                "NO_CLAIM_FIELD",
                f"{config.BOARD_URL} has no {CLAIM_EXPIRES!r} field; a person creates it once with "
                "`projecthub leases ensure-claim-field`",
            )
        target = self.find(project, row_id)
        now = self.clock()
        busy = [i for i in self.mine(holder, now) if i.id != target.id]
        if len(busy) >= max(1, max_live):
            raise self._error(
                "HOLDER_BUSY",
                f"{holder} already holds {', '.join(i.title for i in busy)}; release, park or block it first",
            )

        fresh = target
        read_at = now
        for _ in range(FRESH_READS):
            read_at = self.clock()
            fresh = self._guard(lambda: client.item(target.id))
            if not claimable(fresh, self.clock()):
                self.cache.patch(self.owner, self.number, fresh)
                raise self._refusal(fresh)
            if self.clock() - read_at <= MAX_GAP_S:
                break
        else:
            raise self._offline(
                "every fresh read took longer than the claim protocol allows"
            )

        lease_id = str(uuid.uuid4())
        token = claim_token(holder, lease_id)
        at = self.clock()
        values: dict[str, object] = {
            "Agent": token,
            "Claimed": time.strftime("%Y-%m-%d", time.gmtime(at)),
            "Status": IN_PROGRESS,
            CLAIM_EXPIRES: iso(at + span),
        }
        if "Last activity" in board.fields:
            values["Last activity"] = f"acquire {iso(at)}"
        changes = [Change(fresh.id, name, value) for name, value in values.items()]
        try:
            self.budget.require_write("claim")
        except BudgetLow as error:
            raise self._error("RATE_BUDGET_LOW", str(error)) from error
        self._pacing().take(len(changes))
        outcomes = self._guard(lambda: client.set_values(board, changes))
        written_at = self.clock()
        self._record_status(fresh.id, IN_PROGRESS)
        failed = [o for o in outcomes if not o.ok]

        self.sleep(self.settle_s)
        after = self._guard(lambda: client.item(fresh.id))
        self.cache.patch(self.owner, self.number, after)
        if str(after.values.get("Agent") or "") != token:
            self.store.record(
                lease_id,
                row_key(project, row_id),
                "claim_lost",
                str(after.values.get("Agent")),
            )
            raise self._error(
                "LEASE_HELD",
                f"[{project}] {row_id}: lost the claim to {after.values.get('Agent')!r}, who wrote last",
            )
        if failed or written_at - read_at > self.settle_s:
            why = (
                f"{failed[0].change.field_name}: {failed[0].error}"
                if failed
                else f"the write landed {written_at - read_at:.1f}s after the read, past the {self.settle_s}s settle time"
            )
            self._withdraw(board, after)
            raise self._error(
                "CLAIM_UNCERTAIN",
                f"[{project}] {row_id}: {why}; the claim was withdrawn",
            )
        lease = self.store.acquire(
            project,
            row_id,
            holder,
            scope_files,
            span,
            max_live=max_live,
            branch=branch,
            lease_id=lease_id,
            mirror=True,
            acquired_at=at,
        )
        return lease

    def _refusal(self, item: Item) -> Exception:
        now = self.clock()
        if live_claim(item, now):
            return self._error(
                "LEASE_HELD",
                f"{item.title} is claimed by {item.values.get('Agent')} until {item.values.get(CLAIM_EXPIRES)}",
            )
        return self._error(
            "NOT_CLAIMABLE",
            f"{item.title} is {item.values.get('Status') or 'without a Status'}; only "
            f"{', '.join(CLAIMABLE)} items are claimed",
        )

    def _withdraw(self, board: Project, item: Item) -> None:
        """Clear a claim this process wrote but cannot stand behind: Agent and Claim expires, leaving Status
        In progress, which reads as claimable. A claimant this write may have overwritten puts its own token
        back at its next heartbeat. Best effort: the TTL is the backstop."""
        assert self.client is not None
        client = self.client
        changes = [Change(item.id, "Agent", None), Change(item.id, CLAIM_EXPIRES, None)]
        try:
            self._pacing().take(len(changes))
            client.set_values(board, changes)
            values = {
                k: v
                for k, v in item.values.items()
                if k not in ("Agent", CLAIM_EXPIRES)
            }
            self.cache.patch(self.owner, self.number, replace(item, values=values))
        except GhProjectsError:
            pass

    # -- keeping and ending a claim: the outbox carries these ------------------------------------------

    def heartbeat(self, lease_id: str) -> float:
        """Extend the mirror lease and queue the new Claim expires. When the cached board says another live
        claim holds the item, the claim was lost: the local lease ends and LEASE_LOST is raised."""
        lost = self._lost_to(lease_id)
        if lost:
            self.store.lose(lease_id, lost)
            raise self._error(
                "LEASE_LOST",
                f"the item is now claimed by {lost}; stop and pick up again",
            )
        return float(self.store.heartbeat(lease_id))

    def _lost_to(self, lease_id: str) -> str:
        """The rival's token when the cached Project shows one, else "". Offline or low budget: ""."""
        if self.client is None:
            return ""
        lease = self.store.lease(lease_id)
        if lease is None:
            return ""
        project, _, row = lease.row_key.partition(":")
        try:
            item = self.find(project, row)
        except Exception:  # noqa: BLE001 - a heartbeat must work offline; the mirror decides then
            return ""
        token = claim_token(lease.holder, lease.lease_id)
        agent = str(item.values.get("Agent") or "")
        if agent and agent != token and live_claim(item, self.clock()):
            return agent
        return ""

    def release(
        self, lease_id: str, outcome: str, note: str = "", pr_url: str = ""
    ) -> str:
        return str(self.store.release(lease_id, outcome, note, pr_url))

    def park(self, lease_id: str, note: str) -> str:
        return str(self.store.park(lease_id, note))

    def block(self, lease_id: str, kind: str, detail: str) -> dict[str, object]:
        return dict(self.store.block(lease_id, kind, detail))
