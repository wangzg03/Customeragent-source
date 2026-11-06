"""Fixed stdlib-only worker. JSON in/out, no eval/exec/shell/network/file tools.

Run with Python -I -S, or in the supplied networkless nonroot container.
The process transport is NOT an OS security boundary; use Docker in production.
"""

import copy
import json
import re
import sys

from backend.customer_agent.compatibility.legacy_worker_v3 import apply as legacy_apply
from backend.customer_agent.compatibility.legacy_worker_v4_0 import apply as legacy_v4_apply
from backend.customer_agent.compatibility.legacy_worker_v4_1 import apply as legacy_v41_apply
from backend.customer_agent.compatibility.legacy_worker_v4_2 import apply as legacy_v42_apply
from backend.customer_agent.compatibility.legacy_worker_v4_3 import apply as legacy_v43_apply
from backend.customer_agent.domain.speech import choices_for, CONTRACT
from backend.customer_agent.knowledge.evidence import bundle_error, citations
from backend.customer_agent.domain.rules import safe_closing, violations
from backend.customer_agent.domain.workflow import latch, current_stage, stage_speech_pass, terminal_action

TOOLS = {
    "lookup_policy",
    "speak",
    "schedule_callback",
    "set_opt_out",
    "create_ticket",
    "record_contact",
    "end_call",
}
ARGUMENTS = {
    "lookup_policy": set(),
    "speak": {"text"},
    "schedule_callback": set(),
    "set_opt_out": set(),
    "create_ticket": {"reason"},
    "record_contact": set(),
    "end_call": {"reason"},
}


def apply(request):
    if request["state"].get("reducer_version") == "4.0":
        return legacy_v4_apply(request)
    if request["state"].get("reducer_version") == "4.1":
        return legacy_v41_apply(request)
    if request["state"].get("reducer_version") == "4.2":
        return legacy_v42_apply(request)
    if request["state"].get("reducer_version") == "4.3":
        return legacy_v43_apply(request)
    if request["state"].get("reducer_version") != "4.4":
        return legacy_apply(request)
    original = request["state"]
    state = copy.deepcopy(original)
    action = request["action"]
    tool = action.get("tool")
    args = action.get("arguments", {})

    def denied(reason):
        return {"state": original, "ok": False, "error": reason, "result": {}}

    if (
        set(action) != {"tool", "arguments"}
        or tool not in TOOLS
        or not isinstance(args, dict)
        or set(args) != ARGUMENTS[tool]
    ):
        return denied("unknown_tool_or_arguments")
    if state.get("ended"):
        return denied("call_already_ended")
    stop = state.get("stop_latch") or state.get("constraint")
    if stop:
        latch(state, stop)
        expected = terminal_action(state)
        if tool == "speak":
            if not isinstance(args.get("text"), str) or not safe_closing(
                args["text"], state["scenario"], stop
            ):
                return denied("unsafe_continuation")
            if state.get("terminal_closing_count", 0) >= 1:
                return denied("terminal_closing_already_spoken")
        elif action != expected:
            return denied("terminal_action_required")
    if tool == "lookup_policy":
        if (
            state.get("enforce_business", True)
            and current_stage(state) != "lookup_policy"
        ):
            return denied("policy_lookup_out_of_stage")
        if state.get("knowledge_required"):
            bundle = state.get("knowledge", {})
            if error := bundle_error(bundle, state["scenario"], state["facts"]):
                return denied("knowledge:" + error)
            grounded = "\n".join(row["text"] for row in bundle["clauses"])
            if state["policy"] != grounded:
                return denied("knowledge:policy_mismatch")
        state["policy_loaded"] = True
        result = {"policy": state["policy"]}
        if state.get("knowledge_required"):
            result["knowledge_snapshot"] = state["knowledge"]["snapshot_id"]
            result["document_ids"] = [r["document"]["id"] for r in state["knowledge"]["clauses"]]
    elif tool == "speak":
        msg = args["text"]
        if not isinstance(msg, str) or not msg.strip() or len(msg) > 1000:
            return denied("invalid_text")
        if not stop:
            stage = current_stage(state)
            if state.get("knowledge_required") and stage != "connection_check":
                if not state.get("policy_loaded") or bundle_error(state.get("knowledge", {}), state["scenario"], state["facts"]):
                    return denied("knowledge:invalid_evidence")
                if not citations(state["knowledge"], stage):
                    return denied("knowledge:missing_stage_citation")
            topic = (
                "reward"
                if stage.startswith("reward_") and not re.search(r"多日|合同", msg)
                else None
            )
            bad = violations(msg, state["facts"], state["scenario"], topic)
            if bad:
                return denied("speech_rule:" + ",".join(bad))
            if state.get("generation_contract") == CONTRACT and msg not in choices_for(
                state
            ):
                return denied("unapproved_speech")
            if state.get("enforce_business", True) and not stage_speech_pass(
                state, msg
            ):
                return denied("speech_stage_mismatch:" + current_stage(state))
        else:
            for pattern, field in [
                (r"已登记免打扰", "opt_out"),
                (r"已登记待联系", "callback"),
                (r"已转人工", "ticket"),
            ]:
                if re.search(pattern, msg) and not state.get(field):
                    return denied("false_tool_success_claim")
            state["terminal_closing_count"] = state.get("terminal_closing_count", 0) + 1
        state.setdefault("spoken", []).append(msg)
        state.setdefault("speech_contexts", []).append(
            topic if not stop else "terminal"
        )
        result = {"text": msg}
        if state.get("knowledge_required") and not stop and stage != "connection_check":
            result["citations"] = citations(state["knowledge"], stage)
    elif tool in {"schedule_callback", "set_opt_out", "create_ticket"}:
        kind = {"schedule_callback": "busy", "set_opt_out": "refusal"}.get(
            tool, args.get("reason")
        )
        if (
            kind
            not in {"busy", "refusal", "privacy", "injection", "complaint", "silence", "knowledge"}
            or stop != kind
        ):
            return denied("safety_effect_not_grounded")
        state[
            {
                "schedule_callback": "callback",
                "set_opt_out": "opt_out",
                "create_ticket": "ticket",
            }[tool]
        ] = kind if tool == "create_ticket" else True
        latch(state, kind)
        result = {}
    elif tool == "record_contact":
        if (
            state["scenario"] != "course"
            or stop
            or not state.get("consent")
            or current_stage(state) != "record_contact"
        ):
            return denied("explicit_contact_consent_and_completed_business_required")
        state["contact_recorded"] = True
        result = {}
    else:
        reason = args["reason"]
        if reason not in {
            "completed",
            "driving",
            "busy",
            "refusal",
            "wrong_person",
            "handoff",
        }:
            return denied("invalid_end_reason")
        if not stop and (reason != "completed" or current_stage(state) != "completed"):
            return denied("business_not_complete_or_stop_not_grounded")
        state.update(ended=True, end_reason=reason, phase="closed")
        result = {}
    return {"state": state, "ok": True, "error": None, "result": result}


def main():
    line = sys.stdin.buffer.read(131073)
    if len(line) > 131072:
        raise ValueError("input_too_large")
    payload = json.loads(line)
    result = apply(payload)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
