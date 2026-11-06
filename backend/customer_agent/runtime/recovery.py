"""Recover completed simulator sessions; never retry an ambiguous side effect."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from backend.customer_agent.runtime.harness import replay
from backend.customer_agent.domain.policy import POLICY_VERSION
from backend.customer_agent.core.schemas import JobRequest


def read_events(path):
    events = [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
    ]
    verified = replay(events)
    if not verified["complete"]:
        raise ValueError(
            "incomplete_or_ambiguous_session_requires_manual_reconciliation"
        )
    return events, verified


def inspect_resume(output, cases, request, settings):
    output = Path(output)
    config = json.loads((output / "config.json").read_text(encoding="utf-8"))
    from backend.customer_agent.evaluation.dataset import digest

    if (
        config["environment"]["dataset_sha256"]
        != digest(settings.data_dir / "cases.jsonl")
        or JobRequest.model_validate(config["request"]).model_dump() != request.model_dump()
        or config["selected_case_ids"] != [c.id for c in cases]
    ):
        raise ValueError("resume_configuration_or_dataset_mismatch")
    rows = [
        json.loads(line)
        for line in (output / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    by_id = {c.id: c for c in cases}
    seen = set()
    for row in rows:
        key = (row["case_id"], row["repeat"])
        if (
            key in seen
            or row["case_id"] not in by_id
            or not 0 <= row["repeat"] < request.repeats
        ):
            raise ValueError("resume_duplicate_or_unknown_episode")
        case = by_id[row["case_id"]]
        if (
            hashlib.sha256(case.model_dump_json().encode()).hexdigest()
            != row["case_sha256"]
        ):
            raise ValueError("resume_case_integrity_mismatch")
        events, verified = read_events(
            output / "sessions" / f"{case.id}-{row['repeat']}.jsonl"
        )
        if (
            events != row["harness"]["events"]
            or {k: v for k, v in verified["state"].items() if k != "policy"}
            != row["final_state"]
        ):
            raise ValueError("resume_record_journal_mismatch")
        seen.add(key)
    recovery = []
    for repeat in range(request.repeats):
        for case in cases:
            if (case.id, repeat) in seen:
                continue
            path = output / "sessions" / f"{case.id}-{repeat}.jsonl"
            if path.exists():
                events, verified = read_events(path)
                if (
                    events[0]["payload"].get("case_id") != case.id
                    or events[0]["payload"].get("model") != request.model
                ):
                    raise ValueError("resume_session_identity_mismatch")
                recovery.append((case, repeat, events, verified))
    return config, rows, recovery


def recover_episode(case, repeat, events, verified, config, settings):
    """Rebuild the missing projection from immutable facts; do not edit events."""
    from backend.customer_agent.agent.engine import grade

    missing_model_results = sum(e["kind"] == "model.prepared" for e in events) - sum(
        e["kind"] in {"model.completed", "model.failed"} for e in events
    )
    terminal_error = events[-1]["payload"].get("error")
    if missing_model_results and (
        missing_model_results != 1
        or terminal_error
        not in {
            "invalid_structured_output",
            "generation_truncated",
            "model_unavailable",
            "model_http_or_json_error",
            "retry_exhausted",
        }
    ):
        raise ValueError("missing_model_result_requires_manual_reconciliation")
    trace, calls = [], []
    from backend.customer_agent.domain.workflow import current_stage

    projected_state = events[0]["payload"]["state"]
    turn = 0
    model_this_step = False
    pending = None
    for event in events:
        kind, p = event["kind"], event["payload"]
        if kind == "agent.pre_step":
            turn = p["turn"]
        elif kind == "model.completed":
            calls.append(p)
            model_this_step = True
        elif kind == "model.failed" and p.get("record"):
            calls.append(p["record"])
        elif kind == "tool.intent":
            pending = p["action"]
        elif kind == "tool.completed":
            source = (
                "model"
                if model_this_step
                else "rule_reference"
                if config["request"]["model"] == "rule-reference"
                else "policy_guard"
            )
            trace.append(
                {
                    "turn": turn,
                    "stage": current_stage(projected_state),
                    "source": source,
                    "action": pending,
                    "tool_result": p["result"],
                    "user": None,
                }
            )
            projected_state = p.get("state", projected_state)
            model_this_step = False
        elif kind == "user.observed":
            trace[-1]["user"] = p["text"]
            projected_state = p["state"]
    state = verified["state"]
    if missing_model_results:
        # This is a missing observation marker, NOT a fabricated model reply.
        # The terminal failure is known; raw response, usage and latency are not.
        calls.append(
            {
                "raw": None,
                "error": "model_result_missing_from_legacy_journal",
                "original_error": terminal_error,
                "prompt_tokens": None,
                "completion_tokens": None,
                "latency_ms": None,
                "attempts": None,
                "usage_unknown": True,
            }
        )
    error = events[-1]["payload"].get("error")
    port = SimpleNamespace(state=state, events=[t["tool_result"] for t in trace])
    profile = config["profile_snapshot"]
    elapsed = (
        datetime.fromisoformat(events[-1]["at"])
        - datetime.fromisoformat(events[0]["at"])
    ).total_seconds() * 1000
    counters = {
        "model_calls": len(calls),
        "tool_calls": len(trace),
        "tool_denials": sum(not t["tool_result"]["ok"] for t in trace),
        "context_compactions": sum(e["kind"] == "context.compacted" for e in events),
    }
    if error == "repeated_action_loop":
        counters["loop_stops"] = 1
    if error == "no_business_progress":
        counters["stall_stops"] = 1
    return {
        "schema_version": "2.0",
        "case_id": case.id,
        "group_id": case.group_id,
        "scenario": case.scenario,
        "intent": case.intent,
        "personality": case.personality,
        "split": case.split,
        "model": config["request"]["model"],
        "mode": config["request"]["mode"],
        "generation_contract": state.get("generation_contract", "legacy_unconstrained"),
        "knowledge_mode": config["request"].get("knowledge_mode", "static"),
        "knowledge_snapshot": events[0]["payload"].get("knowledge_snapshot"),
        "retrievals": events[0]["payload"].get("retrievals", []),
        "knowledge_error": events[0]["payload"].get("knowledge_error"),
        "seed": config["request"]["seed"] + repeat,
        "sandbox": config["request"]["sandbox"],
        "policy_version": POLICY_VERSION,
        "profile_id": profile["id"],
        "profile_sha256": profile["sha256"],
        "case_sha256": hashlib.sha256(case.model_dump_json().encode()).hexdigest(),
        "opening": case.opening,
        "expected_terminal": case.expected["terminal"],
        "trace": trace,
        "model_calls": calls,
        "guard_actions": sum(t["source"] == "policy_guard" for t in trace),
        "final_state": {k: v for k, v in state.items() if k != "policy"},
        "duration_ms": round(elapsed, 3),
        "grade": grade(case, port, trace, error),
        "repeat": repeat,
        "recovery": {
            "from_complete_journal": True,
            "duration_clock": "journal UTC timestamp difference",
            "no_model_or_tool_reexecution": True,
            "missing_model_observations": missing_model_results,
        },
        "harness": {
            "version": "3.0-recovered-projection",
            "events": events,
            "counters": counters,
            "tokens_used": sum(
                (c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0)
                for c in calls
            ),
            "replay": {k: v for k, v in verified.items() if k != "state"},
            "tool_latency_ms": [t["tool_result"]["latency_ms"] for t in trace],
            "budgets": {
                "max_turns": settings.max_turns,
                "max_tokens": settings.max_tokens,
                "episode_timeout_s": settings.episode_timeout,
                "context_chars": settings.context_chars,
            },
        },
    }
