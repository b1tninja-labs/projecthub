# RFC-anchored protocol SDKs -- portfolio and gap survey

Asyncio-first Python libraries for network protocols where the Python answer is missing,
stale, or welded to a product. Each is its own repository with a specification obligation
matrix, and none of them pretends to be finished. They are generated from one table by
`projecthub shells generate`, so a convention change is one edit rather than many.

Where this survey stops, the portfolio plan picks up: [PORTFOLIO.md](PORTFOLIO.md) at the
workspace root decides sequencing -- which projects are worked on, in what order, and what
is deliberately not built -- while each project's own `ROADMAP.md` decides its protocol
obligations.

Survey date: SURVEY_DATE. Landscape claims here carry dates deliberately. During this survey
pymasque shipped on 2026-11-04 and pyepp on 2026-11-05, the day of the survey; x509kit
published 0.10.0 three weeks before it. An undated claim that a corner of PyPI is empty is
worth about a week.

## How the gaps were found

1. Two PyPI signals, both required: the simple index for existence, and the JSON API for
   version, summary and last release date. A name can be registered with zero files, which
   the JSON API reports as an error while the index reports success. That is a squat, and it
   is as unavailable as a live project. tr069 and python-epp are both this case.
2. Every candidate specification checked against the RFC Editor record for status,
   publication date and errata. First-draft assumptions that were wrong and got corrected:
   RRDP is RFC 8182 rather than 9183; RFC 6486 is obsolete and RFC 9286 replaced it; RFC 8941
   is obsolete and RFC 9651 replaced it; RFC 4210 is obsolete and RFC 9810 replaced it;
   DHCPv6 RFC 8415 is obsolete and RFC 9915, an Internet Standard published 2026-01, replaced
   it; RFC 7752 is obsolete and RFC 9552 replaced it. RFC 8682 is a pseudorandom number
   generator rather than an NTP profile; RFC 8940 is EAP Session-Id rather than structured
   fields; RFC 6691 is TCP MSS rather than a syslog transport; RFC 7458 is EAP over Wi-Fi
   rather than delegated provisioning; and RFC 8059 is LISP multicast rather than EPP fees,
   which are RFC 8748.
3. Section numbers are cited only where the section was read in full. A scaffold that invents
   a citation is worse than one that cites the document alone.

## The portfolio

TABLE

## Investigated and deliberately not built

| Candidate | Nearest live project | Verified | Why not |
|---|---|---|---|
| mDNS and DNS-SD responder | zeroconf 0.153.0 | 2026-10-31 | maintained owner of the multicast link; the unicast successor is dnssd-srp |
| SSDP discovery | ssdp 1.3.1 and ssdpy 0.4.1 | 2025-09-06 | covered twice; SSDP is a transport inside devicespy |
| UPnP control point | async-upnp-client 0.49.0 | 2026-10-11 | the reference client; devicespy is the device side nobody has |
| QUIC and HTTP/3 client | aioquic 1.7.2 and qh3 2.1.0 | 2026-11-02 | two healthy implementations; h3tunnel is the layer above them |
| DNSSEC validation | namespy, local | 2026-11-03 | owned as a roadmap row there, not as a second repository |
| COSE and CWT | cwt 3.3.0 | 2026-07-16 | active; adding RFC 9964 ML-DSA support there beats starting a rival |
| CDDL validation | pycddl 0.6.4 | 2025-06-11 | maintained by the cbor2 author |
| Plain NTP client | pyntp 0.1.1 | 2026-03-20 | unauthenticated but alive; ntspy is scoped to NTS |
| EPP core and transport | pyepp 0.6.1 | 2026-11-05 | shipped the day of this survey and covers RFC 5730 to 5734 with client certificates |
| X.509 parsing plus hand-rolled crypto | x509kit 0.10.0 | 2026-10-11 | already maximal, and its own RSA, ECDSA and AES in Python is a risk not a feature; pkix is scoped to validation |
| JMAP, RADIUS, NETCONF, CoAP, ACME | jmapc, pyrad, ncclient, aiocoap, acme | 2026-10-29 | each has a maintained owner |

## Names, and why none of them are the obvious one

PyPI squatting policy can block a name one edit away from a live project, and losing a name
after people install it costs more than choosing a distinctive one at the start.

| Slot | Chosen | Rejected |
|---|---|---|
| DHCP | leasespy | dhcpsy beside live pydhcp; dhcpy is a Kea control client; dhcpkit taken and dead |
| MASQUE | h3tunnel | masq3 and pymasque, beside the live pymasque |
| UPnP device | devicespy | upnp-device beside upnpclient and async-upnp-client |
| Structured fields | sfields | structured-fields beside the delisted structured-field-values, whose name stays reserved |
| ACS | cwmp | tr069 is a registered-but-empty squat |
| Certificate transparency | ctspy | ctlog, ctkit, sct and transparency all belong to unrelated projects |
| X.509 validation | pkix | x509 is a 2017 squat and x509kit is a live project with different scope |
| BGP codec | pathattr | bgpkit collides with a tool brand, bgpspy with a service brand, ribkit is vague |
| EPP | eppclient or domainreg | epp dead since 2017, python-epp an empty squat, pyepp live since this survey |

## Shared conventions

Python 3.12 and up; zero runtime dependencies unless a README justifies one; capability named
optional extras; py.typed; sans-IO core with an asyncio surface; ruff at 100 columns with the
full select list; mypy; coverage ratcheted per milestone; four CI gates on Linux and Windows
including a run with asserts stripped; and the docs trio: ROADMAP.md as the obligation matrix,
docs/strategy.md for why the rows are ordered, docs/rfc-map.md for where every citation came
from.

## Honesty rule, inherited from namespy

A matrix row reads [ok] only when a named test would fail if the behaviour regressed. Every
generated stub raises NotImplementedError, and tests/test_public_api.py fails if a stub ever
starts returning a plausible empty value instead.
