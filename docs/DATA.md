# Site data, schema 3

`projecthub site --out data` writes the data the status site at labs.b1tninja.com renders. This file is the
contract between the two: projecthub keeps it in `docs/DATA.md`, and the site repository
(`b1tninja-labs/portfolio`) carries a copy as `DATA.md`. Change both together.

## Source

One paginated GraphQL read of the organization Project (`b1tninja-labs`, number 1): every item with its
field values (Status, Level, Agent, Claimed, Last activity) and its issue's title, url, state, updatedAt and
labels, 100 items a page. Nothing else is read: no product repository is checked out, no ROADMAP is parsed,
and no request is made per repository. The same request reports whether the Project is public and what it
cost (`rateLimit { cost remaining }`).

Each build refuses to run past a point budget (`--budget`, default 100). The first page gives the item count
and its own cost, so the whole run is estimated before the second request; the running total is checked again
after every page. A refused build writes nothing, and the last good site stays up.

Measured on 2026-11-12 against Project 1 with 503 items: 6 requests, 2 points each, 12 points a build.

## How cards are read

- A **project card** has Level `Project`, or, while Level is unset, a title `name: what` (the shape
  `github/board.py` gives it) that is not `Wave N:` or `Decision N:`.
- A **row** has Level `Obligation`, or, while Level is unset, a title `[project] row(): summary`. Milestone
  cards (`[project] M1 - ...`), waves and decisions are neither.
- A row labelled `type:non-goal` is a decision, not work: it is left out of the groups, the counts and the
  progress figure, and counted in `nongoals`.
- A project with rows but no card still appears, in `others`, with `status: null`.

## `index.json`

The only file. Schema 2's `p/<name>.json` files are gone; `write` removes any left in the output directory.

```json
{
  "schema_version": 3,
  "generated_at": "2026-11-12T10:15:00+00:00",
  "board_url": "https://github.com/b1tninja-labs/portfolio/issues",
  "issues_url": "https://github.com/b1tninja-labs/portfolio/issues",
  "active_limit": 6,
  "build": {"items": 503, "requests": 6, "cost": 12, "budget": 100, "remaining": 4743},
  "active": [
    {
      "name": "asn1core",
      "what": "DER and X.509 codec that keeps the bytes it parsed",
      "status": "In progress",
      "url": "https://github.com/b1tninja-labs/portfolio/issues/12",
      "issues_url": "https://github.com/b1tninja-labs/portfolio/issues?q=...",
      "last_activity": "2026-11-12T08:00:00Z",
      "counts": {"Backlog": 4, "In progress": 1, "Done": 18},
      "nongoals": 1,
      "progress": {"done": 18, "total": 23},
      "groups": [
        {"status": "In progress", "rows": [
          {"id": "der.decode", "title": "X.690 sec. 8", "url": "https://github.com/...",
           "holder": "agent-7", "since": "2026-11-11"}
        ]}
      ],
      "claims": [{"row": "der.decode", "holder": "agent-7", "since": "2026-11-11"}],
      "activity": [{"kind": "note:progress", "at": "2026-11-12T08:00:00Z"}]
    }
  ],
  "others": [
    {"name": "ctspy", "status": "Backlog", "issues_url": "https://github.com/...",
     "counts": {"Backlog": 10}, "progress": {"done": 0, "total": 10}}
  ]
}
```

| Field | Meaning |
|---|---|
| `board_url` | The Project while it is public; the planning repository's issues while it is private. |
| `build` | What this build read and spent: item count, requests, GraphQL points, the budget, points left. |
| `active` | Project cards whose Status is `In progress`, most recent `last_activity` first (none last, then by name), at most `active_limit`. An In progress project past the cap is listed in `others`. |
| `last_activity` | The newest event time among the card and its rows: a `Last activity` time, or a `Claimed` date. |
| `counts` | Rows per Status, in the Project's option order (Triage, Backlog, Ready, In progress, In review, Blocked, Done); a row with no Status counts as `No status`. |
| `progress` | `done` rows (Status `Done`) over every row that is not a non-goal. |
| `groups` | The same rows, grouped by Status in that order. `holder` and `since` are set only while the row is In progress, In review or Blocked. |
| `claims` | Every held row: who (the Agent field, reduced to `A-Z a-z 0-9 . _ @ -`) and since when (Claimed). |
| `activity` | The last 10 events as kind and time, newest first. |
| `others` | Every other project: name, Status, counts and progress, no rows. Sorted by Status, then name. |

## Rules the publisher enforces

`problems()` refuses a file that breaks any of these, before anything is written:

- The keys of the index, of each active and other entry and of each row are exactly the ones above.
- Activity carries `kind` and `at` only, and only a `Last activity` value of the form `<kind> <ISO time>`
  becomes an event. Note text never reaches the site.
- While the Project is private, no URL in the file points at it.
- Every URL is a plain `https://` URL; every name matches `^[A-Za-z0-9._-]+$`; names are unique.
