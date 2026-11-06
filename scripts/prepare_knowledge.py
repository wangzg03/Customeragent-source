"""Build an index from operator-supplied reviewed policy documents."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.customer_agent.core.settings import Settings
from backend.customer_agent.knowledge.retrieval import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error("source file is unavailable")
    manifest = build(Settings().validate(), args.source)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
