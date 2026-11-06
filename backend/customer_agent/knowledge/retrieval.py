"""Local immutable SQLite policy catalogue + BM25/dense RRF retrieval.

Uses only dense embeddings from Ollama; BGE sparse/multi-vector output is NOT
claimed. Chinese character bigrams require no new tokenizer dependency. All
filters run before ranking; vectors are scanned exactly for this small corpus.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from backend.customer_agent.runtime.artifacts import write_json
from backend.customer_agent.knowledge.evidence import (
    QUERIES,
    VERSION,
    bundle_error,
    digest,
    document_error,
    render,
    required_rules,
)

TOKENIZER = "zh-bigram-ascii-v1"
PARAMS = {"rrf_k": 60, "dense_min": 0.48, "lexical_min": 1, "top_k": 5}
STOP = {"怎么", "如何", "什么", "可以", "一下", "请问", "现在", "你好", "您好", "是否", "我的", "我们", "你们", "这个", "那个", "多少"}


def tokens(text):
    text = unicodedata.normalize("NFKC", text).lower()
    out = re.findall(r"[a-z0-9_]+", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        out.extend(run[i:i + 2] for i in range(len(run) - 1) if run[i:i + 2] not in STOP)
    return out


@contextmanager
def connect_readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def connect_write(path):
    conn = sqlite3.connect(path, timeout=30)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class Embedder:
    def __init__(self, settings, model="bge-m3:latest", expected_digest=None):
        self.settings, self.model = settings, model
        with httpx.Client(timeout=15, trust_env=False) as client:
            tags = client.get(settings.ollama_url + "/api/tags")
            tags.raise_for_status()
            match = next((m for m in tags.json()["models"] if m["name"] == model), None)
        if not match or expected_digest and match["digest"] != expected_digest:
            raise ValueError("embedding_model_missing_or_changed")
        self.model_digest = match["digest"]
        self.path = settings.runtime_dir / "knowledge-cache.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with connect_write(self.path) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")
        self.requests = self.cache_hits = 0

    def embed(self, texts):
        result = [None] * len(texts)
        missing = []
        with connect_write(self.path) as c:
            for i, text in enumerate(texts):
                key = digest([self.model_digest, text])
                row = c.execute("SELECT vector FROM embeddings WHERE key=?", (key,)).fetchone()
                if row:
                    result[i] = json.loads(row[0])
                    self.cache_hits += 1
                else:
                    missing.append((i, key, text))
        for start in range(0, len(missing), 8):
            batch = missing[start:start + 8]
            with httpx.Client(timeout=30, trust_env=False) as client:
                response = client.post(self.settings.ollama_url + "/api/embed",
                                       json={"model": self.model, "input": [r[2] for r in batch],
                                             "truncate": False, "keep_alive": "5m"})
                response.raise_for_status()
                vectors = response.json()["embeddings"]
            self.requests += 1
            if len(vectors) != len(batch):
                raise ValueError("embedding_count")
            with connect_write(self.path) as c:
                for (i, key, _), v in zip(batch, vectors):
                    if not v or not all(type(x) in {int, float} and math.isfinite(x) for x in v):
                        raise ValueError("embedding_nonfinite")
                    norm = math.sqrt(sum(x * x for x in v))
                    if not norm:
                        raise ValueError("embedding_zero")
                    v = [x / norm for x in v]
                    result[i] = v
                    c.execute("INSERT OR IGNORE INTO embeddings VALUES (?,?)", (key, json.dumps(v)))
        return result


def build(settings, source=None):
    root = settings.data_dir / "knowledge"
    source = Path(source or root / "documents.jsonl")
    docs = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not docs or len(docs) > 2000 or len({d["id"] for d in docs}) != len(docs):
        raise ValueError("knowledge_document_count_or_duplicates")
    for d in docs:
        if error := document_error(d):
            raise ValueError(error + ":" + d.get("id", "unknown"))
    approved = [d for d in docs if d["status"] == "approved"]
    if not approved:
        raise ValueError("no_approved_documents")
    for i, d in enumerate(approved):
        for other in approved[i + 1:]:
            scope = ("scenario", "rule_id", "tenant_id", "contract", "publish")
            if all(d[k] == other[k] for k in scope) and max(d["valid_from"], other["valid_from"]) < min(d["valid_until"], other["valid_until"]):
                raise ValueError("overlapping_approved_versions")
    embedder = Embedder(settings)
    identity = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "embedding_model": embedder.model, "embedding_digest": embedder.model_digest,
                "tokenizer": TOKENIZER, "params": PARAMS, "contract": VERSION}
    sid = digest(identity)
    target = root / "snapshots" / sid
    if target.exists():
        Retriever(settings, sid)
    else:
        vectors = embedder.embed([d["title"] + " " + d["aliases"] + " " + d["body"] for d in approved])
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".building-", dir=target.parent))
        with connect_write(staging / "catalog.sqlite3") as c:
            c.execute("CREATE TABLE documents (id TEXT PRIMARY KEY,scenario TEXT,tenant TEXT,status TEXT,start TEXT,stop TEXT,contract TEXT,publish TEXT,payload TEXT,vector TEXT)")
            c.execute("CREATE INDEX scope ON documents(scenario,tenant,status,start,stop)")
            for d, v in zip(approved, vectors):
                c.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    d["id"], d["scenario"], d["tenant_id"], d["status"], d["valid_from"],
                    d["valid_until"], d["contract"], d["publish"], json.dumps(d, ensure_ascii=False), json.dumps(v)))
        raw = (staging / "catalog.sqlite3").read_bytes()
        manifest = {**identity, "snapshot_id": sid, "source_records": len(docs),
                    "indexed_approved_records": len(approved), "embedding_dimensions": len(vectors[0]),
                    "catalog_sha256": hashlib.sha256(raw).hexdigest(),
                    "created_at": datetime.now(timezone.utc).isoformat(), "synthetic_only": True}
        write_json(staging / "manifest.json", manifest)
        staging.resolve().relative_to(root.resolve())
        target.resolve().relative_to(root.resolve())
        os.replace(staging, target)
    write_json(root / "active.json", {"snapshot_id": sid})
    return Retriever(settings, sid).manifest


class Retriever:
    def __init__(self, settings, snapshot="active"):
        self.settings = settings
        root = settings.data_dir / "knowledge"
        if snapshot == "active":
            snapshot = json.loads((root / "active.json").read_text(encoding="utf-8"))["snapshot_id"]
        if not re.fullmatch(r"[a-f0-9]{64}", snapshot):
            raise ValueError("invalid_knowledge_snapshot")
        self.path = root / "snapshots" / snapshot
        self.manifest = json.loads((self.path / "manifest.json").read_text(encoding="utf-8"))
        identity = {k: self.manifest[k] for k in ("source_sha256", "embedding_model", "embedding_digest", "tokenizer", "params", "contract")}
        if digest(identity) != snapshot or self.manifest["snapshot_id"] != snapshot:
            raise ValueError("knowledge_manifest_integrity")
        if hashlib.sha256((self.path / "catalog.sqlite3").read_bytes()).hexdigest() != self.manifest["catalog_sha256"]:
            raise ValueError("knowledge_catalog_integrity")
        self.embedder = Embedder(settings, self.manifest["embedding_model"], self.manifest["embedding_digest"])

    def search(self, query, scenario, *, as_of=None, tenant="demo", contract="any", publish="any", top_k=5):
        started = time.perf_counter()
        if scenario not in {"delivery", "course"} or not isinstance(query, str) or not 1 <= len(query) <= 1000 or not 1 <= top_k <= 10:
            raise ValueError("invalid_retrieval_request")
        at = as_of or date.today().isoformat()
        date.fromisoformat(at)
        with connect_readonly(self.path / "catalog.sqlite3") as c:
            rows = c.execute("SELECT payload,vector FROM documents WHERE scenario=? AND tenant=? AND status='approved' AND start<=? AND stop>? AND (contract='any' OR contract=?) AND (publish='any' OR publish=?)",
                             (scenario, tenant, at, at, contract, publish)).fetchall()
        if not rows:
            return {"hits": [], "answerable": False, "snapshot_id": self.manifest["snapshot_id"], "query": query,
                    "reason": "no_valid_documents", "latency_ms": (time.perf_counter() - started) * 1000}
        vector = self.embedder.embed([query])[0]
        docs = [json.loads(r[0]) for r in rows]
        counts = [Counter(tokens(d["title"] + " " + d["aliases"] + " " + d["body"])) for d in docs]
        lengths = [sum(t.values()) for t in counts]
        avg = sum(lengths) / len(lengths)
        qt = set(tokens(query))
        lexical, dense = [], []
        for i, (d, row, tf, length) in enumerate(zip(docs, rows, counts, lengths)):
            dv = json.loads(row[1])
            if len(dv) != len(vector):
                raise ValueError("embedding_dimension_changed")
            dense.append(sum(a * b for a, b in zip(vector, dv)))
            value = 0.0
            for term in qt:
                n = sum(term in t for t in counts)
                f = tf.get(term, 0)
                if f:
                    value += math.log(1 + (len(counts) - n + .5) / (n + .5)) * f * 2.2 / (f + 1.2 * (.25 + .75 * length / avg))
            lexical.append(value)
        dr = sorted(range(len(docs)), key=lambda i: (-dense[i], docs[i]["id"]))
        lr = sorted((i for i in range(len(docs)) if lexical[i] > 0), key=lambda i: (-lexical[i], docs[i]["id"]))
        scores = Counter()
        params = self.manifest["params"]
        for order in (dr, lr):
            for rank, i in enumerate(order, 1):
                scores[i] += 1 / (params["rrf_k"] + rank)
        order = sorted(scores, key=lambda i: (-scores[i], docs[i]["id"]))[:top_k]
        hits = [{"document": docs[i], "document_sha256": digest(docs[i]), "score": scores[i],
                 "dense_score": dense[i], "bm25_score": lexical[i], "rank": rank,
                 "lexical_overlap": len(qt & set(counts[i]))}
                for rank, i in enumerate(order, 1)]
        answerable = bool(hits and hits[0]["dense_score"] >= params["dense_min"] and hits[0]["lexical_overlap"] >= params["lexical_min"])
        return {"hits": hits, "answerable": answerable, "snapshot_id": self.manifest["snapshot_id"],
                "query": query, "reason": None if answerable else "insufficient_evidence",
                "latency_ms": (time.perf_counter() - started) * 1000}

    def bundle(self, scenario, facts, opening, *, as_of=None):
        at = as_of or date.today().isoformat()
        kwargs = {"as_of": at, "contract": facts.get("contract", "any"), "publish": facts.get("publish", "any")}
        searches = [self.search(opening, scenario, **kwargs)]
        if re.search(r"[?？]|什么|怎么|如何|多少|哪些|为何|帮我|查询|给我", opening) and not searches[0]["answerable"]:
            raise ValueError("knowledge_unanswerable_question")
        clauses = []
        for rule in required_rules(scenario, facts):
            query = QUERIES[rule]
            if rule == "publish":
                query = "第三方服务商管理勾选低延迟并保存" if facts["publish"] == "third_party" else "Web发布页选择直播类型"
            result = self.search(query, scenario, **kwargs)
            searches.append(result)
            selected = next((h for h in result["hits"] if h["document"]["rule_id"] == rule
                             and h["dense_score"] >= self.manifest["params"]["dense_min"]), None)
            if not selected:
                raise ValueError("knowledge_missing_rule:" + rule)
            clauses.append({"document": selected["document"], "document_sha256": selected["document_sha256"],
                            "text": render(selected["document"], facts), "retrieval_rank": selected["rank"]})
        bundle = {"contract": VERSION, "snapshot_id": self.manifest["snapshot_id"], "scenario": scenario,
                  "tenant_id": "demo", "as_of": at, "clauses": clauses}
        bundle["sha256"] = digest(bundle)
        if error := bundle_error(bundle, scenario, facts):
            raise ValueError(error)
        return bundle, searches
