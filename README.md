# Algotester to CCS API

A bridge that converts Algotester scoreboard data into the ICPC Contest Control System (CCS) Event Feed API format. This allows contest visualization tools like CDS and Spotboard to consume Algotester contest data.

## How It Works

1. Polls the Algotester scoreboard API at regular intervals
2. Converts submissions and judgements into CCS-compatible events
3. Serves data via a FastAPI server implementing the CCS Contest API spec
4. Supports NDJSON streaming for real-time event feeds

## Requirements

- Python 3.10+
- An ICPC contest package with `contest.yaml`, `problems.yaml` and `teams.json`
- Mapping files to translate Algotester IDs to contest package IDs, but there is a script to generate these

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

1. Copy the example config:
   ```bash
   cp config.example.yaml config.yaml
   ```

2. Edit `config.yaml`:
   - `algotester_subdomain`: The subdomain for Algotester
   - `algotester_contest_id`: The contest ID from Algotester URL
   - `contest_package_path`: Path to your ICPC contest package
   - `polling_interval`: How often to fetch scoreboard (seconds)
   - `auth_username`/`auth_password`: Credentials for API access

## Generating Mappings

Use the interactive script to create team and problem mappings:

```bash
python scripts/generate_mappings.py --config config.yaml
```

This reads the contest ID and package path from your config file and creates `team_mapping.yaml` and `problem_mapping.yaml` by letting you match Algotester entities to contest package entities.

## Running

```bash
python main.py --config config.yaml
```

Options:
- `--config`: Path to config file (default: `config.yaml`)
- `--clear-data`: Clear persisted state on startup

The server starts at `http://0.0.0.0:8080` by default.

## API Endpoints

All endpoints require HTTP Basic authentication.

- `GET /` - API info
- `GET /contests` - List contests
- `GET /contests/{id}` - Contest details
- `GET /contests/{id}/problems` - Problems
- `GET /contests/{id}/teams` - Teams
- `GET /contests/{id}/submissions` - Submissions
- `GET /contests/{id}/judgements` - Judgements
- `GET /contests/{id}/event-feed` - Streaming NDJSON event feed

The event feed supports `?since_token=N` for resuming from a specific point.

## Data Persistence

State is persisted to the `data/` directory:
- `submissions.json` - Processed submissions
- `judgements.json` - Judgement results
- `events.json` - Event feed history
- `meta.json` - Token counters and metadata
