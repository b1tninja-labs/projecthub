"""Portfolio position data: tier, wave, hard dependencies and the sequencing rules docs/plan.md states.

Moved verbatim out of the generator (shells/generate.py). This is a transcription of PORTFOLIO.md sections 4, 6 and 7, and
PORTFOLIO.md stays the authority: change it there first, then here.
"""

from __future__ import annotations

# name -> (tier, wave, hard dependencies). Transcribed from PORTFOLIO.md
# sections 4 and 6; an arrow means "cannot deliver behaviour without".
POSITION = {
    # Tier 1, wave 1: the chokepoint itself.
    "rpkit": (1, 1, ("asn1core", "pkix", "signeddata", "sigwire")),
    # Tier 2, wave 2: the certificate cluster.
    "ctspy": (2, 2, ("asn1core",)),
    "pkix": (2, 2, ("asn1core",)),
    "revokekit": (2, 2, ("asn1core", "pkix")),
    # Tier 2, wave 2: the routing-security cluster, anchored on rpkit.
    "rtrkit": (2, 2, ("rpkit",)),
    "routevalid": (2, 2, ("rpkit", "rtrkit", "pathattr")),
    # Tier 2, wave 4: naming and address allocation.
    "dnssd-srp": (2, 4, ("namespy",)),
    "leasespy": (2, 4, ()),
    # Tier 3, wave 3: the routing-operations story.
    "asnkit": (3, 3, ("rpkit", "rtrkit", "pathattr")),
    "bgpfeed": (3, 3, ("asnkit", "pathattr")),
    # Tier 3: solid gaps. rpslkit, bmpkit and pathattr sit in wave 3 with the routing story; lgc, turnkit, ohttp,
    # h3tunnel, sfields and syslogkit are in no wave of PORTFOLIO.md section 7 but "the rest", which is wave 5.
    "rpslkit": (3, 3, ()),
    "bmpkit": (3, 3, ("pathattr",)),
    "pathattr": (3, 3, ()),
    "lgc": (3, 5, ()),
    "turnkit": (3, 5, ()),
    "ohttp": (3, 5, ()),
    "h3tunnel": (3, 5, ()),
    "sfields": (3, 5, ()),
    "syslogkit": (3, 5, ()),
    # Tier 3, wave 2: certificate-shaped, so gated on the substrate.
    "signeddata": (3, 2, ("asn1core",)),
    # Tier 4, wave 5: the rest, in tier order, one at a time.
    "ntspy": (4, 5, ()),
    "tacacs": (4, 5, ()),
    "pgpenv": (4, 5, ("asn1core",)),
    "enrollkit": (4, 5, ("asn1core", "pkix", "signeddata")),
    "shaken": (4, 5, ("asn1core", "pkix")),
    "srtpkit": (4, 5, ()),
    "cwmp": (4, 5, ("devicespy",)),
    "devicespy": (4, 5, ()),
    # Tier 4, wave 5: created against the no-new-shells rule on 2026-11-07, which is why
    # it is hold-tier despite a real gap: pysmi 2.0.0 occupies the job and is current.
    "miblang": (4, 5, ("asn1core", "primarysource")),
}


TIER_NOTE = {
    1: "Tier 1 is work on now: its absence hurts, and finishing it makes several "
    "other projects cheaper.",
    2: "Tier 2 is next: high value, and each entry needs something from Tier 1 "
    "before it can produce behaviour.",
    3: "Tier 3 is planned: a solid gap with no urgency, and better work once "
    "Tier 1 exists.",
    4: "Tier 4 is hold: a real protocol with a weaker case for now, whether "
    "through adequate incumbents, a narrow audience or a long dependency chain.",
}


WAVE_NOTE = {
    # A registered project outside the waves (a tool, a reference library). No generated shell is in wave 0.
    0: "unsequenced: it is in no wave of PORTFOLIO.md section 7, so it is worked on when someone chooses it",
    1: "the chokepoint and the ambient-trust hole",
    2: "the first real answer",
    3: "the routing-operations story",
    4: "naming and address allocation",
    5: "the rest in tier order, one at a time",
}


# One sentence per edge, on why the arrow is a hard dependency rather than a
# preference. Keyed by (project, dependency).
DEPENDS_WHY = {
    ("rpkit", "asn1core"): "every RPKI signed object rpkit fetches is a CMS structure "
    "that has to be decoded from the bytes it arrived as before "
    "its signature means anything, and that codec is asn1core's job",
    (
        "ctspy",
        "asn1core",
    ): "a signed certificate timestamp and a Merkle leaf are verified "
    "over encoded bytes, so an inclusion proof is only as good as "
    "a codec that keeps the original bytes instead of re-encoding "
    "them",
    (
        "pkix",
        "asn1core",
    ): "RFC 5280 section 6 validates paths over certificates and CRLs, "
    "and RFC 5280 section 4.1.1.3 checks the CA signature over the "
    "DER encoding of the TBSCertificate, so validation cannot be "
    "written above a codec that re-encodes what it parsed",
    (
        "revokekit",
        "asn1core",
    ): "an OCSP response is a signed DER object and a CRL is DER "
    "all the way down, so a revocation answer that cannot be "
    "re-derived from the bytes it arrived in is not an answer",
    ("revokekit", "pkix"): "deciding whether a responder may sign for a CA is path "
    "validation against the same trust anchors pkix already "
    "resolves, and a second validator here would only disagree "
    "with the first",
    ("rtrkit", "rpkit"): "RTR announces what has been validated, so the serial "
    "discipline of a cache server is worth nothing unless the "
    "assertions underneath it came from rpkit's repository fetch "
    "and signed-object checks",
    ("routevalid", "rpkit"): "a validity verdict is an RPKI assertion plus its anchor, "
    "and rpkit is the only thing in this portfolio that fetches "
    "and validates the repository content",
    ("routevalid", "rtrkit"): "what a router was actually told to believe is the RTR "
    "stream, and without it there is no live state to join a "
    "RIB dump or an observed AS path against",
    (
        "dnssd-srp",
        "namespy",
    ): "SRP is unicast DNS with authenticated updates and pushed "
    "record changes, so the message codec, the iterative "
    "resolver and the DNSSEC, DoT and DoH legs are namespy's "
    "and are shared rather than rebuilt",
    ("asnkit", "rpkit"): "the question asnkit answers is which AS, on which date, and "
    "what did RPKI say about it, and the last clause is an rpkit "
    "assertion rather than anything derivable from a RIB dump",
    ("asnkit", "rtrkit"): "validation state is joined at query time from the RTR feed "
    "rtrkit holds, because a prefix's history with no state "
    "announced for it is a lookup with no answer attached",
    ("bgpfeed", "asnkit"): "a feed is a stream of UPDATE records until its prefixes "
    "resolve to an AS with a date and a validation state, which "
    "is the lookup asnkit owns",
    ("signeddata", "asn1core"): "CMS is a DER container end to end - SignedData, "
    "EnvelopedData and AuthenticatedData, their attribute "
    "sets, and the issuer and serial that identifies a signer "
    "- so the encoding layer underneath is not this project's",
    (
        "enrollkit",
        "asn1core",
    ): "CMP messages, CRMF requests and PKCS#10 are all DER, and a "
    "request whose encoding changes on the way to the CA is an "
    "enrollment for a different key",
    (
        "enrollkit",
        "pkix",
    ): "an issued certificate is only useful once it validates to a "
    "trust anchor, and that verdict belongs to pkix rather than "
    "being restated at the end of an enrollment flow",
    (
        "pgpenv",
        "asn1core",
    ): "OpenPGP bindings reach X.509 identities through encoded key "
    "material, and pgpenv is scoped to packets and keys, so "
    "reading the DER under a key belongs to asn1core",
    ("shaken", "asn1core"): "a PASSporT is only as trustworthy as the certificate the "
    "originating provider publishes at its x5u, and parsing that "
    "certificate is asn1core's job, not shaken's",
    (
        "srtpkit",
        "asn1core",
    ): "RFC 5764 key establishment presents certificates, and SRTP "
    "keys are only as strong as the identity that negotiated "
    "them, so certificate parsing stays in asn1core",
    (
        "miblang",
        "asn1core",
    ): "an SMIv2 module's SYNTAX and DEFAULT clauses are ASN.1 with a "
    "tag-numbering scheme of its own, and a constraint that "
    "asn1core cannot check would silently widen what a MIB "
    "accepts, so the codec stays asn1core's and miblang keeps "
    "the grammar",
    (
        "miblang",
        "primarysource",
    ): "the modules this reads are normative artifacts published as "
    "text alongside a Recommendation, and an OID answer is only "
    "as good as the edition it was parsed from, so fetching, "
    "digesting and pinning that corpus is primarysource's itu collection's job rather "
    "than a second copy of it per project",
    # Edges decided by PORTFOLIO.md section 9 item 15 (2026-11-13).
    ("rpkit", "pkix"): "RPKI resource certificates are X.509, and path building and "
    "extension decoding are pkix's job, with the RFC 3779 inclusion checks done "
    "during its path validation",
    ("rpkit", "signeddata"): "every RPKI signed object is a CMS SignedData, and CMS "
    "belongs to signeddata, so rpkit composes it rather than keeping a second reader",
    ("rpkit", "sigwire"): "signature verification goes through sigwire[verify], the "
    "strict seam over cryptography that replaced the retired sigprim",
    ("routevalid", "pathattr"): "an origin is read from an AS_PATH, and decoding the "
    "path attributes, AS4 and AS_TRANS included, is pathattr's job",
    ("asnkit", "pathattr"): "MRT RIB and UPDATE records carry BGP path attributes, "
    "which pathattr decodes",
    ("bgpfeed", "pathattr"): "a feed's UPDATE messages carry path attributes, which "
    "pathattr decodes rather than each consumer keeping its own",
    ("bmpkit", "pathattr"): "a BMP Route Monitoring message wraps a BGP UPDATE whose "
    "path attributes pathattr decodes",
    (
        "enrollkit",
        "signeddata",
    ): "enrollment flows exchange CMS structures, and CMS belongs to signeddata "
    "rather than being written again here",
    ("shaken", "pkix"): "a SHAKEN certificate is accepted only once it validates to a "
    "trust anchor, which is pkix's path validation, and its extensions are "
    "decoded there too",
    ("cwmp", "devicespy"): "both parse XML written by a stranger, and the one hardened "
    "XML reader (xmlsafe) lives in devicespy, which cwmp uses by sibling path",
}


# Edges the portfolio plan records as candidate rather than load-bearing.
CANDIDATE_EDGES = {("dnssd-srp", "namespy")}


CANDIDATE_NOTE = (
    "This edge is recorded as a candidate: the message layer and the resolver are shared "
    "with namespy, and the stateful half - SRP updates, registrar checks and pushed "
    "record changes - is what is built here."
)


RULES = [
    "At most two projects in the portfolio carry real effort at once. A third is not "
    "started, and no obligation is taken here, while those two are open.",
    "No new shell is created while any Tier 1 project still has an unimplemented "
    "obligation. The READMEs of the @SHELLS@ generated shells are already the maintenance "
    "liability, "
    "and the failure mode of this portfolio is prose outrunning code rather than "
    "under-building.",
    "A project freezes to Tier 4 when its first release finds no external user, and a "
    "shell is retired publicly when a competitor ships what it promised rather than left "
    "to rot - the retirement is announced, not a README quietly left to go stale.",
]
