from __future__ import annotations

from typing import Any

import httpx


class AlgotesterFetcher:
    BASE_URL = "https://{subdomain}.algotester.com/en/Contest/ListScoreboardWithAPI"

    def __init__(self, api_key: str, subdomain: str, contest_id: int):
        self.BASE_URL = self.BASE_URL.format(subdomain=subdomain)
        self.contest_id = contest_id
        self._client = httpx.AsyncClient(
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "X-API-Key": api_key,
            },
            timeout=30.0,
        )

    async def fetch_scoreboard(self, show_unofficial: bool = False) -> list[dict[str, Any]]:
        """Fetch all rows from the scoreboard."""
        all_rows = []
        offset = 0
        limit = 100

        while True:
            url = (
                f"{self.BASE_URL}/{self.contest_id}"
                f"?showUnofficial={str(show_unofficial)}&offset={offset}&limit={limit}"
            )
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.json()

            rows = data.get("rows", [])
            all_rows.extend(rows)

            if len(rows) < limit:
                break

            offset += limit

        return all_rows

    async def close(self):
        await self._client.aclose()


def parse_scoreboard_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parse a single scoreboard row into a normalized format."""
    return {
        "team_id": row.get("Id"),
        "team_name": row.get("Contestant", {}).get("Text", "").strip(),
        "rank": row.get("Rank"),
        "score": row.get("Score", 0),
        "penalty_ms": row.get("PenaltyMs", 0),
        "is_unofficial": row.get("IsUnofficial", False),
        "group": row.get("Group", {}).get("Text", ""),
        "results": parse_results(row.get("Results", {})),
    }


def parse_results(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse problem results from a scoreboard row."""
    parsed = {}
    for problem_id, result in results.items():
        parsed[problem_id] = {
            "is_accepted": result.get("IsAccepted", False),
            "attempts": result.get("Attempts", 0),
            "pending_attempts": result.get("PendingAttempts", 0),
            "time_ms": result.get("LastImprovementMs", 0),
            "penalty_ms": result.get("PenaltyMs", 0),
            "is_first_accepted": result.get("IsFirstAccepted", False),
        }
    return parsed
