import logging
from pathlib import Path
import threading
from backend.customer_agent.runtime.artifacts import run_batch
from backend.customer_agent.evaluation.dataset import load, select
from backend.customer_agent.core.schemas import JobRequest

log = logging.getLogger(__name__)


class SingleInstance:
    """Prevent multiple API processes from corrupting recovery semantics."""

    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"0")
        self.handle.flush()
        self.handle.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError(
                "Only one API process per runtime directory is supported. Use --workers 1."
            )

    def release(self):
        if self.handle:
            self.handle.close()
            self.handle = None


class JobWorker:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.stop = threading.Event()
        self.wakeup = threading.Event()
        self.thread = threading.Thread(
            target=self.loop, name="evaluation-worker", daemon=True
        )

    def start(self):
        self.store.recover()
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        self.wakeup.set()
        if self.thread.is_alive():
            self.thread.join(timeout=self.settings.model_timeout * 2 + 15)

    def loop(self):
        while not self.stop.is_set():
            job = self.store.claim()
            if not job:
                self.wakeup.wait(1)
                self.wakeup.clear()
                continue
            uid = job["id"]
            try:
                req = JobRequest.model_validate(job["request"])
                cases = select(
                    load(self.settings.data_dir / "cases.jsonl"),
                    req.split,
                    req.scenario,
                    req.limit,
                    req.seed,
                    req.case_ids,
                )
                cancelled = lambda: (
                    self.stop.is_set() or bool(self.store.get(uid)["cancel"])
                )
                summary = run_batch(
                    cases,
                    req,
                    self.settings,
                    self.settings.runtime_dir / "runs" / uid,
                    progress=lambda d, t: self.store.progress(uid, d, t),
                    cancelled=cancelled,
                    resume=bool(job.get("resume")),
                )
                self.store.finish(
                    uid, "cancelled" if cancelled() else "completed", summary=summary
                )
            except Exception:
                log.exception("evaluation_job_failed id=%s", uid)
                self.store.finish(
                    uid,
                    "failed",
                    error="evaluation_execution_failed; inspect server logs",
                )
