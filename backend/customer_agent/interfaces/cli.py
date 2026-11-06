import argparse
import json
from pathlib import Path
from backend.customer_agent.runtime.artifacts import run_batch
from backend.customer_agent.evaluation.dataset import load, select, validate
from backend.customer_agent.core.schemas import JobRequest
from backend.customer_agent.core.settings import Settings


def main():
    parser = argparse.ArgumentParser(
        description="Outbound agent reproducible evaluation"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-data")
    run = sub.add_parser("run")
    run.add_argument("--model", default="rule-reference")
    run.add_argument(
        "--mode", choices=["legacy_prompt", "policy_only", "guarded"], default="guarded"
    )
    run.add_argument("--split", choices=["train", "dev", "test"], default="test")
    run.add_argument("--scenario", choices=["all", "delivery", "course"], default="all")
    run.add_argument("--limit", type=int, default=24)
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--profile", default="baseline")
    run.add_argument("--sandbox", choices=["process", "docker"], default="process")
    run.add_argument("--case-ids", nargs="*", default=[])
    run.add_argument("--knowledge-mode", choices=["static", "rag"], default="static")
    run.add_argument("--knowledge-snapshot", default="active")
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings().validate()
    if args.command == "validate-data":
        print(
            json.dumps(
                validate(settings.data_dir / "cases.jsonl"),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    req = JobRequest(
        name=args.output.name,
        model=args.model,
        mode=args.mode,
        split=args.split,
        scenario=args.scenario,
        limit=args.limit,
        repeats=args.repeats,
        seed=args.seed,
        profile=args.profile,
        sandbox=args.sandbox,
        case_ids=args.case_ids,
        knowledge_mode=args.knowledge_mode,
        knowledge_snapshot=args.knowledge_snapshot,
    )
    if req.knowledge_mode == "rag":
        from datetime import date
        from backend.customer_agent.knowledge.retrieval import Retriever

        req = req.model_copy(update={"knowledge_snapshot": Retriever(settings, req.knowledge_snapshot).manifest["snapshot_id"],
                                     "knowledge_as_of": date.today().isoformat()})
    cases = select(
        load(settings.data_dir / "cases.jsonl"),
        req.split,
        req.scenario,
        req.limit,
        req.seed,
        req.case_ids,
    )
    if not cases:
        raise SystemExit("No matching cases")
    summary = run_batch(
        cases,
        req,
        settings,
        args.output,
        progress=lambda d, t: print(f"episodes {d}/{t}", flush=True),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
