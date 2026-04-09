# Chain & Array Job Visualization in My Jobs Tab

**Date:** 2026-04-10
**Scope:** sltop My Jobs tab enhancement

## Summary

Enhance the My Jobs tab to visually group and display SLURM dependency chains and job arrays, giving users an at-a-glance view of their workflow status.

## Data Fetching

Extend the existing `_queue()` function's `squeue` format string with three additional fields:

| Field          | squeue code | Example value                        |
|----------------|-------------|--------------------------------------|
| Dependencies   | `%E`        | `afterok:12345,afterany:12346`       |
| Array Job ID   | `%F`        | `12350` or `N/A`                     |
| Array Task ID  | `%K`        | `3` or `N/A`                         |

The format string changes from:

```
%i %P %u %j %T %M %l %D %b %R
```

to:

```
%i %P %u %j %T %M %l %D %b %R %E %F %K
```

No additional SLURM commands are needed.

## Chain Reconstruction Logic

A new function `_build_chains(my_jobs)` processes the current user's jobs:

### Step 1 — Index jobs by ID

Build a lookup: `jobs_by_id = {job["jobid"]: job for job in my_jobs}`.

### Step 2 — Build dependency graph

- Parse each job's dependency field (e.g., `afterok:12345,afterany:12346`).
- Extract referenced job IDs.
- Build `parent → children` and `child → parents` mappings.

### Step 3 — Find chain roots

- A root is a job whose parents are either not in the user's job set or has no parents.
- Walk from each root following children to form ordered chains.

### Step 4 — Group array jobs

- Jobs sharing the same array job ID (`%F`) are grouped together.
- If an array group is also part of a dependency chain, the array group is treated as a single node in the chain.

### Step 5 — Classify into three categories

1. **Chains** (2+ jobs linked by dependencies) → vertical chain panel.
2. **Arrays** (jobs sharing array job ID, not part of a chain) → summary panel.
3. **Standalone** (everything else) → current card format, unchanged.

## Rendering

### Chain Panel (`_build_chain_panel`)

A Rich Panel wrapping vertically stacked compact job cards connected by `│` and `▼` arrows:

```
╭─ Chain: 3 jobs ──────────────────────╮
│  ╭─ preprocess ✓ COMPLETED ───────╮  │
│  │  12345 │ gtest │ gpu:1          │  │
│  │  00:15:00 / 01:00:00           │  │
│  ╰────────────────────────────────╯  │
│              │                        │
│              ▼                        │
│  ╭─ train ▶ RUNNING ─────────────╮  │
│  │  12346 │ gtest │ gpu:4          │  │
│  │  01:30:00 / 06:00:00           │  │
│  ╰────────────────────────────────╯  │
│              │                        │
│              ▼                        │
│  ╭─ evaluate ⏳ PENDING ──────────╮  │
│  │  12347 │ gtest │ gpu:1          │  │
│  │  Dependency                     │  │
│  ╰────────────────────────────────╯  │
╰──────────────────────────────────────╯
```

Each inner card is a compact variant of the existing `_build_job_card()` — showing job name, state symbol/colour, job ID, partition, GRES, and elapsed/limit time (or reason for pending jobs).

### Array Panel (`_build_array_panel`)

A single summary panel with a progress bar and state counts:

```
╭─ Array: my_sweep [██████░░░░] 60/100 ╮
│  ✓ Completed: 55                      │
│  ▶ Running: 5  (task 56-60)           │
│  ⏳ Pending: 40                        │
│  ✗ Failed: 0                          │
╰───────────────────────────────────────╯
```

- Shows a progress bar (completed / total).
- Breaks down counts by state.
- If any tasks failed, lists their task IDs explicitly.

### Standalone Jobs

No change — uses the existing `_build_job_card()` as-is.

### Ordering in the scroll view

1. Chains (sorted by earliest job ID in chain).
2. Arrays (sorted by array job ID).
3. Standalone jobs (sorted by job ID).

## Scope

- **Only the My Jobs tab** is affected. The Queue tab and other tabs remain unchanged.
- The existing `_build_job_card()` function is reused for standalone jobs and adapted into a compact variant for chain inner cards.
- No new CLI arguments or configuration needed.
- No new dependencies required.

## Files Modified

- `sltop/sltop.py` — all changes are in this single file:
  - `_queue()`: extend format string with `%E %F %K`.
  - New `_build_chains()`: chain/array grouping logic.
  - New `_build_chain_panel()`: render vertical chain panel.
  - New `_build_array_panel()`: render array summary panel.
  - New `_build_compact_job_card()`: compact job card for chain inner cards.
  - `_fill_my_jobs()`: updated to use grouping and new renderers.
