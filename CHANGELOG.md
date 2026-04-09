# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] — 2026-04-10

### Added

- **Idle timeout** — automatically exits after 5 minutes of no user interaction (keypress, scroll, mouse) to conserve login node resources; prints a friendly message explaining the exit is by design
- New CLI flag `--idle-timeout SECS` (default 300, `0` to disable)

## [0.2.0] — 2026-04-10

### Added

- **Chain job visualization** — dependency chains (`--dependency=afterok:…`) are automatically detected and displayed as vertical panels with `│` `▼` connectors in the My Jobs tab, showing the full workflow from root to leaf
- **Array job visualization** — job arrays (`--array=…`) are grouped into a single summary panel with a progress bar and per-state breakdown (running/pending/failed with task IDs)
- **Compact job cards** — streamlined 2-line cards used inside chain panels for a denser, scannable layout

### Changed

- Extracted shared `_STATE_STYLE` constant for consistent state colour/symbol mapping across job card variants

## [0.1.0] — 2026-03-13

### Added

- **Initial release** of `sltop` — an `nvtop`-inspired interactive SLURM cluster dashboard built with [Textual](https://github.com/Textualize/textual)
- **① Resources tab** — per-partition CPU/GPU/node utilisation bars with alloc/mix/idle/drain breakdown, cluster-wide summary panel, and GPU-used-by-partition tracking via `squeue`
- **② Rules tab** — `scontrol show partition` fields rendered as Rich panels, enriched with QoS GPU limits from `sacctmgr` (MinTRES, MaxTRES, MaxTRESPerNode)
- **③ Queue tab** — full SLURM queue as a sortable `DataTable`; click any column header to sort ascending/descending/clear; PENDING jobs with configuration errors highlighted as `INCORRECT_CONFIG` in red with plain-English explanations
- **④ My Jobs tab** — Rich Panel cards for the current user's jobs showing state, job ID, partition (colour-coded), elapsed/limit time, node count, GRES, GPU mini-bar, and human-readable SLURM reason code translations
- **Reason code translation** — covers 40+ SLURM reason codes (QoS, partition, node, account, dependency) with contextual detail (actual limits inserted inline)
- **Auto-refresh** — configurable interval (default 10 s) via `-n`; force-refresh with `r`; last-refresh timestamp and running/pending counts shown in the subtitle bar
- **Partition filter** (`-p`) and **user filter** (`-u`) CLI arguments
- **Stacked node-state bars** (alloc / mix / idle / drain) with per-state colour coding and count legend
- **Colour-coded utilisation bars** for CPU and GPU: green < 70 %, yellow < 90 %, red ≥ 90 %

[Unreleased]: https://github.com/whats2000/sltop/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/whats2000/sltop/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/whats2000/sltop/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/whats2000/sltop/releases/tag/v0.1.0

