from __future__ import annotations

import json
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

from .contest_package import format_reltime, format_absolute_time


class StateManager:
    def __init__(
        self,
        data_dir: Path,
        team_mapping: dict[str, str],
        problem_mapping: dict[str, str],
        contest_start_time: datetime,
    ):
        self.data_dir = data_dir
        self.team_mapping = team_mapping
        self.problem_mapping = problem_mapping
        self.contest_start_time = contest_start_time

        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._submissions: dict[str, dict[str, Any]] = {}
        self._judgements: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._previous_state: dict[str, dict[str, dict[str, Any]]] = {}
        self._next_submission_id = 1
        self._next_judgement_id = 1
        self._next_token = 1
        self._lock = asyncio.Lock()

        self._load()

    def _load(self):
        """Load persisted state from disk."""
        submissions_file = self.data_dir / "submissions.json"
        judgements_file = self.data_dir / "judgements.json"
        events_file = self.data_dir / "events.json"
        state_file = self.data_dir / "previous_state.json"
        meta_file = self.data_dir / "meta.json"

        if submissions_file.exists():
            with open(submissions_file) as f:
                self._submissions = json.load(f)

        if judgements_file.exists():
            with open(judgements_file) as f:
                self._judgements = json.load(f)

        if events_file.exists():
            with open(events_file) as f:
                self._events = json.load(f)

        if state_file.exists():
            with open(state_file) as f:
                self._previous_state = json.load(f)

        if meta_file.exists():
            with open(meta_file) as f:
                meta = json.load(f)
                self._next_submission_id = meta.get("next_submission_id", 1)
                self._next_judgement_id = meta.get("next_judgement_id", 1)
                self._next_token = meta.get("next_token", 1)

    def save(self):
        """Persist state to disk."""
        with open(self.data_dir / "submissions.json", "w") as f:
            json.dump(self._submissions, f, indent=2)

        with open(self.data_dir / "judgements.json", "w") as f:
            json.dump(self._judgements, f, indent=2)

        with open(self.data_dir / "events.json", "w") as f:
            json.dump(self._events, f, indent=2)

        with open(self.data_dir / "previous_state.json", "w") as f:
            json.dump(self._previous_state, f, indent=2)

        with open(self.data_dir / "meta.json", "w") as f:
            json.dump({
                "next_submission_id": self._next_submission_id,
                "next_judgement_id": self._next_judgement_id,
                "next_token": self._next_token,
            }, f, indent=2)

    def initialize_static_events(
        self,
        contest: dict[str, Any],
        judgement_types: list[dict[str, Any]],
        languages: list[dict[str, Any]],
        problems: list[dict[str, Any]],
        teams: list[dict[str, Any]],
    ):
        """Initialize events for static data (contest, judgement types, languages, problems, teams).

        Only adds these if no events exist yet.
        """
        if self._events:
            return

        # Contest
        self._add_event("contests", contest["id"], contest)

        # Judgement types
        for jt in judgement_types:
            self._add_event("judgement-types", jt["id"], jt)

        # Languages
        for lang in languages:
            self._add_event("languages", lang["id"], lang)

        # Problems
        for prob in problems:
            self._add_event("problems", prob["id"], prob)

        # Teams
        for team in teams:
            self._add_event("teams", team["id"], team)

        self.save()

    def add_team_event(self, team: dict[str, Any]):
        """Add a team event."""
        self._add_event("teams", team["id"], team)

    def _create_submission(
        self,
        team_id: str,
        problem_id: str,
        contest_time_ms: float,
    ) -> dict[str, Any]:
        """Create a new submission."""
        sub_id = f"algotester-{self._next_submission_id}"
        self._next_submission_id += 1

        contest_time = timedelta(milliseconds=contest_time_ms)
        absolute_time = self.contest_start_time + contest_time

        submission = {
            "id": sub_id,
            "team_id": team_id,
            "problem_id": problem_id,
            "language_id": "cpp",  # placeholder
            "time": format_absolute_time(absolute_time),
            "contest_time": format_reltime(contest_time),
        }

        self._submissions[sub_id] = submission
        self._add_event("submissions", sub_id, submission)

        return submission

    def _create_judgement(
        self,
        submission_id: str,
        judgement_type_id: str,
        contest_time_ms: float,
    ) -> dict[str, Any]:
        """Create a new judgement for a submission."""
        judg_id = f"algotester-{self._next_judgement_id}"
        self._next_judgement_id += 1

        contest_time = timedelta(milliseconds=contest_time_ms)
        absolute_time = self.contest_start_time + contest_time

        judgement = {
            "id": judg_id,
            "submission_id": submission_id,
            "judgement_type_id": judgement_type_id,
            "start_time": format_absolute_time(absolute_time),
            "start_contest_time": format_reltime(contest_time),
            "end_time": format_absolute_time(absolute_time),
            "end_contest_time": format_reltime(contest_time),
        }

        self._judgements[judg_id] = judgement
        self._add_event("judgements", judg_id, judgement)

        return judgement

    def _add_event(self, event_type: str, obj_id: str, data: dict[str, Any]):
        """Add an event to the event log."""
        token = str(self._next_token)
        self._next_token += 1

        event = {
            "token": token,
            "id": obj_id,
            "type": event_type,
            "op": "create",
            "data": data,
        }
        self._events.append(event)

    async def process_scoreboard(self, rows: list[dict[str, Any]]):
        """Process scoreboard data and generate submissions/judgements."""
        async with self._lock:
            new_events_start = len(self._events)

            for row in rows:
                algotester_team_id = row["team_id"]
                ccs_team_id = self.team_mapping.get(algotester_team_id)

                if not ccs_team_id:
                    continue

                for algotester_prob_id, result in row["results"].items():
                    problem_id = self.problem_mapping.get(algotester_prob_id)
                    if not problem_id:
                        continue

                    self._process_team_problem(
                        ccs_team_id,
                        problem_id,
                        result,
                    )

                # Update previous state
                if ccs_team_id not in self._previous_state:
                    self._previous_state[ccs_team_id] = {}

                for algotester_prob_id, result in row["results"].items():
                    problem_id = self.problem_mapping.get(algotester_prob_id)
                    if problem_id:
                        self._previous_state[ccs_team_id][problem_id] = result

            self.save()

            return self._events[new_events_start:]

    def _process_team_problem(
        self,
        team_id: str,
        problem_id: str,
        result: dict[str, Any],
    ):
        """Process a single team/problem result and generate submissions/judgements."""
        prev = self._previous_state.get(team_id, {}).get(problem_id, {
            "is_accepted": False,
            "attempts": 0,
            "pending_attempts": 0,
            "time_ms": 0,
        })

        curr_attempts = result["attempts"]
        curr_pending = result["pending_attempts"]
        curr_accepted = result["is_accepted"]
        curr_time_ms = result["time_ms"]

        prev_attempts = prev.get("attempts", 0)
        prev_pending = prev.get("pending_attempts", 0)
        prev_accepted = prev.get("is_accepted", False)

        # Calculate what we need to generate
        # attempts = WA count only, is_accepted adds 1 more
        # Total submissions with judgements in current state
        curr_judged = curr_attempts + (1 if curr_accepted else 0)
        prev_judged = prev_attempts + (1 if prev_accepted else 0)

        # First time seeing this team/problem - generate historical data
        if team_id not in self._previous_state or problem_id not in self._previous_state.get(team_id, {}):
            self._generate_initial_submissions(
                team_id,
                problem_id,
                curr_attempts,
                curr_pending,
                curr_accepted,
                curr_time_ms,
            )
            return

        # Handle new pending attempts (submissions without judgements)
        # - If pending increased: new submissions created
        # - If pending decreased and attempts increased: pending got judged as WA
        # - If pending decreased and is_accepted became true: pending got judged as AC

        # New submissions (pending increased)
        if curr_pending > prev_pending:
            for _ in range(curr_pending - prev_pending):
                # Create submission without judgement
                # Time is unknown, use current time estimate
                self._create_submission(team_id, problem_id, curr_time_ms)

        # Pending submissions got judged
        pending_resolved = prev_pending - curr_pending
        new_judged = curr_judged - prev_judged

        if pending_resolved > 0 and new_judged > 0:
            # Some pending submissions got judgements
            # Find submissions without judgements for this team/problem
            pending_subs = self._get_pending_submissions(team_id, problem_id)

            # Determine how many WA vs AC
            if curr_accepted and not prev_accepted:
                # One of them is AC (the last one)
                wa_count = new_judged - 1
            else:
                wa_count = new_judged

            for i, sub in enumerate(pending_subs[:new_judged]):
                if i < wa_count:
                    self._create_judgement(sub["id"], "WA", curr_time_ms)
                else:
                    self._create_judgement(sub["id"], "AC", curr_time_ms)

        # New judged submissions that weren't pending (direct submissions)
        direct_new = new_judged - pending_resolved
        if direct_new > 0:
            if curr_accepted and not prev_accepted:
                # Create WA submissions then AC
                wa_count = direct_new - 1
                for _ in range(wa_count):
                    sub = self._create_submission(team_id, problem_id, curr_time_ms)
                    self._create_judgement(sub["id"], "WA", curr_time_ms)
                sub = self._create_submission(team_id, problem_id, curr_time_ms)
                self._create_judgement(sub["id"], "AC", curr_time_ms)
            else:
                # All WA
                for _ in range(direct_new):
                    sub = self._create_submission(team_id, problem_id, curr_time_ms)
                    self._create_judgement(sub["id"], "WA", curr_time_ms)

    def _generate_initial_submissions(
        self,
        team_id: str,
        problem_id: str,
        attempts: int,
        pending: int,
        is_accepted: bool,
        time_ms: float,
    ):
        """Generate initial submissions for first-time data."""
        if attempts == 0 and pending == 0 and not is_accepted:
            return

        # attempts = WA count only
        wa_count = attempts

        # Space out submissions before the final time
        total_judged = attempts + (1 if is_accepted else 0)
        time_step = time_ms / (total_judged + 1) if total_judged > 0 else time_ms

        # Generate WA submissions
        for i in range(wa_count):
            sub_time = time_step * (i + 1)
            sub = self._create_submission(team_id, problem_id, sub_time)
            self._create_judgement(sub["id"], "WA", sub_time)

        # Generate AC submission
        if is_accepted:
            sub = self._create_submission(team_id, problem_id, time_ms)
            self._create_judgement(sub["id"], "AC", time_ms)

        # Generate pending submissions
        for _ in range(pending):
            self._create_submission(team_id, problem_id, time_ms)

    def _get_pending_submissions(self, team_id: str, problem_id: str) -> list[dict[str, Any]]:
        """Get submissions without judgements for a team/problem."""
        judged_sub_ids = {j["submission_id"] for j in self._judgements.values()}
        pending = []
        for sub in self._submissions.values():
            if (
                sub["team_id"] == team_id
                and sub["problem_id"] == problem_id
                and sub["id"] not in judged_sub_ids
            ):
                pending.append(sub)
        return sorted(pending, key=lambda x: int(x["id"]))

    def get_submissions(self) -> list[dict[str, Any]]:
        return list(self._submissions.values())

    def get_submission(self, sub_id: str) -> dict[str, Any] | None:
        return self._submissions.get(sub_id)

    def get_judgements(self) -> list[dict[str, Any]]:
        return list(self._judgements.values())

    def get_judgement(self, judg_id: str) -> dict[str, Any] | None:
        return self._judgements.get(judg_id)

    def get_events_since_token(self, since_token: str | None = None) -> list[dict[str, Any]]:
        """Get events since a given token.

        Raises:
            ValueError: If the token is invalid (not a number or out of range).
        """
        if since_token is None:
            return self._events.copy()

        try:
            token_int = int(since_token)
        except ValueError:
            raise ValueError(f"Invalid token format: {since_token}")

        # Check if token is in valid range (0 to last token)
        if token_int < 0:
            raise ValueError(f"Invalid token: {since_token}")

        max_token = self._next_token - 1
        if token_int > max_token:
            raise ValueError(f"Unknown token: {since_token}")

        return [e for e in self._events if int(e["token"]) > token_int]

    def get_all_events(self) -> list[dict[str, Any]]:
        return self._events.copy()

    def get_last_token(self) -> str | None:
        """Get the last event token, or None if no events."""
        if self._events:
            return self._events[-1]["token"]
        return None
