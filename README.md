# sltop — SLURM Cluster Top

<div align="center">

[![PyPI version](https://badge.fury.io/py/sltop.svg)](https://badge.fury.io/py/sltop)
[![Python Versions](https://img.shields.io/pypi/pyversions/sltop)](https://pypi.org/project/sltop/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI/CD](https://github.com/whats2000/sltop/actions/workflows/publish.yml/badge.svg)](https://github.com/whats2000/sltop/actions/workflows/publish.yml)

**An `nvtop`-inspired interactive SLURM cluster dashboard.**  
Monitor partitions, scheduling rules, the full job queue, and your own running/pending jobs — all from a single, keyboard-driven terminal window powered by [Textual](https://github.com/Textualize/textual).

![sltop screenshot](https://raw.githubusercontent.com/whats2000/sltop/refs/heads/main/images/sltop.png)

![sltop screenshot rules](https://raw.githubusercontent.com/whats2000/sltop/refs/heads/main/images/sltop-rules.png)

![sltop screenshot queue](https://raw.githubusercontent.com/whats2000/sltop/refs/heads/main/images/sltop-queue.png)

![sltop screenshot my jobs](https://raw.githubusercontent.com/whats2000/sltop/refs/heads/main/images/sltop-my-jobs.png)

</div>

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
  - [Via pip (recommended)](#via-pip-recommended)
  - [Via pipx (isolated)](#via-pipx-isolated)
  - [Manual install](#manual-install)
- [Usage](#usage)
  - [Arguments](#arguments)
  - [Key Bindings](#key-bindings)
- [Dashboard Tabs](#dashboard-tabs)
  - [① Resources](#-resources)
  - [② Rules](#-rules)
  - [③ Queue](#-queue)
  - [④ My Jobs](#-my-jobs)
- [How It Works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- 📊 **Resources tab** — per-partition CPU/GPU/node utilisation bars with alloc/mix/idle/drain breakdown and cluster-wide totals
- 📋 **Rules tab** — scheduling constraints (MaxTime, QoS, GPU limits, node limits, TRES) rendered as Rich panels
- 📜 **Queue tab** — full SLURM job queue with sortable columns (click any header); PENDING config-errors highlighted as `INCORRECT_CONFIG` in red with a plain-English explanation
- 👤 **My Jobs tab** — cards for the current user's jobs showing elapsed/limit time, resource requests, GPU mini-bar, and a human-readable translation of every SLURM reason code
- 🔗 **Connect to compute node** — one-click "⬡ Connect" button on running jobs to attach to the compute node terminal via `srun --overlap`; supports multi-node jobs with node selection dropdown
- ❌ **Job cancel with confirmation** — "✗ Cancel" button on every job card with chain-aware cancellation (downstream dependents cancelled automatically)
- 🔄 **Auto-refresh** — all tabs update on a configurable interval (default 10 s) with last-refresh timestamp in the subtitle bar
- 🌈 **Rich colour UI** — explicit RGB colours via [Textual](https://github.com/Textualize/textual) + [Rich](https://github.com/Textualize/rich); stacked node-state bars, colour-coded utilisation bars, per-partition colour coding

---

## Requirements

| Requirement                      | Notes                                                |
| -------------------------------- | ---------------------------------------------------- |
| **Python ≥ 3.8**                 | Pure Python — no Bash or SSH required                |
| **SLURM** (`squeue`, `scontrol`, `sinfo`, `sacctmgr`, `scancel`, `srun`) | Must be available on the login node |
| **textual ≥ 0.50**               | Installed automatically as a dependency              |

---

## Installation

### Via pip (recommended)

```bash
pip install sltop
```

This places the `sltop` command on your `PATH`.

### Via pipx (isolated)

[pipx](https://pypa.github.io/pipx/) installs the tool into an isolated environment and exposes the command globally — ideal for shared HPC login nodes.

```bash
pipx install sltop
```

### Manual install

```bash
# Clone
git clone https://github.com/whats2000/sltop.git
cd sltop

# Install in editable mode
pip install -e .
```

---

## Usage

```
sltop [-n SECS] [-p P1,P2] [-u USER]
```

Simply run `sltop` from any terminal on your HPC login node:

```bash
sltop                      # default 10-second refresh, all partitions, all users
sltop -n 5                 # refresh every 5 seconds
sltop -p gpu,cpu           # filter to specific partitions
sltop -u $USER             # show only your jobs in the Queue tab
```

### Arguments

| Argument              | Default | Description                                          |
| --------------------- | ------- | ---------------------------------------------------- |
| `-n` / `--interval`   | `10`    | Refresh interval in seconds                          |
| `-p` / `--partitions` | all     | Comma-separated partition filter                     |
| `-u` / `--user`       | all     | Show only jobs for this user in the Queue tab        |

### Key Bindings

| Key        | Action                              |
| ---------- | ----------------------------------- |
| `Tab`      | Next tab                            |
| `1`        | Jump to ① Resources tab             |
| `2`        | Jump to ② Rules tab                 |
| `3`        | Jump to ③ Queue tab                 |
| `4`        | Jump to ④ My Jobs tab               |
| `r`        | Force refresh now                   |
| `Esc`      | Focus the Queue table               |
| `q`        | Quit                                |

---

## Dashboard Tabs

### ① Resources

Cluster-wide summary panel (total CPU / GPU / node utilisation) followed by one Rich card per partition showing:

- **Availability** — `UP` / `DOWN` indicator
- **MaxTime & QoS** — scheduler policy labels
- **GRES & per-node memory** — hardware totals
- **Constraints** — min/max GPU and node limits (with implied-node inference)
- **CPU / GPU / Node bars** — colour-coded stacked bars (alloc / mix / idle / drain)

### ② Rules

One Rich card per partition with the full set of `scontrol show partition` fields plus QoS GPU limits from `sacctmgr`, including:

- Time limits (Max / Default)
- Node and CPU constraints
- GPU totals and per-node limits
- Access lists (AllowGroups / AllowAccounts)
- TRES breakdown

### ③ Queue

Full `squeue` output as a sortable `DataTable`.  Click any column header to sort ascending; click again to reverse; third click clears the sort.  
PENDING jobs whose reason code indicates a **configuration error** (e.g. `QOSMinGRES`, `InvalidAccount`) are flagged as `INCORRECT_CONFIG` in red with a human-readable explanation appended.

### ④ My Jobs

Interactive cards for every job belonging to the current Unix user, with:

- Job state with colour and symbol
- Job ID, partition (colour-coded), user
- Elapsed time / time limit
- Node count and GRES request
- GPU mini-bar (request vs partition total)
- Plain-English translation of the SLURM reason code
- **⬡ Connect** button (RUNNING jobs) — attaches to the compute node terminal via `srun --overlap`; for multi-node jobs, a dropdown lets you pick which node to connect to
- **✗ Cancel** button — cancels the job with a confirmation dialog; chain-aware (cancels downstream dependents automatically)

---

## How It Works

```
Login Node
┌──────────────────────────────────────────────┐
│ sltop                                        │
│  ├─ sinfo    ──► Resources / Rules tabs      │
│  ├─ scontrol ──► Rules tab                   │
│  ├─ sacctmgr ──► QoS GPU limits              │
│  ├─ squeue   ──► Queue / My Jobs tabs        │
│  ├─ scancel  ──► Job cancellation            │
│  ├─ srun     ──► Connect to compute node     │
│  └─ Textual TUI render loop                  │
└──────────────────────────────────────────────┘
```

On `mount`, `sltop` fires a single `_do_refresh()` pass and schedules it to repeat every `--interval` seconds.  Each pass calls the four SLURM CLI tools, builds Rich renderables, and pushes them into the Textual widget tree — no background threads or SSH connections required.

---

## Troubleshooting

### `No partition data.`

`sinfo` returned no output.  Make sure SLURM is available on the current node (`which sinfo`).

### Rules or QoS data is missing

`scontrol` or `sacctmgr` may not be available, or you may lack the permissions to query QoS data.  `sltop` silently omits unavailable data rather than crashing.

### Queue shows no jobs

There are currently no jobs matching the optional `--user` / `--partitions` filter.  Run without filters to see all jobs.

### `textual` not found

Install it manually: `pip install "textual>=0.50"`.

---

## Contributing

Contributions, bug reports and feature requests are welcome!

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Commit your changes with a descriptive message
4. Open a Pull Request

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

