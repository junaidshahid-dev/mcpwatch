"""Scheduling.

Two ways to drive checks, both free:

  * Serverless cron (recommended): a GitHub Actions workflow or cron-job.org calls
    POST /internal/run-due every few minutes with the scheduler token. The app itself stays a
    single stateless web service. See .github/workflows/cron.yml.

  * Local loop: `python -m mcpwatch.scheduler` runs an in-process loop for development.
"""
from __future__ import annotations

import logging
import time

from . import db, service

log = logging.getLogger("mcpwatch.scheduler")


def tick() -> list[dict]:
    """Run all due monitors once. This is exactly what the cron endpoint calls."""
    return service.run_due_checks()


def run_loop(interval_seconds: int = 60) -> None:
    db.init_db()
    log.info("scheduler loop started (every %ss)", interval_seconds)
    while True:
        try:
            results = tick()
            if results:
                log.info("ran %d due monitor(s)", len(results))
        except Exception as e:  # a loop must not die on one bad run
            log.error("tick failed: %s", e)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_loop()
