import argparse
import json
from pathlib import Path

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
        "--provider",
        choices=["none", "openai", "anthropic", "gemini", "azure", "openrouter", "ollama"],
        default="none",
    )
    fix.add_argument("--model", default="")
    fix.add_argument("--base-url")
    fix.add_argument(
        "--plan", type=Path, help="Use a reviewed CleaningPlan JSON instead of heuristics."
    )
    build = commands.add_parser("build", help="Generate the minimal refreshable TMDL/PBIR project.")
    build.add_argument("file", type=Path)
    build.add_argument("--out", type=Path, default=Path("output/project"))
    build.add_argument("--sheet")
    build.add_argument(
        "--table", help="Explicit output table to include at the single-table milestone."
    )
    build.add_argument(
        "--provider",
        choices=["none", "openai", "anthropic", "gemini", "azure", "openrouter", "ollama"],
        default="none",
    )
    build.add_argument("--model", default="")
    build.add_argument("--base-url")
    build.add_argument("--plan", type=Path)
    args = parser.parse_args(argv)
    try:
        profile = profile_workbook(args.file)
        from sheetforge.llm import LLMConfig, configure
        from sheetforge.planner.llm_planner import plan_cleaning

        configure(LLMConfig(provider=args.provider, model=args.model, base_url=args.base_url))
        if args.plan:
            from sheetforge.contracts import CleaningPlan

            plan = CleaningPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
            planning_notes = ["Applied the explicitly reviewed CleaningPlan; no LLM call was made."]
        else:
            plan, planning_notes = plan_cleaning(profile, args.sheet)
        result = apply_plan(args.file, plan)
        if args.command == "build":
            from sheetforge.pbip import build_project

            project, zipped = build_project(result, args.out, args.table)
            print(
                f"Project: {project}\nZIP: {zipped}\nStep 3: open in Power BI Desktop and report any errors before continuing."
            )
            return 0
        args.out.mkdir(parents=True, exist_ok=True)
        for index, frame in enumerate(result.tables.values(), 1):
            frame.to_csv(args.out / f"table_{index}.csv", index=False, encoding="utf-8")
        audit = {
            "planning_notes": planning_notes,
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
