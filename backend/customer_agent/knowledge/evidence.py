"""Pure, offline-verifiable evidence contract, shared with the networkless worker.

The catalogue contains fictional reviewed policy, never evaluation labels.
Account values remain structured facts. Changing fixed course policy requires a
new business-schema version rather than silently bypassing existing validators.
"""

import hashlib
import json
import re
from datetime import date
from string import Formatter

VERSION = "policy-evidence-v1"
FIXED = {"standard_min": 5, "standard_max": 10, "low_min": 1, "low_max": 2}
ACCOUNT_KEYS = {
    "single_orders", "daily_orders", "days", "reward_days", "reward", "deadline"
}
STAGE_RULE = {
    "contract_notice": "contract", "availability": "availability",
    "requirements": "single", "daily_target": "multi", "contract_days": "multi",
    "reward_days": "reward", "reward_daily": "reward", "reward_amount": "reward",
    "exit_day": "exit", "exit_deadline": "exit", "exit_channel": "exit",
    "safety": "safety", "identity": "identity", "options_intro": "options",
    "standard_named": "options", "low_named": "options",
    "standard_latency": "comparison", "standard_cost": "comparison",
    "low_latency": "comparison", "low_cost": "comparison",
    "publish_route": "publish", "publish_select": "publish", "publish_save": "publish",
    "fees": "fees", "contact_consent": "consent", "price_dependency": "price",
    "price_materials": "price", "no_discount": "discount",
}
QUERIES = {
    "contract": "配送合同生效通知", "availability": "询问今天是否方便配送",
    "single": "单日合同当天最低配送单量", "multi": "多日合同每天最低单量和连续天数",
    "reward": "连续达标奖励条件与每单额外奖励", "exit": "退出取消合同时限和App操作",
    "safety": "行车安全恶劣天气配送提醒", "identity": "确认机构负责人身份",
    "options": "发布页新增直播类型选项", "comparison": "标准直播低延迟直播秒数费用比较",
    "publish": "Web发布或第三方服务商管理勾选低延迟保存配置",
    "fees": "检查学员端附加费用收费配置", "consent": "企业微信联系明确同意授权",
    "price": "套餐具体价格资料说明", "discount": "当前优惠活动折扣",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def required_rules(scenario, facts):
    if scenario == "delivery":
        return ["contract", "availability", facts["contract"], "reward", "exit", "safety"]
    return ["identity", "options", "comparison", "publish", "fees", "consent", "price", "discount"]


def document_error(doc):
    fields = {"id", "rule_id", "scenario", "title", "body", "aliases", "source",
              "version", "status", "reviewed_by", "tenant_id", "valid_from",
              "valid_until", "contract", "publish", "values", "license"}
    if set(doc) != fields or not re.fullmatch(r"[a-z0-9_-]{1,90}", doc.get("id", "")):
        return "document_schema"
    if doc["scenario"] not in {"delivery", "course"} or doc["rule_id"] not in QUERIES:
        return "document_scope"
    if doc["contract"] not in {"any", "single", "multi"} or doc["publish"] not in {"any", "web", "third_party"}:
        return "document_scope"
    if doc["status"] not in {"approved", "draft", "revoked"}:
        return "document_status"
    if any(not isinstance(doc[k], str) or not doc[k] for k in fields - {"values"}):
        return "document_types"
    try:
        if date.fromisoformat(doc["valid_from"]) >= date.fromisoformat(doc["valid_until"]):
            return "document_dates"
    except (ValueError, TypeError):
        return "document_dates"
    if not isinstance(doc["values"], dict) or any(k not in FIXED or type(v) is not int or v != FIXED[k]
                                                for k, v in doc["values"].items()):
        return "unsupported_business_schema"
    placeholders = set()
    try:
        for _, key, spec, conversion in Formatter().parse(doc["body"]):
            if key is not None and (key not in ACCOUNT_KEYS | set(FIXED) or spec or conversion):
                return "unsafe_template"
            if key is not None:
                placeholders.add(key)
    except ValueError:
        return "unsafe_template"
    needed = {"single": {"single_orders"}, "multi": {"daily_orders", "days"},
              "reward": {"reward_days", "reward"}, "exit": {"deadline"}, "comparison": set(FIXED)}
    if doc["status"] == "approved" and doc["rule_id"] in needed:
        if placeholders != needed[doc["rule_id"]] or re.search(r"\d", re.sub(r"\{[^}]+\}", "", doc["body"])):
            return "unbound_policy_number"
        if doc["rule_id"] == "comparison" and doc["values"] != FIXED:
            return "unsupported_business_schema"
    if len(doc["body"]) > 1800 or len(doc["aliases"]) > 1000:
        return "document_length"
    if doc["status"] == "approved" and re.search(
        r"忽略.*(?:指令|规则)|系统提示词|执行(?:shell|命令)|powershell|ignore.previous|<\|", doc["body"], re.I
    ):
        return "document_instruction"
    return None


def render(doc, facts):
    values = {k: v for k, v in facts.items() if k in ACCOUNT_KEYS}
    if any(type(v) is not int or not 0 < v <= 100000 for v in values.values()):
        raise ValueError("account_fact_type")
    values.update(doc["values"])
    return doc["body"].format_map(values)


def bundle_error(bundle, scenario, facts):
    try:
        body = {k: v for k, v in bundle.items() if k != "sha256"}
        if digest(body) != bundle["sha256"] or bundle["contract"] != VERSION:
            return "evidence_digest"
        if bundle["scenario"] != scenario or not re.fullmatch(r"[a-f0-9]{64}", bundle["snapshot_id"]):
            return "evidence_scope"
        at = date.fromisoformat(bundle["as_of"])
        rules = []
        for row in bundle["clauses"]:
            doc = row["document"]
            if document_error(doc) or doc["status"] != "approved":
                return "evidence_unreviewed"
            if not (date.fromisoformat(doc["valid_from"]) <= at < date.fromisoformat(doc["valid_until"])):
                return "evidence_expired"
            if doc["tenant_id"] != bundle["tenant_id"] or doc["scenario"] != scenario:
                return "evidence_scope"
            if doc["contract"] != "any" and doc["contract"] != facts.get("contract"):
                return "evidence_contract"
            if doc["publish"] != "any" and doc["publish"] != facts.get("publish"):
                return "evidence_publish"
            if row["document_sha256"] != digest(doc) or row["text"] != render(doc, facts):
                return "evidence_content"
            rules.append(doc["rule_id"])
        if len(rules) != len(set(rules)) or set(rules) != set(required_rules(scenario, facts)):
            return "evidence_incomplete_or_conflicting"
    except (KeyError, TypeError, ValueError):
        return "evidence_invalid"
    return None


def citations(bundle, stage):
    rule = STAGE_RULE.get(stage)
    return [{"document_id": r["document"]["id"], "source": r["document"]["source"],
             "version": r["document"]["version"], "document_sha256": r["document_sha256"],
             "snapshot_id": bundle["snapshot_id"], "rule_id": rule}
            for r in bundle["clauses"] if r["document"]["rule_id"] == rule]
