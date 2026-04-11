# Connect to Compute Node — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Connect" button to job cards in the My Jobs tab that lets users attach to a running job's compute node via `srun --overlap`.

**Architecture:** Extend `_queue()` to fetch node hostnames, add `_expand_nodelist()` helper, refactor job card mounting to use a Horizontal button row with Connect + node Select + Cancel, and handle the connect action by exiting sltop and replacing the process with `srun --overlap` via `os.execvp`.

**Tech Stack:** Python 3.8+, Textual >= 0.50, SLURM CLI (`squeue`, `scontrol`, `srun`)

---

## File Structure

All changes are in a single file:

- **Modify:** `sltop/sltop.py` — data layer, CSS, UI mounting, button handler, CLI entry point

No new files. No test files (project has no test suite; this is a TUI app tested manually on the cluster).

---

### Task 1: Data Layer — Add nodelist to job data and expand helper

**Files:**
- Modify: `sltop/sltop.py:1094` (squeue format string)
- Modify: `sltop/sltop.py:1101-1136` (field parsing)
- Modify: `sltop/sltop.py` (new function after `_run()`)

- [ ] **Step 1: Add `_expand_nodelist()` helper**

Add this function after the existing `_run()` function (after line 61):

```python
def _expand_nodelist(compact: str) -> list[str]:
    """Expand SLURM compact nodelist notation into individual hostnames."""
    if not compact or compact == "(null)":
        return []
    out = _run(["scontrol", "show", "hostnames", compact])
    return out.splitlines() if out else []
```

- [ ] **Step 2: Add `%N` to squeue format and parse nodelist field**

In `_queue()`, change the squeue format string from:

```python
cmd = ["squeue", "--noheader", "-o", "%i|%P|%u|%j|%T|%M|%l|%D|%b|%R|%E|%F|%K"]
```

to:

```python
cmd = ["squeue", "--noheader", "-o", "%i|%P|%u|%j|%T|%M|%l|%D|%b|%R|%E|%F|%K|%N"]
```

Update the field count check from `if len(parts) < 13` to `if len(parts) < 14`.

Update the unpacking to add `nodelist`:

```python
(
    jobid,
    partition,
    user,
    name,
    state,
    elapsed,
    timelimit,
    nodes,
    gres,
    reason,
    dependency,
    array_job_id,
    array_task_id,
    nodelist,
) = parts[:14]
```

Add `"nodelist": nodelist` to the `rows.append({...})` dict.

- [ ] **Step 3: Verify on the cluster**

Run: `cd /work/whats2000/cluster-utils/sltop && uv run python -c "from sltop.sltop import _queue, _expand_nodelist; rows = _queue(None, None); print([(r['jobid'], r['state'], r['nodelist']) for r in rows[:5]]); print(_expand_nodelist(''))"`

Expected: List of (jobid, state, nodelist) tuples. PENDING jobs have empty nodelist, RUNNING jobs show node names. `_expand_nodelist('')` returns `[]`.

- [ ] **Step 4: Commit**

```bash
cd /work/whats2000/cluster-utils/sltop
git add sltop/sltop.py
git commit -m "feat(sltop): add nodelist to job data and expand helper"
```

---

### Task 2: CSS — Add styles for connect button, node select, and button row

**Files:**
- Modify: `sltop/sltop.py:1292-1357` (CSS string in `SlurmMonitor` class)

- [ ] **Step 1: Remove top margin from `.cancel-link`**

The `.cancel-link` CSS currently has `margin: 1 0 0 0;`. Change it to `margin: 0;` because the top margin will now come from the `.button-row` container instead.

Change:
```css
    .cancel-link {
        background: transparent;
        color: #ff6666;
        text-style: underline;
        border: none;
        min-width: 14;
        height: 1;
        padding: 0 0;
        margin: 1 0 0 0;
    }
```

To:
```css
    .cancel-link {
        background: transparent;
        color: #ff6666;
        text-style: underline;
        border: none;
        min-width: 14;
        height: 1;
        padding: 0 0;
        margin: 0;
    }
```

- [ ] **Step 2: Add new CSS classes**

Add the following CSS rules right after the `.cancel-link:hover` block (after line 1357):

```css
    .button-row {
        height: auto;
        margin: 1 0 0 0;
        align: left middle;
    }
    .connect-link {
        background: transparent;
        color: #6688cc;
        text-style: underline;
        border: none;
        min-width: 14;
        height: 1;
        padding: 0 0;
        margin: 0;
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
    .node-select {
        width: auto;
        min-width: 16;
        height: 1;
        margin: 0 0 0 1;
    }
    .spacer {
        width: 1fr;
        height: 1;
    }
```

- [ ] **Step 3: Commit**

```bash
cd /work/whats2000/cluster-utils/sltop
git add sltop/sltop.py
git commit -m "feat(sltop): add CSS for connect button, node select, and button row"
```

---

### Task 3: Add imports for Horizontal, Select, and Widget

**Files:**
- Modify: `sltop/sltop.py:36-50` (imports)

- [ ] **Step 1: Add Horizontal and Spacer to containers import**

Change:
```python
from textual.containers import Grid, Vertical, VerticalScroll
```

To:
```python
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
```

- [ ] **Step 2: Add Select to widgets import and Widget for spacer**

Change:
```python
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    Static,
    TabbedContent,
    TabPane,
)
```

To:
```python
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
```

Note: `Spacer` doesn't exist in Textual. We use `Widget(classes="spacer")` with `width: 1fr` CSS instead.

- [ ] **Step 3: Commit**

```bash
cd /work/whats2000/cluster-utils/sltop
git add sltop/sltop.py
git commit -m "feat(sltop): add imports for Horizontal, Select, Widget"
```

---

### Task 4: Add connect ID maps to `__init__`

**Files:**
- Modify: `sltop/sltop.py:1395-1397` (SlurmMonitor.__init__)

- [ ] **Step 1: Add connect tracking state in `_fill_my_jobs`**

In `_fill_my_jobs()`, right after line 1525 where `_cancel_id_map` is re-initialized, add:

```python
        self._connect_id_map: dict[str, tuple[str, str]] = {}
```

So lines 1524-1526 become:

```python
        self._chain_dependents = {}
        self._cancel_id_map: dict[str, str] = {}  # sanitized_id -> real job ID
        self._connect_id_map: dict[str, tuple[str, str]] = {}  # safe_id -> (job_id, nodelist)
```

This ensures the map is cleared on every refresh, matching the pattern used by `_cancel_id_map`.

- [ ] **Step 2: Commit**

```bash
cd /work/whats2000/cluster-utils/sltop
git add sltop/sltop.py
git commit -m "feat(sltop): add connect ID map to SlurmMonitor state"
```

---

### Task 5: Refactor `_mount_job_card` to use button row with Connect

**Files:**
- Modify: `sltop/sltop.py:1544-1561` (`_mount_job_card` function inside `_fill_my_jobs`)

This is the core UI change. The `_mount_job_card` inner function currently mounts a `Static` and a `Button` (cancel) inside a `Vertical`. We need to replace the standalone cancel button with a `Horizontal` button row containing: Connect button + optional node Select + Spacer + Cancel button.

- [ ] **Step 1: Refactor `_mount_job_card` to include button row**

Replace the entire `_mount_job_card` function (lines 1544-1561) with:

```python
        def _mount_job_card(
            parent, content: RText, title: str, color: str, job_id: str,
            job_state: str, nodelist_str: str,
        ) -> None:
            """Mount a single job card with connect + cancel button row."""
            safe = _safe_id(job_id)
            self._cancel_id_map[safe] = job_id
            card = Vertical(classes="job-card")
            card.border_title = title
            card.styles.border = ("round", color)
            parent.mount(card)
            card.mount(Static(content))

            # Button row: [Connect] [NodeSelect] <spacer> [Cancel]
            row = Horizontal(classes="button-row")
            card.mount(row)

            is_running = job_state == "RUNNING"
            nodes = _expand_nodelist(nodelist_str) if is_running else []

            if is_running and nodes:
                self._connect_id_map[safe] = (job_id, nodelist_str)
                row.mount(
                    Button(
                        "⬡ Connect",
                        id=f"connect-{safe}",
                        classes="connect-link",
                    )
                )
                if len(nodes) > 1:
                    options = [(n, n) for n in nodes]
                    row.mount(
                        Select(
                            options,
                            value=nodes[0],
                            id=f"node-select-{safe}",
                            classes="node-select",
                        )
                    )
            else:
                row.mount(
                    Button(
                        "⬡ Waiting for node\u2026",
                        id=f"connect-{safe}",
                        classes="connect-link",
                        disabled=True,
                    )
                )

            row.mount(Widget(classes="spacer"))
            row.mount(
                Button(
                    "✗ Cancel",
                    id=f"cancel-{safe}",
                    classes="cancel-link",
                )
            )
```

- [ ] **Step 2: Update all `_mount_job_card` call sites to pass job_state and nodelist**

**Chain jobs** (around line 1582): Change:

```python
                _mount_job_card(group, content, title, color, job["jobid"])
```

To:

```python
                _mount_job_card(
                    group, content, title, color, job["jobid"],
                    job["state"], job.get("nodelist", ""),
                )
```

**Standalone jobs** (around line 1621): Change:

```python
            _mount_job_card(container, content, title, color, row["jobid"])
```

To:

```python
            _mount_job_card(
                container, content, title, color, row["jobid"],
                row["state"], row.get("nodelist", ""),
            )
```

- [ ] **Step 3: Commit**

```bash
cd /work/whats2000/cluster-utils/sltop
git add sltop/sltop.py
git commit -m "feat(sltop): refactor job cards to use button row with connect"
```

---

### Task 6: Add Connect button to array panels

**Files:**
- Modify: `sltop/sltop.py:1597-1613` (array panel mounting in `_fill_my_jobs`)

Array panels are mounted differently from regular job cards — they don't use `_mount_job_card`. We need to add the button row inline.

- [ ] **Step 1: Replace array panel cancel button with full button row**

Replace the array panel mounting block (from `for arr in arrays:` through the Cancel button mount) with:

```python
        for arr in arrays:
            content, title, color = _build_array_panel(arr)
            aid = arr[0].get("array_job_id", arr[0]["jobid"])
            safe = _safe_id(aid)
            self._cancel_id_map[safe] = aid
            card = Vertical(classes="job-card")
            card.border_title = title
            card.styles.border = ("round", color)
            container.mount(card)
            card.mount(Static(content))

            # Button row for array panel
            row = Horizontal(classes="button-row")
            card.mount(row)

            # Find RUNNING tasks with their nodes
            running_tasks = [
                j for j in arr if j["state"] == "RUNNING" and j.get("nodelist", "")
            ]

            if running_tasks:
                # Build task→node options: "Task 5 → gn0621"
                options: list[tuple[str, str]] = []
                first_value = ""
                for j in running_tasks:
                    tid = j.get("array_task_id", "?")
                    nodes = _expand_nodelist(j.get("nodelist", ""))
                    for node in nodes:
                        label = f"Task {tid} \u2192 {node}"
                        value = f"{aid}_{tid}|{node}"
                        options.append((label, value))
                        if not first_value:
                            first_value = value

                if options:
                    self._connect_id_map[safe] = (aid, "")
                    row.mount(
                        Button(
                            "⬡ Connect",
                            id=f"connect-{safe}",
                            classes="connect-link",
                        )
                    )
                    row.mount(
                        Select(
                            options,
                            value=first_value,
                            id=f"node-select-{safe}",
                            classes="node-select",
                        )
                    )
                else:
                    row.mount(
                        Button(
                            "⬡ Waiting for node\u2026",
                            id=f"connect-{safe}",
                            classes="connect-link",
                            disabled=True,
                        )
                    )
            else:
                row.mount(
                    Button(
                        "⬡ Waiting for node\u2026",
                        id=f"connect-{safe}",
                        classes="connect-link",
                        disabled=True,
                    )
                )

            row.mount(Widget(classes="spacer"))
            row.mount(
                Button(
                    "✗ Cancel",
                    id=f"cancel-{safe}",
                    classes="cancel-link",
                )
            )
```

- [ ] **Step 2: Commit**

```bash
cd /work/whats2000/cluster-utils/sltop
git add sltop/sltop.py
git commit -m "feat(sltop): add connect button to array job panels"
```

---

### Task 7: Handle Connect button press and exit with connect result

**Files:**
- Modify: `sltop/sltop.py:1828-1880` (`on_button_pressed` method)

- [ ] **Step 1: Add connect button handler**

In the `on_button_pressed` method, add a handler for `connect-` prefixed buttons. Add this **before** the existing `cancel-` check (before line 1831):

```python
        # Handle connect button
        if btn_id.startswith("connect-"):
            safe_id = btn_id.removeprefix("connect-")
            connect_info = self._connect_id_map.get(safe_id)
            if not connect_info:
                return

            job_id, nodelist_str = connect_info

            # Try to find the node-select widget for this job
            try:
                select = self.query_one(f"#node-select-{safe_id}", Select)
                selected = str(select.value)
            except NoMatches:
                # Single-node job: expand the nodelist directly
                nodes = _expand_nodelist(nodelist_str)
                if not nodes:
                    self.notify("No nodes available", severity="error")
                    return
                selected = nodes[0]

            # For array jobs, selected value is "arrayid_taskid|node"
            if "|" in selected:
                job_id, node = selected.split("|", 1)
            else:
                node = selected

            self.exit(result=("connect", job_id, node))
            return
```

- [ ] **Step 2: Commit**

```bash
cd /work/whats2000/cluster-utils/sltop
git add sltop/sltop.py
git commit -m "feat(sltop): handle connect button press with exit result"
```

---

### Task 8: Handle connect result in `main()` with `os.execvp`

**Files:**
- Modify: `sltop/sltop.py:1924-1951` (`main()` function)

- [ ] **Step 1: Update `main()` to handle connect exit result**

Replace the current `main()` function with:

```python
def main() -> None:
    args = _parse_args()
    partition_filter: Optional[list[str]] = (
        [p.strip() for p in args.partitions.split(",")] if args.partitions else None
    )
    app = SlurmMonitor(
        interval=args.interval,
        partition_filter=partition_filter,
        user_filter=args.user,
        idle_timeout=args.idle_timeout,
    )
    result = app.run()

    # Handle connect-to-node action
    if isinstance(result, tuple) and len(result) == 3 and result[0] == "connect":
        _, job_id, node = result
        print(f"\nConnecting to node {node} (job {job_id})...")
        print("Use 'exit' to disconnect and return to your shell.\n")
        os.execvp(
            "srun",
            ["srun", "--overlap", "--jobid", job_id, "--nodelist", node, "--pty", "bash"],
        )

    if app._idle_exit:
        secs = args.idle_timeout
        if secs >= 60:
            minutes = secs // 60
            duration = f"{minutes} minute{'s' if minutes != 1 else ''}"
        else:
            duration = f"{secs} second{'s' if secs != 1 else ''}"
        print(
            f"\nsltop exited after {duration} of inactivity.\n"
            "This is by design to conserve login node resources.\n"
            "Re-run sltop when you need it again."
        )
```

Note: `app.run()` returns the value passed to `self.exit(result=...)`. The `os.execvp` call replaces the process entirely, so the idle-exit check below it only runs for non-connect exits.

- [ ] **Step 2: Commit**

```bash
cd /work/whats2000/cluster-utils/sltop
git add sltop/sltop.py
git commit -m "feat(sltop): replace process with srun on connect action"
```

---

### Task 9: Manual testing on the cluster

**Files:** None (testing only)

- [ ] **Step 1: Launch sltop and verify UI**

Run: `cd /work/whats2000/cluster-utils/sltop && uv run sltop`

Check:
- Navigate to the "④ My Jobs" tab
- RUNNING jobs should show `[ ⬡ Connect ]` (blue) and `[ ✗ Cancel ]` (red, pushed right) with space between
- PENDING jobs should show `[ ⬡ Waiting for node… ]` (greyed out, disabled) and `[ ✗ Cancel ]` (pushed right)
- Multi-node RUNNING jobs should show a node Select dropdown between Connect and Cancel
- Array panels with RUNNING tasks should show Connect + Select with "Task N → nodeX" entries

- [ ] **Step 2: Test connect action (requires a RUNNING job)**

If a job is RUNNING:
1. Click "⬡ Connect" on a RUNNING job card
2. sltop should exit and print "Connecting to node X (job Y)..."
3. You should land in a bash shell on the compute node
4. Type `hostname` to verify you're on the correct node
5. Type `exit` to disconnect

- [ ] **Step 3: Test disabled button**

On a PENDING job, verify the "⬡ Waiting for node…" button does nothing when clicked (it's disabled).

- [ ] **Step 4: Commit final version bump or changelog update if needed**

```bash
cd /work/whats2000/cluster-utils/sltop
git add -A
git commit -m "feat(sltop): connect to compute node via srun --overlap

Add Connect button to RUNNING job cards in My Jobs tab.
Users can attach to a compute node terminal directly from sltop.
Supports multi-node jobs with node selection dropdown.
Array jobs show per-task node selection."
```
