# apdu-core roadmap fragment

| Status | Obligation | Anchor | Test |
|---|---|---|---|
| [ok] | Case 1: header only, 4 bytes; case 2: header + Le; case 3: header + Lc + data; case 4: header + Lc + data + Le, in that order | CCID 1.1 §7.3 (`dwLength` 4, 5, Lc+5, Lc+6; `abData = CLA,INS,P1,P2,Lc,Data(Lc),00h`); SP 800-73-5 Part 2 §3.1.1, §3.1.2, §3.2.1, §3.2.2 command syntaxes | command_forms::short_case_1_is_the_header; command_forms::short_case_2_le_only; command_forms::short_case_3_data_only [M1]; command_forms::short_case_4_lc_then_data_then_le [M1]; command_forms::short_lengths_match_ccid_dw_length |
| [ok] | Short Le `00` on the wire means 256, both ways; 255 is `FF` | SP 800-73-5 Part 2 Appendix A.3 Table 24 (Le `'00'`: "expected length of the response data field is 256 bytes") | command_forms::le_256_is_wire_00_in_short_form [M2]; sp800_73_vectors::table_22_request_challenge_is_case_4_with_le_256 |
| [ok] | No PIV-specific name or meaning in this crate | design: layers.md §2 (`apdu-core` must not depend on PIV) | none: review of `src/status.rs`; `StatusClass` names only ISO families |

| Status | Ambiguity | What the code does | Row or test |
|---|---|---|---|
| [unread] | A1: IAD source is USB 3.2, not USB 2.0 | Parses it as in USB 3.2 Table 9-24; walks past it; attaches it to the interfaces in its range | IAD row above |
| [ok] | A2: CCID descriptor placement and endpoint order unspecified | Found anywhere in the run; endpoints identified by direction and type, never position | placement row above; C `endpoint_rules` |
| [unread] | A3: fixed `bLength` of device 18, qualifier 10, configuration 9, IAD 8 are sums of the tables, not literals | Uses those sums as minimums; only interface 9, endpoint 7 and ICCD's 12h are literals in the sources | S `device_fields_are_read_at_the_right_offsets_little_endian` and the sentinel test |
