"""Run control (DT.9 as revised): run types, state transitions and area locking.

ops.pipeline_run keeps one row per state change; the current state of a run is its latest row.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from .domain import TERMINAL_RUN_STATES

# Allowed transitions. AWAITING_COST_APPROVAL is a condition of ENRICH (review DT.9); AWAITING_BATCHES
# only in BATCH mode.
TRANSITIONS = {
    "REQUESTED": {"SNAPSHOT_FROZEN", "FAILED", "CANCELLED"},
    "SNAPSHOT_FROZEN": {"RUNNING", "AWAITING_COST_APPROVAL", "FAILED", "CANCELLED"},
    "AWAITING_COST_APPROVAL": {"RUNNING", "FAILED", "CANCELLED"},
    "RUNNING": {"AWAITING_BATCHES", "AWAITING_COST_APPROVAL", "COLLECTING", "CLOSED", "FAILED", "CANCELLED"},
    "AWAITING_BATCHES": {"COLLECTING", "FAILED", "CANCELLED"},
    "COLLECTING": {"AWAITING_BATCHES", "CLOSED", "FAILED"},
    "CLOSED": set(), "FAILED": set(), "CANCELLED": set(),
}


class TransitionError(Exception):
    pass


def check_transition(current: str | None, new: str) -> None:
    if current is None:
        if new != "REQUESTED":
            raise TransitionError(f"a run starts in REQUESTED, not {new}")
        return
    if new not in TRANSITIONS[current]:
        raise TransitionError(f"{current} -> {new} is not allowed")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def current_states(rows: list[dict]) -> dict[str, dict]:
    """run_id -> its latest pipeline_run row."""
    out: dict[str, dict] = {}
    for r in rows:
        cur = out.get(r["run_id"])
        if cur is None or r["inserted_at"] >= cur["inserted_at"]:
            out[r["run_id"]] = r
    return out


def ancestors(area_id: str, areas: dict[str, dict]) -> set[str]:
    out, seen = set(), set()
    cur = areas.get(area_id, {}).get("parent_area_id")
    while cur and cur not in seen:
        seen.add(cur)
        out.add(cur)
        cur = areas.get(cur, {}).get("parent_area_id")
    return out


@dataclass
class LockConflict:
    run_id: str
    area_id: str
    other_area_id: str


def area_conflicts(requested: list[str], running: dict[str, list[str]], areas: dict[str, dict]) -> list[LockConflict]:
    """A new run is rejected only if it shares areas with a run in progress: the same area, one that
    contains it or one it contains (DT.9.3). Runs on different areas go in parallel."""
    out = []
    for run_id, their in running.items():
        for a in requested:
            a_up = ancestors(a, areas)
            for b in their:
                if a == b or b in a_up or a in ancestors(b, areas):
                    out.append(LockConflict(run_id, a, b))
    return out


def in_progress(states: dict[str, dict]) -> dict[str, dict]:
    return {k: v for k, v in states.items() if v["state"] not in TERMINAL_RUN_STATES}
