from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class Settings(BaseModel):
    algotester_api_key: str
    algotester_subdomain: str
    algotester_contest_id: int
    contest_package_path: Path
    polling_interval: int = 30
    data_dir: Path = Path("./data")
    team_mapping_file: Path = Path("./team_mapping.yaml")
    problem_mapping_file: Path = Path("./problem_mapping.yaml")
    host: str = "0.0.0.0"
    port: int = 8080
    auth_username: str
    auth_password: str


def load_config(config_path: Path = Path("config.yaml")) -> Settings:
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return Settings(**data)


def load_mapping(mapping_path: Path) -> dict[str, str]:
    if not mapping_path.exists():
        return {}
    with open(mapping_path) as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    return {str(k): str(v) for k, v in data.items()}
