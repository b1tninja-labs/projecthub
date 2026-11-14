# ccid-proto roadmap fragment

## Conventions and framing

| Status | Obligation | Anchor | Test |
|---|---|---|---|
| [ok] | `dwLength` is little endian | CCID 1.1 §1.3 | commands::xfr_block_dw_length_little_endian [M2] |
| [unread] | A2: reserved set is only "80h and those filling the gaps". Code decodes 80h, C1h-DFh, E1h-EEh, F1h, F9h, FAh as `Reserved`, an arithmetic reading | CCID 1.1 §6.2.6 Table 6.2-2 | framing::error_ranges |
