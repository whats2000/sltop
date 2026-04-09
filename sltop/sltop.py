#!/usr/bin/env python3
"""
slurm_monitor.py  —  Interactive SLURM cluster dashboard

Navigation:
  Tab / 1 / 2 / 3   Switch panel  (Resources · Rules · Queue)
  ↑ ↓ ← →           Scroll / select rows in the active table
  Page Up / Dn       Fast scroll
  r                  Force refresh now
  q  /  Ctrl-C       Quit

Usage:
  python slurm_monitor.py                  # default 10-second refresh
  python slurm_monitor.py -n 5             # 5-second refresh
  python slurm_monitor.py -p gpu,cpu       # filter to specific partitions
  python slurm_monitor.py -u $USER         # show only your jobs in queue

Requirements: textual>=0.50  (pip install "textual>=0.50")
"""

from __future__ import annotations

import argparse
import getpass
import math
import os
import subprocess
import time
from typing import Optional

from rich.console import Group
from rich.panel import Panel
from rich.text import Text as RText

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)
from textual.containers import VerticalScroll

# ── SLURM data helpers ────────────────────────────────────────────────────────


def _run(cmd: list[str]) -> str:
    """Run a command, return stdout; silently return '' on any error."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.stdout.strip()
    except Exception:
        return ""


def _resources(partition_filter: Optional[list[str]]) -> list[dict]:
    """
    sinfo columns used:
      %P partition  %a avail  %C cpu A/I/O/T  %G gres  %l timelimit
      %m mem/node(MB)  %D nodes  %N nodelist  %t node-state

    sinfo emits one row per (partition, node-state); we merge CPU counts
    for multi-state partitions so each partition appears once.
    """
    # %F = Node counts A/I/O/T  %C = CPU counts A/I/O/T  %G = GRES per node
    out = _run(["sinfo", "--noheader", "-o", "%P|%a|%C|%F|%G|%l|%m|%D|%t"])
    seen: dict[str, dict] = {}

    def _add_aiot(s1: str, s2: str) -> str:
        try:
            a, b, c, d = (int(x) for x in s1.split("/"))
            e, f, g, h = (int(x) for x in s2.split("/"))
            return f"{a + e}/{b + f}/{c + g}/{d + h}"
        except ValueError:
            return s1

    def _node_state_bucket(state_raw: str) -> str:
        """Map sinfo %t state string to alloc/mix/idle/drain bucket."""
        s = state_raw.lower().rstrip("*+~#$@^!")
        if "mix" in s:
            return "mix"
        if "alloc" in s or "allocated" in s:
            return "alloc"
        if s == "idle":
            return "idle"
        # drain, down, draining, drained, fail, maint, reboot …
        return "drain"

    for raw in out.splitlines():
        parts = raw.split("|")
        if len(parts) < 9:
            continue
        pname, avail, cpus, nodes_aiot, gres, timelimit, mem, nodes_d, state = parts
        pname = pname.rstrip("*")
        if partition_filter and pname not in partition_filter:
            continue
        bucket = _node_state_bucket(state)
        try:
            nd = int(nodes_d)
        except ValueError:
            nd = 0
        if pname not in seen:
            seen[pname] = {
                "partition": pname,
                "avail": avail,
                "cpus": cpus,
                "nodes_aiot": nodes_aiot,
                "gres": gres,
                "timelimit": timelimit,
                "mem_mb": mem,
                "node_alloc": 0,
                "node_mix": 0,
                "node_idle": 0,
                "node_drain": 0,
            }
        else:
            # Merge A/I/O/T CPU counts across multiple node-state rows
            seen[pname]["cpus"] = _add_aiot(seen[pname]["cpus"], cpus)
            seen[pname]["nodes_aiot"] = _add_aiot(seen[pname]["nodes_aiot"], nodes_aiot)
        seen[pname][f"node_{bucket}"] += nd
    return list(seen.values())


def _rules(partition_filter: Optional[list[str]]) -> list[dict]:
    """
    Parse `scontrol show partition` block-format output.
    Each partition block is separated by a blank line; fields are spread
    across indented continuation lines, so we join all lines of a block
    before tokenising — this avoids the line-wrap breakage of --oneliner.
    """
    out = _run(["scontrol", "show", "partition"])
    rows: list[dict] = []
    # Split into per-partition blocks on blank lines
    for block in out.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        fields: dict[str, str] = {}
        # Join all lines so continuation indentation doesn't matter
        for token in " ".join(block.splitlines()).split():
            if "=" in token:
                k, _, v = token.partition("=")
                fields[k] = v
        pname = fields.get("PartitionName", "")
        if not pname:
            continue
        if partition_filter and pname not in partition_filter:
            continue
        rows.append(
            {
                "partition": pname,
                "state": fields.get("State", "?"),
                "max_time": fields.get("MaxTime", "?"),
                "default_time": fields.get("DefaultTime", "?"),
                "min_nodes": fields.get("MinNodes", "0"),
                "max_nodes": fields.get("MaxNodes", "UNLIMITED"),
                "max_cpus_node": fields.get("MaxCPUsPerNode", "UNLIMITED"),
                "priority": fields.get("PriorityTier", "?"),
                "preempt_mode": fields.get("PreemptMode", "?"),
                "allow_groups": fields.get("AllowGroups", "ALL"),
                "allow_accounts": fields.get("AllowAccounts", "ALL"),
                "qos": fields.get("QoS", "-"),
                "oversubscribe": fields.get("OverSubscribe", "?"),
                "tres": fields.get("TRES", ""),
                # Parse GPU total from TRES for graphical display
                "gpu_total": next(
                    (
                        int(s.split("=")[1])
                        for s in fields.get("TRES", "").split(",")
                        if s.startswith("gres/gpu=") and s.split("=")[1].isdigit()
                    ),
                    0,
                ),
                # min_gpu and max_gpu_node populated later after QoS lookup
                "min_gpu": 0,
                "max_gpu_node": 0,
            }
        )
    # Enrich with QoS GPU limits
    qos_map = _qos_limits()
    for row in rows:
        ql = qos_map.get(row["qos"], {})
        row["min_gpu"] = ql.get("min_gpu", 0)
        row["max_gpu_node"] = ql.get("max_gpu_node", 0)
    return rows


def _qos_limits() -> dict[str, dict]:
    """
    Returns {qos_name: {min_gpu, max_gpu, max_gpu_node}} via sacctmgr.
    Falls back to empty dict if sacctmgr is unavailable.
    """
    out = _run(
        [
            "sacctmgr",
            "show",
            "qos",
            "--noheader",
            "--parsable2",
            "format=Name,MinTRES,MaxTRES,MaxTRESPerNode",
        ]
    )
    result: dict[str, dict] = {}

    def _parse_gpu(tres_str: str) -> int:
        for seg in tres_str.split(","):
            if seg.startswith("gres/gpu="):
                try:
                    return int(seg.split("=", 1)[1])
                except ValueError:
                    pass
        return 0

    for line in out.splitlines():
        parts = line.split("|")
        if not parts or not parts[0].strip():
            continue
        name = parts[0].strip()
        min_tres = parts[1].strip() if len(parts) > 1 else ""
        max_tres = parts[2].strip() if len(parts) > 2 else ""
        max_node = parts[3].strip() if len(parts) > 3 else ""
        result[name] = {
            "min_gpu": _parse_gpu(min_tres),
            "max_gpu": _parse_gpu(max_tres),
            "max_gpu_node": _parse_gpu(max_node),
        }
    return result


def _gpu_used_by_partition() -> dict[str, int]:
    """Return {partition: total_allocated_GPU_count} from currently RUNNING jobs."""
    out = _run(["squeue", "--noheader", "-t", "RUNNING", "-o", "%P|%b|%D"])
    result: dict[str, int] = {}
    for raw in out.splitlines():
        parts = raw.split("|")
        if len(parts) < 3:
            continue
        partition, tres_node, nodes_str = parts
        gpu_per_node = 0
        for seg in tres_node.split(","):
            s = seg.strip()
            # formats: "gres/gpu:8" or "gres:gpu:8"
            for prefix in ("gres/gpu:", "gres:gpu:"):
                if s.startswith(prefix):
                    try:
                        gpu_per_node = int(s[len(prefix) :])
                    except ValueError:
                        pass
        try:
            result[partition] = result.get(partition, 0) + gpu_per_node * int(nodes_str)
        except ValueError:
            pass
    return result


# Dark grey for the empty part of bars — avoids Textual theme pollution
_BAR_EMPTY_STYLE = "#404040"


def _make_node_bar(
    alloc: int, mix: int, idle: int, drain: int, total: int, width: int = 25
) -> RText:
    """
    Stacked bar showing 4 node states:
      █ alloc (fully used)  ▓ mix (partially used)  ░ idle (free)  · drain/down
    Colors: red-orange / yellow / green / dark-grey
    """
    t = RText()
    if total <= 0:
        t.append("─" * width, style=_BAR_EMPTY_STYLE)
        return t
    alloc_w = round(alloc / total * width)
    mix_w = round(mix / total * width)
    idle_w = round(idle / total * width)
    drain_w = max(0, width - alloc_w - mix_w - idle_w)
    t.append("█" * alloc_w, style="#dd4422")  # red-orange — fully occupied
    t.append("▓" * mix_w, style="#ddaa00")  # yellow      — mixed (some CPUs free)
    t.append("░" * idle_w, style="#22cc44")  # green       — completely free
    t.append("·" * drain_w, style="#555555")  # dark        — drain / down
    return t


def _make_bar_text(used: int, total: int, width: int = 30) -> RText:
    """Coloured block-character progress bar. Uses explicit RGB to avoid Textual theme bleed."""
    t = RText()
    if total <= 0:
        t.append("─" * width, style=_BAR_EMPTY_STYLE)
        t.append("  N/A", style="#888888")
        return t
    pct = min(used / total, 1.0)
    filled = round(pct * width)
    fill_color = "#22cc44" if pct < 0.7 else ("#ddaa00" if pct < 0.9 else "#dd2222")
    t.append("█" * filled, style=f"bold {fill_color}")
    t.append("░" * (width - filled), style=_BAR_EMPTY_STYLE)
    t.append(f"  {used:>7,} / {total:<7,}  ({pct * 100:>5.1f}%)", style="#cccccc")
    return t


def _make_mini_bar(used: int, total: int, width: int = 20) -> RText:
    """Compact bar without numeric suffix."""
    t = RText()
    if total <= 0:
        t.append("─" * width, style=_BAR_EMPTY_STYLE)
        return t
    pct = min(used / total, 1.0)
    filled = round(pct * width)
    fill_color = "#22cc44" if pct < 0.7 else ("#ddaa00" if pct < 0.9 else "#dd2222")
    t.append("█" * filled, style=f"bold {fill_color}")
    t.append("░" * (width - filled), style=_BAR_EMPTY_STYLE)
    return t


def _build_partition_card(
    row: dict,
    gpu_used: int,
    gpu_total: int,
    rule: dict | None,
) -> Panel:
    """Build a Rich Panel card for one partition with visual bars and constraints."""
    avail = row["avail"]
    avail_color = "bold #22cc44" if avail == "up" else "bold #dd2222"
    sym = "●" if avail == "up" else "○"
    qos = rule["qos"] if rule else "-"
    max_time = rule["max_time"] if rule else row["timelimit"]
    min_nodes = rule["min_nodes"] if rule else "0"
    max_nodes = rule["max_nodes"] if rule else "UNLIMITED"
    min_gpu = rule["min_gpu"] if rule else 0
    max_gpu_nd = rule["max_gpu_node"] if rule else 0

    # ── parse CPU A/I/O/T ──
    cpu_alloc = cpu_idle = cpu_total = 0
    try:
        ca, ci, _co, ct = (int(x) for x in row["cpus"].split("/"))
        cpu_alloc, cpu_idle, cpu_total = ca, ci, ct
    except (ValueError, IndexError):
        pass

    # ── node state breakdown (tracked per-state by _resources) ──
    node_alloc = row.get("node_alloc", 0)
    node_mix = row.get("node_mix", 0)
    node_idle = row.get("node_idle", 0)
    node_drain = row.get("node_drain", 0)
    node_total = node_alloc + node_mix + node_idle + node_drain

    # ── memory label ──
    try:
        mem_label = f"{int(row['mem_mb']) // 1024:,}G"
    except ValueError:
        mem_label = row["mem_mb"] + "M"

    BAR_W = 25

    t = RText()

    # ── top meta line ──
    t.append(f" {sym} ", style=avail_color)
    t.append(f"{avail.upper()}  ", style=avail_color)
    t.append("│  ", style="#555555")
    t.append("MaxTime ", style="#888888")
    t.append(max_time, style="bold #dd88ff")
    t.append("  │  ", style="#555555")
    t.append("QoS ", style="#888888")
    t.append(qos, style="bold #44dddd")
    t.append("  │  ", style="#555555")
    t.append("GRES ", style="#888888")
    t.append(row["gres"], style="bold #ffdd44")
    t.append("  │  ", style="#555555")
    t.append("Mem ", style="#888888")
    t.append(mem_label, style="bold #88aaff")
    t.append("\n")

    # ── constraint line ──
    # Parse GPU count per node from GRES string, e.g. "gpu:H200:8" → 8
    gpu_per_node = 0
    for seg in row["gres"].split(","):
        if seg.strip().startswith("gpu:"):
            parts_g = seg.strip().split(":")
            try:
                gpu_per_node = int(parts_g[-1])
                break
            except ValueError:
                pass

    constraints: list[str] = []
    if min_gpu > 0:
        constraints.append(f"Min GPU/job: {min_gpu}")
        # Cascade: min GPU → implied minimum node count
        if gpu_per_node > 0:
            implied_nodes = math.ceil(min_gpu / gpu_per_node)
            if implied_nodes > 1:
                constraints.append(f"\u21aa Min nodes (implied): {implied_nodes}")
    if max_gpu_nd > 0:
        constraints.append(f"Max GPU/node: {max_gpu_nd}")
    if min_nodes != "0":
        constraints.append(f"Min nodes: {min_nodes}")
    if max_nodes not in ("UNLIMITED", "0"):
        constraints.append(f"Max nodes: {max_nodes}")
    if constraints:
        t.append(" \u2691 Constraints: ", style="bold #ffaa00")
        t.append("  ·  ".join(constraints), style="#ffaa00")
        t.append("\n")

    t.append("\n")

    # ── utilisation bars ──
    def _bar_line(label: str, used: int, total: int) -> None:
        t.append(f"  {label:<9}", style="bold #dddddd")
        t.append_text(_make_bar_text(used, total, BAR_W))
        t.append("\n")

    _bar_line("CPU used", cpu_alloc, cpu_total)
    # CPU idle: compact mini-bar inline
    idle_pct = (cpu_idle / cpu_total * 100) if cpu_total > 0 else 0.0
    t.append(f"  {'CPU idle':<9}", style="bold #dddddd")
    t.append_text(_make_mini_bar(cpu_idle, cpu_total, BAR_W))
    t.append(f"  {cpu_idle:>6,} ({idle_pct:>5.1f}%)", style="#aaaaaa")
    t.append("\n")

    if gpu_total > 0:
        _bar_line("GPU used", gpu_used, gpu_total)
    elif gpu_used > 0:
        t.append("  GPU used ", style="bold #dddddd")
        t.append(f"{gpu_used:,} in use (total unknown)", style="#ffdd44")
        t.append("\n")

    # ── Node state stacked bar: alloc | mix | idle | drain ──
    t.append(f"  {'Nodes':<9}", style="bold #dddddd")
    t.append_text(
        _make_node_bar(node_alloc, node_mix, node_idle, node_drain, node_total, BAR_W)
    )
    t.append(f"  {node_total} total", style="#888888")
    t.append("\n")
    # Per-state count legend — indented one blank line below the bar
    t.append("\n")
    t.append("   " + " " * 9)  # indent to align with bar
    t.append("\u2588 alloc ", style="#dd4422")
    t.append(f"{node_alloc:<4}", style="#ffaa88")
    t.append("\u25a3 mix ", style="#ddaa00")
    t.append(f"{node_mix:<4}", style="#ffdd88")
    t.append("\u2591 idle ", style="#22cc44")
    t.append(f"{node_idle:<4}", style="#aaffaa")
    if node_drain > 0:
        t.append("\u00b7 drain ", style="#555555")
        t.append(f"{node_drain}", style="#dd4444")
    t.append("\n")

    return Panel(
        t,
        title=f"[bold #ffffff on #003388]  {row['partition']}  [/]",
        border_style="#4488ee",
        expand=True,
        padding=(0, 1),
    )


def _cluster_node_states() -> dict[str, int]:
    """
    Cluster-wide node counts by state, without partition grouping.
    Each physical node is counted exactly once.
    Returns: {"alloc": N, "mix": N, "idle": N, "drain": N}
    """
    # %t = state, %D = count of nodes in that state (cluster-wide, no -p)
    out = _run(["sinfo", "--noheader", "-o", "%t|%D"])

    def _bucket(state_raw: str) -> str:
        s = state_raw.lower().rstrip("*+~#$@^!")
        if "mix" in s:
            return "mix"
        if "alloc" in s:
            return "alloc"
        if s == "idle":
            return "idle"
        return "drain"

    counts: dict[str, int] = {"alloc": 0, "mix": 0, "idle": 0, "drain": 0}
    for raw in out.splitlines():
        parts = raw.split("|")
        if len(parts) < 2:
            continue
        try:
            counts[_bucket(parts[0])] += int(parts[1])
        except (ValueError, KeyError):
            pass
    return counts


def _build_cluster_summary(
    resource_rows: list[dict],
    gpu_used_map: dict[str, int],
    rules_map: dict[str, dict],
) -> Panel:
    """Top-of-dashboard overall cluster stats panel."""
    total_cpu = alloc_cpu = 0
    total_gpu = alloc_gpu = 0

    for row in resource_rows:
        try:
            ca, _ci, _co, ct = (int(x) for x in row["cpus"].split("/"))
            alloc_cpu += ca
            total_cpu += ct
        except (ValueError, IndexError):
            pass
        rule = rules_map.get(row["partition"])
        total_gpu += rule["gpu_total"] if rule else 0
        alloc_gpu += gpu_used_map.get(row["partition"], 0)

    # Node counts from cluster-wide query (not sum of partitions, avoids double-count)
    ns = _cluster_node_states()
    cn_alloc = ns["alloc"]
    cn_mix = ns["mix"]
    cn_idle = ns["idle"]
    cn_drain = ns["drain"]
    cn_total = cn_alloc + cn_mix + cn_idle + cn_drain

    BAR_W = 30
    t = RText()
    t.append("  CLUSTER TOTAL\n", style="bold white")
    t.append("  CPU   ", style="bold #dddddd")
    t.append_text(_make_bar_text(alloc_cpu, total_cpu, BAR_W))
    t.append("\n")
    if total_gpu > 0:
        t.append("  GPU   ", style="bold #dddddd")
        t.append_text(_make_bar_text(alloc_gpu, total_gpu, BAR_W))
        t.append("\n")
    # Stacked node bar (cluster-wide, no double-count)
    t.append("  Nodes ", style="bold #dddddd")
    t.append_text(_make_node_bar(cn_alloc, cn_mix, cn_idle, cn_drain, cn_total, BAR_W))
    t.append(f"  {cn_total} total", style="#888888")
    t.append("\n")
    # Legend — one blank line below
    t.append("\n")
    t.append("         ")
    t.append("\u2588 alloc ", style="#dd4422")
    t.append(f"{cn_alloc:<4}", style="#ffaa88")
    t.append("\u25a3 mix ", style="#ddaa00")
    t.append(f"{cn_mix:<4}", style="#ffdd88")
    t.append("\u2591 idle ", style="#22cc44")
    t.append(f"{cn_idle:<4}", style="#aaffaa")
    if cn_drain > 0:
        t.append("\u00b7 drain ", style="#555555")
        t.append(f"{cn_drain}", style="#dd4444")
    t.append("\n")

    return Panel(t, border_style="#44cccc", expand=True, padding=(0, 1))


def _build_rules_card(rule: dict) -> Panel:
    """Dashboard card for one partition's rules/constraints."""
    state = rule["state"]
    state_color = "#22cc44" if state == "UP" else "#dd2222"
    sym = "●" if state == "UP" else "○"

    def _kv(label: str, value: str, val_style: str = "#cccccc") -> RText:
        row = RText()
        row.append(f"  {label:<22}", style="#888888")
        row.append(value, style=val_style)
        row.append("\n")
        return row

    def _divider() -> RText:
        d = RText()
        d.append("  " + "─" * 50 + "\n", style="#333333")
        return d

    t = RText()
    # ── status + QoS header ──
    t.append(f" {sym} ", style=state_color)
    t.append(state, style=f"bold {state_color}")
    t.append("  │  ", style="#555555")
    t.append("QoS ", style="#888888")
    t.append(rule["qos"], style="bold #44dddd")
    t.append("  │  ", style="#555555")
    t.append("Priority ", style="#888888")
    t.append(rule["priority"], style="bold #ffdd88")
    t.append("  │  ", style="#555555")
    t.append("OverSubscribe ", style="#888888")
    t.append(rule["oversubscribe"], style="bold #aaaaff")
    t.append("\n\n")

    # ── Time limits ──
    t.append_text(_kv("⏱ MaxTime", rule["max_time"], "bold #dd88ff"))
    t.append_text(_kv("⏱ DefaultTime", rule["default_time"], "#cc88ff"))
    t.append_text(_divider())

    # ── Node/CPU limits ──
    t.append_text(_kv("□ MinNodes", rule["min_nodes"], "#88ddff"))
    t.append_text(_kv("□ MaxNodes", rule["max_nodes"], "#88ddff"))
    t.append_text(_kv("☰ MaxCPUs/Node", rule["max_cpus_node"], "#88ddff"))
    t.append_text(_divider())

    # ── GPU constraints (only if non-trivial) ──
    # Derive GPU-per-node from TRES (e.g. gres/gpu=1760, node=220 → 8/node)
    tres_node_count = 0
    for seg in rule["tres"].split(","):
        if seg.strip().startswith("node="):
            try:
                tres_node_count = int(seg.strip()[5:])
            except ValueError:
                pass
    gpu_per_node_rule = (
        rule["gpu_total"] // tres_node_count
        if tres_node_count > 0 and rule["gpu_total"] > 0
        else 0
    )

    gpu_lines = []
    if rule["min_gpu"] > 0:
        implied = (
            math.ceil(rule["min_gpu"] / gpu_per_node_rule)
            if gpu_per_node_rule > 0
            else 0
        )
        line = f"  \u26a1 Min GPU/job    {rule['min_gpu']}"
        if implied > 1:
            line += f"   \u21aa implies \u2265 {implied} nodes"
        elif implied == 1:
            line += "   \u21aa implies \u2265 1 node"
        gpu_lines.append(line + "\n")
    if rule["max_gpu_node"] > 0:
        gpu_lines.append(f"  \u26a1 Max GPU/node   {rule['max_gpu_node']}\n")
    if rule["gpu_total"] > 0:
        extra = f"  ({gpu_per_node_rule}/node)" if gpu_per_node_rule > 0 else ""
        gpu_lines.append(f"  \u26a1 Total GPUs     {rule['gpu_total']:,}{extra}\n")
    for gl in gpu_lines:
        g = RText()
        g.append(gl, style="bold #ffaa00")
        t.append_text(g)
    if gpu_lines:
        t.append_text(_divider())

    # ── Access ──
    t.append_text(_kv("👥 AllowGroups", rule["allow_groups"], "#aaffaa"))
    t.append_text(_kv("👥 AllowAccounts", rule["allow_accounts"], "#aaffaa"))
    t.append_text(_divider())

    # ── TRES ──
    tres_parts = rule["tres"].split(",")
    t.append("  TRES\n", style="#888888")
    for seg in tres_parts:
        if seg.strip():
            t.append(f"    {seg.strip()}\n", style="#99ccff")

    return Panel(
        t,
        title=f"[bold #ffffff on #003388]  {rule['partition']}  [/]",
        border_style="#4488cc",
        expand=True,
        padding=(0, 1),
    )


# Stable per-partition colors derived from partition name hash
_PARTITION_COLORS = [
    "#44bbff",
    "#ffaa44",
    "#aa88ff",
    "#44ffaa",
    "#ff88aa",
    "#88ffdd",
    "#ffdd44",
    "#88aaff",
    "#ff6688",
    "#44ddff",
]


def _partition_color(name: str) -> str:
    return _PARTITION_COLORS[hash(name) % len(_PARTITION_COLORS)]


def _build_job_card(row: dict, rule: Optional[dict]) -> Panel:
    """Rich Panel card for a single job — enhanced My Jobs visualization."""
    state = row["state"]
    p_color = _partition_color(row["partition"])

    # State color + symbol
    _SC = {
        "RUNNING": ("#22cc44", "▶"),
        "PENDING": ("#ddaa00", "⏳"),
        "COMPLETING": ("#44dddd", "↻"),
        "FAILED": ("#dd2222", "✗"),
        "CANCELLED": ("#dd2222", "✗"),
        "TIMEOUT": ("#dd4422", "⏱"),
    }
    s_color, s_sym = _SC.get(state, ("#aaaaaa", "?"))

    # Reason (translated, with config-error detection)
    raw_reason = row["reason"].strip()
    if raw_reason.startswith("(") and raw_reason.endswith(")"):
        raw_reason = raw_reason[1:-1].strip()
    base_reason = raw_reason.partition(",")[0].strip()
    is_config_error = state == "PENDING" and base_reason in _CONFIG_ERROR_REASONS
    translated = _translate_reason(row["reason"], rule, job=row)

    # GPU bar (only when known)
    req_gpu = _parse_job_gpu(row["gres"])
    rule_gpu_total = rule.get("gpu_total", 0) if rule else 0

    t = RText()

    # ── Header line ──
    t.append(f" {s_sym} ", style=f"bold {s_color}")
    t.append(state, style=f"bold {s_color}")
    t.append("  │  ", style="#555555")
    t.append("Job ", style="#888888")
    t.append(row["jobid"], style="bold #ffffff")
    t.append("  │  ", style="#555555")
    t.append("Partition ", style="#888888")
    t.append(row["partition"], style=f"bold {p_color}")
    t.append("  │  ", style="#555555")
    t.append("User ", style="#888888")
    t.append(row["user"], style="bold #aaddff")
    t.append("\n")

    # ── Job name ──
    t.append("  ", style="")
    t.append("Name   ", style="#888888")
    t.append(row["name"], style="bold #ffffff")
    t.append("\n\n")

    # ── Time ──
    t.append("  ", style="")
    t.append("Elapsed    ", style="#888888")
    t.append(row["elapsed"], style="#44ffaa")
    t.append("  /  ", style="#555555")
    t.append("Limit ", style="#888888")
    t.append(row["timelimit"], style="#ffaa44")
    t.append("\n")

    # ── Resources ──
    t.append("  ", style="")
    t.append("Nodes      ", style="#888888")
    t.append(row["nodes"], style="#88ddff")
    t.append("  │  ", style="#555555")
    t.append("GRES ", style="#888888")
    t.append(row["gres"], style="#ffdd44")
    t.append("\n")

    # ── GPU mini-bar if we know total ──
    if req_gpu > 0 and rule_gpu_total > 0:
        t.append("  ")
        t.append("GPU req    ", style="#888888")
        t.append_text(_make_mini_bar(req_gpu, rule_gpu_total, 20))
        t.append(f"  {req_gpu} / {rule_gpu_total}", style="#cccccc")
        t.append("\n")

    # ── Reason ──
    t.append("\n")
    if is_config_error:
        t.append("  ⚠ ", style="bold #dd2222")
        t.append(translated, style="bold #dd2222")
    elif state == "PENDING":
        t.append("  ", style="")
        t.append("Reason     ", style="#888888")
        t.append(translated, style="#ddaa00")
    elif state == "RUNNING":
        t.append("  ", style="")
        t.append("Nodes      ", style="#888888")
        t.append(translated, style="#aaaaaa")
    t.append("\n")

    border = "#dd2222" if is_config_error else s_color
    title_state = f"[bold {s_color}]{s_sym} {state}[/]"
    title_name = row["name"][:30] + ("…" if len(row["name"]) > 30 else "")
    return Panel(
        t,
        title=f"{title_state}  [bold #ffffff]{row['jobid']}[/]  [#aaaaaa]{title_name}[/]",
        border_style=border,
        expand=True,
        padding=(0, 1),
    )


def _build_compact_job_card(row: dict, rule: Optional[dict]) -> Panel:
    """Compact Rich Panel for a job inside a chain -- fewer lines than full card."""
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


_CONFIG_ERROR_REASONS: frozenset[str] = frozenset(
    {
        "QOSMinGRES",
        "QOSMaxGRESPerJob",
        "QOSMaxGRESPerNode",
        "QOSMaxWallDurationPerJob",
        "PartitionTimeLimit",
        "PartitionNodeLimit",
        "BadConstraints",
        "InvalidAccount",
        "InvalidQOS",
        "InvalidPartition",
        "DependencyNeverSatisfied",
        "PartitionDown",
        "PartitionInactive",
        "NodeDown",
        "JobHeldAdmin",
        "launch failed requeued held",
    }
)

_NODE_UNAVAIL_REASONS: frozenset[str] = frozenset(
    {
        "ReqNodeNotAvail",
    }
)


def _parse_job_gpu(gres: str) -> int:
    """Parse requested GPUs from squeue %b field, e.g. 'gpu:8' → 8."""
    if not gres or gres in ("(null)", "N/A", "-"):
        return 0
    for part in gres.split(","):
        p = part.strip().lower()
        if p.startswith("gpu:"):
            try:
                return int(p[4:].split(":")[0])
            except ValueError:
                pass
    return 0


def _translate_reason(
    reason: str,
    rule: Optional[dict],
    job: Optional[dict] = None,
) -> str:
    """
    Convert terse SLURM reason codes into human-readable explanations.
    When a rule dict is provided, actual partition limits are shown.
    When the job dict is provided, the job's own request is compared against
    the rule so the message pinpoints exactly which limit is violated.
    """
    r = reason.strip()
    if not r or r in ("-", "None"):
        return r

    # squeue %R wraps codes in parens: "(QOSMinGRES)" or "(ReqNodeNotAvail,node001)"
    display_r = r
    if r.startswith("(") and r.endswith(")"):
        r = r[1:-1].strip()

    # squeue sometimes appends node-list after comma, e.g. "ReqNodeNotAvail,node001"
    base, _, suffix = r.partition(",")
    base = base.strip()
    b = base.lower()

    min_gpu = rule.get("min_gpu", 0) if rule else 0
    max_gpu_n = rule.get("max_gpu_node", 0) if rule else 0
    max_time = rule.get("max_time", "?") if rule else "?"
    max_nodes = rule.get("max_nodes", "?") if rule else "?"
    min_nodes = rule.get("min_nodes", "?") if rule else "?"

    # Job-side values (what the job actually requested)
    req_gpu = _parse_job_gpu(job["gres"]) if job else 0
    req_nodes = int(job["nodes"]) if job else 0
    try:
        req_nodes = int(job["nodes"]) if job else 0
    except (ValueError, TypeError):
        req_nodes = 0
    req_timelimit = job["timelimit"] if job else "?"

    def _gpu_detail(limit: int, actual: int, label: str) -> str:
        if actual > 0 and limit > 0:
            return f"{label} (you requested {actual}, limit is {limit})"
        if limit > 0:
            return f"{label} (limit is {limit})"
        return label

    _MAP: dict[str, str] = {
        # ── Common waiting reasons ──
        "Priority": "Waiting — higher-priority jobs are ahead in the queue",
        "Resources": "Waiting — not enough CPU/GPU/memory free right now",
        "WaitingForScheduling": "Just submitted — scheduler hasn't processed it yet",
        "BeginTime": "Waiting — job has a future start time (--begin)",
        "Dependency": "Waiting for dependent job(s) to finish",
        "DependencyNeverSatisfied": "\u26a0 Dependency can never be satisfied (check --dependency)",
        # ── QoS / GPU limits ──
        "QOSMinGRES": _gpu_detail(
            min_gpu,
            req_gpu,
            f"\u26a0 Min GPU not met — partition requires \u2265{min_gpu} GPU/job",
        ),
        "QOSMaxGRESPerJob": _gpu_detail(
            max_gpu_n, req_gpu, "\u26a0 GPU request exceeds QoS per-job maximum"
        ),
        "QOSMaxGRESPerNode": (
            f"\u26a0 GPUs/node exceeds QoS limit (you requested {req_gpu} GPU "
            f"across {req_nodes} node(s), max {max_gpu_n}/node)"
            if req_gpu > 0 and req_nodes > 0 and max_gpu_n > 0
            else f"\u26a0 GPUs/node exceeds QoS limit (max {max_gpu_n}/node)"
        ),
        "QOSMaxGRESPerUser": "\u26a0 Your total GPU usage exceeds QoS user limit",
        "QOSMaxJobsPerUser": "\u26a0 Too many running jobs for your QoS — wait for one to finish",
        "QOSMaxSubmitJobPerUser": "\u26a0 Too many pending+running jobs for your QoS",
        "QOSMaxCpuPerUser": "\u26a0 Your total CPU usage exceeds QoS user limit",
        "QOSMaxNodePerUser": "\u26a0 Your total node usage exceeds QoS user limit",
        "QOSMaxMemoryPerUser": "\u26a0 Your total memory usage exceeds QoS user limit",
        "QOSMaxWallDurationPerJob": (
            f"\u26a0 Walltime exceeds QoS limit (you requested {req_timelimit}, max {max_time})"
            if req_timelimit not in ("?", "N/A", "") and max_time != "?"
            else f"\u26a0 Walltime exceeds QoS limit (max {max_time})"
        ),
        "QOSJobLimit": "\u26a0 QoS total concurrent job limit reached",
        "QOSResourceLimit": "\u26a0 QoS total resource limit reached",
        "QOSUsageThreshold": "\u26a0 QoS usage threshold exceeded",
        # ── Partition rules ──
        "PartitionDown": "\u26a0 Partition is DOWN",
        "PartitionInactive": "\u26a0 Partition is INACTIVE",
        "PartitionNodeLimit": (
            f"\u26a0 Node count outside partition limits (you requested {req_nodes}, "
            f"allowed: {min_nodes}–{max_nodes})"
            if req_nodes > 0
            else f"\u26a0 Node count outside partition limits (allowed: {min_nodes}–{max_nodes})"
        ),
        "PartitionTimeLimit": (
            f"\u26a0 Walltime exceeds partition limit (you requested {req_timelimit}, max {max_time})"
            if req_timelimit not in ("?", "N/A", "") and max_time != "?"
            else f"\u26a0 Walltime exceeds partition limit (max {max_time})"
        ),
        # ── Node availability ──
        "NodeDown": "\u26a0 Required node(s) are DOWN",
        "ReqNodeNotAvail": f"\u26a0 Requested specific node(s) not available: {suffix or 'see node list'}",
        "BadConstraints": "\u26a0 No nodes match your --constraint/feature request",
        "InactiveLimit": "\u26a0 Job exceeded inactive time limit",
        # ── Account / association ──
        "InvalidAccount": "\u26a0 Account not valid or not permitted in this partition",
        "InvalidQOS": "\u26a0 QoS is invalid for this account/partition",
        "InvalidPartition": "\u26a0 Partition does not exist or you lack permission",
        "AssocMaxJobsLimit": "\u26a0 Account/association running job limit reached",
        "AssocGrpGRESLimit": (
            f"\u26a0 GPU quota exhausted for your account/group (you have {req_gpu} GPU in this job)"
            if req_gpu > 0
            else "\u26a0 GPU quota exhausted for your account/group"
        ),
        "AssocGrpCpuLimit": "\u26a0 CPU quota exhausted for your account/group",
        "AssocGrpNodeLimit": "\u26a0 Node quota exhausted for your account/group",
        "AssocGrpSubmitJobsLimit": "\u26a0 Submit quota exhausted for your account/group",
        # ── Miscellaneous ──
        "Licenses": "Waiting for software license(s)",
        "launch failed requeued held": "\u26a0 Launch failed — job requeued and held (scontrol release)",
        "JobHeldUser": "Job held by user (scontrol release <jobid> to release)",
        "JobHeldAdmin": "\u26a0 Job held by admin — contact sysadmin",
    }

    if base in _MAP:
        return f"{display_r}  \u2192  {_MAP[base]}"

    # Fuzzy fallback patterns — include any known rule values
    if "qosmin" in b:
        extra = f" (partition min GPU: {min_gpu})" if min_gpu else ""
        return f"{display_r}  \u2192  \u26a0 Min resource requirement not met for QoS{extra}"
    if "qosmax" in b:
        return f"{display_r}  \u2192  \u26a0 Exceeds a QoS maximum limit"
    if "assoc" in b:
        return f"{display_r}  \u2192  \u26a0 Account/association limit reached"
    if "partition" in b:
        return f"{display_r}  \u2192  \u26a0 Partition constraint violated"
    if "dependency" in b:
        return f"{display_r}  \u2192  Waiting on job dependency"
    if "held" in b:
        return f"{display_r}  \u2192  Job is held"
    return display_r


def _queue(
    partition_filter: Optional[list[str]],
    user_filter: Optional[str],
) -> list[dict]:
    """Return current SLURM queue rows."""
    cmd = ["squeue", "--noheader", "-o", "%i|%P|%u|%j|%T|%M|%l|%D|%b|%R|%E|%F|%K"]
    if user_filter:
        cmd += ["-u", user_filter]
    out = _run(cmd)
    rows: list[dict] = []
    for raw in out.splitlines():
        parts = raw.split("|")
        if len(parts) < 13:
            continue
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
    return rows


def _group_my_jobs(
    my_jobs: list[dict],
) -> tuple[list[list[dict]], list[list[dict]], list[dict]]:
    """Classify user jobs into chains, arrays, and standalone.

    Returns:
        (chains, arrays, standalone) where:
        - chains: list of ordered job lists (each chain is root->leaf)
        - arrays: list of job-task groups (each group shares array_job_id)
        - standalone: list of individual jobs
    """
    import re

    # -- Index by job ID --
    jobs_by_id: dict[str, dict] = {}
    for j in my_jobs:
        jobs_by_id[j["jobid"]] = j

    # -- Parse dependencies -> build graph --
    children: dict[str, list[str]] = {}   # parent_id -> [child_ids]
    parents: dict[str, list[str]] = {}    # child_id  -> [parent_ids]

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

    # -- Find chain roots and walk chains --
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

    # -- Group array jobs --
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

    # -- Standalone: everything else --
    standalone = [
        j for j in my_jobs
        if j["jobid"] not in in_chain and j["jobid"] not in in_array
    ]

    return chains, arrays, standalone


# ── Textual App ───────────────────────────────────────────────────────────────


class SlurmMonitor(App):
    """Full-screen SLURM cluster dashboard with scrollable, navigable tables."""

    CSS = """
    TabbedContent {
        height: 1fr;
    }
    TabPane {
        padding: 0;
    }
    DataTable {
        height: 1fr;
    }
    #resources-scroll {
        height: 1fr;
        padding: 1 2;
        background: $background;
    }
    #resources-content {
        height: auto;
    }
    #rules-scroll {
        height: 1fr;
        padding: 1 2;
        background: $background;
    }
    #rules-content {
        height: auto;
    }
    #myjobs-scroll {
        height: 1fr;
        padding: 1 2;
        background: $background;
    }
    #myjobs-content {
        height: auto;
    }
    #statusbar {
        display: none;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("1", "switch_tab('tab-resources')", "Resources"),
        Binding("2", "switch_tab('tab-rules')", "Rules"),
        Binding("3", "switch_tab('tab-queue')", "Queue"),
        Binding("4", "switch_tab('tab-myjobs')", "My Jobs"),
        Binding("escape", "focus_table", "Focus table", show=False),
    ]

    def __init__(
        self,
        interval: int,
        partition_filter: Optional[list[str]],
        user_filter: Optional[str],
    ) -> None:
        super().__init__()
        self.interval = interval
        self.partition_filter = partition_filter
        self.user_filter = user_filter
        self._queue_all_rows: list[dict] = []
        self._rules_cache: list[dict] = []
        # Queue sort state: column key (None = no sort), and reverse flag
        self._sort_col: Optional[str] = None
        self._sort_rev: bool = False
        # Store current user for highlighting in queue view
        self.current_user = getpass.getuser()

    # Column key → dict field mapping (same order as add_columns below)
    _COL_KEYS = [
        ("jobid", "jobid"),
        ("partition", "partition"),
        ("user", "user"),
        ("name", "name"),
        ("state", "state"),
        ("elapsed", "elapsed"),
        ("timelimit", "timelimit"),
        ("nodes", "nodes"),
        ("gres", "gres"),
        ("reason", "reason"),
    ]

    # ── Layout ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-resources"):
            with TabPane("① Resources", id="tab-resources"):
                with VerticalScroll(id="resources-scroll"):
                    yield Static("Loading…", id="resources-content")
            with TabPane("② Rules", id="tab-rules"):
                with VerticalScroll(id="rules-scroll"):
                    yield Static("Loading…", id="rules-content")
            with TabPane("③ Queue", id="tab-queue"):
                tbl = DataTable(id="tbl-queue", zebra_stripes=True, cursor_type="row")
                for display, key in [
                    ("JobID", "jobid"),
                    ("Partition", "partition"),
                    ("User", "user"),
                    ("Job Name", "name"),
                    ("State", "state"),
                    ("Elapsed", "elapsed"),
                    ("Time Limit", "timelimit"),
                    ("Nodes", "nodes"),
                    ("GRES/Node", "gres"),
                    ("Reason / NodeList", "reason"),
                ]:
                    tbl.add_column(display, key=key)
                yield tbl
            with TabPane("④ My Jobs", id="tab-myjobs"):
                with VerticalScroll(id="myjobs-scroll"):
                    yield Static("Loading…", id="myjobs-content")
        yield Footer()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.title = "SLURM Monitor"
        filters = []
        if self.partition_filter:
            filters.append(f"partitions=[{','.join(self.partition_filter)}]")
        if self.user_filter:
            filters.append(f"user={self.user_filter}")
        self.sub_title = (
            "  ".join(filters) + "  ·  " if filters else ""
        ) + f"every {self.interval}s"
        self._do_refresh()
        self.set_interval(self.interval, self._do_refresh)

    def _make_subtitle(self, ts: str, running: int, pending: int, total: int) -> str:
        filters = []
        if self.partition_filter:
            filters.append(f"partitions=[{','.join(self.partition_filter)}]")
        if self.user_filter:
            filters.append(f"user={self.user_filter}")
        base = "  ".join(filters) + "  ·  " if filters else ""
        return (
            base
            + f"✦ run:{running}  ⏳ pend:{pending}  Σ:{total}"
            + f"  ·  {ts}"
            + f"  ·  every {self.interval}s"
        )

    # ── Data refresh ───────────────────────────────────────────────────────

    def _do_refresh(self) -> None:
        # Cache rules first so both Resources and Rules tabs share one SLURM call
        self._rules_cache = _rules(self.partition_filter)
        self._fill_resources()
        self._fill_rules()
        total, running, pending = self._fill_queue()
        self._fill_my_jobs()
        ts = time.strftime("%H:%M:%S")
        self.sub_title = self._make_subtitle(ts, running, pending, total)

    def _fill_my_jobs(self) -> None:
        """Build enhanced per-job cards for the current user."""
        current_user = os.environ.get("USER", "") or os.environ.get("LOGNAME", "")
        rules_map = {r["partition"]: r for r in self._rules_cache}
        my_rows = [r for r in self._queue_all_rows if r["user"] == current_user]
        if my_rows:
            cards = [
                _build_job_card(row, rules_map.get(row["partition"])) for row in my_rows
            ]
            content = Group(*cards)
        else:
            content = "No jobs found for current user."
        try:
            self.query_one("#myjobs-content", Static).update(content)
        except NoMatches:
            pass

    def _fill_resources(self) -> None:
        resource_rows = _resources(self.partition_filter)
        gpu_used_map = _gpu_used_by_partition()
        rules_map = {r["partition"]: r for r in self._rules_cache}

        summary = _build_cluster_summary(resource_rows, gpu_used_map, rules_map)
        cards: list[Panel] = [
            _build_partition_card(
                row,
                gpu_used_map.get(row["partition"], 0),
                rules_map.get(row["partition"], {}).get("gpu_total", 0),
                rules_map.get(row["partition"]),
            )
            for row in resource_rows
        ]

        try:
            self.query_one("#resources-content", Static).update(
                Group(summary, *cards) if cards else Group(summary)
            )
        except NoMatches:
            pass

    def _fill_rules(self) -> None:
        cards = [_build_rules_card(r) for r in self._rules_cache]
        try:
            self.query_one("#rules-content", Static).update(
                Group(*cards) if cards else "No partition data."
            )
        except NoMatches:
            pass

    _STATE_COLOR = {
        "RUNNING": "bold green",
        "PENDING": "bold yellow",
        "COMPLETING": "bold cyan",
        "FAILED": "bold red",
        "CANCELLED": "red",
        "TIMEOUT": "red",
    }

    def _fill_queue(self) -> tuple[int, int, int]:
        """Fetch fresh SLURM queue data, store it, then render with active filters."""
        self._queue_all_rows = _queue(self.partition_filter, self.user_filter)
        return self._apply_queue_filter()

    def _apply_queue_filter(self) -> tuple[int, int, int]:
        """Re-render queue table applying current filters and sort order."""
        tbl = self.query_one("#tbl-queue", DataTable)
        # Save scroll offsets before clear() resets them
        saved_y = tbl.scroll_y
        saved_x = tbl.scroll_x
        tbl.clear()

        # Filter — show all jobs (no My Jobs filter here; that's tab 4)
        rows = list(self._queue_all_rows)

        # Sort — numeric for jobid/nodes, lexicographic for the rest
        if self._sort_col is not None:

            def _sort_key(r: dict) -> str | int:
                v = r.get(self._sort_col, "")
                if self._sort_col in ("jobid", "nodes"):
                    try:
                        return int(v)
                    except (ValueError, TypeError):
                        pass
                return str(v)

            rows.sort(key=_sort_key, reverse=self._sort_rev)

        running = pending = 0
        rules_map = {r["partition"]: r for r in self._rules_cache}
        for row in rows:
            state = row["state"]
            if state == "RUNNING":
                running += 1
            elif state == "PENDING":
                pending += 1
            rule = rules_map.get(row["partition"])
            translated = _translate_reason(row["reason"], rule, job=row)

            # Config-error PENDING jobs: show INCORRECT_CONFIG + reason in red
            # Node-unavail PENDING jobs: show NODE_UNAVAILABLE + reason in orange
            raw_reason = row["reason"].strip()
            # Strip parens that squeue adds: "(QOSMinGRES)" → "QOSMinGRES"
            if raw_reason.startswith("(") and raw_reason.endswith(")"):
                raw_reason = raw_reason[1:-1].strip()
            base_reason = raw_reason.partition(",")[0].strip()
            is_config_error = (
                state == "PENDING" and base_reason in _CONFIG_ERROR_REASONS
            )
            is_node_unavail = (
                state == "PENDING" and base_reason in _NODE_UNAVAIL_REASONS
            )
            if is_config_error:
                display_state = "[bold #dd2222]INCORRECT_CONFIG[/]"
                display_reason = f"[bold #dd2222]{translated}[/]"
            elif is_node_unavail:
                display_state = "[bold #44dddd]NODE_UNAVAILABLE[/]"
                display_reason = f"[bold #44dddd]{translated}[/]"
            else:
                display_state = f"[{self._STATE_COLOR.get(state, 'white')}]{state}[/]"
                display_reason = translated

            # Truncate long job names with an ellipsis
            name = row["name"]
            if len(name) > 22:
                name = name[:21] + "…"

            # Highlight current user in queue view
            user_display = row["user"]
            if row["user"] == self.current_user:
                user_display = f"[bold #ffff00 on #333333]{user_display}[/]"

            tbl.add_row(
                row["jobid"],
                f"[bold {_partition_color(row['partition'])}]{row['partition']}[/]",
                user_display,
                name,
                display_state,
                row["elapsed"],
                row["timelimit"],
                row["nodes"],
                row["gres"],
                display_reason,
            )

        # Restore scroll position after layout pass — scroll_to overrides any
        # cursor-driven scrolling that clear()/add_row() might trigger
        def _restore() -> None:
            tbl.scroll_to(x=saved_x, y=saved_y, animate=False)

        self.call_after_refresh(_restore)
        return len(rows), running, pending

    # ── Events ─────────────────────────────────────────────────────────────

    def on_input_changed(self, event) -> None:
        pass  # no filter inputs

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Click a column header to sort; click again to reverse."""
        if event.data_table.id != "tbl-queue":
            return
        key = (
            event.column_key.value
        )  # .value gives the str key we passed to add_column()
        if key == self._sort_col:
            if self._sort_rev:
                # 3rd click: remove sort entirely
                self._sort_col = None
                self._sort_rev = False
                _cleared = True
            else:
                self._sort_rev = True
                _cleared = False
        else:
            self._sort_col = key
            self._sort_rev = False
            _cleared = False
        # Refresh column labels with sort arrow (no arrow when sort cleared)
        tbl = self.query_one("#tbl-queue", DataTable)
        col_labels = {
            "jobid": "JobID",
            "partition": "Partition",
            "user": "User",
            "name": "Job Name",
            "state": "State",
            "elapsed": "Elapsed",
            "timelimit": "Time Limit",
            "nodes": "Nodes",
            "gres": "GRES/Node",
            "reason": "Reason / NodeList",
        }
        arrow = " \u25bc" if self._sort_rev else " \u25b2"
        for col in tbl.columns.values():
            col_key = col.key.value  # .value, not str()
            base = col_labels.get(col_key, col_key)
            col.label = (
                base + arrow if (not _cleared and col_key == self._sort_col) else base
            )
        tbl.refresh()
        self._apply_queue_filter()

    # ── Actions ────────────────────────────────────────────────────────────

    def action_refresh_now(self) -> None:
        self._do_refresh()

    def action_switch_tab(self, tab_id: str) -> None:
        try:
            self.query_one(TabbedContent).active = tab_id
        except NoMatches:
            pass

    def action_focus_table(self) -> None:
        """Press Esc to focus the queue table."""
        try:
            self.query_one("#tbl-queue", DataTable).focus()
        except NoMatches:
            pass


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Interactive SLURM cluster monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "-n",
        "--interval",
        type=int,
        default=10,
        metavar="SECS",
        help="Refresh interval in seconds (default: 10)",
    )
    p.add_argument(
        "-p",
        "--partitions",
        default=None,
        metavar="P1,P2",
        help="Comma-separated partition filter (default: all)",
    )
    p.add_argument(
        "-u",
        "--user",
        default=None,
        metavar="USER",
        help="Show only jobs for USER in queue (default: all)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    partition_filter: Optional[list[str]] = (
        [p.strip() for p in args.partitions.split(",")] if args.partitions else None
    )
    SlurmMonitor(
        interval=args.interval,
        partition_filter=partition_filter,
        user_filter=args.user,
    ).run()


if __name__ == "__main__":
    main()
