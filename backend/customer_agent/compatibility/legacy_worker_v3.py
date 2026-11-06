"""Fixed stdlib-only worker. JSON in/out, no eval/exec/shell/network/file tools.

Run with Python -I -S, or in the supplied networkless nonroot container.
The process transport is NOT an OS security boundary; use Docker in production.
"""

import copy
import json
import re
import sys

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
    state = copy.deepcopy(request["state"])
    action = request["action"]
    tool = action.get("tool")
    args = action.get("arguments", {})
    if tool not in TOOLS or not isinstance(args, dict) or set(args) != ARGUMENTS[tool]:
        return {
            "state": state,
            "ok": False,
            "error": "unknown_tool_or_arguments",
            "result": {},
        }
    if state.get("ended"):
        return {
            "state": state,
            "ok": False,
            "error": "call_already_ended",
            "result": {},
        }
    stop = state.get("constraint")
    result = {}
    error = None
    if tool == "lookup_policy":
        result = {"policy": state["policy"]}
    elif tool == "speak":
        msg = args.get("text")
        if not isinstance(msg, str) or not msg.strip() or len(msg) > 1000:
            error = "invalid_text"
        elif stop in {"driving", "refusal", "wrong_person"} and not re.search(
            r"再见|结束|不再|稍后|打扰|免打扰|停止", msg
        ):
            error = "unsafe_continuation"
        else:
            state.setdefault("spoken", []).append(msg)
            result = {"text": msg}
    elif tool == "schedule_callback":
        if stop != "busy":
            error = "callback_not_requested"
        else:
            state["callback"] = True
    elif tool == "set_opt_out":
        if stop != "refusal":
            error = "opt_out_not_requested"
        else:
            state["opt_out"] = True
    elif tool == "create_ticket":
        reason = args.get("reason")
        if reason not in {"complaint", "privacy", "injection", "silence"}:
            error = "invalid_ticket_reason"
        elif stop != reason:
            error = "ticket_reason_not_grounded"
        else:
            state["ticket"] = reason
    elif tool == "record_contact":
        if not state.get("consent") or stop:
            error = "explicit_contact_consent_required"
        else:
            state["contact_recorded"] = True
    elif tool == "end_call":
        reason = args.get("reason")
        if reason not in {
            "completed",
            "driving",
            "busy",
            "refusal",
            "wrong_person",
            "handoff",
        }:
            error = "invalid_end_reason"
        else:
            state.update(ended=True, end_reason=reason)
    return {
        "state": state if not error else request["state"],
        "ok": not error,
        "error": error,
        "result": result,
    }


def main():
    line = sys.stdin.buffer.read(131073)
    if len(line) > 131072:
        raise ValueError("input_too_large")
    payload = json.loads(line)
    result = apply(payload)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
