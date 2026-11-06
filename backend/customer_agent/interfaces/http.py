from contextlib import asynccontextmanager
import asyncio
from collections import defaultdict, deque
import hmac
import io
import json
import logging
import re
import threading
import time
import uuid
import zipfile
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import Field
from backend.customer_agent.evaluation.dataset import DatasetUnavailable, digest, load, select, validate
from backend.customer_agent.runtime.jobs import JobWorker, SingleInstance
from backend.customer_agent.domain.policy import PERSONAS
from backend.customer_agent.core.schemas import JobRequest, StrictModel
from backend.customer_agent.core.settings import ROOT, Settings
from backend.customer_agent.runtime.store import Store
from backend.customer_agent.evolution.service import Registry, EvolutionController
from backend.customer_agent.runtime.harness import replay

log = logging.getLogger(__name__)
ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class BodyLimit:
    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                return await JSONResponse(
                    {"detail": "request_body_too_large"}, status_code=413
                )(scope, receive, send)
            body.append(chunk)
            if not message.get("more_body"):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {
                    "type": "http.request",
                    "body": b"".join(body),
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, replay, send)


class LegacyRequest(StrictModel):
    scenario: str = Field(pattern=r"^(delivery|course)$")
    personality: str = Field(min_length=1, max_length=40)


class KnowledgeSearch(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    scenario: str = Field(pattern=r"^(delivery|course)$")
    contract: str = Field(default="any", pattern=r"^(any|single|multi)$")
    publish: str = Field(default="any", pattern=r"^(any|web|third_party)$")
    snapshot: str = Field(default="active", pattern=r"^(active|[a-f0-9]{64})$")
    top_k: int = Field(default=5, ge=1, le=10)


def create_app(settings=None):
    settings = (settings or Settings()).validate()
    store = Store(settings.runtime_dir / "evaluations.sqlite3")
    worker = JobWorker(store, settings)
    instance = SingleInstance(settings.runtime_dir / "service.lock")
    evolution = EvolutionController(settings)
    orchestration_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        instance.acquire()
        try:
            evolution.recover()
            if (settings.data_dir / "cases.jsonl").is_file():
                validate(settings.data_dir / "cases.jsonl")
            worker.start()
            yield
        finally:
            await asyncio.to_thread(evolution.shutdown)
            await asyncio.to_thread(worker.shutdown)
            instance.release()

    app = FastAPI(title="外呼智能体工业评测工作台", version="2.0.0", lifespan=lifespan)
    @app.exception_handler(DatasetUnavailable)
    async def dataset_unavailable(request, exc):
        return JSONResponse({"detail": "dataset_not_provided"}, status_code=503)

    app.state.store = store
    app.state.worker = worker
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8001",
            "http://localhost:8001",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key", "Idempotency-Key"],
    )
    app.add_middleware(BodyLimit, max_bytes=settings.max_body_bytes)
    rates = defaultdict(deque)
    rate_lock = threading.Lock()

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        started = time.perf_counter()
        rid = uuid.uuid4().hex
        if request.url.path.startswith("/api") or request.url.path == "/metrics":
            if (
                request.method != "OPTIONS"
                and settings.api_key
                and not hmac.compare_digest(
                    request.headers.get("X-API-Key", ""), settings.api_key
                )
            ):
                return JSONResponse(
                    {"detail": "unauthorized", "request_id": rid}, status_code=401
                )
            if request.method == "POST":
                host = request.client.host if request.client else "unknown"
                now = time.monotonic()
                with rate_lock:
                    if len(rates) > 4096:
                        for k in list(rates):
                            if not rates[k] or rates[k][-1] < now - 60:
                                del rates[k]
                    if host not in rates and len(rates) >= 4096:
                        return JSONResponse(
                            {"detail": "rate_limit_capacity"}, status_code=429
                        )
                    q = rates[host]
                    while q and q[0] < now - 60:
                        q.popleft()
                    if len(q) >= 30:
                        return JSONResponse(
                            {"detail": "rate_limit"},
                            status_code=429,
                            headers={"Retry-After": "60"},
                        )
                    q.append(now)
        response = await call_next(request)
        response.headers.update(
            {
                "X-Request-ID": rid,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store"
                if request.url.path.startswith("/api")
                else "no-cache",
            }
        )
        log.info(
            "request id=%s method=%s path=%s status=%s duration_ms=%.1f",
            rid,
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    def checked_job(uid):
        if not ID_PATTERN.fullmatch(uid):
            raise HTTPException(404, "job_not_found")
        job = store.get(uid)
        if not job:
            raise HTTPException(404, "job_not_found")
        return job

    def rows_for(uid):
        checked_job(uid)
        p = settings.runtime_dir / "runs" / uid / "episodes.jsonl"
        if not p.exists():
            return []
        rows = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                break
        return rows

    def submit(req, key=None):
        if evolution.status().get("status") == "running":
            raise HTTPException(409, "evolution_cycle_owns_model_budget")
        if settings.environment == "production" and req.sandbox != "docker":
            raise HTTPException(422, "production_requires_docker")
        if req.limit > settings.max_cases:
            raise HTTPException(422, "case_budget_exceeded")
        try:
            if req.knowledge_mode == "rag":
                from datetime import date
                from backend.customer_agent.knowledge.retrieval import Retriever

                try:
                    knowledge = Retriever(settings, req.knowledge_snapshot)
                except (OSError, ValueError, httpx.HTTPError) as exc:
                    raise HTTPException(503, "knowledge_unavailable_or_invalid") from exc
                req = req.model_copy(update={"knowledge_snapshot": knowledge.manifest["snapshot_id"],
                                             "knowledge_as_of": req.knowledge_as_of or date.today().isoformat()})
            # Freeze active version at admission so a later promotion cannot
            # silently change queued experiments.
            req = req.model_copy(
                update={
                    "profile": Registry(settings.runtime_dir / "evolution").resolve(
                        req.profile
                    )["id"]
                }
            )
            cases = select(
                load(settings.data_dir / "cases.jsonl"),
                req.split,
                req.scenario,
                req.limit,
                req.seed,
                req.case_ids,
            )
            if not cases:
                raise ValueError("empty_case_selection")
            job = store.create(req, len(cases) * req.repeats, settings.job_limit, key)
        except DatasetUnavailable as exc:
            raise HTTPException(503, "dataset_not_provided") from exc
        except ValueError as exc:
            raise HTTPException(
                409 if "conflict" in str(exc) else 422, str(exc)
            ) from exc
        except OverflowError as exc:
            raise HTTPException(429, str(exc)) from exc
        worker.wakeup.set()
        return job

    @app.get("/health")
    def health():
        return {"status": "alive", "version": "2.0.0"}

    @app.get("/ready")
    def ready():
        healthy = (
            worker.thread.is_alive() and (settings.data_dir / "cases.jsonl").is_file()
        )
        try:
            with store.connection() as c:
                c.execute("SELECT 1").fetchone()
        except Exception:
            healthy = False
        return JSONResponse(
            {
                "ready": healthy,
                "worker_alive": worker.thread.is_alive(),
                "sandbox": settings.sandbox,
                "scope": "API, queue and dataset; model availability in meta",
            },
            status_code=200 if healthy else 503,
        )

    @app.get("/api/v2/meta")
    def meta():
        dataset_path = settings.data_dir / "cases.jsonl"
        manifest = {"status": "not_provided", "quality": {"rows": 0}}
        if dataset_path.is_file():
            manifest = {"status": "available", "version": "user-provided",
                        "sha256": digest(dataset_path), "quality": validate(dataset_path)}
        available = []
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                available = [
                    m["name"]
                    for m in client.get(settings.ollama_url + "/api/tags").json()[
                        "models"
                    ]
                    if m["name"] in settings.models
                ]
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        return {
            "version": "2.0.0",
            "dataset": manifest,
            "models": ["rule-reference"] + available,
            "sandbox_default": settings.sandbox,
            "environment": settings.environment,
            "max_job_episodes": 200,
            "auth_enabled": bool(settings.api_key),
        }

    @app.get("/api/v2/dataset")
    def dataset(
        split: str = "test", scenario: str = "all", limit: int = Query(24, ge=1, le=100)
    ):
        if not (settings.data_dir / "cases.jsonl").is_file():
            return {"items": [], "total_in_selection": 0, "status": "not_provided"}
        cases = select(load(settings.data_dir / "cases.jsonl"), split, scenario, limit)
        return {
            "items": [c.model_dump() for c in cases],
            "total_in_selection": len(cases),
        }

    @app.get("/api/v2/knowledge")
    def knowledge_info():
        root = settings.data_dir / "knowledge"
        if not (root / "active.json").exists():
            return {"status": "not_built", "manifest": None}
        sid = json.loads((root / "active.json").read_text(encoding="utf-8"))["snapshot_id"]
        if not re.fullmatch(r"[a-f0-9]{64}", sid):
            raise HTTPException(503, "invalid_knowledge_pointer")
        return {"status": "available", "manifest": json.loads((root / "snapshots" / sid / "manifest.json").read_text(encoding="utf-8")),
                "scope": "single_demo_tenant; reviewed fictional policy; no evaluation labels"}

    @app.post("/api/v2/knowledge/search")
    def knowledge_search(req: KnowledgeSearch):
        from backend.customer_agent.knowledge.retrieval import Retriever

        try:
            return Retriever(settings, req.snapshot).search(req.query, req.scenario, contract=req.contract,
                                                           publish=req.publish, top_k=req.top_k)
        except (OSError, ValueError, httpx.HTTPError) as exc:
            raise HTTPException(503, "knowledge_unavailable_or_invalid") from exc

    @app.post("/api/v2/jobs", status_code=202)
    def create(req: JobRequest, request: Request):
        key = request.headers.get("Idempotency-Key")
        if key and (len(key) > 100 or not re.fullmatch(r"[A-Za-z0-9_.-]+", key)):
            raise HTTPException(422, "invalid_idempotency_key")
        with orchestration_lock:
            return submit(req, key)

    @app.get("/api/v2/evolution")
    def evolution_status():
        registry = Registry(settings.runtime_dir / "evolution")
        return {
            "registry": registry.state(),
            "active_profile": registry.resolve("active"),
            "cycle": evolution.status(),
            "actor": "qwen3.5:9b",
            "judge": None,
            "scope": "offline_prompt_evolution",
        }

    @app.post("/api/v2/evolution/cycles", status_code=202)
    def start_evolution():
        with orchestration_lock:
            if any(j["status"] in {"queued", "running"} for j in store.list(100, 0)):
                raise HTTPException(409, "wait_for_existing_jobs")
            try:
                return evolution.start()
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v2/evolution/cancel")
    def cancel_evolution():
        evolution.stop.set()
        return evolution.status()

    @app.post("/api/v2/evolution/rollback")
    def rollback_evolution():
        with orchestration_lock:
            if evolution.status().get("status") == "running":
                raise HTTPException(409, "cycle_running")
            try:
                return Registry(settings.runtime_dir / "evolution").rollback()
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v2/jobs/{uid}/sessions/{case_id}/{repeat}")
    def session_replay(uid: str, case_id: str, repeat: int):
        checked_job(uid)
        if not re.fullmatch(r"[a-z0-9_-]{1,100}", case_id) or not 0 <= repeat < 5:
            raise HTTPException(404)
        p = (
            settings.runtime_dir
            / "runs"
            / uid
            / "sessions"
            / f"{case_id}-{repeat}.jsonl"
        )
        if not p.is_file():
            raise HTTPException(404)
        try:
            events = [
                json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
            ]
            return {"events": events, "replay": replay(events)}
        except ValueError as exc:
            raise HTTPException(
                409, "journal_not_replayable_or_incomplete_write"
            ) from exc

    @app.get("/api/v2/jobs")
    def jobs(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return {"items": store.list(limit, offset)}

    @app.get("/api/v2/jobs/{uid}")
    def get_job(uid: str):
        return checked_job(uid)

    @app.post("/api/v2/jobs/{uid}/cancel")
    def cancel(uid: str):
        checked_job(uid)
        store.cancel(uid)
        return store.get(uid)

    @app.post("/api/v2/jobs/{uid}/resume", status_code=202)
    def resume_job(uid: str):
        from backend.customer_agent.runtime.recovery import inspect_resume

        with orchestration_lock:
            job = checked_job(uid)
            if evolution.status().get("status") == "running":
                raise HTTPException(409, "evolution_cycle_owns_model_budget")
            req = JobRequest.model_validate(job["request"])
            try:
                cases = select(
                    load(settings.data_dir / "cases.jsonl"),
                    req.split,
                    req.scenario,
                    req.limit,
                    req.seed,
                    req.case_ids,
                )
                inspect_resume(
                    settings.runtime_dir / "runs" / uid, cases, req, settings
                )
                result = store.resume(uid, settings.job_limit)
            except (ValueError, OSError, KeyError) as exc:
                raise HTTPException(409, "resume_not_safe: " + str(exc)[:180]) from exc
            worker.wakeup.set()
            return result

    @app.get("/api/v2/jobs/{uid}/episodes")
    def episodes(
        uid: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        rows = rows_for(uid)
        return {"items": rows[offset : offset + limit], "total": len(rows)}

    @app.get("/api/v2/jobs/{uid}/download")
    def download(uid: str):
        checked_job(uid)
        folder = settings.runtime_dir / "runs" / uid
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("config.json", "summary.json", "episodes.jsonl"):
                p = folder / name
                if p.is_file():
                    z.write(p, name)
            for p in (folder / "sessions").glob("*.jsonl"):
                z.write(p, "sessions/" + p.name)
            for p in (folder / "resumes").glob("*.json"):
                z.write(p, "resumes/" + p.name)
        return Response(
            output.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="evaluation_{uid}.zip"'
            },
        )

    @app.get("/api/v2/experiments")
    def experiments():
        items = []
        for p in sorted((settings.runtime_dir / "experiments").glob("*/summary.json")):
            try:
                items.append(
                    {
                        "id": p.parent.name,
                        "summary": json.loads(p.read_text(encoding="utf-8")),
                        "config": json.loads(
                            p.with_name("config.json").read_text(encoding="utf-8")
                        ),
                    }
                )
            except (ValueError, OSError):
                continue
        return {"items": items}

    @app.get("/api/v2/experiments/{name}/episodes")
    def experiment_episodes(
        name: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name):
            raise HTTPException(404)
        p = settings.runtime_dir / "experiments" / name / "episodes.jsonl"
        if not p.is_file():
            raise HTTPException(404)
        rows = [
            json.loads(line)
            for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return {"items": rows[offset : offset + limit], "total": len(rows)}

    @app.get("/metrics")
    def metrics():
        with store.connection() as c:
            counts = c.execute(
                "SELECT status,COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        text = (
            "# HELP evaluation_jobs Job count by status\n# TYPE evaluation_jobs gauge\n"
        )
        text += "".join(
            f'evaluation_jobs{{status="{r["status"]}"}} {r["n"]}\n' for r in counts
        )
        latest = next(
            (
                j
                for j in store.list(100, 0)
                if j["status"] == "completed"
                and j["summary"]
                and j["request"]["model"] != "rule-reference"
            ),
            None,
        )
        if latest:
            # Stable, bounded labels; no job IDs, prompts or customer data.
            for key in (
                "task_success_rate",
                "critical_violation_rate",
                "execution_error_rate",
                "model_latency_p95_ms",
                "business_outcome_rate",
                "safety_pass_rate",
                "quality_pass_rate",
            ):
                value = latest["summary"].get(key)
                if isinstance(value, (int, float)):
                    name = "evaluation_latest_" + key
                    text += f"# TYPE {name} gauge\n{name} {value}\n"
            for key, value in latest["summary"].get("rag_metrics", {}).items():
                if key in {"knowledge_failures", "searches", "valid_citation_speeches", "citation_speeches", "retrieval_p95_ms"} and isinstance(value, (int, float)):
                    name = "evaluation_latest_rag_" + key
                    text += f"# TYPE {name} gauge\n{name} {value}\n"
        return Response(
            text + f"evaluation_worker_alive {int(worker.thread.is_alive())}\n",
            media_type="text/plain; version=0.0.4",
        )

    def legacy_view(job):
        s = job.get("summary") or {}
        req = job["request"]
        uid = job["id"]
        episodes = rows_for(uid)
        first = episodes[0] if episodes else None
        dialogue = (
            []
            if not first
            else [
                {
                    "model": t["action"]["arguments"].get(
                        "text", "[" + t["action"]["tool"] + "]"
                    ),
                    "user": t["user"] or "",
                }
                for t in first["trace"]
            ]
        )
        return {
            "id": uid,
            "name": job["name"],
            "scenario": req["scenario"],
            "personality": "兼容模式",
            "status": "running"
            if job["status"] in {"queued", "running"}
            else "completed"
            if job["status"] == "completed"
            else "failed",
            "overall_score": 100 * s.get("task_success_rate", 0),
            "readiness_score": 0,
            "readiness_comment": "已取消主观就绪分；请查看工业工作台的客观发布门禁。",
            "scores": {
                "task_completion": 100 * s.get("task_success_rate", 0),
                "constraint_compliance": 100 * s.get("mean_constraint_compliance", 0),
            },
            "strengths": [],
            "weaknesses": [],
            "critical_incidents": [],
            "improvement_suggestions": ["按失败轨迹修复，并使用真实授权通话数据复验。"],
            "dialogue_history": dialogue,
            "created_at": job["created_at"],
            "functional_validation": None,
        }

    @app.get("/api/scenarios")
    def scenarios():
        return [
            {
                "key": "delivery",
                "name": "配送外呼",
                "description": "合成合同与安全分支",
            },
            {"key": "course", "name": "课程外呼", "description": "直播选项与授权联系"},
        ]

    @app.get("/api/scenarios/personalities")
    def personalities():
        return [{"key": p, "name": p} for p in PERSONAS]

    @app.post("/api/evaluation")
    def legacy_create(req: LegacyRequest):
        if req.personality not in PERSONAS and req.personality != "random":
            raise HTTPException(422, "unknown_personality")
        cases = [
            c
            for c in load(settings.data_dir / "cases.jsonl")
            if c.split == "test"
            and c.scenario == req.scenario
            and c.intent == "normal"
            and (req.personality == "random" or c.personality == req.personality)
        ]
        r = JobRequest(
            scenario=req.scenario,
            case_ids=[cases[0].id],
            limit=1,
            sandbox=settings.sandbox,
        )
        return legacy_view(submit(r))

    @app.get("/api/evaluation")
    def legacy_list(scenario: str | None = None, name: str | None = None):
        return [
            legacy_view(j)
            for j in store.list(100)
            if (not scenario or j["request"]["scenario"] == scenario)
            and (not name or name in j["name"])
        ]

    @app.get("/api/evaluation/{uid}/dialogue")
    def legacy_dialogue(uid: str):
        return {"dialogue": legacy_view(checked_job(uid))["dialogue_history"]}

    @app.get("/api/evaluation/{uid}/download")
    def legacy_download(uid: str):
        return JSONResponse(
            legacy_view(checked_job(uid)),
            headers={
                "Content-Disposition": f'attachment; filename="report_{uid}.json"'
            },
        )

    @app.get("/api/evaluation/{uid}")
    def legacy_get(uid: str):
        return legacy_view(checked_job(uid))

    dist = ROOT / "frontend/dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "metrics", "health", "ready")):
            raise HTTPException(404)
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        return {
            "message": "Build frontend with npm ci && npm run build in frontend/",
            "docs": "/docs",
        }

    return app
