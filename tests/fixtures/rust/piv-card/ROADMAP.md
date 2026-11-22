# piv-card roadmap

| Status | Obligation | Anchor | Test |
|---|---|---|---|
| [ok] | SELECT of the PIV application is `00 A4 04 00 0B <AID> 00`, the AID being `A0 00 00 03 08 00 00 10 00 01 00` | SP 800-73-5 Part 2 §3.1.1; Part 1 §2.2 (the AID) | `piv_objects.rs::select_piv_matches_the_wire_bytes`, `cardkit_exchange.rs::select_sends_the_piv_aid_and_returns_the_response_data`; broken by cutting the AID data to 9 bytes. The AID constant itself is `piv-model`'s and is not broken from here |
| [ok] | SELECT and GET DATA send Le `00`, which `apdu-core` writes for 256 | SP 800-73-5 Part 2 §3.1.1, §3.1.2 (Le `00`) | `piv_objects.rs::select_piv_matches_the_wire_bytes`, `piv_objects.rs::get_data_for_the_card_authentication_object_matches_the_wire_bytes`; broken by sending Le 255 |
| [ok] | GET DATA is `00 CB 3F FF Lc 5C L <tag>` Le `00`: the object tag is wrapped in tag `5C` | SP 800-73-5 Part 2 §3.1.2, Table 6 | `piv_objects.rs::get_data_for_the_card_authentication_object_matches_the_wire_bytes`; broken by sending tag `5D`, and by miscounting the tag-list length |
| [ok] | The Lc of GET DATA follows the tag length: `05` for a three-byte tag, `03` for `7E`, `04` for `7F61` | SP 800-73-5 Part 2 §3.1.2, footnote 6 | `piv_objects.rs::get_data_handles_tags_shorter_than_three_bytes`; broken by writing the tag-list length one too high (the three-byte test failed too) |

| Status | Obligation | Anchor | Test |
|---|---|---|---|
| [ok] | An object longer than 256 bytes is read by following `61xx` with GET RESPONSE until the card is done | SP 800-73-5 Part 2 Appendix A.4.1 (Command 3) and §3.1.2 footnote 5; GET RESPONSE itself is ISO/IEC 7816-4 (not read here) | `cardkit_exchange.rs::get_data_reads_a_1856_byte_object_delivered_in_61xx_pieces` (one command and seven GET RESPONSE); broken by capping the collected response at 256 bytes. The chaining logic is `apdu-exchange`'s |
| [open] | An object with no `CertInfo` (tag `71`) is read as uncompressed | none: Part 1 lists `CertInfo` as a fixed one-byte element, so this is lenient; kept from the first version | none |
| [open] | A `6Cxx` answer is followed by one resend with the corrected Le | ISO/IEC 7816-4 (not read here) | `cardkit_exchange.rs::a_wrong_le_is_corrected_once_and_the_data_returned` passes over a scripted card; the behaviour is `apdu-exchange`'s and is broken and checked in the `cardkit` repository, not here |
| [open] | A failure after the first response piece is a status error, not a short object | SP 800-73-5 Part 2 §3.1.2 | `cardkit_exchange.rs::a_failure_after_the_first_piece_is_a_status_error_not_a_short_object` passes; not broken from here (`apdu-exchange`) |
