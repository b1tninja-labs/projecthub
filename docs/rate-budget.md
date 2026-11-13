# The rate budget

Project 1 is the authority on work state, so every pickup, claim and drain spends GitHub API budget. This page
records the limits as GitHub documents them, what each projecthub operation costs, and what projecthub does
when the budget runs low. The numbers were checked on 2026-11-12.

## GitHub's limits

From [Rate limits and query limits for the GraphQL API](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api):

| Limit | Value |
|---|---|
| Primary, user token (a personal token or `gh auth`) | 5,000 points an hour |
| Primary, `GITHUB_TOKEN` in Actions | 1,000 points an hour per repository |
| Point cost of a query | the requests its connections need, each at its `first`/`last`, divided by 100 and rounded; at least 1 |
| `first` / `last` | 1 to 100; at most 500,000 nodes in one call |
| Secondary, GraphQL points | 2,000 a minute for the GraphQL endpoint; a request without mutations is 1 point, with mutations 5 |
| Secondary, content-generating requests | in general at most 80 a minute and 500 an hour |
| Secondary, concurrency | 100 concurrent requests, shared with REST |
| Secondary, CPU | 90 s of CPU per 60 s of real time, at most 60 s of it for GraphQL |
| Headers | `x-ratelimit-limit`, `-remaining`, `-used`, `-reset` (UTC epoch seconds), `-resource` (`graphql`) |

From [Rate limits for the REST API](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api):

| Limit | Value |
|---|---|
| Primary, authenticated user | 5,000 requests an hour |
| Secondary, points | 900 a minute for REST; most GET, HEAD and OPTIONS are 1 point, most POST, PATCH, PUT and DELETE are 5 |
| Secondary, content-generating requests | at most 80 a minute and 500 an hour |
| `GET /rate_limit` | does not count against the primary limit, but can count against the secondary one |

## Measured against Project 1

Read-only queries on 2026-11-12, with `rateLimit { cost remaining }` in the reply:

| Query | Reported cost | Effect on `remaining` |
|---|---|---|
| one page of `items(first: 50)` with `fieldValues(first: 30)` and content | 1 | -1 |
| `node(id:)` for one item, the claim's fresh read | 1 | -1 |
| `rateLimit` alone | 1 | none (4,732 before and after) |

Project 1 had 503 items, so a full read is 11 pages: **11 points**. The field list (`fields(first: 100)`) is one
page, 1 point.

Not verified: what a mutation costs against the primary limit. The GraphQL page says the secondary point
values "are separate from the point value calculations for the primary rate limit" and does not give a
primary cost for mutations, and `rateLimit` cannot be selected in a mutation. projecthub charges each
mutation 1 point against the budget it keeps until the next query reports the real figure.

## What each operation costs in this design

| Operation | GraphQL requests (primary points) | Secondary | Content-generating |
|---|---|---|---|
| `pickup` | 1 full read of the items (11) + the claim below | 1 point a query, 5 for the mutation | 1 |
| claim, cache warm | field list (1) + fresh item read (1) + one mutation of 5 field values (1, unverified) + re-read (1): 3 + 1 | 3 + 5 points | 1 request; the pacer counts it as 5 field writes |
| claim, cache cold | the above + a full read (11) | | |
| heartbeat, `note` | none at the call; a cache older than 60 s is refreshed for the lost-claim check (11), else 0 | | 0 at the call |
| the heartbeat's write | rides the next drain batch | | |
| drain batch | field list (1) + a full read (11) + one mutation per 20 field writes | 1 a query, 5 a mutation | 1 per mutation, counted per field |
| drain proposal | repository id (1) + `createIssue` + `addProjectV2ItemById` + a Status write | | 3 |
| rebuild signal | REST `POST /repos/{repo}/dispatches`, at most once every 10 minutes | 5 REST points | 1 |
| `projecthub budget` | `rateLimit` alone (free when measured) + `GET /rate_limit` (free on the primary) | 1 + 1 | 0 |

The client also reads the token's scopes once a process with `GET /user` (1 REST request), and takes the REST
`x-ratelimit-*` headers from that reply.

So an hour in which ten agents each pick up once and heartbeat twice, and the drain runs every five minutes
with one batch, costs about 10 × (11 + 4) + 20 × 11 + 12 × (12 + 1) ≈ 526 points: about a tenth of the hour.

## What projecthub does with it

Every GraphQL query the client sends gets `rateLimit { limit cost remaining resetAt }` added before its last
brace (`ratebudget.with_rate_limit`), and every reply reports into one budget per process (`ratebudget.BUDGET`).
REST headers are read where the client sees them (`gh api -i user`, `GET /rate_limit`).

| Floor | Default | Below it |
|---|---|---|
| read floor (`PROJECTHUB_READ_FLOOR`) | 50 points | the board cache is served even when stale; with no cache the read is refused |
| write floor (`PROJECTHUB_WRITE_FLOOR`) | 250 points | a claim, a drain batch and the rebuild signal stop with `rate budget low until <resetAt>` |

The write floor is the higher one, so a process that has stopped writing can still read. A budget whose reset
time has passed is treated as unknown rather than low; the next query reports it again.

The drain's own pacer (`outbox.Budget`) stays under the secondary content limits independently: 75 field writes
a minute, 450 an hour and 2,000 a day, counted in the outbox database across runs. Claims count their writes
there too.
