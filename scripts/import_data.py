"""Adapt and validate operator-supplied JSONL; no built-in evaluation records."""

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.customer_agent.core.settings import Settings
from backend.customer_agent.evaluation.adapter import adapt_jsonl
from backend.customer_agent.evaluation.dataset import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error("source file is unavailable")
    settings = Settings()
    target = settings.data_dir / "cases.jsonl"
    if target.exists():
        parser.error("local cases already exist; use a different EVAL_DATA_DIR")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    staging = settings.data_dir / (".import-" + uuid.uuid4().hex + ".jsonl")
    try:
        adapt_jsonl(args.source, staging)
        quality = validate(staging)
        if target.exists():
            raise FileExistsError("local cases were created during import")
        staging.rename(target)
        print(json.dumps(quality, ensure_ascii=False, indent=2))
    finally:
        staging.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
