"""Compatibility import; implementation: backend.customer_agent.compatibility.legacy_policy_v3."""
import importlib
import sys

if __name__ == "__main__":
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    importlib.import_module("backend.customer_agent.compatibility.legacy_policy_v3").main()
else:
    sys.modules[__name__] = importlib.import_module("backend.customer_agent.compatibility.legacy_policy_v3")
