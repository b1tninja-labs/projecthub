"""Generator data for the routing shells: RPKI, RTR, ASN data, BGP path attributes, BMP, RPSL, BGP feeds, route validation and looking glasses.

Moved verbatim out of the generator (shells/generate.py), which keeps the logic. PROJECTS lists one dict per project in the
order generate.PROJECT_ORDER expects; OPEN_QUESTIONS feeds section 5 of each generated ROADMAP.md.
Editing a value here changes what the generator would write; run `projecthub shells generate --check` to see
which files that touches before anything is written.
"""

from __future__ import annotations

from typing import Any

PROJECTS: list[dict[str, Any]] = [
    {
        "name": "rpkit",
        "pkg": "rpkit",
        "title": "rpkit -- an RPKI relying party you can import",
        "desc": "RPKI relying party: RRDP and repository fetch, CMS signed objects, manifests, "
        "validation paths, ROA extraction and SLURM filtering",
        "kw": [
            "rpki",
            "rrdp",
            "signed-objects",
            "manifests",
            "rov",
            "bgp",
            "rfc9286",
            "rfc9981",
        ],
        "why": (
            "Python has no RPKI relying-party implementation at all. Validation, RRDP and "
            "repository handling live in Rust (Routinator, rcynic) and C (rpki-client), so any "
            "Python tool that wants origin validation today shells out to a daemon and parses "
            "its output. The specification is also moving: RFC 9981 (2026-05) and RFC 9829 "
            "(2025-07) both landed inside the last year and neither has a Python consumer. "
            "rpkit is the importable relying party: fetch, verify, validate, with the "
            "provenance of every assertion attached to the answer instead of assumed by the "
            "caller."
        ),
        "docs": [
            {
                "n": "6480",
                "status": "Informational",
                "date": "2012-02",
                "why": "the architecture: repositories, RAs, ROAs and relying-party duties",
            },
            {
                "n": "8182",
                "status": "Proposed Standard",
                "date": "2017-07",
                "why": "RRDP: notification, snapshot and delta files over HTTPS",
            },
            {
                "n": "9697",
                "status": "Proposed Standard",
                "date": "2024-12",
                "why": "detecting RRDP session desynchronisation",
            },
            {
                "n": "6488",
                "status": "Proposed Standard",
                "date": "2012-02",
                "why": "the SignedObject CMS wrapper every RPKI file wears",
            },
            {
                "n": "9286",
                "status": "Proposed Standard",
                "date": "2022-06",
                "why": "manifests: the anti-replay view of a publication point",
            },
            {
                "n": "9981",
                "status": "Proposed Standard",
                "date": "2026-05",
                "why": "manifest number behaviour at the 2**32 minus 1 boundary",
            },
            {
                "n": "6487",
                "status": "Proposed Standard",
                "date": "2012-02",
                "why": "resource certificate and CRL profile, relying-party path validation",
            },
            {
                "n": "7935",
                "status": "Proposed Standard",
                "date": "2016-08",
                "why": "the algorithms and key sizes a relying party must speak",
            },
            {
                "n": "9691",
                "status": "Proposed Standard",
                "date": "2024-12",
                "why": "trust anchor key (TAK) profile",
            },
            {
                "n": "8416",
                "status": "Proposed Standard",
                "date": "2018-08",
                "why": "SLURM: local filtering of validated assertions",
            },
            {
                "n": "9674",
                "status": "Proposed Standard",
                "date": "2024-12",
                "why": "same-origin policy for RRDP: delta and snapshot files isolated per repository server",
            },
            {
                "n": "9829",
                "status": "Proposed Standard",
                "date": "2025-07",
                "why": "how the RPKI handles CRL Number extensions, updating the RFC 6487 profile",
            },
        ],
        "errata": [
            "RFC 8182 carries 1 verified erratum and is updated by RFC 9674 and RFC 9697. Read all three before writing the RRDP client.",
            "RFC 6487 carries 4 verified errata (algorithm and CRL number details; see also RFC 9829, 2025-07).",
            "RFC 9286 carries 1 verified erratum. RFC 6486 is OBSOLETE and must never be cited as the manifest specification.",
        ],
        "prior": [
            {
                "name": "rov",
                "version": "0.6.0",
                "date": "2025-07-09",
                "note": "offline batch ROV over IRR and dump files; no validator library, no RRDP, no RTR",
            },
            {
                "name": "rpki",
                "version": "-",
                "date": "-",
                "note": "name does not exist on PyPI (checked 2026-11-05)",
            },
            {
                "name": "routinator, rpki-client, rcynic",
                "version": "-",
                "date": "-",
                "note": "Rust and C daemons; no embeddable Python API",
            },
        ],
        "gaps": [
            "RRDP client with notification, snapshot and delta files, and RFC 9697 desync recovery",
            "CMS signed-object decode and verification using the RFC 7935 algorithm profile",
            "manifest enforcement including the RFC 9981 number boundary nobody implements yet",
            "validation path walk with per-assertion reason codes: stale, revoked, suspended, filtered",
            "SLURM filtering as a first-class testable step rather than a daemon option",
        ],
        "nongoals": [
            "issuing certificates: this is a relying party, not a CA",
            "talking to routers over RTR, which is rtrkit",
            "IRR database parsing",
            "being the fastest validator; correctness and provenance are the product",
        ],
        "mods": [
            (
                "anchors",
                "trust anchors and TAKs: load, pin, expire (RFC 9691, RFC 6480)",
            ),
            (
                "repository",
                "publication point model, cache layout, rsync and RRDP selection (RFC 6480)",
            ),
            (
                "rrdp",
                "notification, snapshot and delta fetch plus desync recovery (RFC 8182, RFC 9697)",
            ),
            (
                "signedobject",
                "CMS SignedObject envelope decode and verification (RFC 6488)",
            ),
            (
                "manifest",
                "manifest objects, file hashes and number handling (RFC 9286, RFC 9981)",
            ),
            ("roa", "route origin authorisation objects: prefix, ASN, max length"),
            ("validation", "certificate path and resource extension checks (RFC 6487)"),
            ("slurm", "local exception filtering (RFC 8416)"),
        ],
        "api": [
            (
                "anchors",
                "def",
                "load_trust_anchor",
                "path: pathlib.Path",
                "TrustAnchor",
                "RFC 9691",
                "M4",
            ),
            (
                "repository",
                "def",
                "open_cache",
                "root: pathlib.Path",
                "Repository",
                "RFC 6480",
                "M2",
            ),
            (
                "rrdp",
                "def",
                "fetch_notification",
                "uri: str",
                "Notification",
                "RFC 8182",
                "M2",
            ),
            (
                "rrdp",
                "def",
                "apply_delta",
                "state: SessionState, delta: DeltaFile",
                "Repository",
                "RFC 8182, RFC 9697",
                "M2",
            ),
            (
                "signedobject",
                "def",
                "decode_signed_object",
                "blob: bytes",
                "SignedObject",
                "RFC 6488",
                "M1",
            ),
            (
                "manifest",
                "def",
                "check_manifest",
                "manifest: SignedObject, files: dict",
                "list of str",
                "RFC 9286",
                "M3",
            ),
            (
                "manifest",
                "def",
                "next_manifest_number",
                "current: int",
                "int",
                "RFC 9981",
                "M3",
            ),
            (
                "validation",
                "def",
                "validate_roa",
                "roa: SignedObject, store: Repository",
                "ValidationResult",
                "RFC 6487",
                "M4",
            ),
            (
                "validation",
                "def",
                "validate_origin",
                "prefix: str, asn: int, roas: Iterable",
                "RovState",
                "RFC 6480",
                "M4",
            ),
            (
                "slurm",
                "def",
                "apply_slurm",
                "roas: Iterable, config: Slurm",
                "Iterable",
                "RFC 8416",
                "M5",
            ),
        ],
        "steps": [
            "M1 signed objects: RFC 6488 envelope plus RFC 7935 signature verification against live RIR data",
            "M2 repository: RRDP notification, snapshot and delta, with RFC 9697 desync recovery",
            "M3 manifests: hash verification, stale detection, RFC 9981 number boundary",
            "M4 validation: path walk, resource extensions, ROA extraction, ROV result with reason codes",
            "M5 SLURM filtering and the assertion stream rtrkit consumes",
            "M6 publication: 0.1.0 on PyPI with a reproducible repository fixture in the sdist",
        ],
        "extras": {"xml": [("defusedxml", "0.7")]},
        "cli": "rpkit validate 1.2.3.0/24 64512 -- state and every reason behind it",
    },
    {
        "name": "rtrkit",
        "pkg": "rtrkit",
        "title": "rtrkit -- RPKI-to-Router protocol, both ends",
        "desc": "RPKI-to-Router (RTR) version 0 and 1: cache server and router client, IPv4 and "
        "IPv6 prefix and router-key records, serial notify and reset query",
        "kw": [
            "rpki",
            "rtr",
            "rov",
            "bgp",
            "rfc8210",
            "rfc6810",
            "router-key",
            "cache-server",
        ],
        "why": (
            "RTR is the only protocol between an RPKI cache and a router, and Python cannot "
            "speak it. On PyPI there is nothing at all: rtr, rtrkit, rpki-rtr and rtr-client "
            "were all absent when checked on 2026-11-05. rtrkit is deliberately the small half "
            "of the problem. It validates nothing; it moves already validated assertions over a "
            "socket with the right serial discipline, which is exactly what makes it testable "
            "against a real router without needing a repository, and what lets rpkit stay a "
            "validator instead of growing a server."
        ),
        "docs": [
            {
                "n": "8210",
                "status": "Proposed Standard",
                "date": "2017-09",
                "why": "RTR version 1: PDU set, record types, error handling",
            },
            {
                "n": "6810",
                "status": "Proposed Standard",
                "date": "2013-01",
                "why": "version 0, still what most routers speak",
            },
            {
                "n": "6480",
                "status": "Informational",
                "date": "2012-02",
                "why": "where a cache server sits and what it must deliver",
            },
            {
                "n": "9324",
                "status": "Proposed Standard",
                "date": "2022-12",
                "why": "why fresh RPKI data must not trigger a BGP route refresh",
            },
        ],
        "errata": [
            "RFC 6810 is updated by RFC 8210. Implement 8210 and negotiate down to 0 rather than writing two stacks.",
            "RFC 8210 reports no errata as of 2026-10-29. Re-check before the M1 freeze.",
        ],
        "prior": [
            {
                "name": "rov",
                "version": "0.6.0",
                "date": "2025-07-09",
                "note": "batch validator, no RTR",
            },
            {
                "name": "FRR and BIRD RTR clients",
                "version": "-",
                "date": "-",
                "note": "C daemons, not importable code",
            },
            {
                "name": "rtr, rpki-rtr, rtr-client",
                "version": "-",
                "date": "-",
                "note": "absent from PyPI (2026-11-05)",
            },
        ],
        "gaps": [
            "cache server: serial discipline, reset query, cache notify, refresh and retry timers",
            "router client: IPv4 and IPv6 prefix records plus router-key records with the error PDU contract",
            "incremental and diff-only update modes that a validator like rpkit can drive directly",
            "a fault injector for truncated records, serial wrap and reset-query storms, the bugs routers actually ship with",
        ],
        "nongoals": [
            "repository access or validation, which is rpkit",
            "BGP itself, which is pathattr",
            "MRT files",
        ],
        "mods": [
            ("pdu", "RTR PDU framing and every record type (RFC 8210)"),
            (
                "serialize",
                "endian-exact encode and decode, hostile input refusal (RFC 8210, RFC 6810)",
            ),
            (
                "session",
                "client and server state machine: version negotiation, serials, timers (RFC 8210)",
            ),
            ("inject", "fault injection helpers for conformance testing"),
        ],
        "api": [
            ("serialize", "def", "decode_pdu", "raw: bytes", "Pdu", "RFC 8210", "M1"),
            (
                "serialize",
                "def",
                "encode_reset_query",
                "session_id: int",
                "ResetQuery",
                "RFC 8210",
                "M1",
            ),
            (
                "serialize",
                "def",
                "encode_ipv4_prefix_record",
                "prefix: str, asn: int, max_len: int, flags: int",
                "PrefixRecord",
                "RFC 8210",
                "M1",
            ),
            (
                "serialize",
                "def",
                "encode_router_key_record",
                "asn: int, ski: bytes, spki: bytes",
                "RouterKeyRecord",
                "RFC 8210",
                "M1",
            ),
            (
                "session",
                "def",
                "serve",
                "store: AssertionStore, host: str, port: int",
                "None",
                "RFC 8210",
                "M3",
            ),
            (
                "session",
                "async def",
                "connect",
                "host: str, port: int",
                "RouterClient",
                "RFC 8210",
                "M2",
            ),
            (
                "inject",
                "def",
                "truncated_record",
                "payload: bytes, keep: int",
                "bytes",
                "design",
                "M4",
            ),
        ],
        "steps": [
            "M1 PDU codec, byte-exact, with fuzzed-input refusal",
            "M2 client session: reset query, serial notify, record application",
            "M3 server session: timers, cache notify, error PDUs, concurrent routers",
            "M4 fault injection suite and a loopback conformance test against itself",
            "M5 drive it from an rpkit assertion stream end to end",
            "M6 publication: 0.1.0 with the conformance trace in the docs",
        ],
        "extras": {},
        "cli": "rtrkit serve --listen 127.0.0.1:8323 -- source a store and watch routers attach",
    },
    {
        "name": "asnkit",
        "pkg": "asnkit",
        "title": "asnkit -- prefix to ASN, current and historical",
        "desc": "MRT RIB table parsing, radix longest-prefix match and historical AS lookups "
        "with optional route origin validation state joined in",
        "kw": ["bgp", "mrt", "rib", "asn", "prefix", "radix", "lpm", "rov"],
        "why": (
            "pyasn is the tool everyone uses and it stopped moving in 2023: 1.6.2, 2023-09-01, "
            "a C extension out of a Python 2 lineage with no type hints, no async and no ROV "
            "state. MRT parsing alone is covered by a dozen one-off scripts; what is missing is "
            "a typed library that answers the question an operator actually asks, which AS, on "
            "which date, and what did RPKI say about it, while streaming its input instead of "
            "demanding a build step. The RIB index sits behind an interface so the pure-Python "
            "radix and an optional native one are interchangeable."
        ),
        "docs": [
            {
                "n": "6396",
                "status": "Proposed Standard",
                "date": "2011-10",
                "why": "MRT format: table dumps, BGP messages, state changes",
            },
            {
                "n": "4271",
                "status": "Draft Standard",
                "date": "2006-01",
                "why": "UPDATE and path attribute semantics inside a table dump",
            },
            {
                "n": "6480",
                "status": "Informational",
                "date": "2012-02",
                "why": "what a validation state joined onto a RIB row means",
            },
            {
                "n": "8210",
                "status": "Proposed Standard",
                "date": "2017-09",
                "why": "a live feed via rtrkit instead of a static dump",
            },
        ],
        "errata": [
            "RFC 4271 carries 9 verified errata and 12 updates. Consult them before trusting any UPDATE parsing recipe copied from a blog post.",
            "RFC 6396 carries 2 verified errata.",
        ],
        "prior": [
            {
                "name": "pyasn",
                "version": "1.6.2",
                "date": "2023-09-01",
                "note": "the incumbent: C extension, no typing, no ROV, a build-a-db step",
            },
            {
                "name": "netflow",
                "version": "0.12.2",
                "date": "2023-08-20",
                "note": "flow export codecs, adjacent rather than overlapping",
            },
            {
                "name": "py-radix",
                "version": "-",
                "date": "-",
                "note": "a trie primitive; no MRT, no history",
            },
        ],
        "gaps": [
            "a streaming TABLE_DUMP_V2 reader that never loads a whole RIB into memory",
            "historical lookups by date from the same index, without a per-archive build step",
            "validation state joined at query time from rtrkit or an rpkit assertion set",
            "typed API with an explicit provenance field on every answer",
        ],
        "nongoals": [
            "a BGP speaker or session FSM",
            "flow data",
            "a hosted API that hides where the answer came from",
        ],
        "mods": [
            ("mrt", "MRT record and subtype framing (RFC 6396)"),
            (
                "rib",
                "TABLE_DUMP_V2 view, prefix and AS_PATH records (RFC 6396, RFC 4271)",
            ),
            ("lpm", "radix tree with longest-prefix match and a stable iterator"),
            ("history", "date-scoped indexes over multiple RIB dumps"),
            (
                "rov",
                "origin validation join from rtrkit or an rpkit assertion set (RFC 6480)",
            ),
        ],
        "api": [
            ("mrt", "def", "iter_records", "stream: IO", "Iterator", "RFC 6396", "M1"),
            (
                "rib",
                "def",
                "iter_rib_entries",
                "dump: IO",
                "Iterator",
                "RFC 6396",
                "M1",
            ),
            (
                "lpm",
                "def",
                "longest_match",
                "tree: Radix, address: str",
                "Optional[RibEntry]",
                "design: equals a brute-force scan for the longest covering prefix over the same entries",
                "M2",
            ),
            ("lpm", "def", "build_radix", "entries: Iterable", "Radix", "design", "M2"),
            (
                "history",
                "def",
                "lookup_asn",
                "archive: Archive, address: str, when: datetime.date",
                "list",
                "design",
                "M3",
            ),
            (
                "rov",
                "def",
                "annotate",
                "entries: Iterable, feed: RovFeed",
                "Iterable",
                "RFC 6480",
                "M4",
            ),
        ],
        "steps": [
            "M1 MRT reader for TABLE_DUMP_V2 with a streaming test corpus",
            "M2 radix LPM with a pure-Python reference and a native fast path behind the interface",
            "M3 date-scoped history over several RIB dumps",
            "M4 validation join from rtrkit, with reason codes carried into the answer",
            "M5 CLI parity with pyasn so a migration is a one-line change",
            "M6 publication: 0.1.0 with a migration note from pyasn",
        ],
        "extras": {"fast": [("msgspec", "0.19")]},
        "cli": "asnkit lookup 8.8.8.8 -- asn, prefix, source RIB and validation state",
    },
    {
        "name": "pathattr",
        "pkg": "pathattr",
        "title": "pathattr -- the BGP wire codec a looking glass is built on",
        "desc": "BGP-4 message and path attribute codec: OPEN, UPDATE, NOTIFICATION and "
        "KEEPALIVE with every attribute, 4-octet AS, multiprotocol NLRI, ADD-PATH, "
        "large and extended communities, and role capability",
        "kw": [
            "bgp",
            "path-attributes",
            "update",
            "nlri",
            "communities",
            "rfc4271",
            "looking-glass",
        ],
        "why": (
            "Everything a looking glass shows is a BGP UPDATE, and Python has no decoder it "
            "can own. ExaBGP is a daemon with a pipe protocol, not a library; pybgp3 has not "
            "moved in years; and the parsers people copy from blog posts get 4-octet AS "
            "numbers, ADD-PATH and the extended community sub-typing wrong. This is the shared "
            "dependency for the whole cluster: BMP carries UPDATEs inside its path mapping "
            "message, the RIB store indexes what it decodes, leak detection reasons over it, "
            "and the router text parsers produce the same objects. It is a codec and a "
            "stateless reader, deliberately not a router.",
        ),
        "docs": [
            {
                "n": "4271",
                "status": "Draft Standard",
                "date": "2006-01",
                "why": "BGP-4: messages, the attribute flag octet, NLRI and the finite state machine vocabulary",
            },
            {
                "n": "4360",
                "status": "Proposed Standard",
                "date": "2006-02",
                "why": "extended communities and their sub-type namespaces",
            },
            {
                "n": "8092",
                "status": "Proposed Standard",
                "date": "2017-02",
                "why": "the large community attribute, four-octet safe by construction",
            },
            {
                "n": "7911",
                "status": "Proposed Standard",
                "date": "2016-07",
                "why": "ADD-PATH: a path identifier alongside the prefix",
            },
            {
                "n": "9234",
                "status": "Proposed Standard",
                "date": "2022-05",
                "why": "roles in OPEN and UPDATE for leak prevention and detection",
            },
            {
                "n": "6793",
                "status": "Proposed Standard",
                "date": "2012-12",
                "why": "the four-octet AS number space, updating RFC 4271",
            },
            {
                "n": "4760",
                "status": "Draft Standard",
                "date": "2007-01",
                "why": "multiprotocol extensions: MP_REACH_NLRI and MP_UNREACH_NLRI",
            },
            {
                "n": "7606",
                "status": "Proposed Standard",
                "date": "2015-08",
                "why": "revised error handling for UPDATE messages, updating RFC 4271, RFC 4360 and RFC 4760",
            },
        ],
        "errata": [
            "RFC 4271 carries 9 verified errata and is updated by RFC 4724, RFC 6286, RFC 6608, RFC 6793, RFC 7606, RFC 7607, RFC 7705, RFC 8212, RFC 8654, RFC 9072, RFC 9687 and RFC 9774. Several of those change the wire: 6793 is 4-octet AS, 7606 is error handling, 9072 is extended message lengths.",
            "RFC 4360 carries 2 verified errata and is updated by RFC 7153 and RFC 7606.",
            "RFC 7911 and RFC 9234 report no errata as of 2026-10-29.",
        ],
        "prior": [
            {
                "name": "exabgp",
                "version": "5.0.15",
                "date": "2026-10-17",
                "note": "a BGP daemon with an external process API, not an importable codec",
            },
            {
                "name": "pybgp3",
                "version": "-",
                "date": "-",
                "note": "stale; absent from PyPI as a maintained project",
            },
            {
                "name": "bgpkit, bgpspy, ribkit, pathattr",
                "version": "-",
                "date": "-",
                "note": "all free on PyPI (2026-11-05); bgpkit and bgpspy collide with tool and service brands",
            },
        ],
        "gaps": [
            "a codec with no parser bugs: attribute flag semantics, optional and transitive handling, and length discipline on every path",
            "4-octet AS in AS_PATH, AS4_PATH, and the two-byte fallback, plus confederation handling",
            "multiprotocol NLRI with label and SID forms left opaque rather than guessed at",
            "extended and large communities decoded against the IANA registries, with unknown sub-types preserved",
            "hostile input refusal, since a looking glass parses what strangers announce",
        ],
        "nongoals": [
            "a BGP speaker: no sessions, no timers, no announcement; ExaBGP, FRR and BIRD do that",
            "best path selection or any policy engine",
            "RIB storage and indexing, which is asnkit",
        ],
        "mods": [
            (
                "message",
                "the four message types with header and length discipline (RFC 4271)",
            ),
            (
                "attribute",
                "attribute flags, type registry and per-attribute codecs (RFC 4271)",
            ),
            (
                "aspath",
                "AS_SEQUENCE, AS_SET and the 4-octet variants including confederations (RFC 6793)",
            ),
            (
                "nlri",
                "prefix, multiprotocol reachability and unreachability (RFC 4271, RFC 4760 lineage)",
            ),
            (
                "communities",
                "well-known, extended and large communities against the IANA registries (RFC 4360, RFC 8092)",
            ),
            (
                "addpath",
                "path identifier encoding in NLRI and in capabilities (RFC 7911)",
            ),
            ("roles", "the BGP_ROLE capability and path attribute (RFC 9234)"),
            ("limits", "bounds on attribute count, AS path length and message size"),
        ],
        "api": [
            ("message", "def", "decode", "raw: bytes", "Message", "RFC 4271", "M1"),
            ("message", "def", "encode", "message: Message", "bytes", "RFC 4271", "M1"),
            (
                "attribute",
                "def",
                "decode_attributes",
                "raw: bytes",
                "list",
                "RFC 4271",
                "M3",
            ),
            (
                "aspath",
                "def",
                "parse_as_path",
                "raw: bytes, four_octet: bool",
                "AsPath",
                "RFC 6793",
                "M3",
            ),
            (
                "nlri",
                "def",
                "parse_nlri",
                "raw: bytes, afi: int, safi: int, add_path: bool",
                "list",
                "RFC 7911",
                "M4",
            ),
            (
                "communities",
                "def",
                "decode_communities",
                "raw: bytes",
                "list",
                "RFC 8092",
                "M5",
            ),
            (
                "communities",
                "def",
                "decode_extended",
                "raw: bytes",
                "list",
                "RFC 4360",
                "M5",
            ),
            (
                "roles",
                "def",
                "peer_role",
                "capability: bytes",
                "Role",
                "RFC 9234",
                "M2",
            ),
            ("limits", "def", "check", "plan: ParsePlan", "None", "design", "M1"),
        ],
        "steps": [
            "M1 message framing and NOTIFICATION and KEEPALIVE handling",
            "M2 OPEN with the capability registry, including ADD-PATH and role",
            "M3 path attributes with 4-octet AS and AS4 fallback",
            "M4 multiprotocol NLRI, withdrawn and announce paths",
            "M5 community decoding against the registries, unknown types preserved",
            "M6 publication: 0.1.0 with captures from a live RIB as fixtures",
        ],
        "extras": {},
        "cli": "pathattr decode update.bin --json --registry iana",
    },
    {
        "name": "bmpkit",
        "pkg": "bmpkit",
        "title": "bmpkit -- stop screen scraping your routers",
        "desc": "BGP Monitoring Protocol version 3 collector and decoder: initiation and peer "
        "distinguishing, route monitoring with path attributes, Adj-RIB-Out and Loc-RIB "
        "mirroring, statistics and route mirroring",
        "kw": [
            "bmp",
            "bgp",
            "monitoring",
            "rib",
            "looking-glass",
            "rfc7854",
            "rfc8671",
            "rfc9069",
        ],
        "why": (
            "BMP exists precisely because obtaining route views used to mean scraping a "
            "terminal, and Python still cannot consume it. FRR, BIRD, Cisco and Juniper all "
            "emit BMP; nothing in Python reads it. The name bmp on PyPI is a retrieval library "
            "and pybmp is a BMP image handler, so the acronym is unclaimed in this sense. "
            "bmpkit decodes to the same update objects pathattr defines and hands them to a "
            "store, which is exactly the seam a looking glass, an analyst tool and a hijack "
            "detector all need.",
        ),
        "docs": [
            {
                "n": "7854",
                "status": "Proposed Standard",
                "date": "2016-06",
                "why": "BMP: common header, per peer header, route monitoring, statistics, termination and route mirroring",
            },
            {
                "n": "8671",
                "status": "Proposed Standard",
                "date": "2019-11",
                "why": "Adj-RIB-Out mirroring and the peer flag that distinguishes it",
            },
            {
                "n": "9069",
                "status": "Proposed Standard",
                "date": "2022-02",
                "why": "Loc-RIB access, the selected routes rather than the received ones",
            },
        ],
        "errata": [
            "RFC 7854 carries 3 verified errata and is updated by RFC 8671, RFC 9069, RFC 9515 and RFC 9736. Read all of them before M1. RFC 9515 only changes registration procedures for several BMP registries; RFC 9736 gives the Peer Up message its own namespace and moves codepoints from RFC 8671 and RFC 9069 into it (rfclib, 2026-11-07). RFC 9515 and RFC 9736 report no verified errata.",
            "RFC 8671 and RFC 9069 report no errata as of 2026-10-29.",
        ],
        "prior": [
            {
                "name": "bmp",
                "version": "0.2.8",
                "date": "2026-10-16",
                "note": "block-max learned sparse retrieval, unrelated",
            },
            {
                "name": "pybmp",
                "version": "1.0.1",
                "date": "2016-11-05",
                "note": "a BMP image handler",
            },
            {
                "name": "GoBGP and HurricanE Electric BMP tooling",
                "version": "-",
                "date": "-",
                "note": "Go and C; no importable Python consumer",
            },
        ],
        "gaps": [
            "a collector that terminates many router sessions with per-peer state and no head-of-line blocking",
            "route mirroring passthrough, which is how you debug a session without touching the router",
            "correct handling of the three RIB views: Adj-RIB-In, Adj-RIB-Out and Loc-RIB",
            "stats and termination records with reason codes, so gaps in the mirrored view are visible rather than silent",
            "reconnect and sequence discipline, because TCP carries BMP and reordering is a real analyst hazard",
        ],
        "nongoals": [
            "a time series store or a web UI; it emits objects to whatever you have",
            "router configuration: BMP is a monitoring channel and this library never writes to one",
            "BGP speaker behaviour, which is pathattr's boundary and below",
        ],
        "mods": [
            (
                "header",
                "common and per peer headers with the version and flag discipline (RFC 7854)",
            ),
            (
                "messages",
                "route monitoring, statistics, peer down and up, initiation and termination, route mirroring (RFC 7854)",
            ),
            (
                "rib",
                "view selection across Adj-RIB-In, Adj-RIB-Out and Loc-RIB (RFC 8671, RFC 9069)",
            ),
            ("server", "the asyncio collector with per connection state"),
            ("mirror", "passthrough of mirrored traffic for capture and replay"),
            ("store", "the sink interface a mirrored RIB is written to"),
            ("replay", "deterministic re-reading of a capture for tests and analysts"),
        ],
        "api": [
            (
                "header",
                "def",
                "decode_common",
                "raw: bytes",
                "CommonHeader",
                "RFC 7854",
                "M1",
            ),
            (
                "messages",
                "def",
                "decode_route_monitoring",
                "raw: bytes, peer: PeerKey",
                "Update",
                "RFC 7854",
                "M1",
            ),
            (
                "messages",
                "def",
                "decode_statistics",
                "raw: bytes",
                "Stats",
                "RFC 7854",
                "M4",
            ),
            (
                "rib",
                "def",
                "view_of",
                "peer: PeerKey, flags: int",
                "View",
                "RFC 8671, RFC 9069",
                "M3",
            ),
            (
                "server",
                "async def",
                "serve",
                "host: str, port: int, sink: Sink",
                "None",
                "RFC 7854",
                "M2",
            ),
            (
                "mirror",
                "def",
                "extract_bgp_message",
                "message: RouteMirroring",
                "bytes",
                "RFC 7854",
                "M4",
            ),
            (
                "replay",
                "def",
                "from_capture",
                "path: pathlib.Path",
                "Iterator",
                "design",
                "M5",
            ),
        ],
        "steps": [
            "M1 header and message codec with vectors from a live FRR or BIRD speaker",
            "M2 the collector server with per peer state and bounded buffers",
            "M3 view selection for Adj-RIB-In, Adj-RIB-Out and Loc-RIB",
            "M4 statistics, termination reasons and the mirroring passthrough",
            "M5 capture and replay tooling wired into the test suite",
            "M6 publication: 0.1.0 with a mirrored RIB comparison against a router CLI",
        ],
        "extras": {"codec": [("pathattr", "0.1")]},
        "cli": "bmpkit listen --listen 0.0.0.0:11019 --mirror ./captures",
    },
    {
        "name": "rpslkit",
        "pkg": "rpslkit",
        "title": "rpslkit -- RPSL parsing and IRR queries without a decade old dependency",
        "desc": "Routing Policy Specification Language objects, IRR database clients, and "
        "route-set and origin resolution that reports ambiguity instead of guessing",
        "kw": [
            "rpsl",
            "irr",
            "whois",
            "route-object",
            "origin",
            "rfc2622",
            "looking-glass",
        ],
        "why": (
            "IRR is still the origin authority most operators filter on, and Python's only "
            "serious tool, irrtree, last shipped 1.4.0 on 2021-06-24. Worse, the obvious "
            "package names are gone: rpsl is a Raspberry Pi sensor library and irr is a numpy "
            "joke. RFC 2622 defines the language, carries 3 verified errata and is updated by "
            "RFC 4012 and RFC 7909, and the databases disagree with each other in ways a "
            "library should surface rather than hide. rpslkit parses RPSL, queries the IRRs "
            "concurrently, resolves route sets, and reports conflicts between IRR, RPKI and "
            "observed routing as first class output.",
        ),
        "docs": [
            {
                "n": "2622",
                "status": "Proposed Standard",
                "date": "1999-06",
                "why": "RPSL: object definitions, the set expression grammar and the delete syntax",
            },
            {
                "n": "4012",
                "status": "Proposed Standard",
                "date": "2005-03",
                "why": "RPSLng: routing policy for the IPv6 and multicast address families",
            },
            {
                "n": "7909",
                "status": "Proposed Standard",
                "date": "2016-06",
                "why": "RPKI signatures on RPSL objects, updating RFC 2622 and RFC 4012",
            },
            {
                "label": "IRRd database query interface",
                "status": "operational practice",
                "date": "-",
                "why": "whois style queries, the newer HTTP and REST interfaces, and per database syntax quirks",
            },
        ],
        "errata": [
            "RFC 2622 carries 3 verified errata and is updated by RFC 4012 and RFC 7909; RFC 4012 adds the IPv6 and multicast policy extensions and RFC 7909 adds a signature attribute validated against the RPKI (rfclib, 2026-11-07). Verify both before M2.",
            "The four large IRRs differ in practice on comments, mangling and mirroring latency. Treat divergence as data and record which database answered.",
        ],
        "prior": [
            {
                "name": "irrtree",
                "version": "1.4.0",
                "date": "2021-06-24",
                "note": "the working solution, four years without a release, no asyncio",
            },
            {
                "name": "rpsl",
                "version": "0.1.dev1",
                "date": "2019-11-30",
                "note": "a Raspberry Pi sensor library squatting the acronym",
            },
            {
                "name": "irr",
                "version": "0.2",
                "date": "2021-09-28",
                "note": "a numpy joke",
            },
        ],
        "gaps": [
            "a RPSL parser that keeps source, references and comments so provenance survives",
            "concurrent queries to RIPE, RADB, NTTC, AltDB and the regional archives with per source policy",
            "route set and as set expansion with cycle detection and explicit ambiguity reporting",
            "conflict output comparing IRR assertions against RPKI assertions and observed routing",
            "prefix match semantics that say which object matched and why",
        ],
        "nongoals": [
            "generating router configuration from RPSL",
            "being an IRR server or handling maintainer authentication and object updates",
            "validating route authorisation policy, which is rpkit",
        ],
        "mods": [
            (
                "objects",
                "RPSL object classes, attributes and the reserved word set (RFC 2622)",
            ),
            (
                "parser",
                "lenient line parsing that preserves source and comments (RFC 2622)",
            ),
            (
                "sets",
                "route set and as set expansion with cycles and imports (RFC 2622)",
            ),
            ("client", "whois and HTTP queries with timeouts and per source budgets"),
            ("resolve", "origin resolution for a prefix with ambiguity preserved"),
            ("compare", "IRR against RPKI and against observed routing"),
            ("sources", "registry of IRR databases, endpoints and quirks"),
        ],
        "api": [
            (
                "parser",
                "def",
                "parse",
                "text: str, source: str",
                "list",
                "RFC 2622",
                "M1",
            ),
            (
                "objects",
                "def",
                "matches_prefix",
                "obj: RouteObject, prefix: str",
                "bool",
                "RFC 2622",
                "M4",
            ),
            (
                "sets",
                "def",
                "expand",
                "name: str, store: Store, depth: int",
                "Expansion",
                "RFC 2622 section 5",
                "M3",
            ),
            (
                "client",
                "async def",
                "query",
                "source: str, needle: str, budget: Budget",
                "list",
                "design",
                "M2",
            ),
            (
                "resolve",
                "async def",
                "origins",
                "prefix: str, sources: list",
                "list",
                "RFC 2622",
                "M4",
            ),
            (
                "compare",
                "def",
                "conflicts",
                "irr: list, roas: list, observed: list",
                "list",
                "design",
                "M5",
            ),
        ],
        "steps": [
            "M1 RPSL parser with round trip preserving comments and source",
            "M2 whois and HTTP clients for the four major databases",
            "M3 set expansion with cycle detection and ambiguity output",
            "M4 prefix origin resolution with provenance per answer",
            "M5 the IRR against RPKI against observed comparison",
            "M6 publication: 0.1.0",
        ],
        "extras": {},
        "cli": "rpslkit origins 1.2.3.0/24 --sources ripncc,radb,nttc --json",
    },
    {
        "name": "bgpfeed",
        "pkg": "bgpfeed",
        "title": "bgpfeed -- RIPE RIS and RouteViews, without the C dependency",
        "desc": "Live and archive upstream feeds: RIPE RIS websocket streaming and RouteViews "
        "BGP2K MRT deltas, with checkpointing and a normalised event model shared with "
        "bmpkit",
        "kw": [
            "ris",
            "routeviews",
            "mrt",
            "bgp",
            "streaming",
            "monitoring",
            "looking-glass",
        ],
        "why": (
            "A looking glass that only shows your own routers is a status page. The upstream "
            "view comes from RIPE RIS and RouteViews, and in Python that means the bgpstream C "
            "library with its build headaches, or hand written FTP polling. Neither name is "
            "taken on PyPI: bgpstream, ris and routeviews are all absent as of 2026-11-05. "
            "bgpfeed is the small honest client for both, emitting the same objects bmpkit "
            "emits, so anything built on one works with the other.",
        ),
        "docs": [
            {
                "n": "6396",
                "status": "Proposed Standard",
                "date": "2011-10",
                "why": "MRT, the format RouteViews publishes its updates in",
            },
            {
                "label": "RIPE RIS live API",
                "status": "operational practice",
                "date": "-",
                "why": "the websocket protocol, viewer selection and message shape",
            },
            {
                "label": "RouteViews archive layout",
                "status": "operational practice",
                "date": "-",
                "why": "per collector hourly directories, file naming and retention",
            },
        ],
        "errata": [
            "RFC 6396 carries 2 verified errata.",
            "RIS and RouteViews are operational services, not specifications: pin the endpoint and message version in tests and expect change without notice.",
        ],
        "prior": [
            {
                "name": "bgpstream",
                "version": "-",
                "date": "-",
                "note": "the C library everyone wraps; absent from PyPI (2026-11-05)",
            },
            {
                "name": "ris, routeviews, bgpfeed",
                "version": "-",
                "date": "-",
                "note": "all free on PyPI (2026-11-05)",
            },
            {
                "name": "exabgp rib scripts",
                "version": "-",
                "date": "-",
                "note": "ad hoc pollers, not a resumable client",
            },
        ],
        "gaps": [
            "a RIS websocket client with viewer selection, reconnect and gap detection",
            "a RouteViews delta poller that checkpoints by file and never re-reads or skips silently",
            "one normalised event model across live feeds, archives and BMP sessions",
            "backpressure and drop accounting, so a slow analyst does not silently lose updates",
            "honest rate limiting that respects the operators running these services",
        ],
        "nongoals": [
            "storage or query indexes, which is asnkit",
            "analysis and alerting, which is routevalid",
            "running a collector: these are clients of other people's infrastructure",
        ],
        "mods": [
            ("model", "the common event and update objects shared with bmpkit"),
            ("ris", "the websocket protocol, viewers and resubscription"),
            ("routeviews", "archive discovery, delta polling and checkpoints"),
            ("mrt", "UPDATE_FILE and TABLE_DUMP reading via asnkit"),
            ("state", "resume points and gap accounting per source"),
            ("budget", "politeness: concurrency caps, jitter and backoff"),
        ],
        "api": [
            (
                "ris",
                "async def",
                "subscribe",
                "viewers: list, collectors: list, sink: Sink",
                "Stream",
                "design",
                "M2",
            ),
            (
                "routeviews",
                "async def",
                "follow",
                "collectors: list, since: datetime, sink: Sink",
                "Stream",
                "design",
                "M3",
            ),
            (
                "mrt",
                "def",
                "iter_updates",
                "path: pathlib.Path",
                "Iterator",
                "RFC 6396",
                "M1",
            ),
            (
                "state",
                "def",
                "checkpoint",
                "source: str, position: object",
                "None",
                "design",
                "M3",
            ),
            ("state", "def", "resume", "source: str", "object", "design", "M3"),
            (
                "budget",
                "def",
                "policy",
                "max_concurrent: int, jitter: timedelta",
                "Budget",
                "design",
                "M4",
            ),
        ],
        "steps": [
            "M1 the shared event model, and MRT update reading through asnkit",
            "M2 RIS websocket client with reconnect and gap reporting",
            "M3 RouteViews delta poller with checkpoints",
            "M4 politeness and drop accounting",
            "M5 a replay fixture so downstream tests never touch the network",
            "M6 publication: 0.1.0",
        ],
        "extras": {"mrt": [("asnkit", "0.1")]},
        "cli": "bgpfeed ris --view rrc00 --collector rrc00-collector-1 --out events.ndjson",
    },
    {
        "name": "routevalid",
        "pkg": "routevalid",
        "title": "routevalid -- hijack and leak detection with evidence attached",
        "desc": "Correlates BMP sessions, RIS and RouteViews feeds, RPKI assertions, RTR "
        "updates and IRR objects into typed events with a replayable evidence bundle",
        "kw": [
            "hijack",
            "route-leak",
            "rov",
            "monitoring",
            "rpki",
            "bgp",
            "rfc9234",
            "detection",
        ],
        "why": (
            "Detection tools either are research prototypes or are hosted dashboards you cannot "
            "self host. What is missing is a library: given observations from your own BMP "
            "sessions plus RIS and RouteViews, plus RPKI from rpkit and RTR state from rtrkit, "
            "emit typed events with the evidence that justified each one. RFC 9234 gives the "
            "role information that makes leaks checkable against declared relationships rather "
            "than guessed, and that document has no errata and almost no implementations, which "
            "is exactly the kind of gap this portfolio exists for.",
        ),
        "docs": [
            {
                "n": "9234",
                "status": "Proposed Standard",
                "date": "2022-05",
                "why": "roles in OPEN and UPDATE: the basis for leak detection against declared relationships",
            },
            {
                "n": "6480",
                "status": "Informational",
                "date": "2012-02",
                "why": "the RPKI architecture whose assertions an origin check consumes",
            },
            {
                "n": "9324",
                "status": "Proposed Standard",
                "date": "2022-12",
                "why": "why fresh RPKI data must not trigger a route refresh when re-evaluating",
            },
            {
                "n": "6396",
                "status": "Proposed Standard",
                "date": "2011-10",
                "why": "the archive format for replaying history against a detection rule",
            },
            {
                "n": "6811",
                "status": "Proposed Standard",
                "date": "2013-01",
                "why": "BGP prefix origin validation: the Valid, Invalid and NotFound states an origin check returns",
            },
            {
                "n": "8481",
                "status": "Proposed Standard",
                "date": "2018-09",
                "why": "clarifications to origin validation: every prefix gets a state, no policy without operator configuration",
            },
        ],
        "errata": [
            "RFC 9234 reports no errata as of 2026-10-29, and deployment is thin enough that you should expect to test against peers who do not send the attribute.",
            "RFC 6480 has 1 erratum on file, none verified; RFC 9324 updates RFC 8481 and should be read together with it. RFC 6811 is updated by RFC 8481 and RFC 8893; neither RFC 6811 nor RFC 8481 has a verified erratum (rfclib, 2026-11-07).",
        ],
        "prior": [
            {
                "name": "STEAL-179 and similar research tools",
                "version": "-",
                "date": "-",
                "note": "research artefacts, not maintained libraries",
            },
            {
                "name": "hosted monitoring dashboards",
                "version": "-",
                "date": "-",
                "note": "BGPStream based services: useful, but not something you can embed",
            },
            {
                "name": "routevalid, leakkit, stealer, bgp-hijack",
                "version": "-",
                "date": "-",
                "note": "all free on PyPI (2026-11-05)",
            },
        ],
        "gaps": [
            "prefix visibility tracking: who announces it, from which AS, seen by which collectors",
            "origin checks combining RPKI state, RPSL assertions and observed AS paths with explicit disagreement handling",
            "leak checks using declared roles where available and inferred relationships where not",
            "confidence scoring from collector diversity, so one vantage point cannot raise a page",
            "an evidence bundle per event, replayable from the raw observations",
        ],
        "nongoals": [
            "a dashboard, an alerting pipeline or a ticket system",
            "storage engine design: it consumes a sink",
            "community based mitigation or any action against a router: detection only",
        ],
        "mods": [
            ("observe", "the normalised observation model from bmpkit and bgpfeed"),
            (
                "visibility",
                "prefix and AS visibility tracking over collectors and time",
            ),
            ("origin", "origin checks combining RPKI, RPSL and observed paths"),
            ("leak", "role and relationship checks on AS paths (RFC 9234)"),
            ("score", "confidence from collector diversity and disagreement"),
            ("event", "typed events with severity, evidence and replay references"),
            ("replay", "deterministic re-evaluation of history against a rule set"),
            ("rules", "the policy object deciding what counts as an event"),
        ],
        "api": [
            (
                "observe",
                "def",
                "record",
                "update: Update, source: Source, at: datetime",
                "None",
                "design",
                "M1",
            ),
            (
                "visibility",
                "def",
                "announcers",
                "prefix: str, window: timedelta",
                "list",
                "design",
                "M1",
            ),
            (
                "origin",
                "def",
                "check_origin",
                "prefix: str, asn: int, roas: list, irr: list",
                "OriginVerdict",
                "RFC 6480",
                "M2",
            ),
            (
                "leak",
                "def",
                "check_path",
                "asn_path: list, relationships: Relationships",
                "LeakVerdict",
                "RFC 9234",
                "M3",
            ),
            (
                "score",
                "def",
                "confidence",
                "observations: list, diversity: int",
                "float",
                "design",
                "M4",
            ),
            (
                "event",
                "def",
                "emit",
                "kind: str, evidence: Evidence, severity: str",
                "Event",
                "design",
                "M4",
            ),
            (
                "replay",
                "def",
                "reevaluate",
                "store: Store, rules: Rules, since: datetime",
                "list",
                "RFC 9324",
                "M5",
            ),
        ],
        "steps": [
            "M1 the observation model and visibility tracking",
            "M2 origin checks against rpkit assertions and rpslkit output",
            "M3 role based leak checks with an inferred relationship fallback",
            "M4 confidence scoring and the event schema with evidence",
            "M5 replay, then a historical corpus to measure false positives",
            "M6 publication: 0.1.0 with a measured precision report",
        ],
        "extras": {
            "feeds": [("bmpkit", "0.1"), ("bgpfeed", "0.1")],
            "rpki": [("rpkit", "0.1"), ("rtrkit", "0.1")],
        },
        "cli": "routevalid watch --prefix 1.2.3.0/24 --sources bmp,ris --min-confidence 0.8",
    },
    {
        "name": "lgc",
        "pkg": "lgc",
        "title": "lgc -- looking glass client that returns objects, not terminal text",
        "desc": "Typed looking glass clients: the de-facto LGC and LGS wire protocol, HTTP and "
        "SSH backends, and vendor output parsers that produce pathattr objects",
        "kw": [
            "looking-glass",
            "lgc",
            "lgs",
            "bgp",
            "operations",
            "parsers",
            "frr",
            "bird",
        ],
        "why": (
            "The last mile of every looking glass is text: someone parses show ip bgp output "
            "with a regular expression written in a hurry. The de-facto LGC and LGS protocol has "
            "no Python client, the old looking-glass package died in 2019, and pybird only "
            "covers BIRD. lgc is a client library with one promise: every query returns typed "
            "objects from pathattr and asnkit, and where a device can only give text a named "
            "parser with tests owns the conversion. It is read only by construction, which is "
            "the property that lets you point it at production.",
        ),
        "docs": [
            {
                "label": "LGC and LGS protocol",
                "status": "de-facto practice",
                "date": "-",
                "why": "the request, response and progress framing used by open source looking glasses",
            },
            {
                "label": "FRR and BIRD command interfaces",
                "status": "implementation practice",
                "date": "-",
                "why": "vtysh, the FRR REST API and birdc JSON output",
            },
            {
                "n": "4271",
                "status": "Draft Standard",
                "date": "2006-01",
                "why": "the semantics every parser ultimately claims to render",
            },
        ],
        "errata": [
            "LGC, LGS, FRR and BIRD interfaces are not specifications with errata: pin versions in fixtures and treat output drift as a bug in the parser, not the device.",
            "Vendor text formats change between releases without notice, which is why every parser needs a captured fixture per version.",
        ],
        "prior": [
            {
                "name": "looking-glass",
                "version": "1.0.4",
                "date": "2019-03-26",
                "note": "a web visualisation tool, abandoned",
            },
            {
                "name": "pybird",
                "version": "1.2.0",
                "date": "2023-02-08",
                "note": "BIRD only, no typed BGP objects",
            },
            {
                "name": "lgc, birdc, frr, gobgp, quagga",
                "version": "-",
                "date": "-",
                "note": "all free on PyPI (2026-11-05)",
            },
        ],
        "gaps": [
            "an LGC and LGS client with capability discovery, timeouts and an allow list of query types",
            "parsers per device and version producing pathattr objects rather than strings",
            "RIB, RIB-in, RIB-out and per-prefix detail queries with one return type",
            "read only enforcement in the type system, so a caller cannot send a configuration command by accident",
            "concurrent multi site queries with per site latency and error accounting",
        ],
        "nongoals": [
            "a web front end or the RIB viewer itself; it returns objects you can render",
            "device configuration or any write path, ever",
            "generic network automation, which is scrapli, netmiko and napalm territory",
        ],
        "mods": [
            ("protocol", "LGC and LGS framing, request types and progress reporting"),
            ("transport", "HTTP, SSH and raw TCP backends with budgets and host keys"),
            ("parsers", "per vendor and version output conversion into typed objects"),
            (
                "query",
                "the typed query surface: rib, ribin, ribout, detail, traceroute",
            ),
            ("sites", "registry of looking glasses with capabilities and health"),
            ("guard", "read only enforcement and an explicit allow list"),
            ("federation", "concurrent queries across sites with per site accounting"),
        ],
        "api": [
            (
                "protocol",
                "def",
                "encode_request",
                "query: Query",
                "bytes",
                "design",
                "M2",
            ),
            (
                "protocol",
                "def",
                "decode_response",
                "raw: bytes",
                "Response",
                "design",
                "M2",
            ),
            (
                "transport",
                "async def",
                "call",
                "site: Site, query: Query, budget: Budget",
                "Response",
                "design",
                "M2",
            ),
            (
                "parsers",
                "def",
                "parse_rib",
                "text: str, flavour: str",
                "list",
                "RFC 4271",
                "M3",
            ),
            (
                "query",
                "async def",
                "rib",
                "site: Site, prefix: str",
                "list",
                "RFC 4271",
                "M1",
            ),
            (
                "query",
                "async def",
                "detail",
                "site: Site, prefix: str",
                "list",
                "RFC 4271",
                "M1",
            ),
            (
                "guard",
                "def",
                "assert_read_only",
                "query: Query",
                "None",
                "design",
                "M1",
            ),
            (
                "federation",
                "async def",
                "query_many",
                "sites: list, query: Query",
                "list",
                "design",
                "M5",
            ),
        ],
        "steps": [
            "M1 the query and result model plus read only enforcement",
            "M2 LGS and LGC transport with capability discovery",
            "M3 FRR and BIRD parsers with captured fixtures per version",
            "M4 SSH and HTTP backends with host key pinning",
            "M5 multi site federation with per site accounting",
            "M6 publication: 0.1.0 with a worked example composing rpkit state",
        ],
        "extras": {"codec": [("pathattr", "0.1")]},
        "cli": "lgc query --site route-server-example --prefix 1.2.3.0/24 --detail --json",
    },
    {
        "name": "miblang",
        "pkg": "miblang",
        "title": "miblang -- SMIv2 module parsing that says which edition an answer came from",
        "desc": "Parses SMIv2 MIB modules into an OID-addressed object tree: the macro forms, the RFC 2579 textual conventions, RFC 2580 conformance statements, and the ITU-T formal-description catalogue as a digest-pinned module source",
        "kw": [
            "mib",
            "smi",
            "smiv2",
            "snmp",
            "oid",
            "network-management",
            "rfc2578",
            "rfc2579",
            "itu-t",
        ],
        "why": (
            "snmpy carries about 150 KB of hand-maintained management-information code and not one line of "
            "grammar: mib_view.py is 40,505 bytes of hand-written per-MIB views, mib.py is 25,969 bytes of "
            "hand-copied OID constants whose own header cites RFC 1156, a HISTORIC document obsoleted by the "
            "MIB-II of RFC 1213, and across all 70 of its python files there is exactly one stray "
            "NOTIFICATION-TYPE token and no MODULE-IDENTITY or OBJECT-TYPE anywhere. Every MIB it speaks was "
            "transcribed by hand, and a transcription is where an OID goes wrong in silence. The corpus a "
            "reader would consume is published and machine-shaped: the ITU-T formal-descriptions catalogue "
            "holds 977 normative artifacts in ASN.1, MIB, GDMO, ABNF, JSON, XML, TTCN-3 and seven other "
            "languages, each row keyed by Recommendation, approval date, language, module name and OID, and "
            "each artifact served unauthenticated at HTTP 200 from a stable per-module URL (measured "
            "2026-11-05). What is missing is a stdlib-only reader that turns those modules into an OID tree "
            "able to name the edition an answer came from. pysmi 2.0.0 does this job and is current, and it "
            "is declined as a dependency because lark, Jinja2 and requests arrive attached to it; that trade, "
            "and the creation of this shell against the no-new-shells rule, are recorded with their evidence "
            "in PORTFOLIO.md sections 5 and 7."
        ),
        "docs": [
            {
                "n": "2578",
                "status": "Internet Standard (STD 58)",
                "date": "1999-04",
                "why": "SMIv2 itself: the MODULE-IDENTITY, OBJECT-TYPE, OBJECT-IDENTITY and NOTIFICATION-TYPE macros and their ASN.1 mapping, which is the grammar this parses",
            },
            {
                "n": "2579",
                "status": "Internet Standard (STD 58)",
                "date": "1999-04",
                "why": "the TEXTUAL-CONVENTION macro that RFC 2578 section 7.1.5 defers to, and the initial convention set every deployed module imports",
            },
            {
                "n": "2580",
                "status": "Internet Standard (STD 58)",
                "date": "1999-04",
                "why": "MODULE-COMPLIANCE, AGENT-CAPABILITY and the group forms: conformance as data rather than as prose",
            },
            {
                "n": "1213",
                "status": "Internet Standard (STD 17)",
                "date": "1991-03",
                "why": "MIB-II, the module a reader has to get right first and the one snmpy's constants are a hand copy of, updated by RFC 2011, RFC 2012 and RFC 2013",
            },
            {
                "n": "1902",
                "status": "Draft Standard (Obsolete)",
                "date": "1996-01",
                "why": "the SMI text older modules were written against, obsoleted by RFC 2578 yet still parsed in practice, so the reader must accept both and say which one it saw",
            },
            {
                "label": "ITU-T Recommendations formal descriptions catalogue",
                "status": "operational catalogue, page marked for deactivation",
                "date": "-",
                "why": "977 machine-readable normative artifacts keyed by Recommendation, approval date, language, module name and OID; measured 2026-11-05, and the module corpus this project reads once itulib mirrors it",
            },
        ],
        "errata": [
            "RFC 2579 carries 3 verified errata (rfclib, 2026-10-29): parse the corrected text, and record which one a fixture was taken from.",
            "RFC 2578 and RFC 2580 report no errata, but RFC 1213 is updated by RFC 2011, RFC 2012 and RFC 2013, so an interfaces or IP group answer has to name the updating document as well as the base one.",
            "The catalogue page announces its own deactivation in favour of MyWorkspace, and its language filter is an ASP.NET postback rather than a queryable endpoint: a GET with language=MIB still returns the unfiltered 977 rows (measured 2026-11-05), so enumeration must walk the per-recommendation listings and pin digests.",
        ],
        "prior": [
            {
                "name": "pysmi",
                "version": "2.0.0",
                "date": "2026-04-26",
                "note": "the incumbent, and current: a pure-Python SNMP/SMI MIB parsing and conversion library whose runtime deps are lark, Jinja2 and requests (lextudio/pysmi). Declined as a dependency rather than ignored: no project here can carry a grammar toolkit, a template engine and an HTTP client in order to read a text file.",
            },
            {
                "name": "pysnmp",
                "version": "7.1.16",
                "date": "-",
                "note": "what pysmi is developed against; the protocol itself is already spoken here by snmpy, which is why this project stops at what a name means",
            },
            {
                "name": "mibkit, smikit, mibble, pymib",
                "version": "-",
                "date": "2026-11-05",
                "note": "each free on both PyPI signals and each refused: one keystroke from 6, 5, 6 and 24 live names respectively against a snapshot of 931,439 canonical names (names.py --index). miblang has zero live neighbours.",
            },
        ],
        "gaps": [
            "a stdlib-only SMIv2 reader, because the incumbent brings three runtime dependencies along with the parser",
            "an OID answer that carries its edition: which module, which version of that module, and which publisher put it where",
            "a way to notice that a deployed table was transcribed from an obsolete document, which is what snmpy's mib.py header admits it is",
            "MIB modules read from a corpus with digests rather than from a directory of files of unknown vintage",
        ],
        "nongoals": [
            "the SNMP protocol: snmpy speaks it, and miblang only answers what a name means and where it sits in the tree",
            "agent or manager behaviour: no transport, no table storage, no set handling",
            "GDMO and the ITU-T management definitions that predate SMIv2, a different notation and a different grammar even though the catalogue lists them",
            "MIB to Python code generation for agent frameworks: what a generated view buys over a resolver is unproven here",
        ],
        "mods": [
            (
                "lexer",
                "the comment, quoting, OID-literal and continuation rules every macro form sits inside",
            ),
            (
                "grammar",
                "the RFC 2578 macro forms and the RFC 2579 TEXTUAL-CONVENTION form, kept as syntax rather than as a tree of dicts",
            ),
            (
                "resolve",
                "cross-module imports and the OID tree in both directions, with the module edition attached to every answer",
            ),
            (
                "conventions",
                "the RFC 2579 conventions and their SUB-TYPE ranges expressed as asn1core constraints, not as a second type system",
            ),
            (
                "conformance",
                "MODULE-COMPLIANCE and AGENT-CAPABILITY reduced to an obligation set a test can check",
            ),
            (
                "catalogue",
                "the ITU-T formal-description artifacts itulib mirrors: Recommendation, edition, language, module, OID and digest",
            ),
        ],
        "api": [
            ("lexer", "def", "lex", "text: str", "list[Token]", "RFC 2578", "M1"),
            (
                "grammar",
                "def",
                "module_identity",
                "tokens: Sequence[Token]",
                "ModuleHeader",
                "RFC 2578",
                "M1",
            ),
            (
                "grammar",
                "def",
                "object_type",
                "tokens: Sequence[Token]",
                "Object",
                "RFC 2578",
                "M1",
            ),
            (
                "resolve",
                "def",
                "load",
                "path: Path, *, imports: Imports | None",
                "Module",
                "RFC 2578",
                "M2",
            ),
            ("resolve", "def", "resolve", "name: str", "Object", "RFC 2578", "M2"),
            ("resolve", "def", "index", "oid: str", "Object | None", "RFC 2578", "M2"),
            (
                "conventions",
                "def",
                "textual_convention",
                "name: str",
                "Constraint",
                "RFC 2579",
                "M3",
            ),
            (
                "conformance",
                "def",
                "compliance",
                "module: str",
                "ComplianceSet",
                "RFC 2580",
                "M4",
            ),
            (
                "catalogue",
                "def",
                "modules_of",
                "rec: str, *, edition: str",
                "list[Artifact]",
                "ITU-T",
                "M5",
            ),
            ("resolve", "def", "provenance", "oid: str", "Provenance", "design", "M5"),
        ],
        "steps": [
            "M1 tokenizer plus the MODULE-IDENTITY, OBJECT-TYPE and TEXTUAL-CONVENTION forms, proven against published modules byte for byte",
            "M2 cross-module imports and the resolver, answering name to OID and OID to name with the edition attached",
            "M3 the RFC 2579 conventions and their SUB-TYPE ranges as asn1core constraints rather than a parallel type system",
            "M4 RFC 2580 conformance statements reduced to an obligation set",
            "M5 the ITU-T formal-description catalogue through itulib, with digest verification and edition pinning: blocked until itulib mirrors artifacts as well as prose",
            "M6 publication: 0.1.0 with a measured claim, how many published modules parse and how many fail and why",
        ],
        "extras": {
            "catalogue": [("itulib", "0.1")],
            "conventions": [("asn1core", "0.1")],
        },
        "cli": "miblang resolve IF-MIB::ifPhysAddress --prove",
    },
]

OPEN_QUESTIONS: dict[str, list[str]] = {
    "rpkit": [
        "rsync or RRDP first: rsync is universal, RRDP is the future and the easier test target",
        "repository cache format, and whether it is shared with rtrkit on disk",
    ],
    "rtrkit": [
        "does the server hold a full assertion set or stream from rpkit incrementally",
        "M5 (drive it from an rpkit assertion stream) has no row: add one once the question above is settled, or drop the rung",
    ],
    "asnkit": [
        "pure-Python radix versus an optional native one, decided by a benchmark on a full RIB",
        "M5 (CLI parity with pyasn) has no row: add one naming the CLI surface, or drop the rung",
    ],
    "bgpfeed": [
        "M5 (a replay fixture so downstream tests never touch the network) has no row: add one, or drop the rung",
    ],
    "lgc": [
        "M4 (SSH and HTTP backends with host key pinning) has no row; transport.call sits on M2: add a pinning row on M4, or fold M4 into M2",
    ],
    "bmpkit": [
        "messages.decode_statistics is placed on M4, the rung that names statistics, but M1 is the message codec: keep it on M4 or move it to M1",
        "RFC 9736 moves Peer Up onto its own namespace and RFC 9515 changes registry procedures only: does M1 decode Peer Up information against the RFC 9736 registry, and does a row need to name it",
    ],
    "pathattr": [
        "nlri.parse_nlri is placed on M4 (multiprotocol NLRI) but its Spec cell cites RFC 7911 (ADD-PATH) only: add RFC 4760 to the cell, or split the ADD-PATH handling into its own row",
    ],
    "miblang": [
        "M5 depends on itulib mirroring artifacts and not only prose: its registry is 16 editions of the ASN.1 series today, so the catalogue work is a prerequisite rather than a rung, and no M5 row can be closed before it",
        "whether snmpy's hand-written mib_view.py is regenerated from miblang output, retired module by module in favour of the resolver, or left alone: the 40,505 bytes exist and one of the three has to be said out loud",
    ],
}
