"""RAG outcome metrics use actual evidence/tool traces, without an LLM judge."""

from backend.customer_agent.knowledge.evidence import bundle_error, citations
from backend.customer_agent.evaluation.metrics import percentile


def summarize_rag(rows):
    episodes = [r for r in rows if r.get("knowledge_mode") == "rag"]
    if not episodes:
        return {}
    queried = [r for r in episodes if r.get("retrievals")]
    bound = [r for r in episodes if r["final_state"].get("policy_loaded")]
    business = [r for r in episodes if r.get("expected_terminal") is None]
    calls = [q for r in episodes for q in r.get("retrievals", [])]
    speeches = [(r, t) for r in episodes for t in r["trace"]
                if t["action"]["tool"] == "speak" and t["tool_result"]["ok"]
                and t["stage"] not in {"connection_check", "terminating"}]
    valid, total_citations = 0, 0
    for r, t in speeches:
        bundle = r["final_state"].get("knowledge", {})
        expected = citations(bundle, t["stage"]) if bundle else []
        actual = t["tool_result"]["result"].get("citations", [])
        total_citations += len(actual)
        valid += int(bool(expected) and actual == expected and not bundle_error(bundle, r["scenario"], r["final_state"]["facts"]))
    failures = [r for r in episodes if r.get("knowledge_error")]
    safe_fallback = sum(r["final_state"].get("ticket") == "knowledge" and r["final_state"].get("end_reason") == "handoff"
                        and not r["final_state"].get("contact_recorded") and not r["final_state"].get("spoken") for r in failures)
    result = {"episodes": len(episodes), "knowledge_bound_episodes": len(bound), "retrieval_episodes": len(queried),
              "business_episodes": len(business), "business_successes": sum(r["grade"]["success"] for r in business),
              "model_invoked_episodes": sum(bool(r["model_calls"]) for r in episodes),
              "pure_guard_episodes": sum(not r["model_calls"] and not r["retrievals"] and not r["knowledge_error"] for r in episodes),
              "searches": len(calls), "citation_speeches": len(speeches), "valid_citation_speeches": valid,
              "citations": total_citations, "knowledge_failures": len(failures), "safe_fallbacks": safe_fallback,
              "retrieval_p95_ms": percentile([q["latency_ms"] for q in calls], .95),
              "snapshots": sorted({r["knowledge_snapshot"] for r in episodes if r.get("knowledge_snapshot")})}
    metrics = []
    def ratio(key, title, numerator, denominator, scope):
        metrics.append({"key": key, "title": title, "unit": "ratio", "value": numerator / denominator if denominator else None,
                        "numerator": numerator, "denominator": denominator, "scope": scope})
    ratio("rag_business_success", "RAG业务分支成功率", result["business_successes"], len(business), "仅业务用例，安全终止不混入分母")
    ratio("rag_citation_validity", "话术引用有效率", valid, len(speeches), "成功业务话术的来源、版本、摘要、字段渲染与阶段引用一致；不等于开放语义蕴含评测")
    ratio("rag_knowledge_load", "业务知识加载率", len(bound), len(business), "实际lookup_policy成功的用例/业务用例")
    ratio("rag_safe_fallback", "知识失败安全转人工率", safe_fallback, len(failures), "失败用例须有工单、handoff且无营销与联系写入；无故障样例时N/A")
    metrics.append({"key": "rag_retrieval_p95", "title": "知识检索P95", "value": result["retrieval_p95_ms"],
                    "unit": "ms", "scope": "实际检索调用，缓存命中与冷查询按实验配置披露"})
    return {"rag_metrics": result, "rag_operational_metrics": metrics}
