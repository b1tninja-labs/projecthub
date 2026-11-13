"""Generator data for the certificate-shaped shells: PKIX, CT, revocation, enrollment, CMS, OpenPGP, STIR/SHAKEN and SRTP keying.

Moved verbatim out of the generator (shells/generate.py), which keeps the logic. PROJECTS lists one dict per project in the
order generate.PROJECT_ORDER expects; OPEN_QUESTIONS feeds section 5 of each generated ROADMAP.md.
Editing a value here changes what the generator would write; run `projecthub shells generate --check` to see
which files that touches before anything is written.
"""

from __future__ import annotations

from typing import Any

PROJECTS: list[dict[str, Any]] = [
    {
        "name": "signeddata",
        "pkg": "signeddata",
        "title": "signeddata -- CMS and S-MIME that can actually verify",
        "desc": "Cryptographic Message Syntax: SignedData, EnvelopedData and AuthenticatedData, "
        "countersignatures, ESS attributes, signed properties and RFC 3161 timestamps",
        "kw": [
            "cms",
            "pkcs7",
            "smime",
            "signed-data",
            "timestamp",
            "rfc5652",
            "rfc3161",
            "ess",
        ],
        "why": (
            "CMS, the format people still call PKCS number 7, is everywhere: S-MIME mail, "
            "European e-invoicing, e-delivery envelopes, firmware manifests, contract "
            "signatures. cryptography ships a narrow PKCS7 writer, and the rest of the Python "
            "field is abandoned (smime 0.0.5, 2019-07-23), a name squat on an unrelated "
            "argument parser (pkcs7 0.1.2, 2017-03-23) or a bare ASN.1 toolkit with no CMS "
            "process rules (asn1crypto 1.5.1, 2022-03-15). Countersignatures, signed properties, "
            "ESS signing-certificate-v2 and timestamp tokens cannot be verified in Python "
            "today, which is why every project that needs them shells out to openssl smime.",
        ),
        "deps": [("asn1crypto", "1.5.1")],
        "deps_note": (
            "This project breaks the portfolio zero-dependency rule deliberately. DER is not "
            "something to re-derive per project, and a half-written ASN.1 parser inside a "
            "signature verifier is exactly the bug class this library exists to remove."
        ),
        "docs": [
            {
                "n": "5652",
                "status": "Internet Standard",
                "date": "2009-09",
                "why": "CMS: the content types and every attribute that rides on them",
            },
            {
                "n": "8551",
                "status": "Proposed Standard",
                "date": "2019-04",
                "why": "S-MIME version 4: how CMS travels in mail",
            },
            {
                "n": "2634",
                "status": "Proposed Standard",
                "date": "1999-06",
                "why": "Enhanced Security Services attributes",
            },
            {
                "n": "5035",
                "status": "Proposed Standard",
                "date": "2007-08",
                "why": "CertID algorithm agility, replacing the SHA-1 binding",
            },
            {
                "n": "3161",
                "status": "Proposed Standard",
                "date": "2001-08",
                "why": "TSP: timestamp requests and the token that outlives the signature",
            },
            {
                "n": "3370",
                "status": "Proposed Standard",
                "date": "2002-09",
                "why": "CMS algorithm bindings: AES-CBC, HMAC, RSA-OAEP",
            },
            {
                "n": "9044",
                "status": "Proposed Standard",
                "date": "2021-06",
                "why": "AES-GMAC with CMS",
            },
            {
                "n": "8702",
                "status": "Proposed Standard",
                "date": "2020-01",
                "why": "SHAKE one-way functions in CMS",
            },
        ],
        "errata": [
            "RFC 3161 carries 3 verified errata and is updated by RFC 5816, which adds the SHA-2 identifiers everyone actually uses.",
            "RFC 5035 carries 1 verified erratum and updates RFC 2634: a CertID built the 1999 way is SHA-1 bound.",
            "RFC 5652 carries 2 verified errata and is updated by RFC 8933 (algorithm identifier protection) and RFC 9629 (KEM recipients); RFC 8551 has no errata on file and is updated by RFC 9788 (header protection). Checked with rfclib on 2026-11-07.",
        ],
        "prior": [
            {
                "name": "cryptography",
                "version": "current",
                "date": "-",
                "note": "a PKCS7 writer with no countersignature, no ESS, no verify of arbitrary signers",
            },
            {
                "name": "smime",
                "version": "0.0.5",
                "date": "2019-07-23",
                "note": "abandoned before S-MIME v4",
            },
            {
                "name": "asn1crypto",
                "version": "1.5.1",
                "date": "2022-03-15",
                "note": "ASN.1 toolkit: definitions without process rules",
            },
            {
                "name": "pkcs7, cms",
                "version": "0.1.2, 0.0.2",
                "date": "2017-03-23",
                "note": "unrelated projects holding the obvious names",
            },
        ],
        "gaps": [
            "verify arbitrary SignedData with signed properties, message digest and ESS CertID checks",
            "countersignatures, which is where long term validity actually comes from",
            "timestamp token creation and validation as a first-class path (RFC 3161)",
            "EnvelopedData decryption across key transport and password based recipient infos",
            "an algorithm policy object so a verifier can refuse SHA-1 instead of accepting it silently",
        ],
        "nongoals": [
            "an X.509 or PKI stack: certificate path building stays in cryptography",
            "a mail client: this produces and consumes objects, RFC 8551 wrapping is the boundary",
            "business rules for any particular e-invoicing or e-delivery profile",
        ],
        "mods": [
            (
                "cms",
                "content info, signed data and the digested and encrypted variants (RFC 5652)",
            ),
            (
                "signer",
                "SignerInfo, signed and unsigned attributes, countersignature (RFC 5652)",
            ),
            (
                "ess",
                "signing-certificate and CertID with algorithm agility (RFC 2634, RFC 5035)",
            ),
            (
                "verify",
                "digest and signature checks plus algorithm policy enforcement (RFC 3370)",
            ),
            (
                "envelope",
                "EnvelopedData and AuthenticatedData recipient handling (RFC 5652, RFC 9044)",
            ),
            (
                "tsp",
                "time stamp request and response, token validation (RFC 3161, RFC 5816)",
            ),
            ("smime", "the mail facing wrapper and its protection rules (RFC 8551)"),
            ("policy", "the explicit allow list every verifier consults"),
        ],
        "api": [
            (
                "cms",
                "def",
                "load",
                "raw: bytes",
                "ContentInfo",
                "RFC 5652 sec. 3",
                "M1",
            ),
            (
                "cms",
                "def",
                "dump",
                "info: ContentInfo",
                "bytes",
                "RFC 5652 sec. 3",
                "M1",
            ),
            (
                "signer",
                "def",
                "build_signer_info",
                "key: SigningKey, certs: list, attrs: list",
                "SignerInfo",
                "RFC 5652 sec. 5.3",
                "M3",
            ),
            (
                "ess",
                "def",
                "signing_certificate_v2",
                "cert: Certificate",
                "Attribute",
                "RFC 5035 sec. 3",
                "M2",
            ),
            (
                "verify",
                "def",
                "verify",
                "info: ContentInfo, content: bytes, store: TrustStore",
                "Verification",
                "RFC 5652 sec. 5.6",
                "M2",
            ),
            (
                "verify",
                "def",
                "countersign",
                "info: ContentInfo, signer: int",
                "ContentInfo",
                "RFC 5652 sec. 11.4",
                "M3",
            ),
            (
                "envelope",
                "def",
                "decrypt",
                "info: ContentInfo, key: PrivateKey",
                "bytes",
                "RFC 5652 sec. 6",
                "M5",
            ),
            (
                "tsp",
                "def",
                "build_request",
                "message_imprint: bytes, nonce: int",
                "bytes",
                "RFC 3161 sec. 2.4.1",
                "M4",
            ),
            (
                "tsp",
                "def",
                "verify_token",
                "token: bytes, request: bytes, roots: list",
                "DateTime",
                "RFC 3161 sec. 2.4.2",
                "M4",
            ),
            (
                "policy",
                "def",
                "refuse_weak",
                "algorithms: list",
                "Policy",
                "design",
                "M2",
            ),
        ],
        "steps": [
            "M1 load and dump for SignedData with byte-exact round trip on captured samples",
            "M2 verify: digest, signature, ESS CertID, message digest attribute",
            "M3 signed properties, signing time and countersignature",
            "M4 timestamp request, response and token chain validation",
            "M5 EnvelopedData and AuthenticatedData, then the S-MIME wrapper",
            "M6 publication: 0.1.0 with an interop matrix against openssl smime",
        ],
        "extras": {},
        "cli": "signeddata verify doc.p7s --content doc.pdf --policy no-sha1",
    },
    {
        "name": "pgpenv",
        "pkg": "pgpenv",
        "title": "pgpenv -- OpenPGP messages and keys to RFC 9580",
        "desc": "OpenPGP packet layer, v4 and v6 keys, encryption and signatures, AEAD "
        "encrypted sessions and the armored stream that carries them",
        "kw": ["openpgp", "pgp", "gpg", "encryption", "signatures", "rfc9580", "aead"],
        "why": (
            "RFC 9580 replaced RFC 4880 in July 2024 with version 6 keys, a revised algorithm "
            "policy and new key material handling. Python's answer has not moved since before "
            "that document existed: pgpy 0.6.0 was released 2022-11-24, and the openpgp name "
            "belongs to a 2005 package. Everything current is C or Rust (Sequoia, RNP, GnuPG), "
            "so a Python service either runs a subprocess with a keyring it does not want or "
            "ignores OpenPGP. pgpenv is scoped to the message and key layer: correct packets, "
            "honest verification, no daemon.",
        ),
        "deps": [("cryptography", "42.0")],
        "deps_note": (
            "OpenPGP without an AEAD, HKDF and EdDSA backend means writing those primitives, "
            "which is a worse project than this one. One pinned dependency with no ceiling."
        ),
        "docs": [
            {
                "n": "9580",
                "status": "Proposed Standard",
                "date": "2024-07",
                "why": "the current format: packets, v6 keys, algorithm policy, AEAD",
            },
            {
                "n": "4880",
                "status": "Obsolete",
                "date": "2007-11",
                "why": "the v4 wire a decade of material is still wearing; readable, never writable",
            },
            {
                "n": "9980",
                "status": "Proposed Standard",
                "date": "2026-06",
                "why": "post-quantum public key algorithms layered on RFC 9580; read before fixing the algorithm policy",
            },
        ],
        "errata": [
            "RFC 9580 carries 7 verified errata. Read them before writing a single parser, because several concern key material encoding.",
            "RFC 9580 obsoletes RFC 4880, RFC 5581 and RFC 6637: recipes copied from those documents may describe behaviour it removed.",
            "RFC 9980 extends RFC 9580 with ML-KEM, ML-DSA and SLH-DSA; it has no verified errata (1 on file) as of 2026-11-07 (rfclib).",
        ],
        "prior": [
            {
                "name": "pgpy",
                "version": "0.6.0",
                "date": "2022-11-24",
                "note": "the incumbent, released before RFC 9580 existed",
            },
            {
                "name": "openpgp",
                "version": "0.2.3",
                "date": "2005-07-01",
                "note": "a squat on the obvious name",
            },
            {
                "name": "Sequoia, RNP, GnuPG",
                "version": "-",
                "date": "-",
                "note": "current and good, in C and Rust: subprocess or FFI from Python",
            },
        ],
        "gaps": [
            "version 6 keys and packets, plus a v4 reader for material that already exists",
            "AEAD encrypted sessions with the granularity rules the specification sets",
            "signature creation and verification that reports what it checked and what it refused",
            "key certifications and binding checks handled so the web of trust is not silently trusted",
            "armored streams with CRC-24 and line discipline that survive truncated input",
        ],
        "nongoals": [
            "an agent, daemon or keyring service",
            "a mail user agent or any MIME handling",
            "compatibility with pre-standard PGP wire formats",
            "a compliance claim: interoperability evidence ships with the release, not before it",
        ],
        "mods": [
            ("packet", "packet framing, tags, old and new format headers (RFC 9580)"),
            ("key", "public keys, v4 and v6 fingerprints, subkeys (RFC 9580)"),
            (
                "signature",
                "signature packets and the hashing rules that bind them (RFC 9580)",
            ),
            (
                "session",
                "symmetric and public key session key handling, AEAD granularity (RFC 9580)",
            ),
            (
                "message",
                "literals, compression and the nesting that makes MIME work safe",
            ),
            ("armor", "radix64 with CRC-24 headers and trailers (RFC 9580)"),
            (
                "crypto",
                "the seam onto cryptography, pinned to an allowed algorithm list",
            ),
            (
                "limits",
                "bounds on packet size, nesting depth and key count on every parse path",
            ),
        ],
        "api": [
            (
                "packet",
                "def",
                "iter_packets",
                "raw: IO",
                "Iterator",
                "RFC 9580 sec. 4",
                "M1",
            ),
            (
                "packet",
                "def",
                "encode_packet",
                "packet: Packet",
                "bytes",
                "RFC 9580 sec. 4.2",
                "M1",
            ),
            ("key", "def", "load_key", "raw: bytes", "Key", "RFC 9580 sec. 5.5", "M2"),
            (
                "key",
                "def",
                "fingerprint",
                "key: Key",
                "bytes",
                "RFC 9580 sec. 5.5.4",
                "M2",
            ),
            (
                "signature",
                "def",
                "sign",
                "data: bytes, key: Key, policy: Policy",
                "Signature",
                "RFC 9580 sec. 5.2",
                "M3",
            ),
            (
                "signature",
                "def",
                "verify",
                "data: bytes, sig: Signature, key: Key",
                "Verification",
                "RFC 9580 sec. 5.2",
                "M3",
            ),
            (
                "session",
                "def",
                "encrypt",
                "data: IO, keys: list, aead: Aead",
                "Message",
                "RFC 9580",
                "M4",
            ),
            (
                "session",
                "def",
                "decrypt",
                "msg: Message, key: Key",
                "bytes",
                "RFC 9580",
                "M4",
            ),
            (
                "armor",
                "def",
                "encode",
                "packets: bytes, headers: dict",
                "str",
                "RFC 9580 sec. 6.2",
                "M1",
            ),
            ("armor", "def", "decode", "text: str", "bytes", "RFC 9580 sec. 6.2", "M1"),
            ("limits", "def", "check", "plan: ParsePlan", "None", "design", "M1"),
        ],
        "steps": [
            "M1 packet framing and armor with adversarial input tests",
            "M2 v6 key parsing and fingerprints, v4 read path included",
            "M3 signature create and verify against RFC 9580 test material",
            "M4 classic and AEAD session encryption",
            "M5 certifications and binding checks, then the policy object",
            "M6 publication: 0.1.0 with an interop matrix against Sequoia and GnuPG",
        ],
        "extras": {},
        "cli": "pgpenv sign file --key env:PGP_KEY --armor",
    },
    {
        "name": "shaken",
        "pkg": "shaken",
        "title": "shaken -- PASSporT and SIP Identity for telephone traffic",
        "desc": "Create and verify PASSporT claims and the SIP Identity header: attestation "
        "levels, credential retrieval by x5u and x5c, and verification policy",
        "kw": [
            "stir",
            "shaken",
            "passport",
            "sip",
            "voice",
            "telephony",
            "rfc8225",
            "rfc8588",
        ],
        "why": (
            "Robocall mitigation runs on PASSporT: a signed assertion about the originating "
            "number, carried in a SIP Identity header and verified against a certificate "
            "published by the originating provider. RFC 8225 defines the token, RFC 8588 the "
            "SHAKEN attestation levels, RFC 8224 the header. Python has none of it: stir is an "
            "unrelated package from 2018, passport is a 2014 configuration tool, and shaken "
            "and stirshaken are free names as of 2026-11-05. Any Python SIP service today ships "
            "unsigned traffic, which increasingly means it is blocked.",
        ),
        "docs": [
            {
                "n": "8225",
                "status": "Proposed Standard",
                "date": "2018-02",
                "why": "PASSporT: the compact token, its claims and canonicalisation",
            },
            {
                "n": "8224",
                "status": "Proposed Standard",
                "date": "2018-02",
                "why": "the SIP Identity header and verification behaviour",
            },
            {
                "n": "8588",
                "status": "Proposed Standard",
                "date": "2019-05",
                "why": "the SHAKEN extension: attestation levels and origin identifier",
            },
            {
                "n": "9475",
                "status": "Proposed Standard",
                "date": "2023-12",
                "why": "messaging use cases and extensions beyond voice",
            },
            {
                "label": "ATIS-1000074 (SHAKEN)",
                "status": "ATIS / SIP Forum",
                "date": "-",
                "why": "the deployment profile that fixes policy, caching and headers",
            },
        ],
        "errata": [
            "RFC 8224 carries 2 verified errata and is updated by RFC 8946; RFC 8588 carries 1 verified erratum.",
            "RFC 8225 has 2 errata on file and none verified as of 2026-11-07 (rfclib): 5392 is Held for Document Update and concerns the iat claim, 5985 is Reported and editorial.",
            "Policy questions (what to do with a failed verification) belong to ATIS, not the RFCs. Say which document decided.",
        ],
        "prior": [
            {
                "name": "stir",
                "version": "0.0.3",
                "date": "2018-06-20",
                "note": "an unrelated microservice merge tool",
            },
            {
                "name": "passport",
                "version": "0.1.1",
                "date": "2014-07-22",
                "note": "a Consul configuration manager",
            },
            {
                "name": "shaken, stirshaken",
                "version": "-",
                "date": "-",
                "note": "absent from PyPI (2026-11-05)",
            },
        ],
        "gaps": [
            "create and verify a PASSporT with the canonicalisation the specification demands",
            "build and parse the Identity header including info, ppt and alg parameters",
            "credential retrieval by x5u with a pinned CA store, and x5c inline validation",
            "attestation level handling (A, B, C) as explicit policy rather than a boolean",
            "replay and cache control: iat and txn discipline without inventing a cache",
        ],
        "nongoals": [
            "a SIP stack or transport: this handles a header and a token",
            "an STI-VI registry client or policy database service",
            "number routing, carrier relationships or any business logic about who may sign",
        ],
        "mods": [
            (
                "passport",
                "token construction, canonicalisation and signature (RFC 8225)",
            ),
            (
                "claims",
                "the registered claims and the SHAKEN extension set (RFC 8225, RFC 8588)",
            ),
            ("identity", "the SIP header field build and parse (RFC 8224)"),
            ("credentials", "x5u retrieval with bounds, x5c validation, CA pinning"),
            (
                "attest",
                "attestation levels and the policy a verifier applies (RFC 8588)",
            ),
            ("verify", "the outcome object: what passed, what was skipped and why"),
            ("messaging", "the extensions for non-voice communications (RFC 9475)"),
        ],
        "api": [
            (
                "passport",
                "def",
                "create",
                "claims: Claims, key: SigningKey, certs: list",
                "str",
                "RFC 8225",
                "M1",
            ),
            (
                "passport",
                "def",
                "verify",
                "token: str, store: TrustStore",
                "Verification",
                "RFC 8225",
                "M1",
            ),
            (
                "claims",
                "def",
                "shaken_claims",
                "orig: str, dest: str, level: str",
                "Claims",
                "RFC 8588",
                "M1",
            ),
            (
                "identity",
                "def",
                "build_header",
                "token: str, info: str",
                "str",
                "RFC 8224 sec. 4",
                "M2",
            ),
            (
                "identity",
                "def",
                "parse_header",
                "value: str",
                "IdentityHeader",
                "RFC 8224 sec. 4",
                "M2",
            ),
            (
                "credentials",
                "def",
                "resolve",
                "x5u: Optional[str], x5c: Optional[list], store: TrustStore",
                "Certificate",
                "RFC 8225 sec. 4.3",
                "M3",
            ),
            (
                "attest",
                "def",
                "policy_for",
                "level: str",
                "Policy",
                "RFC 8588 sec. 4",
                "M4",
            ),
            (
                "verify",
                "def",
                "verify_request",
                "headers: Headers, store: TrustStore",
                "Verification",
                "RFC 8224 sec. 6.2",
                "M4",
            ),
        ],
        "steps": [
            "M1 PASSporT create and verify with canonicalisation vectors",
            "M2 Identity header build and parse",
            "M3 credential resolution with x5u bounds and CA pinning",
            "M4 attestation policy and the verification outcome object",
            "M5 interop against a captured carrier trace",
            "M6 publication: 0.1.0",
        ],
        "extras": {"crypto": [("cryptography", "42.0")]},
        "cli": "shaken verify --request capture.txt --ca ./sti-ca.pem",
    },
    {
        "name": "srtpkit",
        "pkg": "srtpkit",
        "title": "srtpkit -- SRTP and SRTCP in pure Python",
        "desc": "SRTP protection and recovery, key derivation, replay databases, the AES-GCM "
        "profile, SRTCP and DTLS-SRTP key export",
        "kw": ["srtp", "rtp", "rtcp", "media", "aead", "rfc3711", "rfc7714", "rfc5764"],
        "why": (
            "RTP in Python has a good async owner in aiortp 0.8.0 (2026-10-18), but securing it "
            "is a separate protocol that Python does not have. The WebRTC stacks bundle a "
            "native SRTP, so a Python media service either depends on that or transmits in the "
            "clear. RFC 3711 defines the transform, RFC 7714 the AEAD profile that should be "
            "used for new deployments, and RFC 5764 the DTLS key establishment that a modern "
            "signalling path expects. srtpkit is deliberately the transform and the key "
            "handling, not a media stack.",
        ),
        "docs": [
            {
                "n": "3711",
                "status": "Proposed Standard",
                "date": "2004-03",
                "why": "SRTP: transforms, the index and ROC, replay and key derivation",
            },
            {
                "n": "7714",
                "status": "Proposed Standard",
                "date": "2015-12",
                "why": "AES-GCM authenticated encryption in SRTP",
            },
            {
                "n": "5764",
                "status": "Proposed Standard",
                "date": "2010-05",
                "why": "DTLS handshake to establish SRTP and SRTCP keys",
            },
            {
                "n": "5506",
                "status": "Proposed Standard",
                "date": "2009-04",
                "why": "reduced-size RTCP, which updates RFC 3711",
            },
            {
                "n": "6904",
                "status": "Proposed Standard",
                "date": "2013-04",
                "why": "encryption of RTP header extensions, which updates RFC 3711",
            },
            {
                "n": "9335",
                "status": "Proposed Standard",
                "date": "2023-01",
                "why": "complete encryption of header extensions and CSRCs, which updates RFC 3711",
            },
        ],
        "errata": [
            "RFC 5764 carries 3 verified errata and is updated by RFC 7983 and RFC 9443, the latter covering multiplexing when QUIC joins the picture.",
            "RFC 7714 carries 2 verified errata.",
            "RFC 3711 has 6 errata on file with none verified, and is updated by RFC 5506, RFC 6904 and RFC 9335. Read all three before implementing the transform.",
        ],
        "prior": [
            {
                "name": "aiortp",
                "version": "0.8.0",
                "date": "2026-10-18",
                "note": "asyncio RTP and RTCP; the transport this sits under, not the security layer",
            },
            {
                "name": "aiortc",
                "version": "current",
                "date": "-",
                "note": "bundles a native SRTP implementation inside a WebRTC stack",
            },
            {
                "name": "srtp, srtpkit",
                "version": "-",
                "date": "-",
                "note": "absent from PyPI (2026-11-05)",
            },
        ],
        "gaps": [
            "protection and recovery with correct sequence number rollover handling, the bug that corrupts long calls",
            "replay databases with the window and options the specification allows, including index based",
            "the SRTP key derivation function and the session parameter set",
            "the AES-GCM profile per RFC 7714, which is what a new deployment should negotiate",
            "SRTCP with the E-bit and SRTCP index, plus key export from a DTLS handshake",
        ],
        "nongoals": [
            "codecs, jitter buffers or any media handling",
            "RTP transport, which aiortp already does properly",
            "the legacy transforms nobody should negotiate, kept out of the default policy",
        ],
        "mods": [
            (
                "params",
                "session parameters: cipher, auth, key and salt lengths (RFC 3711)",
            ),
            ("kdf", "the SRTP key derivation function (RFC 3711)"),
            (
                "transform",
                "encryption and authentication, AES-GCM profile (RFC 3711, RFC 7714)",
            ),
            ("context", "stream and session context, rollover estimate and index"),
            ("replay", "window and bit vector, index based variant (RFC 3711)"),
            ("srtcp", "control packet protection and the E bit (RFC 3711)"),
            ("mki", "master key identifier handling (RFC 3711)"),
            ("dtls", "key export and the use_srtp negotiation (RFC 5764)"),
            (
                "policy",
                "what may be negotiated, so weak profiles are refused by default",
            ),
        ],
        "api": [
            (
                "params",
                "def",
                "select_profile",
                "offered: list, policy: Policy",
                "Profile",
                "RFC 3711, RFC 7714",
                "M1",
            ),
            (
                "kdf",
                "def",
                "derive",
                "master: MasterSecret, label: int, index: int, length: int",
                "bytes",
                "RFC 3711 sec. 4.3.1",
                "M1",
            ),
            (
                "context",
                "def",
                "for_stream",
                "ssrc: int, keys: SessionKeys",
                "StreamContext",
                "RFC 3711 sec. 3.2",
                "M1",
            ),
            (
                "transform",
                "def",
                "protect",
                "packet: bytes, ctx: StreamContext, aad: bytes",
                "bytes",
                "RFC 7714 sec. 8",
                "M2",
            ),
            (
                "transform",
                "def",
                "unprotect",
                "packet: bytes, ctx: StreamContext",
                "bytes",
                "RFC 7714 sec. 8",
                "M2",
            ),
            (
                "replay",
                "def",
                "check",
                "ctx: StreamContext, index: int",
                "bool",
                "RFC 3711 sec. 3.3.2",
                "M3",
            ),
            (
                "srtcp",
                "def",
                "protect_rtcp",
                "packet: bytes, ctx: Context",
                "bytes",
                "RFC 3711 sec. 3.4",
                "M4",
            ),
            (
                "mki",
                "def",
                "select_key",
                "identifier: bytes, keys: MasterKeys",
                "SessionKeys",
                "RFC 3711",
                "M4",
            ),
            (
                "dtls",
                "def",
                "export_keys",
                "tls_export: bytes, policy: Policy",
                "SessionKeys",
                "RFC 5764 sec. 4.2",
                "M5",
            ),
        ],
        "steps": [
            "M1 parameters, KDF and context against the worked examples in the specification",
            "M2 AES-GCM protect and unprotect, with vectors",
            "M3 rollover estimate and replay window",
            "M4 SRTCP and MKI",
            "M5 DTLS key export and profile negotiation policy",
            "M6 publication: 0.1.0 with a loopback call against a real RTP stack",
        ],
        "extras": {"crypto": [("cryptography", "42.0")], "rtp": [("aiortp", "0.8")]},
        "cli": "python -m srtpkit bench --profile AES_128_GCM",
    },
    {
        "name": "pkix",
        "pkg": "pkix",
        "title": "pkix -- RFC 5280 path validation, the part Python never had",
        "desc": "Certification path discovery and validation per RFC 5280: trust anchors, name "
        "constraints, policy mapping and constraints, CRL and OCSP status hooks, "
        "explicit result provenance",
        "kw": [
            "x509",
            "pkix",
            "path-validation",
            "trust-anchor",
            "name-constraints",
            "rfc5280",
        ],
        "why": (
            "Python can build a certificate and parse a certificate, and it cannot tell you "
            "whether a certificate is valid. cryptography has no path validator; the one "
            "serious implementation, certvalidator 0.11.1, has not been released since "
            "2016-07-29 and sits on oscrypto, itself dead since 2022-03-18. Everything else "
            "shells out to openssl verify and parses text. That is the odd gap in PKI: the "
            "specification is a single algorithm, RFC 5280 section 6, and it is the one "
            "nobody maintains in Python. Parsing is a solved problem and x509kit proved it "
            "again in 2026-10-11 by writing a whole toolkit including its own RSA, ECDSA and "
            "AES; pkix deliberately owns the opposite half: decisions, not bytes, and "
            "primitives borrowed from cryptography rather than re-invented.",
        ),
        "deps": [("cryptography", "42.0")],
        "deps_note": (
            "Signature verification is the one thing this project must not write itself. A "
            "path validator that re-implements RSA or ECDSA in Python turns a well studied "
            "algorithm problem into a timing side channel project, so primitives come from "
            "cryptography, which vendors audited OpenSSL."
        ),
        "docs": [
            {
                "n": "5280",
                "status": "Proposed Standard",
                "date": "2008-05",
                "why": "the profile and the section 6 path validation algorithm",
            },
            {
                "n": "5912",
                "status": "Informational",
                "date": "2010-06",
                "why": "the ASN.1 modules every PKIX structure is written in",
            },
            {
                "n": "6960",
                "status": "Proposed Standard",
                "date": "2013-06",
                "why": "OCSP status, one of the two revocation inputs the algorithm consults",
            },
        ],
        "errata": [
            "RFC 5280 carries 6 verified errata and is updated by RFC 6818, RFC 8398, RFC 8399, RFC 9549, RFC 9598, RFC 9608, RFC 9618, RFC 9925 and RFC 10007. The updates are not editorial: internationalised names and notBefore bounds changed the answer.",
            "RFC 5912 carries 8 verified errata.",
            "RFC 6960 carries 6 verified errata.",
        ],
        "prior": [
            {
                "name": "certvalidator",
                "version": "0.11.1",
                "date": "2016-07-29",
                "note": "the only Python path validator, dead for a decade on a dead crypto backend",
            },
            {
                "name": "cryptography",
                "version": "50.0.2",
                "date": "2026-09-30",
                "note": "builds and parses certificates, and verifies signatures, but builds no path and applies no policy",
            },
            {
                "name": "x509kit",
                "version": "0.10.0",
                "date": "2026-10-11",
                "note": "structures and its own crypto; validation policy is not the product",
            },
        ],
        "gaps": [
            "section 6.1 user data and 6.2 certification path validity checks, implemented as written",
            "name constraints including the subtle excluded-subtrees rule and the check-only-at-issuance exception",
            "policy mapping, policy constraints and explicit policy with anyPolicy handled correctly",
            "revocation as an injected interface, so CRL, OCSP, stapled and cached answers are all opinions you can test",
            "a result object that states which trust anchor, which chain, which policy and which revocation source decided it",
        ],
        "nongoals": [
            "a certificate parser: accept DER or a cryptography object and refuse anything else",
            "crypto primitives of any kind",
            "ACME, CMP or any issuance protocol: enrollkit asks pkix whether the result is acceptable",
            "the CA/Browser Forum baseline requirements, which are a policy profile built on top, not this algorithm",
        ],
        "mods": [
            (
                "anchors",
                "trust anchor selection, including name restrictions and certificates only sets (RFC 5280 sec. 6.1)",
            ),
            (
                "chain",
                "candidate path discovery against a store or a directory of intermediates",
            ),
            (
                "validity",
                "the section 6.2 processing: signature, validity, issuer, name constraints (RFC 5280)",
            ),
            (
                "names",
                "name forms, general names and constraint comparison, including internationalised forms",
            ),
            (
                "policy",
                "policy mappings, constraints and anyPolicy resolution (RFC 5280)",
            ),
            (
                "revocation",
                "the status interface plus an allow list of trusted decisions",
            ),
            ("result", "the outcome object with provenance for every decision"),
            ("store", "certificate, CRL and OCSP caches behind one interface"),
        ],
        "api": [
            (
                "anchors",
                "def",
                "load_anchor",
                "cert_der: bytes, name_restriction: Optional[str]",
                "TrustAnchor",
                "RFC 5280 sec. 6.1",
            ),
            (
                "chain",
                "def",
                "candidates",
                "leaf: Certificate, intermediates: Store, anchors: list",
                "list",
                "RFC 5280 sec. 6.1",
            ),
            (
                "validity",
                "def",
                "check_signature",
                "cert: Certificate, issuer: Certificate",
                "None",
                "RFC 5280",
            ),
            (
                "validity",
                "def",
                "check_validity",
                "cert: Certificate, at: datetime",
                "None",
                "RFC 5280",
            ),
            (
                "names",
                "def",
                "constraint_holds",
                "name: GeneralName, constraints: Constraints, at_issuance: bool",
                "bool",
                "RFC 5280",
            ),
            (
                "policy",
                "def",
                "resolve",
                "chain: list, expected: Optional[set]",
                "PolicyOutcome",
                "RFC 5280",
            ),
            (
                "revocation",
                "def",
                "status_of",
                "cert: Certificate, issuer: Certificate, at: datetime",
                "Status",
                "RFC 5280",
            ),
            (
                "result",
                "def",
                "validate",
                "leaf: Certificate, anchors: list, at: datetime, policy: ValidationPolicy",
                "Result",
                "RFC 5280 sec. 6",
            ),
            ("store", "def", "remember", "cert: Certificate", "None", "design"),
        ],
        "steps": [
            "M1 anchors and chain discovery with a fixture set of real chains",
            "M2 section 6.2 validity: signature, dates, issuer matching, critical extensions",
            "M3 name constraints including excluded subtrees and the issuance-time exception",
            "M4 policy mapping, constraints and anyPolicy",
            "M5 revocation interface plus CRL and OCSP backed implementations and cross-checks against openssl verify",
            "M6 publication: 0.1.0 with the conformance trace against NIST and vendor path test suites",
        ],
        "extras": {},
        "cli": "pkix verify leaf.pem --chain intermediate.pem --anchor root.pem --at 2026-11-05T00:00:00Z",
    },
    {
        "name": "revokekit",
        "pkg": "revokekit",
        "title": "revokekit -- OCSP and CRL clients that know the current specification",
        "desc": "Revocation in practice: OCSP request and response handling to RFC 9654 and RFC "
        "9919, CRL and delta CRL processing, stapled response checks and caching",
        "kw": [
            "ocsp",
            "crl",
            "revocation",
            "pkix",
            "stapling",
            "rfc6960",
            "rfc9654",
            "rfc9919",
        ],
        "why": (
            "OCSP is being rewritten while we watch. RFC 9654 obsoleted RFC 8954 in 2024-08 "
            "and redefined the Nonce extension, and RFC 9919 obsoleted RFC 5019 in July 2026 "
            "with a lightweight high volume profile that recommends SHA-256. cryptography can "
            "build and parse an OCSP request but ships no client, no caching, no responder "
            "chain rules and no nonce policy, and the only library that ever did revocation "
            "end to end died in 2016. Meanwhile the deployments that matter check stapled "
            "responses whose must-staple extension comes from RFC 7633. revokekit is the client "
            "and the policy, written against the documents as they now stand, and it is the "
            "revocation provider pkix consumes.",
        ),
        "docs": [
            {
                "n": "6960",
                "status": "Proposed Standard",
                "date": "2013-06",
                "why": "OCSP request, response and the basic confirmation types",
            },
            {
                "n": "9654",
                "status": "Proposed Standard",
                "date": "2024-08",
                "why": "the Nonce extension, obsoleting RFC 8954 and updating RFC 6960",
            },
            {
                "n": "9919",
                "status": "Proposed Standard",
                "date": "2026-07",
                "why": "the lightweight high volume profile, obsoleting RFC 5019",
            },
            {
                "n": "7633",
                "status": "Proposed Standard",
                "date": "2015-10",
                "why": "the TLS Feature extension behind OCSP must-staple",
            },
            {
                "n": "5940",
                "status": "Proposed Standard",
                "date": "2010-08",
                "why": "revocation choices carried inside CMS, the signed document case",
            },
            {
                "n": "5280",
                "status": "Proposed Standard",
                "date": "2008-05",
                "why": "the CRL and delta CRL profile and CRL validation the crl module follows",
            },
        ],
        "errata": [
            "RFC 6960 carries 6 verified errata and is updated by RFC 8954 and RFC 9654: read 9654 before implementing Nonce, because it supersedes the older constraint.",
            "RFC 9654 and RFC 9919 report no errata as of 2026-11-07 (rfclib), and both are new enough that implementations are the open question rather than corrections.",
            "RFC 5280 carries 6 verified errata and is updated by nine later RFCs (6818, 8398, 8399, 9549, 9598, 9608, 9618, 9925, 10007) as of 2026-11-07 (rfclib). RFC 8954 is obsoleted by RFC 9654 and is not a document to implement.",
            "RFC 9919 obsoletes RFC 5019. Recipes copied from 5019 describe the superseded profile.",
        ],
        "prior": [
            {
                "name": "cryptography.x509.ocsp",
                "version": "50.0.2",
                "date": "2026-09-30",
                "note": "request and response types, no client, no cache, no responder policy",
            },
            {
                "name": "certvalidator",
                "version": "0.11.1",
                "date": "2016-07-29",
                "note": "did OCSP and CRL checking; unmaintained for ten years",
            },
            {
                "name": "x509kit",
                "version": "0.10.0",
                "date": "2026-10-11",
                "note": "OCSP structures and signing, not a network client or a policy",
            },
        ],
        "gaps": [
            "an HTTP client with bounds: timeouts, response size caps, no redirect chasing, per-responder circuit breaking",
            "nonce handling that actually follows RFC 9654, including what to do when the responder omits it",
            "responder validation: delegated signing certificate, key purpose and OCSP no-check",
            "thisUpdate, nextUpdate and maxAge discipline with clock skew tolerance stated explicitly",
            "CRL and delta CRL fetching with issuer matching, and stapled response verification for TLS servers",
        ],
        "nongoals": [
            "being an OCSP responder, which is a deployment product",
            "path validation, which is pkix and consumes this library",
            "CRL generation for CA operators",
        ],
        "mods": [
            (
                "request",
                "build and sign requests, single and batch (RFC 6960, RFC 9919)",
            ),
            (
                "response",
                "parse and verify signatures, including delegated responders (RFC 6960)",
            ),
            (
                "nonce",
                "generation, echo verification and the missing nonce policy (RFC 9654)",
            ),
            (
                "freshest",
                "thisUpdate, nextUpdate, maxAge and skew tolerance (RFC 6960, RFC 9919)",
            ),
            ("http", "bounded transport with retries, budgets and circuit breaking"),
            ("crl", "CRL and delta CRL fetch, matching and cache (RFC 5280)"),
            (
                "stapling",
                "verify a TLS server's stapled response against the connection (RFC 7633)",
            ),
            ("cache", "storage with explicit staleness decisions"),
        ],
        "api": [
            (
                "request",
                "def",
                "build",
                "cert: Certificate, issuer: Certificate, nonce: Optional[bytes]",
                "bytes",
                "RFC 6960 sec. 4.1, RFC 9919 sec. 3.1.1",
                "M1",
            ),
            (
                "response",
                "def",
                "parse_and_verify",
                "raw: bytes, responder_certs: list",
                "Response",
                "RFC 6960",
                "M2",
            ),
            (
                "nonce",
                "def",
                "generate",
                "length: int",
                "bytes",
                "RFC 9654 sec. 2.1",
                "M3",
            ),
            (
                "nonce",
                "def",
                "check",
                "request_nonce: Optional[bytes], response_nonce: Optional[bytes], policy: NoncePolicy",
                "None",
                "RFC 9654 sec. 2.1",
                "M3",
            ),
            (
                "freshest",
                "def",
                "is_fresh",
                "response: Response, at: datetime, tolerance: timedelta",
                "bool",
                "RFC 9919 sec. 5",
                "M4",
            ),
            (
                "http",
                "async def",
                "query",
                "url: str, body: bytes, budget: Budget",
                "Response",
                "RFC 6960 appendix A",
                "M4",
            ),
            (
                "crl",
                "async def",
                "fetch",
                "distribution_point: DistributionPoint, reason: Optional[int]",
                "Crl",
                "RFC 5280 sec. 4.2.1.13",
                "M5",
            ),
            (
                "stapling",
                "def",
                "verify_stapled",
                "response: Response, peer_cert: Certificate, sans: list",
                "Status",
                "RFC 7633 sec. 4.3.3",
                "M5",
            ),
        ],
        "steps": [
            "M1 request and response codec against RFC 6960 vectors",
            "M2 signature and delegated responder verification",
            "M3 nonce rules per RFC 9654 with explicit missing-nonce policy",
            "M4 freshness, caching and clock skew, then the bounded HTTP client",
            "M5 CRL and delta CRL, and the stapling check",
            "M6 publication: 0.1.0, wired as the revocation provider for pkix",
        ],
        "extras": {},
        "cli": "revokekit query https://ocsp.example.org --cert leaf.pem --issuer ca.pem --nonce",
    },
    {
        "name": "enrollkit",
        "pkg": "enrollkit",
        "title": "enrollkit -- CMP and EST clients for private PKIs",
        "desc": "Certificate enrollment: CMP to RFC 9810 with CRMF requests, EST to RFC 7030, "
        "PKCS#10 fallback, and the challenge and authentication mechanics around them",
        "kw": [
            "cmp",
            "est",
            "crmf",
            "enrollment",
            "pkcs10",
            "rfc9810",
            "rfc7030",
            "pki",
        ],
        "why": (
            "CMP was rewritten in 2025-07. RFC 9810 obsoletes RFC 4210 and RFC 9480, replaces "
            "EncryptedValue with EnvelopedData and adds KEM key support, and it has already "
            "accumulated 4 verified errata. No Python library implements CMP at any revision, "
            "and EST, the simpler HTTPS enrollment profile, has none either. ACME is the well "
            " served half, acme 5.9.0 shipped on 2026-10-29, and this project deliberately does "
            "not compete with it: private and enterprise PKIs run CMP and EST, and today that "
            "means openssl CLI invocations or a Java stack.",
        ),
        "deps": [("asn1crypto", "1.5.1"), ("cryptography", "42.0")],
        "deps_note": (
            "CMP messages are deep ASN.1 inside CMS. Writing that schema layer again would be "
            "the third Python implementation of the same structures, so definitions come from "
            "asn1crypto and primitives from cryptography. What this project owns is the "
            "protocol: message order, protection, and recovery from the failures."
        ),
        "docs": [
            {
                "n": "9810",
                "status": "Proposed Standard",
                "date": "2025-07",
                "why": "current CMP: messages, protection, EnvelopedData, KEM support",
            },
            {
                "n": "9811",
                "status": "Proposed Standard",
                "date": "2025-07",
                "why": "HTTP transfer for CMP, including the well-known URI prefix",
            },
            {
                "n": "9481",
                "status": "Proposed Standard",
                "date": "2023-11",
                "why": "the algorithm set CMP must and may support",
            },
            {
                "n": "4211",
                "status": "Proposed Standard",
                "date": "2005-09",
                "why": "CRMF: the certificate request message CMP carries",
            },
            {
                "n": "7030",
                "status": "Proposed Standard",
                "date": "2013-10",
                "why": "EST: enrollment over HTTPS with CMC, plus CA certificate retrieval",
            },
        ],
        "errata": [
            "RFC 9810 already carries 4 verified errata as of 2026-11-07 (rfclib). Implement 9810, not the 4210 text that most tutorials still copy.",
            "RFC 7030 carries 3 verified errata and is updated by RFC 8951, RFC 8996 and RFC 9908.",
            "RFC 4211 carries 2 verified errata and is updated by RFC 9045. RFC 9811 obsoletes RFC 6712 and, with RFC 9810, RFC 9480.",
            "RFC 4210 is OBSOLETE: cite it only when explaining a legacy server.",
        ],
        "prior": [
            {
                "name": "acme",
                "version": "5.9.0",
                "date": "2026-10-29",
                "note": "the ACME half, actively maintained; deliberately out of scope here",
            },
            {
                "name": "certipy",
                "version": "0.2.3",
                "date": "2026-04-29",
                "note": "Active Directory certificate services tooling, a different backend",
            },
            {
                "name": "cmp, crmf, cmpclient, enrollkit",
                "version": "-",
                "date": "-",
                "note": "cmp is a 2019 name squat, est belongs to a spectroscopy package, the rest are absent (2026-11-05)",
            },
        ],
        "gaps": [
            "CMP message construction and parsing to RFC 9810, including the EnvelopedData change",
            "both protection mechanisms: shared secret MAC and signature based, with senderNonce replay discipline",
            "the full interaction set: IP, CP, CR, Kurt, poll, conf, p10cr, rblu and error handling",
            "EST client with /simpleEnroll, /cacerts and /curl plus HTTP Basic and TLS client authentication",
            "poll and pending flows, which is where real CA integrations actually break",
        ],
        "nongoals": [
            "ACME, which acme already does well",
            "a CA or an RA: this is the client side of a conversation",
            "key generation policy: bring your own keys, or accept a hardware-backed signer through an interface",
        ],
        "mods": [
            ("messages", "CMP PKIMessage and its body variants (RFC 9810, RFC 9811)"),
            (
                "protection",
                "POPOSIMESTAMP, POPOWITNESS and signature protection (RFC 9810)",
            ),
            ("crmf", "request objects, keys and proof of possession (RFC 4211)"),
            ("flow", "the transaction state machine including poll and confirm"),
            ("est", "the HTTPS profile: simpleEnroll, cacerts and curl (RFC 7030)"),
            ("http", "bounded transport with retry and proxy awareness"),
            (
                "trust",
                "server authentication for enrollment endpoints, the part everyone bolts on",
            ),
        ],
        "api": [
            (
                "messages",
                "def",
                "build_cr",
                "request: CrmfRequest, sender: str, recipient: Optional[str]",
                "PkiMessage",
                "RFC 9810 sec. 5.3.3",
                "M1",
            ),
            (
                "messages",
                "def",
                "parse",
                "raw: bytes",
                "PkiMessage",
                "RFC 9810 sec. 5.1",
                "M1",
            ),
            (
                "protection",
                "def",
                "protect",
                "msg: PkiMessage, secret: bytes",
                "PkiMessage",
                "RFC 9810 sec. 5.1.3",
                "M2",
            ),
            (
                "protection",
                "def",
                "verify_protection",
                "msg: PkiMessage, trust: Trust",
                "None",
                "RFC 9810 sec. 5.1.3",
                "M2",
            ),
            (
                "crmf",
                "def",
                "build_request",
                "public_key: object, subject: str, validity: Validity",
                "CrmfRequest",
                "RFC 4211 sec. 5",
                "M3",
            ),
            (
                "flow",
                "async def",
                "enroll",
                "endpoint: str, request: CrmfRequest, auth: Auth",
                "EnrollmentResult",
                "RFC 9810",
                "M4",
            ),
            (
                "flow",
                "async def",
                "poll_until_ready",
                "endpoint: str, transaction: bytes, budget: Budget",
                "EnrollmentResult",
                "RFC 9810 sec. 5.3.22",
                "M4",
            ),
            (
                "est",
                "async def",
                "simple_enroll",
                "endpoint: str, csr: bytes, auth: Auth",
                "Certificate",
                "RFC 7030 sec. 4.2.1",
                "M5",
            ),
            (
                "est",
                "async def",
                "ca_certs",
                "endpoint: str, auth: Auth",
                "list",
                "RFC 7030 sec. 4.1",
                "M5",
            ),
        ],
        "steps": [
            "M1 CMP message codec round trip against captured vendor traffic",
            "M2 protection both ways, with senderNonce replay tests",
            "M3 CRMF request building and proof of possession",
            "M4 the transaction state machine including poll and error responses",
            "M5 EST client and its two authentication modes",
            "M6 publication: 0.1.0 with interop notes against at least two CA implementations",
        ],
        "extras": {},
        "cli": "enrollkit est --url https://ca.example/simpleenroll --key device.key --csr device.csr",
    },
    {
        "name": "ctspy",
        "pkg": "ctspy",
        "title": "ctspy -- certificate transparency verification in Python",
        "desc": "Certificate Transparency to RFC 9162: signed certificate timestamps, Merkle "
        "inclusion and consistency proofs, log storage tiles, and monitoring",
        "kw": [
            "certificate-transparency",
            "ct",
            "sct",
            "merkle",
            "logs",
            "rfc9162",
            "monitoring",
        ],
        "why": (
            "CT is the audit trail for the whole Web PKI and Python cannot read it. Verifying "
            "that a certificate really is in a log means parsing an SCT, checking a signed "
            "tree head, and validating Merkle inclusion and consistency proofs over the tile "
            "layout logs publish. RFC 9162 defines version 2.0 and it is worth saying plainly "
            "that the RFC Editor records it as Experimental, obsoleting RFC 6962, which is why "
            "deployment policy rather than the RFC is the real compatibility target. Nothing "
            "on PyPI does any of this: ctlog is a combinatorial testing tool, ctkit is "
            "medical image processing, sct is a radar calibration toolbox, and transparency is "
            "a model explanation library.",
        ),
        "docs": [
            {
                "n": "9162",
                "status": "Experimental",
                "date": "2021-12",
                "why": "CT 2.0: SCTs, signed tree heads, the two proof types and the TLS extension",
            },
            {
                "n": "6962",
                "status": "Obsolete",
                "date": "2013-07",
                "why": "version 1.0, still what many logs and captures emit",
            },
        ],
        "errata": [
            "RFC 9162 is Experimental with 1 erratum on file and none verified: expect variation between implementations and pin tests to real logs.",
            "RFC 9162 obsoletes RFC 6962; log artifacts from 2013 deployments still follow 6962 shapes, so the reader must accept both.",
        ],
        "prior": [
            {
                "name": "ctlog",
                "version": "0.7.1",
                "date": "2026-06-14",
                "note": "SAT based combinatorial testing, unrelated",
            },
            {
                "name": "ctkit",
                "version": "0.1.1",
                "date": "2026-09-11",
                "note": "CT image processing, unrelated",
            },
            {
                "name": "Trillian, tombstone, ct-go",
                "version": "-",
                "date": "-",
                "note": "the Go tooling that actually runs the logs; no Python equivalent",
            },
        ],
        "gaps": [
            "SCT parsing from the TLS extension, from X.509 and from OCSP, and signature verification against log keys",
            "signed tree head verification and the static Merkle tree with hashed leaf prefixes",
            "inclusion and consistency proof validation, including the empty tree and single leaf edges",
            "log storage tile fetching with cache control and mirror fallback",
            "a monitor that watches issued certificates and diffs them against what logs claim",
        ],
        "nongoals": [
            "running a log: the audit trail needs independent operators, not another client library",
            "browser policy decisions about which logs are accepted, which belong to the platform",
            "certificate issuance, which is enrollkit and pkix",
        ],
        "mods": [
            (
                "sct",
                "timestamp structure, versions and signature verification (RFC 9162)",
            ),
            (
                "sth",
                "signed tree heads, timestamps and the STH consistency rule (RFC 9162)",
            ),
            (
                "merkle",
                "static Merkle trees, leaf and node hash prefixes, proof validation (RFC 9162)",
            ),
            ("tiles", "log storage layout, fetching and caching (RFC 9162)"),
            (
                "client",
                "add chain, add prechain, get sth and the submission endpoints (RFC 9162)",
            ),
            ("monitor", "stream reading, diffing and alerting on unexpected issuance"),
            ("policy", "which logs and which artifacts a deployment will accept"),
        ],
        "api": [
            ("sct", "def", "parse", "raw: bytes", "Sct", "RFC 9162"),
            (
                "sct",
                "def",
                "verify",
                "sct: Sct, entry: MerkleTreeEntry, log_key: LogKey",
                "None",
                "RFC 9162",
            ),
            (
                "sth",
                "def",
                "verify",
                "sth: SignedTreeHead, log_key: LogKey",
                "None",
                "RFC 9162",
            ),
            (
                "merkle",
                "def",
                "verify_inclusion",
                "leaf: Hash, tree_size: int, proof: list, sth: SignedTreeHead",
                "bool",
                "RFC 9162",
            ),
            (
                "merkle",
                "def",
                "verify_consistency",
                "old: int, new: int, proof: list, old_root: Hash, new_root: Hash",
                "bool",
                "RFC 9162",
            ),
            (
                "tiles",
                "async def",
                "fetch_tile",
                "log: LogUrl, index: int, width: int",
                "bytes",
                "RFC 9162",
            ),
            (
                "client",
                "async def",
                "add_chain",
                "log: LogUrl, chain: list",
                "Sct",
                "RFC 9162",
            ),
            (
                "monitor",
                "async def",
                "watch",
                "log: LogUrl, since: int, sink: Sink",
                "None",
                "RFC 9162",
            ),
            (
                "policy",
                "def",
                "acceptable",
                "scts: list, at: datetime, policy: Policy",
                "Decision",
                "design",
            ),
        ],
        "steps": [
            "M1 static Merkle trees and both proof types against published vectors",
            "M2 SCT and STH parsing plus signature verification",
            "M3 tile fetching with caching and a mirror fallback",
            "M4 the submission client for chains and pre-certificates",
            "M5 a monitor with a durable cursor and diff output",
            "M6 publication: 0.1.0 with cross checks against a production log",
        ],
        "extras": {"crypto": [("cryptography", "42.0")]},
        "cli": "ctspy check cert.pem --logs logs.json --verify-inclusion",
    },
]

OPEN_QUESTIONS: dict[str, list[str]] = {
    "signeddata": [
        "how far the EnvelopedData recipient-info matrix extends in a first release",
        "acceptance for `policy.refuse_weak()` (Spec: design, placed on M2): proposed as 'verify() given a SignedData whose digest or signature algorithm is outside the policy allow list returns a Verification that names the refused algorithm and is not valid'; the row keeps its meaning until the owner accepts a sentence (D24)",
    ],
    "pgpenv": [
        "which algorithm policy is default, and whether v4 writing is ever offered",
        "acceptance for `limits.check()` (Spec: design, placed on M1): proposed as 'a packet stream that exceeds the packet size, nesting depth or key count bound raises before any further packet is decoded'; the row keeps its meaning until the owner accepts a sentence (D24)",
        "M5 (certifications and binding checks, then the policy object) has no api row: add rows for it or drop the rung (D24)",
    ],
    "shaken": [
        "how x5u retrieval is bounded so a verifier cannot be turned into a traffic amplifier",
        "M5 (interop against a captured carrier trace) has no api row: add a row for it or drop the rung (D24)",
    ],
    "srtpkit": [
        "which profiles are negotiable by default once RFC 3711 and RFC 7714 are applied",
    ],
    "revokekit": [
        "whether `response.parse_and_verify()` splits into a parse row (M1, the codec) and a verify row (M2, signature and delegated responder); it stays one row on M2 until decided (D24)",
    ],
    "enrollkit": [
        "which section of RFC 9810 `flow.enroll()` is accepted against: the transaction spans the certification request, response and confirmation exchanges, so its Spec cell stays the bare RFC until that is decided",
    ],
}
