import argparse
import json
from pathlib import Path

from sheetforge.profiler import profile_workbook
from sheetforge.transformer import apply_plan


def main(argv=None):
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "serve":
        from sheetforge.desktop import main as serve

        return serve(arguments[1:])
    parser = argparse.ArgumentParser(prog="pivotra")
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
    inspect = commands.add_parser("inspect", help="Print the data quality report.")
    clean = commands.add_parser("clean", help="Apply quality decisions from a JSON file.")
    clean.add_argument("--decisions", type=Path, required=True)
    clean.add_argument("--out", type=Path, default=Path("output/quality"))
    for command in (inspect, clean):
        command.add_argument("file", type=Path)
        command.add_argument("--sheet")
        command.add_argument("--plan", type=Path)
        command.add_argument(
            "--provider",
            choices=["none", "openai", "anthropic", "gemini", "azure", "openrouter", "ollama"],
            default="none",
        )
        command.add_argument("--model", default="")
        command.add_argument("--base-url")
    inspect.add_argument("--json", action="store_true", help="Print the raw JSON report.")
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
        if args.command in {"inspect", "clean"}:
            return quality_command(args, plan)
        result = apply_plan(args.file, plan)
        if args.command == "build":
            from sheetforge.modeler import plan_model
            from sheetforge.pbip import build_semantic_project

            if args.table:
                if args.table not in result.tables:
                    raise ValueError("Selected output table does not exist.")
                result.tables = {args.table: result.tables[args.table]}
            model_plan, _ = plan_model(result.tables)
            project, zipped = build_semantic_project(result, args.out, model_plan)
            print(
                f"Project: {project}\nZIP: {zipped}\nOpen in Power BI Desktop and select Refresh."
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


def quality_command(args, plan):
    from sheetforge import quality
    from sheetforge.presentation import clean_excel
    from sheetforge.transformer import cast_table

    raw = apply_plan(args.file, plan, cast=False)
    reports = {}
    for name, frame in raw.tables.items():
        rules, _ = quality.propose_rules(frame, raw.types[name])
        reports[name] = quality.inspect(frame, raw.types[name], rules)
    if args.command == "inspect":
        if args.json:
            print(json.dumps({n: r.model_dump(mode="json") for n, r in reports.items()}, indent=2))
            return 0
        for name, report in reports.items():
            print(f"{name}: {report.rows} rows")
            for column in report.columns:
                print(
                    f"  {column.column} [{column.type}] missing {column.null_pct}%"
                    f" placeholders {column.placeholder_nulls} wrong-type {column.type_mismatches}"
                    f" formatting {column.formatting_issues} impossible {column.impossible_values}"
                )
            if report.duplicate_key:
                print(f"  proposed duplicate key: {' + '.join(report.duplicate_key)}")
            for item in report.categories or []:
                print(f"  - {item.category}: {item.detail} Options: {', '.join(item.strategies)}")
            if not report.categories:
                print("  No quality issues found.")
        return 0
    chosen = json.loads(args.decisions.read_text(encoding="utf-8"))
    if set(chosen) <= set(quality.models.STRATEGIES):
        chosen = {name: chosen for name in raw.tables}  # flat decisions apply to every table
    raw.tables, audits = dict(raw.tables), {}
    for name, frame in list(raw.tables.items()):
        fixed, log = quality.apply(frame, chosen.get(name, {}), reports[name])
        raw.tables[name] = cast_table(fixed, raw.types[name], name, raw)
        audits[name] = log.model_dump() | {"stopped": log.stopped}
    args.out.mkdir(parents=True, exist_ok=True)
    clean_excel(raw, args.out / "cleaned.xlsx", audits)
    (args.out / "audit.json").write_text(json.dumps(audits, indent=2), encoding="utf-8")
    (args.out / "quality_report.json").write_text(
        json.dumps({n: r.model_dump(mode="json") for n, r in reports.items()}, indent=2),
        encoding="utf-8",
    )
    stopped = [name for name, audit in audits.items() if audit["stopped"]]
    print(f"Wrote {args.out / 'cleaned.xlsx'} and {args.out / 'audit.json'}.")
    if stopped:
        print("Stopped: you chose to fix some issues yourself. Fix the source and run again.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
