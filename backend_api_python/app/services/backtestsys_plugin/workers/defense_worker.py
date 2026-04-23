"""Defense worker — consumes bts:defense:jobs and runs WalkForwardAnalyzer etc.

Run inside the Docker stack via docker-compose.override.yml (defense_worker
service). Can also be run standalone for smoke testing:

    FLASK_APP=app python -m app.services.backtestsys_plugin.workers.defense_worker
"""

from __future__ import annotations

import logging
import os

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("bts.defense_worker")


def main():
    # Flask app context needed for SQLAlchemy session
    from app import create_app  # upstream factory
    from app.extensions import db
    from app.services.backtestsys_plugin.api.common import DEFENSE_QUEUE, run_worker_loop
    from app.services.backtestsys_plugin.api.defense_service import process_defense_job

    app = create_app()
    with app.app_context():
        def handler(job_id: str):
            process_defense_job(job_id, db_session=db.session)

        run_worker_loop(DEFENSE_QUEUE, handler)


if __name__ == "__main__":
    main()
