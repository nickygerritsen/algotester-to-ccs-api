from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import secrets

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

logger = logging.getLogger(__name__)

from .config import Settings, load_mapping
from .contest_package import ContestPackage
from .state_manager import StateManager
from .algotester import AlgotesterFetcher, parse_scoreboard_row


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Algotester to CCS Event Feed")

    # Setup authentication
    security = HTTPBasic()

    def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
        """Verify HTTP Basic credentials."""
        correct_username = secrets.compare_digest(
            credentials.username.encode("utf8"),
            settings.auth_username.encode("utf8")
        )
        correct_password = secrets.compare_digest(
            credentials.password.encode("utf8"),
            settings.auth_password.encode("utf8")
        )

        if not (correct_username and correct_password):
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        return True

    # Load contest package
    contest_package = ContestPackage(settings.contest_package_path)

    # Load mappings
    team_mapping = load_mapping(settings.team_mapping_file)
    problem_mapping = load_mapping(settings.problem_mapping_file)

    # Parse contest start time
    contest_data = contest_package.get_contest()
    from datetime import datetime
    start_time_str = contest_data.get("start_time")
    if start_time_str:
        contest_start_time = datetime.fromisoformat(start_time_str)
    else:
        contest_start_time = datetime.now()

    # Initialize state manager
    state_manager = StateManager(
        data_dir=settings.data_dir,
        team_mapping=team_mapping,
        problem_mapping=problem_mapping,
        contest_start_time=contest_start_time,
    )

    # Initialize fetcher
    fetcher = AlgotesterFetcher(settings.algotester_api_key, settings.algotester_subdomain, settings.algotester_contest_id)

    # Store in app state
    app.state.contest_package = contest_package
    app.state.state_manager = state_manager
    app.state.fetcher = fetcher
    app.state.settings = settings
    app.state.team_mapping = team_mapping
    app.state.problem_mapping = problem_mapping
    app.state.polling_task = None
    app.state.new_events = None  # Created in startup

    # Judgement types data
    def get_judgement_types_data():
        return [
            {"id": "AC", "name": "Accepted", "penalty": False, "solved": True},
            {"id": "WA", "name": "Wrong Answer", "penalty": True, "solved": False},
            {"id": "TLE", "name": "Time Limit Exceeded", "penalty": True, "solved": False},
            {"id": "RTE", "name": "Run-Time Error", "penalty": True, "solved": False},
            {"id": "CE", "name": "Compile Error", "penalty": False, "solved": False},
        ]

    # Languages data
    def get_languages_data():
        return [
            {"id": "c", "name": "C"},
            {"id": "cpp", "name": "C++"},
            {"id": "java", "name": "Java"},
            {"id": "kotlin", "name": "Kotlin"},
            {"id": "python3", "name": "Python 3"},
        ]

    # Initialize static events (including teams)
    state_manager.initialize_static_events(
        contest=contest_data,
        judgement_types=get_judgement_types_data(),
        languages=get_languages_data(),
        problems=contest_package.get_problems(),
        teams=contest_package.get_teams(),
    )

    @app.on_event("startup")
    async def startup():
        # Create event in the correct event loop
        app.state.new_events = asyncio.Event()
        # Start background polling
        app.state.polling_task = asyncio.create_task(poll_scoreboard())

    @app.on_event("shutdown")
    async def shutdown():
        if app.state.polling_task:
            app.state.polling_task.cancel()
        await fetcher.close()

    async def poll_scoreboard():
        """Background task to poll Algotester scoreboard."""
        while True:
            try:
                rows = await fetcher.fetch_scoreboard()
                parsed_rows = [parse_scoreboard_row(row) for row in rows]

                # Process and generate events
                new_events = await state_manager.process_scoreboard(parsed_rows)
                if new_events:
                    app.state.new_events.set()
                    for event in new_events:
                        event_type = event["type"]
                        event_id = event["id"]
                        if event_type == "submissions":
                            sub = event["data"]
                            logger.info(f"New submission: {event_id} (team={sub['team_id']}, problem={sub['problem_id']})")
                        elif event_type == "judgements":
                            judg = event["data"]
                            logger.info(f"New judgement: {event_id} (submission={judg['submission_id']}, result={judg['judgement_type_id']})")

                # Save after team events
                state_manager.save()

            except Exception as e:
                logger.error(f"Error polling scoreboard: {e}")

            await asyncio.sleep(settings.polling_interval)

    # API information endpoint
    @app.get("/")
    async def api_info(_: bool = Depends(verify_credentials)):
        return {
            "version": "draft",
            "version_url": "https://ccs-specs.icpc.io/draft/contest_api",
            "provider": {
                "name": "Algotester to CCS Event Feed",
            }
        }

    # Contest endpoints
    @app.get("/contests")
    async def get_contests(_: bool = Depends(verify_credentials)):
        return [contest_package.get_contest()]

    @app.get("/contests/{contest_id}")
    async def get_contest(contest_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        return contest

    # Judgement types endpoint
    @app.get("/contests/{contest_id}/judgement-types")
    async def get_judgement_types(contest_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        return get_judgement_types_data()

    # Languages endpoint
    @app.get("/contests/{contest_id}/languages")
    async def get_languages(contest_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        return get_languages_data()

    # Problems endpoints
    @app.get("/contests/{contest_id}/problems")
    async def get_problems(contest_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        return contest_package.get_problems()

    @app.get("/contests/{contest_id}/problems/{problem_id}")
    async def get_problem(contest_id: str, problem_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        problem = contest_package.get_problem_by_id(problem_id)
        if not problem:
            raise HTTPException(status_code=404, detail="Problem not found")
        return problem

    # Teams endpoints
    @app.get("/contests/{contest_id}/teams")
    async def get_teams(contest_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        return contest_package.get_teams()

    @app.get("/contests/{contest_id}/teams/{team_id}")
    async def get_team(contest_id: str, team_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        team = contest_package.get_team_by_id(team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        return team

    # Submissions endpoints
    @app.get("/contests/{contest_id}/submissions")
    async def get_submissions(contest_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        return state_manager.get_submissions()

    @app.get("/contests/{contest_id}/submissions/{submission_id}")
    async def get_submission(contest_id: str, submission_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        submission = state_manager.get_submission(submission_id)
        if not submission:
            raise HTTPException(status_code=404, detail="Submission not found")
        return submission

    # Judgements endpoints
    @app.get("/contests/{contest_id}/judgements")
    async def get_judgements(contest_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        return state_manager.get_judgements()

    @app.get("/contests/{contest_id}/judgements/{judgement_id}")
    async def get_judgement(contest_id: str, judgement_id: str, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")
        judgement = state_manager.get_judgement(judgement_id)
        if not judgement:
            raise HTTPException(status_code=404, detail="Judgement not found")
        return judgement

    # Event feed endpoint (NDJSON streaming)
    @app.get("/contests/{contest_id}/event-feed")
    async def event_feed(contest_id: str, request: Request, since_token: Optional[str] = None, _: bool = Depends(verify_credentials)):
        contest = contest_package.get_contest()
        if contest["id"] != contest_id:
            raise HTTPException(status_code=404, detail="Contest not found")

        # Validate token before starting stream
        if since_token is not None:
            try:
                token_int = int(since_token)
                if token_int < 0:
                    raise HTTPException(status_code=400, detail=f"Invalid token: {since_token}")
                max_token = state_manager._next_token - 1
                if token_int > max_token:
                    raise HTTPException(status_code=400, detail=f"Unknown token: {since_token}")
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid token format: {since_token}")

        client_ip = request.client.host if request.client else "unknown"

        async def generate():
            logger.info(f"Event feed client connected: {client_ip} (since_token={since_token})")
            try:
                # Get events since token (or all events if no token)
                events = state_manager.get_events_since_token(since_token)

                # Send all events
                for event in events:
                    yield json.dumps(event) + "\n"

                # Track last token for streaming new events
                last_token = state_manager.get_last_token()

                # Keep connection alive and stream new events
                import time
                last_send_time = time.time()

                while True:
                    # Wait for new events or timeout
                    try:
                        await asyncio.wait_for(
                            app.state.new_events.wait(),
                            timeout=30.0
                        )
                        app.state.new_events.clear()
                    except asyncio.TimeoutError:
                        pass

                    # Get new events since last token
                    new_events = state_manager.get_events_since_token(last_token)
                    if new_events:
                        for event in new_events:
                            yield json.dumps(event) + "\n"
                            last_token = event["token"]
                        last_send_time = time.time()
                    elif time.time() - last_send_time >= 120:
                        # Send keepalive newline per CCS spec
                        yield "\n"
                        last_send_time = time.time()
            finally:
                logger.info(f"Event feed client disconnected: {client_ip}")

        return StreamingResponse(
            generate(),
            media_type="application/x-ndjson",
        )

    return app
