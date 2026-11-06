"""Finite, observable fact slots; partial true statements can add progress.

No fixture intent/expected labels, no model-generated summary, no tool writes.
"""

import re

try:
    from backend.customer_agent.domain.rules import detect_steps, product_slots, capped_or_negated
    from backend.customer_agent.domain.normalization import normalize
except ImportError:
    from business_rules import detect_steps, product_slots, capped_or_negated
    from normalization import normalize


def slots(state, extra=None):
    texts = list(state.get("spoken", [])) + ([extra] if extra is not None else [])
    joined = "。".join(texts)
    f = state["facts"]
    found = set(detect_steps(joined, f, state["scenario"]))
    if "options" in found:
        found.add("options_intro")
    if state["scenario"] == "delivery":
        for text in texts:
            v = normalize(text)
            if not capped_or_negated(v):
                if re.search(rf"每日.*?(?<!\d){f['daily_orders']}单", v):
                    found.add("daily_target")
                if re.search(rf"连续{f['days']}天", v) and not re.search(
                    r"额外|奖励", v
                ):
                    found.add("contract_days")
            if not capped_or_negated(v):
                if re.search(rf"连续{f['reward_days']}天", v):
                    found.add("reward_days")
                if "每日达标" in v:
                    found.add("reward_daily")
                if re.search(rf"每单(?:额外|奖励){f['reward']}(?:\.0)?元", v):
                    found.add("reward_amount")
                if "前1天" in v:
                    found.add("exit_day")
                if re.search(rf"(?<!\d){f['deadline']}点前", v):
                    found.add("exit_deadline")
                if "app" in v and "取消" in v:
                    found.add("exit_channel")
        return found
    for text in texts:
        v = normalize(text)
        if not capped_or_negated(v):
            if "直播" in v and re.search(r"新增|选项|两种|发布页", v):
                found.add("options_intro")
            if "标准" in v:
                found.add("standard_named")
            if "低延迟" in v:
                found.add("low_named")
            if f["publish"] == "third_party":
                if "服务商管理" in v:
                    found.add("publish_route")
                if "勾选" in v and "低延迟" in v:
                    found.add("publish_select")
                if "保存" in v:
                    found.add("publish_save")
            else:
                if "web" in v and "发布" in v:
                    found.add("publish_route")
                if re.search(r"选.*直播|直播.*选择", v):
                    found.add("publish_select")
            if "套餐" in v and re.search(r"因.*而异|视.*而定|取决于|价格不同", v):
                found.add("price_dependency")
            if "资料" in v and re.search(r"需|可|发|提供", v):
                found.add("price_materials")
        if re.search(r"没有优惠|暂无优惠|无优惠活动", v):
            found.add("no_discount")
    for name, product in [("standard", "标准"), ("low", "低延迟")]:
        for kind, present in product_slots(joined, product).items():
            if present:
                found.add(name + "_" + kind)
    return found


def topic_slots(state):
    topic = state.get("question_topic")
    if topic == "reward":
        return (
            ["reward_days", "reward_daily", "reward_amount"]
            if state["scenario"] == "delivery"
            else ["price_dependency", "price_materials"]
        )
    if topic == "exit":
        return (
            ["exit_day", "exit_deadline", "exit_channel"]
            if state["scenario"] == "delivery"
            else ["no_discount"]
        )
    return []


def business_slots(state):
    if state["scenario"] == "delivery":
        target = (
            ["requirements"]
            if state["facts"]["contract"] == "single"
            else ["daily_target", "contract_days"]
        )
        return (
            ["contract_notice", "availability"]
            + topic_slots(state)
            + target
            + ["safety"]
        )
    publishing = ["publish_route", "publish_select"] + (
        ["publish_save"] if state["facts"]["publish"] == "third_party" else []
    )
    return (
        ["identity"]
        + topic_slots(state)
        + [
            "options_intro",
            "standard_named",
            "low_named",
            "standard_latency",
            "standard_cost",
            "low_latency",
            "low_cost",
        ]
        + publishing
        + ["fees", "contact_consent"]
    )


def next_business_stage(state):
    seen = slots(state)
    return next((slot for slot in business_slots(state) if slot not in seen), None)


def adds_valid_fact(state, text):
    before = slots(state)
    after = slots(state, text)
    if "contact_consent" in after - before:
        if any(
            k not in before for k in business_slots(state) if k != "contact_consent"
        ):
            return False
    return bool((after - before) & set(business_slots(state)))


def instruction(state):
    stage, f = next_business_stage(state), state["facts"]
    guides = {
        "contract_notice": "告知配送合同已生效。",
        "availability": "只询问今天是否方便配送。",
        "requirements": f"说明单日合同当日至少完成{f['single_orders']}单，不能说最多或限量。",
        "daily_target": f"说明多日合同每天至少完成{f['daily_orders']}单。",
        "contract_days": f"补充多日合同要求连续{f['days']}天。",
        "safety": "提醒路上注意安全。",
        "identity": "询问是否为机构负责人。",
        "options_intro": "说明发布页新增两种直播选项。",
        "standard_named": "补充两个选项名称：标准直播、低延迟直播。",
        "low_named": "补充低延迟直播这一选项名称。",
        "standard_latency": "只说明标准直播延迟5-10秒。",
        "standard_cost": "只说明标准直播费用低。",
        "low_latency": "只说明低延迟直播延迟1-2秒。",
        "low_cost": "只说明低延迟直播费用较高。",
        "publish_route": "引导在服务商管理操作。"
        if f["publish"] == "third_party"
        else "引导在Web发布页操作。",
        "publish_select": "引导勾选低延迟。"
        if f["publish"] == "third_party"
        else "引导选择直播类型。",
        "publish_save": "补充提醒保存设置。",
        "fees": "提醒检查学员端附加费用配置。",
        "contact_consent": "询问是否同意通过企业微信联系，等待明确回答。",
        "price_dependency": "说明具体价格因套餐而异，不能编造具体金额。",
        "price_materials": "说明可发送套餐资料。",
        "no_discount": "说明目前没有优惠活动；政策没有取消或退款时限，不得编造30天等规则。",
        "reward_days": f"说明连续{f['reward_days']}天的奖励条件。",
        "reward_daily": "补充奖励要求每天达标。",
        "reward_amount": f"说明每单额外奖励{f['reward']}元。",
        "exit_day": "说明取消操作须前一天进行。",
        "exit_deadline": f"补充退出操作截止为{f['deadline']}点前。",
        "exit_channel": "补充在App取消。",
    }
    return guides.get(stage, "完成当前必要业务事实。")
