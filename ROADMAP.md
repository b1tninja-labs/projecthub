# projecthub roadmap

Rows the next sync batch should already know. The Bluetooth MX map is the source. This file does not
assign advertising-data type numbers or Flags bits: the Core Specification Supplement, Part A, and
Assigned Numbers are not imported.

| Obligation | Status | Spec | Where |
|---|---|---|---|
| `bt.flags-unread` | `[no]` | Core Specification 7.0 is imported. `bluetooth:core-specification-7-0§3.C.17` names the Supplement and Assigned Numbers, and neither document is in the catalog | do not write an advertising-data type number or a Flags bit |
| `bt.obd-bridge-matrix` | `[ok]` | `obd-bridge/ROADMAP.md` is the obligation matrix, and `obd-bridge` is in `RUST_OWNED` | `tests/test_registry.py::test_the_bluetooth_mx_path_is_registered` |
| `bt.fakegatt-sync` | `[ ]` | `fakegatt/ROADMAP.md` still says the Core Specification is not in `primarysource`, so `fakegatt` stays out of `RUST_OWNED` | `tests/test_registry.py::test_the_bluetooth_mx_path_is_registered` |
| `bt.portfolio-edges` | `[ ]` | `PORTFOLIO.md` section 3 omits `obdhub`, `fakegatt` and `obdlink-re`, and section 6 draws no vehicle edge. This repository does not edit that file | `tests/test_registry.py::test_the_bluetooth_mx_path_is_registered` |
| `bt.two-winrt-clients` | `[ ]` | The emulator path is `obdhub` over `gattmirror`. `voltkit bench` starts `obd-bridge`, which keeps its own WinRT client | `tests/test_registry.py::test_the_bluetooth_mx_path_is_registered` |
