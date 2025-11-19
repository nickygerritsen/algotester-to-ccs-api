from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

import yaml


def parse_duration(duration_val: str | int | float) -> timedelta:
    """Parse duration string like '5:00:00' or seconds into timedelta."""
    # YAML parses unquoted H:MM:SS as sexagesimal (base 60) integer
    if isinstance(duration_val, (int, float)):
        return timedelta(seconds=duration_val)

    parts = duration_val.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = map(int, parts)
        return timedelta(hours=hours, minutes=minutes, seconds=seconds)
    elif len(parts) == 2:
        minutes, seconds = map(int, parts)
        return timedelta(minutes=minutes, seconds=seconds)
    else:
        return timedelta(seconds=int(parts[0]))


def format_reltime(td: timedelta) -> str:
    """Format timedelta as CCS RELTIME (H:MM:SS.sss)."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    millis = int(td.microseconds / 1000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_absolute_time(dt: datetime) -> str:
    """Format datetime as CCS TIME (yyyy-MM-dd'T'HH:mm:ss.SSSXXX)."""
    # Format: 2025-01-01T10:00:00.000+02:00
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    millis = int(dt.microsecond / 1000)

    # Format timezone as +HH:MM or Z
    if dt.tzinfo is None:
        tz_str = ""
    else:
        offset = dt.utcoffset()
        if offset is None or offset.total_seconds() == 0:
            tz_str = "Z"
        else:
            total_seconds = int(offset.total_seconds())
            sign = "+" if total_seconds >= 0 else "-"
            total_seconds = abs(total_seconds)
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            tz_str = f"{sign}{hours:02d}:{minutes:02d}"

    return f"{base}.{millis:03d}{tz_str}"


class ContestPackage:
    def __init__(self, package_path: Path):
        self.package_path = package_path
        self._contest: dict[str, Any] = {}
        self._problems: list[dict[str, Any]] = []
        self._teams: list[dict[str, Any]] = []
        self._load()

    def _load(self):
        contest_file = self.package_path / "contest.yaml"
        problems_file = self.package_path / "problems.yaml"
        teams_file = self.package_path / "teams.json"

        with open(contest_file) as f:
            self._contest = yaml.safe_load(f)

        with open(problems_file) as f:
            self._problems = yaml.safe_load(f)

        if teams_file.exists():
            with open(teams_file) as f:
                self._teams = json.load(f)

    @property
    def contest_id(self) -> str:
        return self._contest.get("id", "unknown")

    def get_contest(self) -> dict[str, Any]:
        """Return contest in CCS API format."""
        start_time = self._contest.get("start_time")
        if isinstance(start_time, str):
            # Parse and reformat to ensure consistent format
            dt = datetime.fromisoformat(start_time)
            start_time_str = format_absolute_time(dt)
        elif isinstance(start_time, datetime):
            start_time_str = format_absolute_time(start_time)
        else:
            start_time_str = None

        duration = parse_duration(self._contest.get("duration", "5:00:00"))

        result = {
            "id": self._contest.get("id"),
            "name": self._contest.get("name", self._contest.get("formal_name", "")),
            "formal_name": self._contest.get("formal_name", self._contest.get("name", "")),
            "start_time": start_time_str,
            "duration": format_reltime(duration),
            "scoreboard_freeze_duration": format_reltime(
                parse_duration(self._contest.get("scoreboard_freeze_duration", "1:00:00"))
            ),
            "penalty_time": self._contest.get("penalty_time", 20),
        }

        return result

    def get_problems(self) -> list[dict[str, Any]]:
        """Return problems in CCS API format."""
        result = []
        for i, prob in enumerate(self._problems):
            result.append({
                "id": prob.get("id"),
                "label": prob.get("label"),
                "name": prob.get("name"),
                "ordinal": i,
                "rgb": prob.get("rgb", "#000000"),
                "color": prob.get("color", "black"),
                "time_limit": prob.get("time_limit", 1.0),
                "test_data_count": prob.get("test_data_count", 1),
            })
        return result

    def get_problem_by_label(self, label: str) -> dict[str, Any] | None:
        """Get problem by label (A, B, C, etc.)."""
        for prob in self.get_problems():
            if prob["label"] == label:
                return prob
        return None

    def get_problem_by_id(self, problem_id: str) -> dict[str, Any] | None:
        """Get problem by ID."""
        for prob in self.get_problems():
            if prob["id"] == problem_id:
                return prob
        return None

    def get_teams(self) -> list[dict[str, Any]]:
        """Return teams in CCS API format."""
        result = []
        for team in self._teams:
            result.append({
                "id": team.get("id"),
                "name": team.get("name"),
                "display_name": team.get("display_name", team.get("name")),
                "group_ids": team.get("group_ids", []),
                "organization_id": team.get("organization_id"),
                "icpc_id": team.get("icpc_id"),
            })
        return result

    def get_team_by_id(self, team_id: str) -> dict[str, Any] | None:
        """Get team by ID."""
        for team in self.get_teams():
            if team["id"] == team_id:
                return team
        return None
