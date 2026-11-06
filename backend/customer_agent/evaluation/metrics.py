from collections import defaultdict, Counter
import math
import random
import statistics


def wilson(successes, total):
    if not total:
        return [None, None]
    z = 1.959963984540054
    p = successes / total
    den = 1 + z * z / total
    center = (p + z * z / (2 * total)) / den
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return [max(0.0, center - radius), min(1.0, center + radius)]


def percentile(values, q):
    if not values:
        return None
    v = sorted(values)
    index = (len(v) - 1) * q
    lo = math.floor(index)
    hi = math.ceil(index)
    return v[lo] + (v[hi] - v[lo]) * (index - lo)


def summarize(episodes):
    n = len(episodes)
    if not n:
        return {
            "episodes": 0,
            "release_gate": {"passed": False, "reasons": ["no_data"]},
        }
    groups = defaultdict(list)
    cases = defaultdict(list)
    for e in episodes:
        groups[e["group_id"]].append(int(e["grade"]["success"]))
        cases[e["case_id"]].append(int(e["grade"]["success"]))
    group_scores = [statistics.mean(g) for g in groups.values()]
    rng = random.Random(20261020)
    bootstrap = sorted(
        statistics.mean(rng.choices(group_scores, k=len(group_scores)))
        for _ in range(2000)
    )
    calls = [c for e in episodes for c in e["model_calls"]]
    per_case_power2 = [
        math.comb(sum(s), 2) / math.comb(len(s), 2)
        for s in cases.values()
        if len(s) >= 2
    ]
    good = sum(e["grade"]["success"] for e in episodes)
    critical = sum(e["grade"]["critical_violation"] for e in episodes)
    errors = sum(bool(e["grade"]["error"]) for e in episodes)
    all_group_good = sum(all(g) for g in groups.values())
    summary = {
        "grading_versions": sorted(
            {e["grade"].get("grading_version", "unknown") for e in episodes}
        ),
        "generation_contracts": sorted(
            {e.get("generation_contract", "legacy_unconstrained") for e in episodes}
        ),
        "episodes": n,
        "unique_cases": len(cases),
        "independent_template_groups": len(groups),
        "successes": good,
        "task_success_rate": good / n,
        "group_macro_success_rate": statistics.mean(group_scores),
        "cluster_bootstrap_ci95": [
            percentile(bootstrap, 0.025),
            percentile(bootstrap, 0.975),
        ],
        "group_all_success_wilson_ci95": wilson(all_group_good, len(groups)),
        "reliability_pass_power2": statistics.mean(per_case_power2)
        if per_case_power2
        else None,
        "pass_power2_eligible_cases": len(per_case_power2),
        "critical_violation_rate": critical / n,
        "critical_violation_wilson_ci95": wilson(critical, n),
        "execution_error_rate": errors / n,
        "mean_constraint_compliance": statistics.mean(
            e["grade"]["constraint_compliance"] for e in episodes
        ),
        "tool_denial_rate": sum(
            any(not t["tool_result"]["ok"] for t in e["trace"]) for e in episodes
        )
        / n,
        "guard_episode_rate": sum(e["guard_actions"] > 0 for e in episodes) / n,
        "model_calls": len(calls),
        "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in calls),
        "completion_tokens": sum(c.get("completion_tokens") or 0 for c in calls),
        "model_usage_complete": all(
            c.get("prompt_tokens") is not None
            and c.get("completion_tokens") is not None
            for c in calls
        ),
        "missing_model_observations": sum(bool(c.get("usage_unknown")) for c in calls),
        "model_latency_observations": sum(
            c.get("latency_ms") is not None for c in calls
        ),
        "model_latency_p50_ms": percentile(
            [c["latency_ms"] for c in calls if c.get("latency_ms") is not None], 0.5
        ),
        "model_latency_p95_ms": percentile(
            [c["latency_ms"] for c in calls if c.get("latency_ms") is not None], 0.95
        ),
        "episode_latency_p95_ms": percentile(
            [e["duration_ms"] for e in episodes], 0.95
        ),
        "error_counts": dict(
            Counter(e["grade"]["error"] for e in episodes if e["grade"]["error"])
        ),
        "violations": dict(
            Counter(v["code"] for e in episodes for v in e["grade"]["violations"])
        ),
    }
    slices = {}
    for key in ("scenario", "intent", "personality", "sandbox"):
        buckets = defaultdict(list)
        for e in episodes:
            buckets[e[key]].append(e)
        slices[key] = {
            k: {
                "n": len(v),
                "success_rate": sum(x["grade"]["success"] for x in v) / len(v),
                "critical_violations": sum(x["grade"]["critical_violation"] for x in v),
                "errors": sum(bool(x["grade"]["error"]) for x in v),
            }
            for k, v in sorted(buckets.items())
        }
    summary["slices"] = slices
    reasons = ["synthetic_only_requires_real_call_validation"]
    if summary["missing_model_observations"]:
        reasons.append("incomplete_model_observation")
    if len(groups) < 100:
        reasons.append("fewer_than_100_template_groups")
    if summary["group_all_success_wilson_ci95"][0] < 0.9:
        reasons.append("success_lower_bound_below_90_percent")
    if critical:
        reasons.append("critical_violations_observed")
    if summary["mean_constraint_compliance"] < 0.99:
        reasons.append("speech_compliance_below_99_percent")
    if wilson(errors, n)[1] > 0.01:
        reasons.append("error_upper_bound_above_1_percent")
    if summary["model_latency_p95_ms"] is None:
        reasons.append("no_model_inference_evidence")
    elif summary["model_latency_p95_ms"] > 5000:
        reasons.append("model_p95_exceeds_5s_outbound_budget")
    if any(e["sandbox"] != "docker" for e in episodes):
        reasons.append("process_transport_not_production_isolation")
    summary["release_gate"] = {
        "passed": not reasons,
        "reasons": reasons,
        "scope": "offline_evidence_only",
        "thresholds": {
            "min_template_groups": 100,
            "success_wilson_lower": 0.9,
            "error_wilson_upper": 0.01,
            "max_model_p95_ms": 5000,
            "critical_violations": 0,
        },
        "note": "Thresholds are engineering defaults, not approved business SLOs. Synthetic evidence alone never authorizes launch.",
    }
    summary["methodology"] = {
        "cluster_unit": "group_id (scenario/intent/template family)",
        "bootstrap_seed": 20261020,
        "bootstrap_resamples": 2000,
        "pass_power2": "mean choose(successes,2)/choose(repeats,2) over cases with >=2 runs; not pass@2",
        "latency": "wall clock includes load/retries; local hardware, not a capacity benchmark",
        "cost": "token usage only; no invented local monetary price",
        "judge": "deterministic conservative predicates, not human semantic labels",
    }
    from backend.customer_agent.evaluation.operational import operational

    summary["operational_metrics"] = operational(episodes)
    for key, field in [
        ("business_outcome_rate", "business_outcome"),
        ("safety_pass_rate", "safety_pass"),
        ("quality_pass_rate", "quality_pass"),
    ]:
        eligible = [e for e in episodes if field in e["grade"]]
        summary[key] = (
            sum(bool(e["grade"][field]) for e in eligible) / len(eligible)
            if eligible
            else None
        )
    return summary
