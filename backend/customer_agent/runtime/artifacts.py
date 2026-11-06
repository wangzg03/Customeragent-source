import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import httpx
from backend.customer_agent.core.settings import ROOT
from backend.customer_agent.evaluation.dataset import digest


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if Path(tmp).exists():
            Path(tmp).unlink()


def source_files():
    return sorted(p for directory in ("backend/customer_agent", "backend/industrial", "backend/prompts")
                  for p in (ROOT / directory).rglob("*.py") if "__pycache__" not in p.parts)


def source_hash():
    h = hashlib.sha256()
    for p in source_files():
        h.update(p.relative_to(ROOT).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def environment(settings):
    import zipfile

    source = _CAPTURED_HASH
    if source_hash() != source:
        raise RuntimeError(
            "source_changed_since_process_start_restart_service_before_running"
        )
    snapshot = settings.runtime_dir / "source_snapshots" / (source + ".zip")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot.exists():
        with zipfile.ZipFile(snapshot, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in _CAPTURED_FILES.items():
                archive.writestr(name, content)
    info = {
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "source_tree_sha256": source,
        "source_origin": "captured_at_process_import; changed-on-disk source rejected",
        "dataset_sha256": digest(settings.data_dir / "cases.jsonl"),
    }
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            info["ollama_version"] = client.get(
                settings.ollama_url + "/api/version"
            ).json()
            info["ollama_models"] = [
                {"name": m["name"], "digest": m["digest"], "size": m["size"]}
                for m in client.get(settings.ollama_url + "/api/tags").json()["models"]
                if m["name"] in settings.models
            ]
    except (httpx.HTTPError, ValueError, KeyError):
        info["ollama_status"] = "unavailable"
    try:
        p = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                settings.sandbox_image,
                "--format",
                "{{.Id}}",
            ],
            capture_output=True,
            timeout=8,
        )
        info["sandbox_image_id"] = (
            p.stdout.decode().strip() if p.returncode == 0 else None
        )
    except (OSError, subprocess.TimeoutExpired):
        info["sandbox_image_id"] = None
    return info


def run_batch(
    cases,
    request,
    settings,
    output,
    progress=lambda done, total: None,
    cancelled=lambda: False,
    resume=False,
):
    from backend.customer_agent.agent.engine import run_episode
    from backend.customer_agent.evaluation.metrics import summarize
    from datetime import datetime, timezone
    from backend.customer_agent.evolution.service import Registry

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    episode_file = output / "episodes.jsonl"
    if episode_file.exists() and not resume:
        raise FileExistsError("refusing to overwrite experiment trajectories")
    profile = Registry(settings.runtime_dir / "evolution").resolve(request.profile)
    retriever = None
    if request.knowledge_mode == "rag":
        from backend.customer_agent.knowledge.retrieval import Retriever

        retriever = Retriever(settings, request.knowledge_snapshot)
        if request.knowledge_snapshot == "active":
            raise ValueError("freeze_knowledge_snapshot_before_job_execution")
    new_config = {
        "request": request.model_dump(),
        "selected_case_ids": [c.id for c in cases],
        "environment": environment(settings),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "timestamp_timezone": "UTC",
        "profile_snapshot": profile,
        "knowledge_manifest": retriever.manifest if retriever else None,
    }
    if resume:
        from backend.customer_agent.runtime.recovery import inspect_resume, recover_episode
        import uuid

        config, rows, recoverable = inspect_resume(output, cases, request, settings)
        if profile != config["profile_snapshot"]:
            raise ValueError("resume_profile_integrity_mismatch")
        write_json(
            output / "resumes" / (uuid.uuid4().hex + ".json"),
            {
                "original_config_preserved": True,
                "new_execution": new_config,
                "existing_episodes": len(rows),
                "recoverable_projections": [c.id for c, _, _, _ in recoverable],
                "no_ambiguous_tool_retry": True,
            },
        )
        recovered = [
            recover_episode(c, repeat, events, verified, config, settings)
            for c, repeat, events, verified in recoverable
        ]
    else:
        config = new_config
        write_json(output / "config.json", config)
        rows, recovered = [], []
    total = len(cases) * request.repeats
    with episode_file.open("a" if resume else "x", encoding="utf-8") as stream:
        for row in recovered:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            rows.append(row)
        seen = {(r["case_id"], r["repeat"]) for r in rows}
        progress(len(rows), total)
        for repeat in range(request.repeats):
            for c in cases:
                if (c.id, repeat) in seen:
                    continue
                if cancelled():
                    break
                row = run_episode(
                    c,
                    model=request.model,
                    mode=request.mode,
                    seed=request.seed + repeat,
                    settings=settings,
                    sandbox_mode=request.sandbox,
                    cancelled=cancelled,
                    profile=profile,
                    journal_path=output / "sessions" / f"{c.id}-{repeat}.jsonl",
                    knowledge_mode=request.knowledge_mode,
                    knowledge_snapshot=request.knowledge_snapshot,
                    knowledge_as_of=request.knowledge_as_of,
                    retriever=retriever,
                )
                row["repeat"] = repeat
                row["generation_source_sha256"] = new_config["environment"][
                    "source_tree_sha256"
                ]
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                rows.append(row)
                progress(len(rows), total)
            if cancelled():
                break
    summary = summarize(rows)
    if request.knowledge_mode == "rag":
        from backend.customer_agent.evaluation.retrieval_metrics import summarize_rag

        rag = summarize_rag(rows)
        summary.update(rag)
        summary.setdefault("operational_metrics", []).extend(rag.get("rag_operational_metrics", []))
    summary["data_complete"] = len(rows) == total and not cancelled()
    summary["expected_episodes"] = total
    summary["episodes_sha256"] = digest(episode_file)
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(output / "summary.json", summary)
    return summary


# Capture immutable source bytes before any experiment can be admitted. A
# long-lived service must restart after code edits; disk hashes must not be
# mistaken for the code already loaded by its Python process.
_CAPTURED_FILES = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in source_files()}
_captured_digest = hashlib.sha256()
for _name, _content in sorted(_CAPTURED_FILES.items()):
    _captured_digest.update(_name.encode())
    _captured_digest.update(_content)
_CAPTURED_HASH = _captured_digest.hexdigest()
