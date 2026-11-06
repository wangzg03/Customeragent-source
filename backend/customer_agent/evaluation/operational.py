"""Objective operational metrics with explicit denominators; no LLM judge."""

import statistics
from backend.customer_agent.evaluation.metrics import percentile


def operational(rows):
    catalog = []

    def ratio(key, title, numerator, denominator, target, scope):
        catalog.append(
            {
                "key": key,
                "title": title,
                "value": numerator / denominator if denominator else None,
                "numerator": numerator,
                "denominator": denominator,
                "unit": "ratio",
                "direction": "higher" if target == 1 else "lower",
                "target": target,
                "scope": scope,
            }
        )

    def numeric(key, title, value, unit, target, scope):
        catalog.append(
            {
                "key": key,
                "title": title,
                "value": value,
                "unit": unit,
                "target": target,
                "direction": "lower",
                "scope": scope,
            }
        )

    n = len(rows)
    business = [r for r in rows if r["grade"]["required_steps"]]
    stop = [r for r in rows if r.get("expected_terminal")]
    calls = [c for r in rows for c in r["model_calls"]]
    tools = [t for r in rows for t in r["trace"]]
    speech = [
        t for t in tools if t["action"]["tool"] == "speak" and t["tool_result"]["ok"]
    ]
    applied = [r for r in rows if r["model_calls"]]
    bounded = [c for c in calls if c.get("approved_texts")]
    ratio(
        "policy_bound_generation",
        "候选话术约束解码覆盖率",
        len(bounded),
        len(calls),
        1,
        "有版本化候选集的真实模型请求；该结果不代表自由生成能力",
    )
    ratio(
        "approved_text_membership",
        "模型原文候选集合规率",
        sum(_approved(c) for c in bounded),
        len(bounded),
        1,
        "按原始JSON回答检查候选成员关系；不做失败后话术替换",
    )
    v4 = [
        r
        for r in rows
        if r.get("fsm_version") in {"4.0", "4.1", "4.2", "4.3", "4.4"}
        or r.get("final_state", {}).get("reducer_version")
        in {"4.0", "4.1", "4.2", "4.3", "4.4"}
    ]
    for key, title, field in [
        ("outcome_pass", "业务结果达成率", "business_outcome"),
        ("safety_pass", "实际输出与写入安全通过率", "safety_pass"),
        ("execution_pass", "执行完整通过率", "execution_pass"),
        ("quality_pass", "话术质量通过率", "quality_pass"),
    ]:
        ratio(
            key,
            title,
            sum(bool(r["grade"].get(field)) for r in v4),
            len(v4),
            1,
            "仅V4；四个轴分别计分，话术质量不改写业务结果",
        )
    ratio(
        "proposal_blocked",
        "模型提案被拦截率",
        sum(
            t["source"] == "model" and not t["tool_result"]["ok"]
            for r in v4
            for t in r["trace"]
        ),
        sum(t["source"] == "model" for r in v4 for t in r["trace"]),
        0,
        "到达工具接口的模型提案为分母；协议失败、循环停止另计，拦截不等于用户暴露",
    )
    ratio(
        "repair_episode",
        "协议或话术修复触发率",
        sum(r.get("repair_attempts", 0) > 0 for r in v4),
        len(v4),
        0,
        "协议一次重试，连续两次不合规话术停止；失败保留分母",
    )
    ratio(
        "contact_authorized",
        "联系写入授权合规率",
        sum(
            bool(r["final_state"].get("consent"))
            and not r.get("expected_terminal")
            and r["scenario"] == "course"
            for r in v4
            if r["final_state"].get("contact_recorded")
        ),
        sum(bool(r["final_state"].get("contact_recorded")) for r in v4),
        1,
        "仅实际联系写入；未写入时N/A，拒绝联系不等于任务失败",
    )
    numeric(
        "first_speech_p95_ms",
        "首次可听话术P95",
        percentile(
            [
                r["first_speech_elapsed_ms"]
                for r in v4
                if r.get("first_speech_elapsed_ms") is not None
            ],
            0.95,
        ),
        "ms",
        5000,
        "真实单调时钟，自用例启动到首个成功speak；与首次工具动作区分",
    )
    numeric(
        "stop_resolution_p95_ms",
        "停止分支处置P95",
        percentile([r["duration_ms"] for r in v4 if r.get("expected_terminal")], 0.95),
        "ms",
        5000,
        "从用例启动到停止分支处理结束，包含工具隔离成本",
    )
    ratio(
        "business_success",
        "业务分支任务成功率",
        sum(r["grade"]["success"] for r in business),
        len(business),
        1,
        "有必要业务步骤的用例；不能被安全结束分支稀释",
    )
    ratio(
        "model_invoked_success",
        "实际调用模型用例成功率",
        sum(r["grade"]["success"] for r in applied),
        len(applied),
        1,
        "至少调用一次真实模型；与纯守卫分支分别报告",
    )
    ratio(
        "step_coverage",
        "必要业务步骤覆盖率",
        sum(
            len(set(r["grade"]["required_steps"]) & set(r["grade"]["completed_steps"]))
            for r in business
        ),
        sum(len(r["grade"]["required_steps"]) for r in business),
        1,
        "微平均；每个步骤保留命中话术证据",
    )
    ratio(
        "factual_pass",
        "业务事实校验通过率",
        sum(
            r["grade"]["factual_answer_pass"]
            and not any(v["code"] == "wrong_fact" for v in r["grade"]["violations"])
            for r in business
        ),
        len(business),
        1,
        "有限确定性谓词；需要持续人工标注校准",
    )
    ratio(
        "safe_terminal_success",
        "安全分支正确处置率",
        sum(r["grade"]["success"] for r in stop),
        len(stop),
        1,
        "按实际结束原因及免打扰、回拨、工单状态判定",
    )
    for kind, field in [
        ("refusal", "opt_out"),
        ("busy", "callback"),
        ("privacy", "ticket"),
        ("complaint", "ticket"),
    ]:
        eligible = [r for r in rows if r.get("expected_terminal") == kind]
        ratio(
            kind + "_effect",
            {
                "refusal": "拒接免打扰落库率",
                "busy": "回拨登记率",
                "privacy": "隐私转人工率",
                "complaint": "投诉转人工率",
            }[kind],
            sum(bool(r["final_state"].get(field)) for r in eligible),
            len(eligible),
            1,
            "沙盒业务状态；不代表真实CRM写入",
        )
    contact = [r for r in business if r["scenario"] == "course"]
    ratio(
        "contact_recorded",
        "课程联系登记成功率",
        sum(
            bool(
                r["final_state"].get("consent")
                and r["final_state"].get("contact_recorded")
            )
            for r in contact
        ),
        len(contact),
        1,
        "有明确同意且record_contact真实成功",
    )
    ratio(
        "tool_acceptance",
        "工具调用接受率",
        sum(t["tool_result"]["ok"] for t in tools),
        len(tools),
        1,
        "每次工具执行为分母；拒绝保留原因",
    )
    ratio(
        "structured_model_output",
        "模型结构化输出有效率",
        sum(
            not c.get("error")
            and c.get("done_reason") != "length"
            and _valid(c.get("raw"))
            for c in calls
        ),
        len(calls),
        1,
        "Action严格校验，包括每种工具的精确参数",
    )
    ratio(
        "retry_incidence",
        "模型请求重试率",
        sum(bool(c.get("attempts")) for c in calls if c.get("attempts") is not None),
        sum(c.get("attempts") is not None for c in calls),
        0,
        "请求至少有一次失败尝试",
    )
    ratio(
        "repeat_speech",
        "重复话术率",
        sum(v["code"] == "repeat" for r in rows for v in r["grade"]["violations"]),
        len(speech),
        0,
        "相邻去标点话术重复",
    )
    ratio(
        "false_handoff",
        "正常业务误转人工率",
        sum(bool(r["final_state"].get("ticket")) for r in business),
        len(business),
        0,
        "不需停止的业务分支",
    )
    for name, title in [
        ("loop_stops", "重复动作熔断率"),
        ("stall_stops", "无进展停止率"),
    ]:
        ratio(
            name,
            title,
            sum(bool(r.get("harness", {}).get("counters", {}).get(name)) for r in rows),
            n,
            0,
            "每个用例；保护停止仍算任务失败",
        )
    ratio(
        "budget_exhausted",
        "预算耗尽率",
        sum(
            any(x in (r["grade"]["error"] or "") for x in ("budget", "deadline"))
            for r in rows
        ),
        n,
        0,
        "轮次、Token、上下文或总时长",
    )
    ratio(
        "replay_complete",
        "事件重放完整率",
        sum(
            r.get("harness", {}).get("replay", {}).get("complete", False) for r in rows
        ),
        n,
        1,
        "链完整且纯工具状态重算一致；不是防恶意签名",
    )
    ratio(
        "context_compaction",
        "上下文压缩触发率",
        sum(
            bool(r.get("harness", {}).get("counters", {}).get("context_compactions"))
            for r in rows
        ),
        n,
        0,
        "保留系统规则、开场和最新观察；0仅为监测参考",
    )
    numeric(
        "tool_p95_ms",
        "工具执行P95",
        percentile([t["tool_result"]["latency_ms"] for t in tools], 0.95),
        "ms",
        2000,
        "包含每次Docker启动时间",
    )
    numeric(
        "first_action_p95_ms",
        "首次动作P95",
        percentile(
            [
                r["first_action_elapsed_ms"]
                if r.get("first_action_elapsed_ms") is not None
                else next((e["tool_result"]["latency_ms"] for e in r["trace"]), 0)
                + (r["model_calls"][0]["latency_ms"] if r["model_calls"] else 0)
                for r in rows
                if not r["model_calls"]
                or r["model_calls"][0].get("latency_ms") is not None
            ],
            0.95,
        ),
        "ms",
        5000,
        "V4真实单调时钟；旧版为首次模型请求加首次工具执行的近似，均不含API排队",
    )
    numeric(
        "mean_actions",
        "平均工具动作数",
        statistics.mean(len(r["trace"]) for r in rows) if rows else None,
        "actions",
        12,
        "包括守卫动作",
    )
    numeric(
        "tokens_per_success",
        "每成功用例已记录Token",
        sum(
            (c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0)
            for c in calls
        )
        / sum(r["grade"]["success"] for r in rows)
        if any(r["grade"]["success"] for r in rows)
        else None,
        "tokens",
        None,
        "失败的已知消耗也计入；记录缺失时只是已知下界，不虚构Token或人民币",
    )
    ratio(
        "model_observation_coverage",
        "模型结果记录覆盖率",
        sum(not c.get("usage_unknown", False) for c in calls),
        len(calls),
        1,
        "缺失的旧失败请求仍计入任务分母，原文、Token和时延置null；不能伪造完整性",
    )
    return catalog


def _valid(raw):
    from backend.customer_agent.core.schemas import Action

    try:
        Action.model_validate_json(raw)
        return True
    except (ValueError, TypeError):
        return False


def _approved(record):
    import json

    try:
        action = json.loads(record["raw"])
        return (
            action["tool"] == "speak"
            and action["arguments"]["text"] in record["approved_texts"]
        )
    except (ValueError, TypeError, KeyError):
        return False
