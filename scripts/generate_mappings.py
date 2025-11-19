#!/usr/bin/env python3
"""
Interactive script to generate team and problem mapping files by fetching
data from Algotester and mapping to contest package entities.

Run with: python scripts/generate_mappings.py --config config.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import httpx
import questionary
import yaml
from pathlib import Path

# Add parent directory to path to import src modules
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import load_config


def fetch_problem_ids_from_html(contest_id: int) -> list[str]:
    """Fetch problem IDs from the HTML scoreboard page."""
    url = f"https://icpc.algotester.com/en/Contest/ViewScoreboard/{contest_id}?showUnofficial=False"
    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()

    # Parse problem IDs from JavaScript formatter functions
    # Pattern: var formatter10197 = function(value, row, index)
    pattern = r'var formatter(\d+)\s*='
    problem_ids = re.findall(pattern, response.text)

    # Remove duplicates while preserving order
    seen = set()
    unique_ids = []
    for pid in problem_ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)

    return unique_ids


def fetch_scoreboard(contest_id: int) -> dict:
    """Fetch all scoreboard data from Algotester."""
    all_rows = []
    offset = 0
    limit = 100

    while True:
        url = (
            f"https://icpc.algotester.com/en/Contest/ListScoreboard/{contest_id}"
            f"?showUnofficial=False&offset={offset}&limit={limit}"
        )
        response = httpx.get(
            url,
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        rows = data.get("rows", [])
        all_rows.extend(rows)

        if len(rows) < limit:
            break

        offset += limit

    return {"rows": all_rows, "total": len(all_rows)}


def load_contest_package(package_path: Path) -> tuple[list[dict], list[dict]]:
    """Load problems and teams from contest package."""
    problems_file = package_path / "problems.yaml"
    teams_file = package_path / "teams.json"

    with open(problems_file) as f:
        problems = yaml.safe_load(f)

    teams = []
    if teams_file.exists():
        with open(teams_file) as f:
            teams = json.load(f)

    return problems, teams


def main():
    parser = argparse.ArgumentParser(description="Generate mapping files from Algotester")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file",
    )
    args = parser.parse_args()

    settings = load_config(args.config)
    contest_id = settings.algotester_contest_id
    contest_package_path = settings.contest_package_path

    print(f"Fetching problem list from HTML for contest {contest_id}...")
    algotester_problem_ids = fetch_problem_ids_from_html(contest_id)

    print(f"Fetching scoreboard for contest {contest_id}...")
    data = fetch_scoreboard(contest_id)

    rows = data.get("rows", [])
    if not rows:
        print("No data found!")
        return

    print(f"Loading contest package from {contest_package_path}...")
    problems, teams = load_contest_package(contest_package_path)

    print(f"\nFound {len(algotester_problem_ids)} problems in Algotester")
    print(f"Found {len(problems)} problems in contest package")
    print(f"Found {len(teams)} teams in contest package")

    # Build problem choices
    problem_choices = [
        questionary.Choice(
            title=f"{p['label']}: {p['name']} ({p['id']})",
            value=p["id"]
        )
        for p in problems
    ]
    problem_choices.append(questionary.Choice(title="Skip (no mapping)", value=None))

    # Map problems interactively
    print("\n" + "=" * 60)
    print("PROBLEM MAPPING")
    print("=" * 60)

    problem_mapping = {}
    for i, algo_id in enumerate(algotester_problem_ids):
        # Default to i-th problem if available
        default_id = problems[i]["id"] if i < len(problems) else None

        try:
            chosen = questionary.select(
                f"Algotester problem {algo_id} -> ",
                choices=problem_choices,
                default=default_id,
            ).unsafe_ask()
        except KeyboardInterrupt:
            print("\nAborted.")
            return

        if chosen is None:
            print(f"  {algo_id} -> Skipped")
        else:
            problem_mapping[algo_id] = chosen
            label = next((p["label"] for p in problems if p["id"] == chosen), "?")
            print(f"  {algo_id} -> {label} ({chosen})")

    # Build team choices and sort both lists for default mapping
    if teams:
        # Sort teams by ID
        sorted_teams = sorted(teams, key=lambda t: t["id"])
        team_choices = [
            f"{t['id']}: {t.get('display_name', t.get('name', 'Unknown'))}"
            for t in sorted_teams
        ]
        team_id_list = [t["id"] for t in sorted_teams]
    else:
        team_choices = None
        team_id_list = []

    # Sort algotester teams by ID for default mapping
    sorted_rows = sorted(rows, key=lambda r: r.get("Id", ""))

    # Map teams interactively
    print("\n" + "=" * 60)
    print("TEAM MAPPING")
    print("=" * 60)

    team_mapping = {}
    for i, row in enumerate(sorted_rows):
        algo_id = row.get("Id")
        team_name = row.get("Contestant", {}).get("Text", "").strip()

        # Default to i-th team if available
        default_team = team_id_list[i] if i < len(team_id_list) else None

        try:
            if team_choices:
                # Use autocomplete with default
                chosen = questionary.autocomplete(
                    f"{algo_id} ({team_name}) -> ",
                    choices=team_choices,
                    default=f"{default_team}: " if default_team else "",
                ).unsafe_ask()

                if chosen:
                    # Extract team ID from the choice
                    team_id = chosen.split(":")[0].strip()
                    team_mapping[algo_id] = team_id
                    print(f"  {algo_id} -> {team_id}")
                else:
                    print(f"  {algo_id} -> Skipped")
            else:
                # No teams in package, ask for manual input
                team_id = questionary.text(
                    f"{algo_id} ({team_name}) -> ",
                    default=algo_id,
                ).unsafe_ask()

                if team_id and team_id.lower() != "skip":
                    team_mapping[algo_id] = team_id
        except KeyboardInterrupt:
            print("\nAborted.")
            return

    # Write problem mapping to the paths specified in config
    with open(settings.problem_mapping_file, "w") as f:
        f.write("# Problem mapping: Algotester problem ID -> CCS problem ID\n\n")
        yaml.dump(problem_mapping, f, default_flow_style=False)
    print(f"\nWrote problem mapping to {settings.problem_mapping_file}")

    # Write team mapping
    with open(settings.team_mapping_file, "w") as f:
        f.write("# Team mapping: Algotester team ID -> CCS team ID\n\n")
        yaml.dump(team_mapping, f, default_flow_style=False)
    print(f"Wrote team mapping to {settings.team_mapping_file}")


if __name__ == "__main__":
    main()
