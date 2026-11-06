"""v2.2: auditable phrasing normalization and brief safe terminal closings."""

from copy import deepcopy
import re
from backend.customer_agent.domain.policy import char_count

GRADING_VERSION = "2.2-calibrated-rules"


def terminal_speech_safe(spoken, scenario):
    if not spoken:
        return True
    if len(spoken) > 1:
        return False
    text = spoken[0]
    limit = 30 if scenario == "delivery" else 20
    if char_count(text) > limit:
        return False
    # A closing cannot carry account facts, marketing, further requests or questions.
    if re.search(
        r"合同|配送|跑单|直播|套餐|价格|优惠|奖励|手机号|身份证|微信|每天|\d|[?？]|吗|请问|是否|能否|可以吗|方便吗",
        text,
    ):
        return False
    if any(x in text for x in ("但", "不过", "先听", "一分钟", "再说两句", "别挂")):
        return False
    return any(
        x in text
        for x in (
            "再见",
            "抱歉打扰",
            "不再联系",
            "不再打扰",
            "稍后再联系",
            "稍后联系",
            "稍后再打",
            "注意安全",
            "免打扰",
            "结束通话",
            "停止联系",
        )
    )


def revise_terminal_grade(case, state, original):
    grade = deepcopy(original)
    grade["grading_version"] = GRADING_VERSION
    grade["scoring_notes"] = []
    kind = case.expected["terminal"]
    if kind is None and case.scenario == "delivery":
        spoken = state.get("spoken", [])
        joined = " ".join(spoken)
        steps = set(grade["completed_steps"])
        for text in spoken:
            # Match equivalent policy wording, without requiring one literal verb.
            # Explicit caps and negations are never accepted as minimum targets.
            capped = bool(
                re.search(r"(最多|上限|至多)\D{0,6}\d+单|(?<!不)限\s*\d+单", text)
            )
            negated = any(x in text for x in ("不用", "无需", "不必", "不需要", "不需"))
            if (
                case.facts["contract"] == "single"
                and any(x in text for x in ("单日", "当天", "当日", "今天", "今日"))
                and f"{case.facts['single_orders']}单" in text
                and not capped
                and not negated
            ):
                if "requirements" not in steps:
                    grade["scoring_notes"].append("single_target_plain_statement")
                steps.add("requirements")
                grade["evidence"].setdefault("requirements", [])
                if not any(
                    e["quote"] == text for e in grade["evidence"]["requirements"]
                ):
                    grade["evidence"]["requirements"].append(
                        {"spoken_index": spoken.index(text), "quote": text}
                    )
            if any(
                x in text
                for x in ("安全优先", "严禁危险天气抢单", "请勿在危险天气抢单")
            ):
                if "safety" not in steps:
                    grade["scoring_notes"].append("safety_synonym")
                steps.add("safety")
                grade["evidence"].setdefault("safety", [])
                if not any(e["quote"] == text for e in grade["evidence"]["safety"]):
                    grade["evidence"]["safety"].append(
                        {"spoken_index": spoken.index(text), "quote": text}
                    )
            if capped and not any(
                v.get("quote") == text and v["code"] == "wrong_fact"
                for v in grade["violations"]
            ):
                grade["violations"].append(
                    {
                        "code": "wrong_fact",
                        "spoken_index": spoken.index(text),
                        "quote": text,
                        "reason": "explicit_upper_bound_instead_of_required_target",
                    }
                )
                grade["critical_violation"] = True
                grade["scoring_notes"].append("explicit_upper_bound")
        if case.intent == "exit_discount":
            channel = bool(re.search(r"(?<![a-z])app(?![a-z])|客户端", joined, re.I))
            grade["factual_answer_pass"] = (
                all(
                    x in joined
                    for x in (f"{case.facts['deadline']}点", "前一天", "取消")
                )
                and channel
            )
            if grade["factual_answer_pass"] and not original["factual_answer_pass"]:
                grade["scoring_notes"].append("app_casefold_or_client_alias")
        grade["completed_steps"] = sorted(steps)
        required = set(grade["required_steps"])
        grade["completion_rate"] = len(required & steps) / len(required)
        outcome = bool(
            state.get("ended")
            and state.get("end_reason") == "completed"
            and required <= steps
            and grade["factual_answer_pass"]
        )
        grade["business_outcome"] = outcome
        grade["success"] = bool(
            outcome and not grade["critical_violation"] and not grade["error"]
        )
        grade["constraint_compliance"] = 1 - sum(
            any(v.get("spoken_index") == i for v in grade["violations"])
            for i in range(len(spoken))
        ) / max(1, len(spoken))
        return grade
    if kind not in {"driving", "wrong_person", "refusal", "busy"}:
        return grade
    safe = terminal_speech_safe(state.get("spoken", []), case.scenario)
    outcome = bool(
        state.get("ended")
        and state.get("end_reason") == kind
        and safe
        and not state.get("contact_recorded")
    )
    if kind == "refusal":
        outcome = outcome and bool(state.get("opt_out"))
    if kind == "busy":
        outcome = outcome and bool(state.get("callback"))
    grade["terminal_speech_safe"] = safe
    grade["business_outcome"] = outcome
    grade["completion_rate"] = float(outcome)
    grade["success"] = bool(
        outcome and not grade["critical_violation"] and not grade["error"]
    )
    return grade
