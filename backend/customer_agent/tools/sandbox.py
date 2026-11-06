import json
import os
import subprocess
import sys
import time
import uuid
from backend.customer_agent.core.settings import Settings, ROOT


class SandboxError(RuntimeError):
    pass


class Sandbox:
    def __init__(self, state, settings=None, mode=None):
        self.state = state
        self.settings = settings or Settings()
        self.mode = mode or self.settings.sandbox
        if self.mode not in {"docker", "process"}:
            raise ValueError("invalid sandbox mode")
        self.events = []

    def call(self, action):
        payload = json.dumps(
            {"state": self.state, "action": action}, ensure_ascii=False
        ).encode("utf-8")
        if len(payload) > 131072:
            raise SandboxError("sandbox payload limit")
        container_name = "outbound-eval-" + uuid.uuid4().hex
        if self.mode == "docker":
            cmd = [
                "docker",
                "run",
                "--rm",
                "-i",
                "--name",
                container_name,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "32",
                "--memory",
                "128m",
                "--cpus",
                "0.5",
                "--user",
                "65532:65532",
                self.settings.sandbox_image,
            ]
            env = None
        else:
            cmd = [
                sys.executable,
                "-I",
                "-S",
                str(ROOT / "backend/industrial/sandbox_worker.py"),
            ]
            env = {
                k: v
                for k, v in os.environ.items()
                if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LANG"}
            }
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.settings.tool_timeout,
                env=env,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            if self.mode == "docker":
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True,
                    timeout=10,
                    shell=False,
                )
            raise SandboxError("sandbox_timeout") from exc
        except OSError as exc:
            raise SandboxError("sandbox_unavailable") from exc
        if proc.returncode or len(proc.stdout) > 262144:
            raise SandboxError("sandbox_worker_failed")
        try:
            response = json.loads(proc.stdout)
            if (
                not isinstance(response["state"], dict)
                or type(response["ok"]) is not bool
            ):
                raise ValueError("invalid worker envelope")
        except (ValueError, KeyError, TypeError) as exc:
            raise SandboxError("invalid_sandbox_response") from exc
        self.state = response["state"]
        event = {
            "index": len(self.events),
            "action": action,
            "ok": response["ok"],
            "error": response.get("error"),
            "result": response.get("result", {}),
            "latency_ms": round((time.perf_counter() - start) * 1000, 3),
            "transport": self.mode,
        }
        self.events.append(event)
        return event
