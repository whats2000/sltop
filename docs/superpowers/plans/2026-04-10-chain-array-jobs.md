# Chain & Array Job Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the sltop My Jobs tab to visually group SLURM dependency chains and job arrays with vertical chain panels and array summary panels.

**Architecture:** Extend the existing `squeue` format string with dependency/array fields (`%E`, `%F`, `%K`). Add a grouping layer that classifies jobs into chains, arrays, or standalone. Add three new rendering functions for each category. Only the My Jobs tab changes.

**Tech Stack:** Python 3.8+, Rich (panels, text), Textual (TUI framework), SLURM CLI (`squeue`)

**Spec:** `docs/superpowers/specs/2026-04-10-chain-array-jobs-design.md`

---

## File Structure

All changes are in a single file:

- **Modify:** `sltop/sltop.py`
  - `_queue()` (line 963-996): add `%E`, `%F`, `%K` to format string and parsing
  - New function `_group_my_jobs()`: chain/array grouping logic
  - New function `_build_compact_job_card()`: compact card for chain inner jobs
  - New function `_build_chain_panel()`: vertical chain panel renderer
  - New function `_build_array_panel()`: array summary panel renderer
  - `_fill_my_jobs()` (line 1159-1174): updated to use grouping + new renderers

No new files, no new dependencies, no tests directory exists (project has none).

---

### Task 1: Extend `_queue()` with dependency and array fields

**Files:**
- Modify: `sltop/sltop.py:963-996`

- [ ] **Step 1: Update the squeue format string**

In `_queue()`, change the format string from 10 fields to 13 fields:

```python
cmd = ["squeue", "--noheader", "-o", "%i|%P|%u|%j|%T|%M|%l|%D|%b|%R|%E|%F|%K"]
```

- [ ] **Step 2: Update the field count check**

Change the minimum field count from 10 to 13:

```python
if len(parts) < 13:
    continue
```

- [ ] **Step 3: Update the destructuring and dict**

Extend the unpacking to include the three new fields and add them to the dict:

```python
(
    jobid, partition, user, name, state, elapsed,
    timelimit, nodes, gres, reason, dependency, array_job_id, array_task_id,
) = parts[:13]
if partition_filter and partition not in partition_filter:
    continue
rows.append(
    {
        "jobid": jobid,
        "partition": partition,
        "user": user,
        "name": name,
        "state": state,
        "elapsed": elapsed,
        "timelimit": timelimit,
        "nodes": nodes,
        "gres": gres,
        "reason": reason,
        "dependency": dependency,
        "array_job_id": array_job_id,
        "array_task_id": array_task_id,
    }
)
```

- [ ] **Step 4: Verify no breakage in existing tabs**

Run the app manually (`python -m sltop`) to verify:
- Queue tab still renders correctly (new fields are not displayed in the table)
- My Jobs tab still shows existing cards
- Resources and Rules tabs unaffected

- [ ] **Step 5: Commit**

```bash
git add sltop/sltop.py
git commit -m "feat: extend squeue with dependency and array fields (%E %F %K)"
```

---

### Task 2: Add `_group_my_jobs()` function

**Files:**
- Modify: `sltop/sltop.py` (insert after `_queue()` function, around line 997)

- [ ] **Step 1: Write the grouping function**

Insert this function after `_queue()` and before the `class SlurmMonitor` line:

```python
def _group_my_jobs(
    my_jobs: list[dict],
) -> tuple[list[list[dict]], list[list[dict]], list[dict]]:
    """Classify user jobs into chains, arrays, and standalone.

    Returns:
        (chains, arrays, standalone) where:
        - chains: list of ordered job lists (each chain is root→…→leaf)
        - arrays: list of job-task groups (each group shares array_job_id)
        - standalone: list of individual jobs
    """
    import re

    # ── Index by job ID ──
    jobs_by_id: dict[str, dict] = {}
    for j in my_jobs:
        jobs_by_id[j["jobid"]] = j

    # ── Parse dependencies → build graph ──
    children: dict[str, list[str]] = {}   # parent_id → [child_ids]
    parents: dict[str, list[str]] = {}    # child_id  → [parent_ids]

    dep_re = re.compile(r"(?:after\w*):(\d+)")

    for j in my_jobs:
        dep_str = j.get("dependency", "")
        if not dep_str or dep_str in ("(null)", ""):
            continue
        for m in dep_re.finditer(dep_str):
            parent_id = m.group(1)
            child_id = j["jobid"]
            # Only track edges where parent is also in user's jobs
            if parent_id in jobs_by_id:
                children.setdefault(parent_id, []).append(child_id)
                parents.setdefault(child_id, []).append(parent_id)

    # ── Find chain roots and walk chains ──
    in_chain: set[str] = set()
    chains: list[list[dict]] = []

    # Roots: jobs that have children but no parents in the set
    roots = [
        jid for jid in jobs_by_id
        if jid in children and jid not in parents
    ]
    # Sort roots by job ID for deterministic ordering
    roots.sort(key=lambda x: int(x) if x.isdigit() else x)

    for root in roots:
        chain: list[dict] = []
        stack = [root]
        visited: set[str] = set()
        while stack:
            cur = stack.pop(0)  # BFS for chain ordering
            if cur in visited or cur not in jobs_by_id:
                continue
            visited.add(cur)
            chain.append(jobs_by_id[cur])
            in_chain.add(cur)
            for child in sorted(children.get(cur, []),
                                key=lambda x: int(x) if x.isdigit() else x):
                if child not in visited:
                    stack.append(child)
        if len(chain) >= 2:
            chains.append(chain)
        else:
            # Single-node "chain" is not a chain
            in_chain.discard(root)

    # Also mark jobs that are children but whose root wasn't detected
    # (parent already completed and left the queue)
    for jid in parents:
        if jid not in in_chain and jid in jobs_by_id:
            # This job has a dependency on something outside the set;
            # it's standalone unless part of a detected chain
            pass

    # ── Group array jobs ──
    array_groups: dict[str, list[dict]] = {}
    for j in my_jobs:
        aid = j.get("array_job_id", "N/A")
        if aid and aid != "N/A" and aid != "0" and j["jobid"] not in in_chain:
            array_groups.setdefault(aid, []).append(j)

    arrays: list[list[dict]] = []
    in_array: set[str] = set()
    for aid in sorted(array_groups, key=lambda x: int(x) if x.isdigit() else x):
        group = array_groups[aid]
        if len(group) >= 2:
            arrays.append(group)
            for j in group:
                in_array.add(j["jobid"])

    # ── Standalone: everything else ──
    standalone = [
        j for j in my_jobs
        if j["jobid"] not in in_chain and j["jobid"] not in in_array
    ]

    return chains, arrays, standalone
```

- [ ] **Step 2: Verify function parses correctly**

Run `python -c "import sltop.sltop"` to confirm no syntax errors.

- [ ] **Step 3: Commit**

```bash
git add sltop/sltop.py
git commit -m "feat: add _group_my_jobs() for chain/array classification"
```

---

### Task 3: Add `_build_compact_job_card()` for chain inner cards

**Files:**
- Modify: `sltop/sltop.py` (insert after `_build_job_card()`, around line 776)

- [ ] **Step 1: Write the compact card function**

Insert after the `_build_job_card()` function (after line 775):

```python
def _build_compact_job_card(row: dict, rule: Optional[dict]) -> Panel:
    """Compact Rich Panel for a job inside a chain — fewer lines than full card."""
    state = row["state"]
    p_color = _partition_color(row["partition"])

    _SC = {
        "RUNNING": ("#22cc44", "\u25b6"),
        "PENDING": ("#ddaa00", "\u23f3"),
        "COMPLETING": ("#44dddd", "\u21bb"),
        "FAILED": ("#dd2222", "\u2717"),
        "CANCELLED": ("#dd2222", "\u2717"),
        "TIMEOUT": ("#dd4422", "\u23f1"),
    }
    s_color, s_sym = _SC.get(state, ("#aaaaaa", "?"))

    t = RText()

    # Line 1: ID | partition | GRES
    t.append(row["jobid"], style="bold #ffffff")
    t.append("  \u2502  ", style="#555555")
    t.append(row["partition"], style=f"bold {p_color}")
    t.append("  \u2502  ", style="#555555")
    t.append(row["gres"] if row["gres"] else "no gpu", style="#ffdd44")
    t.append("\n")

    # Line 2: time or reason
    if state == "RUNNING":
        t.append(row["elapsed"], style="#44ffaa")
        t.append("  /  ", style="#555555")
        t.append(row["timelimit"], style="#ffaa44")
    elif state == "PENDING":
        raw_reason = row["reason"].strip()
        if raw_reason.startswith("(") and raw_reason.endswith(")"):
            raw_reason = raw_reason[1:-1].strip()
        t.append(_translate_reason(row["reason"], rule, job=row), style="#ddaa00")
    elif state in ("COMPLETED", "COMPLETING"):
        t.append(row["elapsed"], style="#44ffaa")
    else:
        t.append(row["elapsed"], style="#aaaaaa")

    title_name = row["name"][:25] + ("\u2026" if len(row["name"]) > 25 else "")
    return Panel(
        t,
        title=f"[bold {s_color}]{title_name} {s_sym} {state}[/]",
        border_style=s_color,
        expand=True,
        padding=(0, 1),
    )
```

- [ ] **Step 2: Verify no syntax errors**

Run `python -c "import sltop.sltop"` to confirm.

- [ ] **Step 3: Commit**

```bash
git add sltop/sltop.py
git commit -m "feat: add _build_compact_job_card() for chain inner cards"
```

---

### Task 4: Add `_build_chain_panel()` for vertical chain visualization

**Files:**
- Modify: `sltop/sltop.py` (insert after `_build_compact_job_card()`)

- [ ] **Step 1: Write the chain panel function**

Insert after `_build_compact_job_card()`:

```python
def _build_chain_panel(chain: list[dict], rules_map: dict[str, dict]) -> Panel:
    """Rich Panel showing a vertical dependency chain with arrow connectors."""
    parts: list = []
    for i, job in enumerate(chain):
        rule = rules_map.get(job["partition"])
        parts.append(_build_compact_job_card(job, rule))
        if i < len(chain) - 1:
            # Connector between cards
            connector = RText()
            connector.append("              \u2502\n", style="#555555")
            connector.append("              \u25bc", style="#555555")
            parts.append(connector)

    # Chain title with state summary
    n_done = sum(1 for j in chain if j["state"] in ("COMPLETED", "COMPLETING"))
    n_run = sum(1 for j in chain if j["state"] == "RUNNING")
    n_pend = sum(1 for j in chain if j["state"] == "PENDING")
    n_fail = sum(1 for j in chain if j["state"] in ("FAILED", "CANCELLED", "TIMEOUT"))

    status_parts: list[str] = []
    if n_done:
        status_parts.append(f"[#22cc44]\u2713{n_done}[/]")
    if n_run:
        status_parts.append(f"[#22cc44]\u25b6{n_run}[/]")
    if n_pend:
        status_parts.append(f"[#ddaa00]\u23f3{n_pend}[/]")
    if n_fail:
        status_parts.append(f"[#dd2222]\u2717{n_fail}[/]")
    status = " ".join(status_parts)

    title = f"[bold #88aaff]Chain: {len(chain)} jobs[/]  {status}"

    return Panel(
        Group(*parts),
        title=title,
        border_style="#4488cc",
        expand=True,
        padding=(1, 2),
    )
```

- [ ] **Step 2: Verify no syntax errors**

Run `python -c "import sltop.sltop"` to confirm.

- [ ] **Step 3: Commit**

```bash
git add sltop/sltop.py
git commit -m "feat: add _build_chain_panel() for vertical chain visualization"
```

---

### Task 5: Add `_build_array_panel()` for array summary

**Files:**
- Modify: `sltop/sltop.py` (insert after `_build_chain_panel()`)

- [ ] **Step 1: Write the array panel function**

Insert after `_build_chain_panel()`:

```python
def _build_array_panel(array_jobs: list[dict]) -> Panel:
    """Rich Panel summarizing a job array with progress bar and state counts."""
    total = len(array_jobs)

    # Count states
    completed = sum(1 for j in array_jobs if j["state"] in ("COMPLETED", "COMPLETING"))
    running = sum(1 for j in array_jobs if j["state"] == "RUNNING")
    pending = sum(1 for j in array_jobs if j["state"] == "PENDING")
    failed = sum(1 for j in array_jobs if j["state"] in ("FAILED", "CANCELLED", "TIMEOUT"))

    # Array name from first job
    array_name = array_jobs[0]["name"]
    array_name_display = array_name[:25] + ("\u2026" if len(array_name) > 25 else "")

    # Progress bar
    bar_width = 20
    filled = round(completed / total * bar_width) if total else 0
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
    if completed == total:
        bar_color = "#22cc44"
    elif failed > 0:
        bar_color = "#dd2222"
    else:
        bar_color = "#ddaa00"

    t = RText()

    # Progress line
    t.append(f"  [{bar}] {completed}/{total}\n", style=bar_color)
    t.append("\n")

    # State breakdown
    if completed:
        t.append("  \u2713 ", style="#22cc44")
        t.append(f"Completed: {completed}\n", style="#22cc44")
    if running:
        running_tasks = [
            j.get("array_task_id", "?") for j in array_jobs if j["state"] == "RUNNING"
        ]
        task_str = ", ".join(running_tasks[:10])
        if len(running_tasks) > 10:
            task_str += f" \u2026+{len(running_tasks) - 10}"
        t.append("  \u25b6 ", style="#22cc44")
        t.append(f"Running: {running}  (task {task_str})\n", style="#22cc44")
    if pending:
        t.append("  \u23f3 ", style="#ddaa00")
        t.append(f"Pending: {pending}\n", style="#ddaa00")
    if failed:
        failed_tasks = [
            j.get("array_task_id", "?")
            for j in array_jobs
            if j["state"] in ("FAILED", "CANCELLED", "TIMEOUT")
        ]
        task_str = ", ".join(failed_tasks[:10])
        if len(failed_tasks) > 10:
            task_str += f" \u2026+{len(failed_tasks) - 10}"
        t.append("  \u2717 ", style="#dd2222")
        t.append(f"Failed: {failed}  (task {task_str})\n", style="#dd2222")

    aid = array_jobs[0].get("array_job_id", "?")
    title = f"[bold #aa88ff]Array: {array_name_display}[/]  [#aaaaaa]({aid})[/]"

    return Panel(
        t,
        title=title,
        border_style="#aa88ff",
        expand=True,
        padding=(0, 1),
    )
```

- [ ] **Step 2: Verify no syntax errors**

Run `python -c "import sltop.sltop"` to confirm.

- [ ] **Step 3: Commit**

```bash
git add sltop/sltop.py
git commit -m "feat: add _build_array_panel() for array job summary"
```

---

### Task 6: Update `_fill_my_jobs()` to use grouping and new renderers

**Files:**
- Modify: `sltop/sltop.py:1159-1174`

- [ ] **Step 1: Rewrite `_fill_my_jobs()`**

Replace the existing `_fill_my_jobs()` method (lines 1159-1174) with:

```python
    def _fill_my_jobs(self) -> None:
        """Build enhanced per-job cards for the current user, grouping chains and arrays."""
        current_user = os.environ.get("USER", "") or os.environ.get("LOGNAME", "")
        rules_map = {r["partition"]: r for r in self._rules_cache}
        my_rows = [r for r in self._queue_all_rows if r["user"] == current_user]

        if not my_rows:
            try:
                self.query_one("#myjobs-content", Static).update(
                    "No jobs found for current user."
                )
            except NoMatches:
                pass
            return

        chains, arrays, standalone = _group_my_jobs(my_rows)

        panels: list = []

        # 1. Chain panels (sorted by earliest job ID)
        chains.sort(
            key=lambda c: min(
                int(j["jobid"]) if j["jobid"].isdigit() else 0 for j in c
            )
        )
        for chain in chains:
            panels.append(_build_chain_panel(chain, rules_map))

        # 2. Array panels (sorted by array job ID)
        arrays.sort(
            key=lambda a: int(a[0].get("array_job_id", "0"))
            if a[0].get("array_job_id", "0").isdigit()
            else 0
        )
        for arr in arrays:
            panels.append(_build_array_panel(arr))

        # 3. Standalone jobs (sorted by job ID)
        standalone.sort(
            key=lambda j: int(j["jobid"]) if j["jobid"].isdigit() else 0
        )
        for row in standalone:
            panels.append(_build_job_card(row, rules_map.get(row["partition"])))

        try:
            self.query_one("#myjobs-content", Static).update(Group(*panels))
        except NoMatches:
            pass
```

- [ ] **Step 2: Verify no syntax errors**

Run `python -c "import sltop.sltop"` to confirm.

- [ ] **Step 3: Manual end-to-end test**

Run `python -m sltop` and switch to the My Jobs tab (key `4`). Verify:
- Standalone jobs render as before
- If you have chain jobs, they appear in vertical chain panels with connectors
- If you have array jobs, they appear as summary panels with progress bars
- Ordering is chains first, then arrays, then standalone

- [ ] **Step 4: Commit**

```bash
git add sltop/sltop.py
git commit -m "feat: update _fill_my_jobs() with chain/array grouping"
```

---

### Task 7: Final verification and edge case check

- [ ] **Step 1: Test with no jobs**

Ensure the "No jobs found for current user." message still displays when there are no jobs.

- [ ] **Step 2: Test dependency field parsing edge cases**

Verify `_group_my_jobs()` handles these `%E` values gracefully:
- `(null)` — no dependencies
- Empty string — no dependencies
- `afterok:12345` — single dependency
- `afterok:12345,afterany:12346` — multiple dependencies
- `afterok:12345_*` — dependency on array job (the `_*` suffix)

If `_*` array dependency syntax needs handling, update the regex in `_group_my_jobs()` from:

```python
dep_re = re.compile(r"(?:after\w*):(\d+)")
```

This already handles `afterok:12345_*` correctly — it captures `12345` and ignores `_*`.

- [ ] **Step 3: Test array field edge cases**

Verify `array_job_id` values of `N/A`, `0`, and empty string are treated as non-array jobs.

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add sltop/sltop.py
git commit -m "fix: handle edge cases in chain/array grouping"
```
