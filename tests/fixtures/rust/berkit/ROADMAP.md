# berkit roadmap

## Policy decisions

| Status | Policy | Why |
|---|---|---|
| [open] | BER decoding enforces the standard's "shall" value rules, including minimal INTEGER, OID subidentifier and tag-number forms. There is no lenient mode in version 1. | X.690 §8.3.2, §8.19.2 and §8.1.2.4.2 are "shall" with no sender's option; a lenient reader is a deliberate deviation, so it would be a separate, named option |
| [open] | DER decoding rejects every non-canonical encoding of §10 and §11. | X.690 states only the sender's rule; "reject" is derived from §7.1 with §10 and §11 |

## Modules

| Module | Source | Roadmap | `[ok]` | `[unread]` | `[open]` | Tests |
|---|---|---|---|---|---|---|
| framing (`tag`, `length`, `tlv`) | X.690 §8.1, §10.1 | `framing.md` | 23 | 4 | 10 | 38 |
| numeric (`boolean`, `integer`, `null`, `real`) | X.690 §8.2 to §8.8, §11.1, §11.3 | `numeric.md` | 30 | 2 | 4 | 41 |
| `oid` | X.690 §8.19 to §8.22 | `oid.md` | 11 | 2 | 3 | 27 |

## Across the modules

| Status | Obligation | Anchor | Test |
|---|---|---|---|
| [unread] | 22 of the 26 framing rows were re-read from X.690 on 2026-11-13 and are now `[ok]`; four are not: F-16 (Figure 4 is an image), F-17 (a POLICY), F-44 (its named test did not fail under a breakage) and stream order (its anchor is not X.690). One row text was wrong and is corrected: the high-tag leading octet is `xxx11111` | `crates/berkit/roadmap/framing.md`, section "Clause reading" | clauses read through `primarysource itu read`; each flipped row has a fresh breakage recorded there |
| [ok] | The corpus runs against the code: 1179 cases, 2355 runs, all agree, with a known-disagreement list that is empty | `corpus/` 0.2.0 | `tests/corpus.rs::corpus_agrees_with_the_code_except_the_known_list`; breaking INTEGER minimality produced 7 disagreements, DER indefinite length 20, the PrintableString alphabet 183, the OID first-arc decode 23, REAL minus zero 1 |
| [open] | Agreement is code against corpus, not either against the standard: the corpus expectations are an author's reading of the clauses, written from the same research notes the code was built from, and checked by a Python reference decoder that is not independent of them | none | an independent check (a second implementation written without the notes, or a real-certificate sample) is not done |
