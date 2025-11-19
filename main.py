#!/usr/bin/env python3
import argparse
import logging
from pathlib import Path

import uvicorn

from src.config import load_config
from src.api import create_app


def main():
    parser = argparse.ArgumentParser(description="Algotester to CCS Event Feed")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file",
    )
    parser.add_argument(
        "--clear-data",
        action="store_true",
        help="Clear all persisted data on startup",
    )
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    settings = load_config(args.config)

    # Clear data directory if requested
    if args.clear_data:
        import shutil
        if settings.data_dir.exists():
            shutil.rmtree(settings.data_dir)
            logging.info(f"Cleared data directory: {settings.data_dir}")

    app = create_app(settings)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
