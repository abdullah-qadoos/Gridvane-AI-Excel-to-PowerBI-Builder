import argparse
import json
from pathlib import Path

from sheetforge.planner import heuristic_plan
from sheetforge.profiler import profile_workbook
from sheetforge.transformer import apply_plan


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sheetforge")
    commands = parser.add_subparsers(dest="command", required=True)
    fix = commands.add_parser(
        "fix", help="Clean an XLSX deterministically; write tables and an audit."
    )
    fix.add_argument("file", type=Path)
    fix.add_argument("--out", type=Path, default=Path("output/cleaned"))
    fix.add_argument("--sheet")
    fix.add_argument(
        "--plan", type=Path, help="Use a reviewed CleaningPlan JSON instead of heuristics."
    )
    args = parser.parse_args(argv)
    try:
        profile = profile_workbook(args.file)
        plan = heuristic_plan(profile, args.sheet)
        if args.plan:
            from sheetforge.contracts import CleaningPlan

            plan = CleaningPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
        result = apply_plan(args.file, plan)
        args.out.mkdir(parents=True, exist_ok=True)
        for index, frame in enumerate(result.tables.values(), 1):
            frame.to_csv(args.out / f"table_{index}.csv", index=False, encoding="utf-8")
        audit = {
            "profile": profile.to_dict(),
            "plan": plan.model_dump(),
            "steps": [step.model_dump() for step in result.steps],
            "warnings": result.warnings,
        }
        (args.out / "audit.json").write_text(
            json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Cleaned {len(result.tables)} table(s). Review {args.out / 'audit.json'}")
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(1, f"sheetforge: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
