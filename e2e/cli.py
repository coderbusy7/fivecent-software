from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from e2e.config import E2EConfig
from e2e.runner import ScenarioReport, run_scenario
from e2e.scenarios.registry import get_scenario, list_scenarios


def _norm_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def _print_report(report: ScenarioReport, *, verbose: bool) -> None:
    mark = "PASS" if report.ok else "FAIL"
    print(f"\n=== {mark}  {report.scenario_id} — {report.title}")
    print(f"mode={report.mode} phone={report.phone} api={report.api_base}")
    if report.error:
        print(f"error: {report.error}")
    for turn in report.turns:
        tmark = "ok" if turn.ok else "FAIL"
        print(f"\n-- turn {turn.index} [{tmark}] {turn.elapsed_ms}ms status={turn.status}")
        print(f"USER: {turn.user}")
        if turn.reply:
            preview = (
                turn.reply
                if verbose
                else (turn.reply[:280] + ("…" if len(turn.reply) > 280 else ""))
            )
            print(f"ELENA: {preview}")
        elif turn.mode == "webhook":
            print("ELENA: (delivered to WhatsApp guest; not in HTTP body)")
            if turn.raw.get("payload_id"):
                print(f"payload_id={turn.raw.get('payload_id')}")
        for check in turn.checks:
            cmark = "PASS" if check.ok else "FAIL"
            print(f"  [{cmark}] {check.name}: {check.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fivecent-e2e",
        description="Simulate guest chat and verify Elena replies for Fivecent use cases.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List available scenarios")
    list_p.add_argument("--tag", action="append", default=[], help="Filter by tag")

    run_p = sub.add_parser("run", help="Run one or more scenarios")
    run_p.add_argument(
        "scenarios",
        nargs="*",
        help="Scenario ids (default: greeting). Use 'all' for every scenario.",
    )
    run_p.add_argument(
        "--mode",
        choices=("chat", "webhook"),
        default=None,
        help="chat=/chat sync replies (default); webhook=signed Meta ingest",
    )
    run_p.add_argument("--phone", default=None, help="Guest phone digits (default E2E_PHONE)")
    run_p.add_argument("--api-base", default=None, help="API base URL")
    run_p.add_argument("--tag", action="append", default=[], help="Run scenarios with tag")
    run_p.add_argument("--json-out", type=Path, default=None, help="Write JSON report path")
    run_p.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "list":
        rows = list_scenarios()
        if args.tag:
            rows = [s for s in rows if any(t in s.tags for t in args.tag)]
        for scenario in rows:
            tags = ",".join(scenario.tags) if scenario.tags else "-"
            print(f"{scenario.id:16}  [{tags}]  {scenario.title}")
            print(f"{'':16}  {scenario.description}")
        return 0

    base = E2EConfig.from_env(mode=args.mode)
    config = E2EConfig(
        api_base=(args.api_base or base.api_base).rstrip("/"),
        phone=_norm_phone(args.phone or base.phone),
        whatsapp_app_secret=base.whatsapp_app_secret,
        whatsapp_phone_number_id=base.whatsapp_phone_number_id,
        pause_seconds=base.pause_seconds,
        mode=(args.mode or base.mode),
    )

    if args.tag:
        selected_ids = [
            s.id for s in list_scenarios() if any(t in s.tags for t in args.tag)
        ]
    elif not args.scenarios:
        selected_ids = ["greeting"]
    elif args.scenarios == ["all"]:
        selected_ids = [s.id for s in list_scenarios()]
    else:
        selected_ids = list(args.scenarios)

    if not selected_ids:
        print("No scenarios selected.", file=sys.stderr)
        return 2

    reports: list[ScenarioReport] = []
    for scenario_id in selected_ids:
        scenario = get_scenario(scenario_id)
        report = run_scenario(scenario, config)
        reports.append(report)
        _print_report(report, verbose=args.verbose)

    passed = sum(1 for r in reports if r.ok)
    failed = len(reports) - passed
    print(f"\n========== SCORECARD ==========\nPASS {passed}  FAIL {failed}  TOTAL {len(reports)}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps([r.as_dict() for r in reports], indent=2),
            encoding="utf-8",
        )
        print(f"Wrote {args.json_out}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
