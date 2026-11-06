"""Run with python -m uvicorn backend.app:app --workers 1."""

from backend.customer_agent.interfaces.http import create_app

app = create_app()
