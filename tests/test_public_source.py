"""Public-source behavior checks; no private records or historical outputs."""

import secrets

import httpx
import pytest
from backend.customer_agent.core.schemas import Action, JobRequest
from backend.customer_agent.core.settings import Settings
from backend.customer_agent.evaluation.dataset import DatasetUnavailable, load
from backend.customer_agent.interfaces.http import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "input",
        runtime_dir=tmp_path / "output",
        sandbox="process",
        environment="local",
        api_key="",
    )


@pytest.fixture
def no_model_service(monkeypatch):
    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline test")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", offline)


def test_missing_data_has_explicit_error(tmp_path):
    with pytest.raises(DatasetUnavailable, match="dataset_not_provided"):
        load(tmp_path / "absent.jsonl")


def test_empty_checkout_starts(settings, no_model_service):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        meta = client.get("/api/v2/meta").json()
        assert meta["dataset"]["status"] == "not_provided"
        assert meta["models"] == ["rule-reference"]
        assert client.get("/api/v2/dataset").json()["items"] == []
        assert client.get("/api/v2/jobs").json()["items"] == []
        assert client.get("/api/v2/experiments").json()["items"] == []
        assert client.get("/api/v2/knowledge").json()["status"] == "not_built"
        response = client.post("/api/v2/jobs", json={"model": "rule-reference"})
        assert response.status_code == 503
        assert response.json()["detail"] == "dataset_not_provided"
    assert not settings.data_dir.exists()


def test_auth_reads_only_runtime_secret(settings, no_model_service):
    settings.api_key = secrets.token_urlsafe(32)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v2/jobs").status_code == 401
        assert (
            client.get(
                "/api/v2/jobs", headers={"X-API-Key": settings.api_key}
            ).status_code
            == 200
        )
        assert client.get("/health").status_code == 200


def test_runtime_positions_come_from_environment(tmp_path, monkeypatch):
    for key, suffix in (("EVAL_DATA_DIR", "input"), ("EVAL_RUNTIME_DIR", "output")):
        monkeypatch.setenv(key, str(tmp_path / suffix))
    settings = Settings()
    assert settings.data_dir == tmp_path / "input"
    assert settings.runtime_dir == tmp_path / "output"


def test_production_rejects_missing_auth_and_process(settings):
    settings.environment = "production"
    with pytest.raises(ValueError, match="production requires"):
        settings.validate()


@pytest.mark.parametrize(
    "payload",
    [
        {"limit": 100, "repeats": 5},
        {"knowledge_mode": "rag", "mode": "legacy_prompt"},
        {"endpoint": "unapproved"},
    ],
)
def test_job_boundaries(payload):
    with pytest.raises(ValueError):
        JobRequest.model_validate(payload)


def test_tool_schema_rejects_additional_arguments():
    with pytest.raises(ValueError):
        Action.model_validate({"tool": "lookup_policy", "arguments": {"extra": True}})
