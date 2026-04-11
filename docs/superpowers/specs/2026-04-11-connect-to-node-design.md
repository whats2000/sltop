# Connect to Compute Node — Design Spec

**Date:** 2026-04-11
**Status:** Approved

## Overview

Add a "Connect" button to RUNNING job cards in the My Jobs tab that lets the user attach to a compute node's terminal via `srun --overlap`. The button sits alongside a node selector and the existing Cancel button in a consistent button row layout.

## Requirements

- Connect to a compute node directly from the sltop TUI
- Uses `srun --overlap --jobid <id> --nodelist <node> --pty bash` (not SSH)
- Node selection defaults to the first node (head node), with a dropdown to pick others for multi-node jobs
- Cancel button is always pushed to the right end of the button row, regardless of whether Connect is present
- RUNNING jobs show an active Connect button; non-RUNNING jobs show a disabled "Waiting for node…" button for discoverability
- sltop exits and is replaced by the `srun` process; user relaunches sltop manually after disconnecting

## Data Layer

### Node list in job data

Add `%N` (nodelist) to the `squeue` format string in `_queue()`. Each job dict gains a `"nodelist"` field containing the SLURM compact node notation (e.g., `"gn0621"`, `"gn[0621-0624]"`, or `""` for PENDING jobs).

### Node list expansion

Add `_expand_nodelist(compact: str) -> list[str]` that calls `scontrol show hostnames <compact>` to expand SLURM compact notation into individual hostnames:

- `"gn0621"` → `["gn0621"]`
- `"gn[0621-0624]"` → `["gn0621", "gn0622", "gn0623", "gn0624"]`
- `""` → `[]`

Using `scontrol` for robust handling of all SLURM nodelist formats.

## UI Layout — Button Row

### Horizontal button row for all job cards

Replace the current standalone Cancel button with a `Horizontal` container (`.button-row`) for all job cards. Cancel is always pushed to the right end via a `Spacer()` widget.

Layout by job state:

```
RUNNING single-node:  [ ⬡ Connect ]                       [ ✗ Cancel ]
RUNNING multi-node:   [ ⬡ Connect ]  [ Select: node ▼ ]   [ ✗ Cancel ]
PENDING/other:        [ ⬡ Waiting for node… ] (disabled)   [ ✗ Cancel ]
```

### Standalone & chain job cards

- RUNNING jobs: Show active "⬡ Connect" button + node `Select` widget (hidden if single node) + Cancel
- Non-RUNNING jobs: Show disabled "⬡ Waiting for node…" button + Cancel

### Array job panels

- Array summary panels show the Connect button if any tasks are RUNNING
- The `Select` widget lists RUNNING tasks with their nodes: `"Task 5 → gn0621"`, `"Task 12 → gn0622"`
- The selected value maps to a specific task's full job ID in SLURM format (`<array_job_id>_<task_id>`, e.g., `12345_5`) and its node
- If no array tasks are RUNNING, show disabled "⬡ Waiting for node…" button

## Connect Action — Exit & Execute

### Flow

1. User clicks "⬡ Connect"
2. Read the target node from the `Select` widget (or the single node if no Select)
3. Exit sltop with a structured result: `self.exit(result=("connect", job_id, node_name))`
4. In `main()`, check the exit result:
   - If `("connect", job_id, node)`: call `os.execvp("srun", ["srun", "--overlap", "--jobid", job_id, "--nodelist", node, "--pty", "bash"])`
   - Otherwise: normal exit behavior

### Why `os.execvp`

Replaces the sltop process entirely with `srun`. No zombie processes, no subprocess management. The user relaunches sltop manually after disconnecting from the node.

## Styling

### Connect button (`.connect-link`)

```css
.connect-link {
    background: transparent;
    color: #6688cc;
    text-style: underline;
    border: none;
    min-width: 14;
    height: 1;
    padding: 0 0;
}
.connect-link:hover {
    background: #112244;
    color: #4499ff;
    text-style: bold underline;
}
.connect-link:disabled {
    color: #555555;
    text-style: none;
}
```

### Node select (`.node-select`)

```css
.node-select {
    width: auto;
    min-width: 16;
    height: 1;
    margin: 0 0 0 1;
}
```

### Button row (`.button-row`)

```css
.button-row {
    height: auto;
    margin: 1 0 0 0;
    align: left middle;
}
```

### Cancel alignment

A `Widget(classes="spacer")` with `width: 1fr` CSS is placed between the left-side widgets (Connect + Select) and the Cancel button, pushing Cancel to the right end. This applies to all job cards, including those without the Connect button. (Note: Textual has no built-in `Spacer` widget.)

## Edge Cases

| Case | Behavior |
|------|----------|
| RUNNING job with empty nodelist | Show disabled "Waiting for node…" button (treat as non-RUNNING) |
| Job ends between click and execution | `srun` fails with "Invalid job id" error in terminal; user relaunches sltop |
| Single-node job | Connect button shown, Select widget hidden |
| Multi-node job | Connect button + Select with all nodes, defaulting to first (head) node |
| Array summary — no RUNNING tasks | Show disabled "Waiting for node…" button |
| Array summary — has RUNNING tasks | Connect + Select listing `"Task N → nodeX"` per RUNNING task |
| Chain jobs | Per-card: RUNNING jobs get Connect, PENDING jobs don't |
