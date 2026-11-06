"""Public-observation state machine; no case intent or expected label is read here."""

from copy import deepcopy
import re

try:
    from backend.customer_agent.compatibility.legacy_rules_v4_0 import terminal_intent, detect_steps, answer_pass
    from backend.customer_agent.compatibility.legacy_normalization_v4_0 import normalize
except ImportError:
    from legacy_rules_v4_0 import terminal_intent, detect_steps, answer_pass
    from legacy_normalization_v4_0 import normalize

VERSION = "4.0"
STAGES = {
    "contract_notice": "告知当前配送合同已生效。",
    "availability": "只询问今天是否方便配送，等待回答。",
    "requirements": "只说明当前合同类型的准确最低单量和天数，不要混入另一种合同。",
    "safety": "提醒路上注意安全。",
    "identity": "只询问对方是否为机构负责人。",
    "options": "说明发布页新增标准直播和低延迟选项。",
    "standard": "只介绍标准直播延迟5-10秒，费用低。",
    "low_latency": "只介绍低延迟直播延迟1-2秒，费用较高。",
    "publish": "说明该账户对应的发布方式；第三方须服务商管理勾选低延迟并保存，Web须在发布页选直播类型。",
    "fees": "提醒检查学员端附加费用配置。",
    "contact_consent": "只询问是否同意通过企业微信联系，必须等对方回答。",
    "reward": "按已查询的政策准确回答奖励或套餐价格问题。",
    "exit": "按已查询的政策准确回答退出时限或优惠问题。",
    "connection_check": "只询问对方能否听清，不介绍业务。",
}


def latch(state, kind):
    if not kind:
        return
    # A stop is irrevocable within this call. Driving can escalate any stop.
    if not state.get("stop_latch") or kind == "driving":
        state.setdefault("stop_spoken_index", len(state.get("spoken", [])))
        state["stop_latch"] = kind
    state["phase"] = "terminating"


def observe_user(state, text):
    s = deepcopy(state)
    t = normalize(text)
    latch(s, terminal_intent(text))
    if re.search(r"听不清|信号不好|听不到|…", t):
        s["silence_count"] = s.get("silence_count", 0) + 1
        if s["silence_count"] >= 2:
            latch(s, "silence")
            s["constraint"] = s.get("constraint") or "silence"
    elif re.search(r"能听清|听得清|能听见|听清了", t):
        s["silence_count"] = 0
    # Public user observations can revoke consent; a stop always prevents writes.
    if "企业微信" in t:
        if re.search(r"不同意|不接受|不允许|不要|拒绝", t):
            s["consent"] = False
            s["contact_declined"] = True
        elif re.search(r"我同意|明确同意|可以通过企业微信|同意通过企业微信", t):
            s["consent"] = True
            s["contact_declined"] = False
    if re.search(r"我是负责人|我就是负责人", t):
        s["identity_confirmed"] = True
    if re.search(r"可以|方便|能配送", t) and not re.search(r"不可以|不方便", t):
        s["availability_confirmed"] = True
    if re.search(r"奖励|额外.*元|套餐.*价格|具体.*价格", t):
        s["question_topic"] = "reward"
    if re.search(
        r"退出|取消方式|取消.*几点|几.*点前|优惠券|优惠活动|能.*优惠|关心.*优惠", t
    ):
        s["question_topic"] = "exit"
    return s


def initial_state(scenario, facts, policy, opening, world_constraint=None):
    state = {
        "reducer_version": VERSION,
        "scenario": scenario,
        "facts": deepcopy(facts),
        "constraint": world_constraint,
        "policy": policy,
        "spoken": [],
        "consent": False,
        "ended": False,
        "phase": "business",
        "stop_latch": None,
        "policy_loaded": False,
        "identity_confirmed": False,
        "availability_confirmed": False,
        "silence_count": 0,
    }
    return observe_user(state, opening)


def completed_steps(state):
    return set(
        detect_steps(
            "。".join(state.get("spoken", [])), state["facts"], state["scenario"]
        )
    )


def current_stage(state):
    if state.get("ended"):
        return "closed"
    if state.get("stop_latch"):
        return "terminating"
    if state.get("silence_count", 0):
        return "connection_check"
    if not state.get("policy_loaded"):
        return "lookup_policy"
    seen = completed_steps(state)
    if state["scenario"] == "delivery":
        order = ["contract_notice", "availability"]
    else:
        order = ["identity"]
    for stage in order:
        if stage not in seen:
            return stage
    if state["scenario"] == "course" and not state.get("identity_confirmed"):
        return "identity"
    topic = state.get("question_topic")
    if topic and not answer_pass(
        "。".join(state.get("spoken", [])), state["facts"], state["scenario"], topic
    ):
        return topic
    if state["scenario"] == "delivery":
        return next(
            (k for k in ("requirements", "safety") if k not in seen), "completed"
        )
    for stage in (
        "options",
        "standard",
        "low_latency",
        "publish",
        "fees",
        "contact_consent",
    ):
        joined = normalize("。".join(state.get("spoken", [])))
        if stage == "standard":
            present = bool(re.search(r"标准[^。]*5-10秒[^。]*(?:费用低|便宜)", joined))
        elif stage == "low_latency":
            present = bool(
                re.search(r"低延迟[^。]*1-2秒[^。]*(?:费用较高|费用高|更贵)", joined)
            )
        else:
            present = stage in seen
        if not present:
            return stage
    if not state.get("consent") and not state.get("contact_declined"):
        return "contact_consent"
    if state.get("consent") and not state.get("contact_recorded"):
        return "record_contact"
    return "completed"


def stage_speech_pass(state, text):
    stage = current_stage(state)
    seen = set(detect_steps(text, state["facts"], state["scenario"]))
    t = normalize(text)
    if stage in {"reward", "exit"}:
        return answer_pass(text, state["facts"], state["scenario"], stage)
    if stage == "connection_check":
        return bool(
            re.fullmatch(
                r"(?:请问|您好|您|现在|能|可以|听清|听见|吗|声音|清楚|是否|我|说话|\?|。|，|,)+",
                t,
            )
        ) and bool(re.search(r"听清|听见|清楚", t))
    if stage == "standard":
        return bool(re.search(r"标准.*5-10秒.*(?:费用低|便宜)", t))
    if stage == "low_latency":
        return bool(re.search(r"低延迟.*1-2秒.*(?:费用较高|费用高|更贵)", t))
    if stage == "publish":
        if state["facts"]["publish"] == "third_party":
            return all(x in t for x in ("服务商", "勾选", "低延迟", "保存"))
        return "web" in t and "发布" in t and bool(re.search(r"选.*直播", t))
    return stage in seen


def terminal_action(state):
    kind = state.get("stop_latch")
    if kind == "refusal" and not state.get("opt_out"):
        return {"tool": "set_opt_out", "arguments": {}}
    if kind == "busy" and not state.get("callback"):
        return {"tool": "schedule_callback", "arguments": {}}
    if kind in {"privacy", "injection", "complaint", "silence"} and not state.get(
        "ticket"
    ):
        return {"tool": "create_ticket", "arguments": {"reason": kind}}
    return {
        "tool": "end_call",
        "arguments": {
            "reason": "handoff"
            if kind in {"privacy", "injection", "complaint", "silence"}
            else kind
        },
    }


def automatic_action(state):
    stage = current_stage(state)
    if stage == "terminating":
        return terminal_action(state)
    if stage == "lookup_policy":
        return {"tool": stage, "arguments": {}}
    if stage == "record_contact":
        return {"tool": stage, "arguments": {}}
    if stage == "completed":
        return {"tool": "end_call", "arguments": {"reason": "completed"}}
    return None
