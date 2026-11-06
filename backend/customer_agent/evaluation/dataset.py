"""Load and validate user-provided evaluation cases; no bundled dataset."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import random
from backend.customer_agent.core.schemas import Case
from backend.customer_agent.core.settings import ROOT


class DatasetUnavailable(ValueError):
    """The operator has not supplied the local evaluation dataset."""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path=None):
    path = Path(path or ROOT / "data/cases.jsonl")
    if not path.is_file():
        raise DatasetUnavailable("dataset_not_provided: import local cases before evaluation")
    return [
        Case.model_validate_json(line)
        for line in path.read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


def validate(path):
    cases = load(path)
    if not cases:
        raise ValueError("empty dataset")
    ids = set()
    groups = {}
    contents = set()
    for c in cases:
        if c.id in ids:
            raise ValueError("duplicate ID: " + c.id)
        ids.add(c.id)
        if c.group_id in groups and groups[c.group_id] != c.split:
            raise ValueError("group leakage")
        groups[c.group_id] = c.split
        fp = json.dumps(
            [c.scenario, c.opening, c.facts, c.personality],
            ensure_ascii=False,
            sort_keys=True,
        )
        if fp in contents:
            raise ValueError("duplicate content")
        contents.add(fp)
        expected = c.expected["terminal"]
        if expected and expected not in {
            "refusal",
            "driving",
            "busy",
            "wrong_person",
            "injection",
            "privacy",
            "complaint",
            "silence",
        }:
            raise ValueError("unknown terminal label: " + c.id)
        for key in (
            "single_orders",
            "daily_orders",
            "days",
            "reward_days",
            "reward",
            "deadline",
        ):
            if type(c.facts.get(key)) is not int or c.facts[key] <= 0:
                raise ValueError("invalid facts")
    return {
        "rows": len(cases),
        "unique_ids": len(ids),
        "groups": len(groups),
        "duplicate_records": len(cases) - len(contents),
        "group_leakage": False,
        "by_split": dict(Counter(c.split for c in cases)),
        "by_scenario": dict(Counter(c.scenario for c in cases)),
        "by_intent": dict(Counter(c.intent for c in cases)),
        "by_persona": dict(Counter(c.personality for c in cases)),
        "caveat": "structural checks only; distribution and labeling quality require separate review",
    }


def select(cases, split="test", scenario="all", limit=12, seed=42, case_ids=None):
    pool = [
        c
        for c in cases
        if c.split == split and (scenario == "all" or c.scenario == scenario)
    ]
    if case_ids:
        chosen = set(case_ids)
        pool = [c for c in pool if c.id in chosen]
        if {c.id for c in pool} != chosen:
            raise ValueError("case IDs absent from requested split/scenario")
        if len(pool) > limit:
            raise ValueError("case_ids exceeds limit")
        return sorted(pool, key=lambda c: c.id)
    # Round-robin strata; avoid first-N selection bias towards a single scenario/intent.
    rng = random.Random(seed)
    buckets = {}
    for c in pool:
        buckets.setdefault((c.scenario, c.intent), []).append(c)
    keys = sorted(buckets)
    rng.shuffle(keys)
    for v in buckets.values():
        rng.shuffle(v)
    out = []
    while len(out) < min(limit, len(pool)):
        for key in keys:
            if buckets[key] and len(out) < limit:
                out.append(buckets[key].pop())
    return out
