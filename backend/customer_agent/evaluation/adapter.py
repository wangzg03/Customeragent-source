"""Offline administrator adapter; never imports unreviewed data through the API."""

import copy
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from backend.customer_agent.core.schemas import Case

SCENARIOS = {
    "外卖配送": "delivery",
    "配送": "delivery",
    "课程平台": "course",
    "课程": "course",
    "delivery": "delivery",
    "course": "course",
}
PERSONALITIES = dict(
    zip(
        ["合作型", "怀疑型", "打断型", "沉默型", "议价型", "易怒拒绝型", "从众型"],
        [
            "cooperative",
            "skeptical",
            "interruptive",
            "silent",
            "bargain",
            "irritable_refuse",
            "follow_wait",
        ],
    )
)
FACT_KEYS = (
    "single_orders",
    "daily_orders",
    "days",
    "reward_days",
    "reward",
    "deadline",
)


def normalize_text(text):
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"1[3-9]\d{9}", "[PHONE]", text)
    text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "[ID]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[EMAIL]", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()


def adapt_record(raw):
    row = copy.deepcopy(raw)
    if row.get("scenario") not in SCENARIOS:
        raise ValueError("unsupported scenario; manual domain mapping required")
    row["scenario"] = SCENARIOS[row["scenario"]]
    row["personality"] = PERSONALITIES.get(
        row.get("personality"), row.get("personality")
    )
    row["opening"] = normalize_text(row["opening"])
    for key in FACT_KEYS:
        value = row["facts"][key]
        if type(value) is bool or not re.fullmatch(r"\d+", str(value)):
            raise ValueError("non-integer fact: " + key)
        row["facts"][key] = int(value)
    row["provenance"]["adaptation"] = [
        "NFKC",
        "contact_identifier_redaction",
        "scenario_and_persona_alias_mapping",
        "strict_integer_facts",
        "schema_validation",
    ]
    return Case.model_validate(row).model_dump()


def adapt_jsonl(source, target):
    source = Path(source)
    target = Path(target)
    if source.resolve() == target.resolve():
        raise ValueError("source and target must differ")
    if target.exists():
        raise FileExistsError("refusing to overwrite adapted data")
    rows = []
    seen = set()
    for index, line in enumerate(source.read_text(encoding="utf-8-sig").splitlines()):
        if not line.strip():
            continue
        try:
            row = adapt_record(json.loads(line))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(
                f"record {index + 1}: invalid input; {type(exc).__name__}"
            ) from exc
        key = json.dumps(
            [row["scenario"], row["opening"], row["facts"], row["personality"]],
            sort_keys=True,
            ensure_ascii=False,
        )
        if key in seen:
            raise ValueError(f"record {index + 1}: duplicate normalized content")
        seen.add(key)
        rows.append(row)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8",
    )
    return {
        "rows": len(rows),
        "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "note": "PII patterns are a baseline filter, not a guarantee of anonymization; authorization/manual review required for real data.",
    }
