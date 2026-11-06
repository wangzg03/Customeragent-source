"""Bounded execution harness. No runtime-loaded code or shell capabilities.

Ports follow the lifecycle/session ideas in Globex and DeepSeek Harness; this is
an independent Python implementation, not an import of either SDK.
"""

from collections import Counter
from datetime import datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Protocol

from backend.customer_agent.core.schemas import Action
from backend.customer_agent.tools.worker import apply
from backend.customer_agent.domain.workflow import current_stage
from backend.customer_agent.domain.facts import slots


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checksum(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class HarnessStop(RuntimeError):
    pass


class ModelPort(Protocol):
    def generate(self, messages): ...


class ToolPort(Protocol):
    def call(self, action): ...


class Lifecycle(Protocol):
    """Trusted in-process observers only; exceptions stop the episode."""

    def on_event(self, event): ...


class Journal:
    def __init__(self, path=None, hooks=()):
        self.path = Path(path) if path else None
        self.events = []
        self.hooks = hooks
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # An interrupted stream is retained, never silently appended/retried.
            self.path.open("x", encoding="utf-8").close()

    def emit(self, kind, payload):
        event = {
            "seq": len(self.events),
            "kind": kind,
            "at": datetime.now(timezone.utc).isoformat(),
            "previous": self.events[-1]["sha256"] if self.events else "0" * 64,
            "payload": copy.deepcopy(payload),
        }
        event["sha256"] = checksum(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(canonical(event) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        self.events.append(event)
        for hook in self.hooks:
            hook.on_event(copy.deepcopy(event))
        return event


def replay(events):
    """Verify hashes AND re-execute only the pure simulator reducer, offline.

    A pending intent is reported ambiguous; no network/tool side effect is
    retried. Real adapters must provide transactional idempotency before resume.
    Hash chains detect accidental tampering, not a malicious writer with access.
    """
    previous = "0" * 64
    state = None
    pending = None
    seen = set()
    finished = False
    for index, event in enumerate(events):
        body = {k: v for k, v in event.items() if k != "sha256"}
        if (
            event["seq"] != index
            or event["previous"] != previous
            or checksum(body) != event["sha256"]
        ):
            raise ValueError("journal_integrity_failure")
        if finished:
            raise ValueError("events_after_session_end")
        previous = event["sha256"]
        p = event["payload"]
        if event["kind"] == "session.started":
            if state is not None:
                raise ValueError("duplicate_session_start")
            state = copy.deepcopy(p["state"])
        elif event["kind"] == "tool.intent":
            if pending or p["idempotency_key"] in seen or state is None:
                raise ValueError("duplicate_or_overlapping_tool_intent")
            pending = p
        elif event["kind"] == "tool.completed":
            if not pending or pending["idempotency_key"] != p["idempotency_key"]:
                raise ValueError("tool_result_without_intent")
            expected = apply({"state": state, "action": pending["action"]})
            if (
                expected["ok"] != p["result"]["ok"]
                or expected["state"] != p["state"]
                or expected["result"] != p["result"]["result"]
                or expected.get("error") != p["result"].get("error")
            ):
                raise ValueError("tool_transition_mismatch")
            state = copy.deepcopy(p["state"])
            seen.add(p["idempotency_key"])
            pending = None
        elif event["kind"] == "user.observed":
            # Simulator observation may add consent or an observed stop; it
            # cannot mutate tool-owned fields or business completion.
            if pending or state is None:
                raise ValueError("observation_out_of_order")
            if state.get("reducer_version") in {"4.0", "4.1", "4.2", "4.3", "4.4"}:
                if state["reducer_version"] == "4.0":
                    from backend.customer_agent.compatibility.legacy_workflow_v4_0 import observe_user
                elif state["reducer_version"] == "4.1":
                    from backend.customer_agent.compatibility.legacy_workflow_v4_1 import observe_user
                elif state["reducer_version"] == "4.2":
                    from backend.customer_agent.compatibility.legacy_workflow_v4_2 import observe_user
                elif state["reducer_version"] == "4.3":
                    from backend.customer_agent.compatibility.legacy_workflow_v4_3 import observe_user
                else:
                    from backend.customer_agent.domain.workflow import observe_user

                observed = observe_user(state, p["text"])
                if observed != p.get("state"):
                    raise ValueError("observation_transition_mismatch")
                state = observed
                continue
            from backend.customer_agent.compatibility.legacy_policy_v3 import terminal_intent

            if (
                p.get("consent")
                and not state.get("consent")
                and not any(x in p["text"] for x in ("我同意", "明确同意"))
            ):
                raise ValueError("ungrounded_consent")
            if (
                p.get("constraint")
                and p["constraint"] != state.get("constraint")
                and p["constraint"]
                not in {
                    terminal_intent(p["text"]),
                    "silence",
                }
            ):
                raise ValueError("ungrounded_stop")
            state["consent"] = bool(p.get("consent"))
            if p.get("constraint"):
                state["constraint"] = p["constraint"]
        elif event["kind"] == "session.finished":
            if state is None or p["state_sha256"] != checksum(state):
                raise ValueError("final_checkpoint_mismatch")
            finished = True
    return {
        "integrity_pass": True,
        "complete": finished and not pending,
        "ambiguous_tool_intent": pending is not None,
        "events": len(events),
        "tool_effects": len(seen),
        "state": state,
        "head": previous,
    }


class Harness:
    def __init__(self, settings, state, metadata, journal_path=None, hooks=()):
        self.settings = settings
        self.started = time.monotonic()
        self.journal = Journal(journal_path, hooks)
        self.journal.emit("session.started", {"state": state, **metadata})
        self.counts = Counter()
        self.actions = []
        self.last_progress = None
        self.stalls = 0
        self.tokens = 0
        self.tool_ms = []

    def before_step(self, turn, cancelled):
        if cancelled():
            raise HarnessStop("cancelled")
        if time.monotonic() - self.started >= self.settings.episode_timeout:
            raise HarnessStop("episode_deadline_exceeded")
        if self.tokens >= self.settings.max_tokens:
            raise HarnessStop("token_budget_exceeded")
        self.journal.emit("agent.pre_step", {"turn": turn, "tokens_used": self.tokens})

    def prepare(self, messages):
        # Keep the system policy and original opening plus latest observations.
        # Do not summarize safety policy with another LLM or discard consent.
        result = copy.deepcopy(messages)
        limit = self.settings.context_chars
        if len(canonical(result)) > limit:
            fixed, tail = result[:2], result[2:]
            while tail and len(canonical(fixed + tail)) > limit:
                tail = tail[2:]
            result = fixed + tail
            self.counts["context_compactions"] += 1
            self.journal.emit(
                "context.compacted",
                {"before_messages": len(messages), "after_messages": len(result)},
            )
        if len(canonical(result)) > limit:
            raise HarnessStop("context_budget_exceeded")
        self.journal.emit(
            "model.prepared",
            {
                "request_sha256": checksum(result),
                "messages": len(result),
                "characters": len(canonical(result)),
            },
        )
        return result

    def model_completed(self, record):
        self.tokens += record.get("prompt_tokens", 0) + record.get(
            "completion_tokens", 0
        )
        self.counts["model_calls"] += 1
        self.journal.emit("model.completed", record)

    def execute(self, port: ToolPort, chosen, turn):
        chosen = Action.model_validate(chosen).model_dump()
        fingerprint = checksum(chosen)
        # Three identical consecutive actions cannot make conversational progress.
        if len(self.actions) >= 2 and self.actions[-2:] == [fingerprint] * 2:
            self.counts["loop_stops"] += 1
            raise HarnessStop("repeated_action_loop")
        self.actions.append(fingerprint)
        key = checksum({"session": self.journal.events[0]["sha256"], "turn": turn})
        self.journal.emit("tool.intent", {"idempotency_key": key, "action": chosen})
        result = port.call(chosen)
        self.tool_ms.append(result["latency_ms"])
        self.counts["tool_calls"] += 1
        self.counts["tool_denials"] += int(not result["ok"])
        self.journal.emit(
            "tool.completed",
            {"idempotency_key": key, "result": result, "state": port.state},
        )
        return result

    def observe(self, text, state):
        self.journal.emit(
            "user.observed",
            {
                "text": text,
                "consent": state.get("consent", False),
                "constraint": state.get("constraint"),
                **(
                    {"state": state}
                    if state.get("reducer_version")
                    in {"4.0", "4.1", "4.2", "4.3", "4.4"}
                    else {}
                ),
            },
        )

    def progress(self, steps, state):
        fingerprint = checksum(
            {
                "fact_slots": sorted(slots(state))
                if state.get("reducer_version") == "4.4"
                else None,
                "steps": sorted(steps),
                "consent": state.get("consent"),
                "ended": state.get("ended"),
                "contact": state.get("contact_recorded"),
                "opt_out": state.get("opt_out"),
                "ticket": state.get("ticket"),
                "callback": state.get("callback"),
                "stage": current_stage(state)
                if state.get("reducer_version") in {"4.0", "4.1", "4.2", "4.3", "4.4"}
                else None,
            }
        )
        self.stalls = self.stalls + 1 if fingerprint == self.last_progress else 0
        self.last_progress = fingerprint
        self.journal.emit(
            "agent.progress",
            {"steps": sorted(steps), "consecutive_stalls": self.stalls},
        )
        if self.stalls >= self.settings.max_stalls:
            self.counts["stall_stops"] += 1
            raise HarnessStop("no_business_progress")

    def finish(self, state, error):
        self.journal.emit(
            "session.finished", {"state_sha256": checksum(state), "error": error}
        )
        verified = replay(self.journal.events)
        return {
            "version": state.get("reducer_version", "3.0"),
            "events": self.journal.events,
            "counters": dict(self.counts),
            "tokens_used": self.tokens,
            "replay": {k: v for k, v in verified.items() if k != "state"},
            "tool_latency_ms": self.tool_ms,
            "budgets": {
                "max_turns": self.settings.max_turns,
                "max_tokens": self.settings.max_tokens,
                "episode_timeout_s": self.settings.episode_timeout,
                "context_chars": self.settings.context_chars,
            },
        }
