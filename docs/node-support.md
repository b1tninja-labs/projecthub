# Node support

There is no `projecthub shells node new`, and no `projecthub shells verify --node`. This document
is the profile. A Node repository is created by hand and then owned by that repository. `asn1wire`
is the first one. It is listed in `HAND_OWNED` in `src/projecthub/db/parser.py`. Nothing here is
wired into the generator's manifest.

The commands, run in the package directory:

    npm ci
    npm test
    npm run typecheck

`npm test` runs `node --test` on that package's test file. `npm run typecheck` runs `tsc --noEmit`.

## Choices

Checked 2026-11-17 against Node.js v26.13.1 documentation and the npm CLI 11 package.json page.
The machine that applied this profile was Node v24.22.1 and npm 11.12.1. `node --test` passed
there without `--experimental-strip-types`.

- Node floor `>=24.12.0`. The release schedule
  (<https://github.com/nodejs/release/blob/main/README.md>) has 26.x Active LTS (Lithium, since
  2026-10-28) and 24.x Maintenance LTS (Krypton). 27.x is Alpha until Current starts 2027-04-22.
  There is no Current line on this date. Type stripping is Stability 2 as of v25.2.0 and v24.12.0
  (<https://nodejs.org/api/typescript.html>). 22.x is still Maintenance LTS, and stripping is
  enabled there by default since 22.18.0, but the stability row does not list 22, so 22 is below
  the floor.
- TypeScript `^5.9.3` and `@types/node` `^24.10.1`. Node's type-stripping page recommends
  TypeScript 5.8 or newer with `module: nodenext`, `verbatimModuleSyntax`, `erasableSyntaxOnly`,
  and `rewriteRelativeImportExtensions`. The npm `typescript` package's stable version on this
  date is 7.0.2. TypeScript 7.0 is a native port that ships no API and takes 6.0's defaults,
  including `module: esnext`
  (<https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/>). This profile stays on
  5.9 so `module` stays `nodenext`. `@types/node` stays on 24 so the floor is what gets
  typechecked.
- `node:test`, stable since v20.0.0 (<https://nodejs.org/api/test.html>). No Vitest, no Jest.
- No linter and no formatter. `tsc --noEmit` is the language-owned gate. Biome, Oxlint, and
  typescript-eslint each claim a different job, and no TypeScript or Node page fetched on this
  date names one of them the default.
- Package manager is npm. Lockfile is `package-lock.json` at `lockfileVersion` 3
  (<https://docs.npmjs.com/cli/v11/configuring-npm/package-lock-json>). `.npmrc` sets
  `engine-strict=true` and `ignore-scripts=true`.
- `"type": "module"`, `"private": true`, `"sideEffects": false`, `noEmit`, explicit `.ts` import
  extensions. Exports point at the TypeScript source while the package is private. A publish, when
  one happens, emits `.js` and `.d.ts` without committing them, and puts `"types"` first in the
  conditional export (<https://nodejs.org/api/packages.html>). Node refuses to run `.ts` files
  under `node_modules`.

## Package-name rules

The registry is npm, not PyPI. From the package.json docs
(<https://docs.npmjs.com/cli/v11/configuring-npm/package-json/>, fetched 2026-11-17): the name is
at most 214 characters including a scope; a new package has no uppercase letters; the name is part
of a URL, so it cannot contain characters that are not URL-safe; a scoped name may start with `.`
or `_` and an unscoped name may not. The same page says not to use a core module's name and not to
put `js` or `node` in the name. <https://docs.npmjs.com/package-name-guidelines/> adds: unique,
descriptive, no uppercase, and an unscoped name should not be spelled like another package or
confuse authorship.

`asn1wire` matches those rules. <https://www.npmjs.com/package/asn1wire> answered 404 on
2026-11-17. That is one page, not a canary: no second request was sent, and a 404 was not compared
with a known package. A missing page is not a reservation. `"private": true` stays until a publish.

## Gates

| Python gate | Node gate |
| --- | --- |
| compile | not applicable: `noEmit` is set, so there is no JS build; printed as `compile=n/a` |
| pytest | `npm test` (`node --test` on the package's `*.test.ts` file) |
| ruff | not adopted; printed as `ruff=n/a` |
| format | not adopted; printed as `format=n/a` |
| mypy | `npm run typecheck` (`tsc --noEmit`) |

There is no verifier command yet. A missing `package.json` is a reason to stop, not a green run.
These gates are what a person runs. They are not wired into `projecthub shells verify`.

## How Node ROADMAPs are read

`asn1wire` is in `HAND_OWNED`. `projecthub sync` reads it with the same grammar as the other
hand-owned projects. The grammar is the one in `src/projecthub/db/parser.py`. This profile does not
add a grammar.

A matrix is a markdown table whose header's first column is `Obligation` or `Id` and which has a
`Status` column. Other columns are found by header name, never by position. `asn1wire/ROADMAP.md`
is that table:

    | Obligation | Spec | Status | Where | Milestone |

The id is the code span in the first cell (`asn1wire.der`, `asn1wire.certificate`). Status `[ok]`
is done. `[ ]` is open. `[part]`, `[no]`, `[x]`, `[open]`, and `[unread]` are the same tokens the
parser already accepts. The Spec cell is the document the row rests on. The Where cell is where the
behaviour lives and, in this file, also carries the `Test:` citation. The Milestone cell is `-`.
Rows live in the one `ROADMAP.md`. There are no fragments.

The evidence check does not see a TypeScript test. A citation of `test/cert.test.ts` is not
confirmed by the Python test finder. Sync still reads the row. It does not check that the Node
test exists, and an issue should say so. Do not add a Test column, do not rename Where to
Evidence, and do not rewrite the ids so a checker will pass. A row is done when the named test
would fail if the behaviour regressed. That test is named in the Where cell, and a person runs
`npm test`.
