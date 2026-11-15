"""Bounded cleanup for expired challenges/sessions and stale rate-limit buckets."""

import argparse
import json

from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.auth.store import AuthStore
from identity_workspace.config import ConfigurationError, load_settings
from identity_workspace.database import create_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("limit must be between 1 and 10000")
    database = None
    try:
        database = create_database(load_settings())
        counts = AuthStore(database).cleanup(args.limit)
        print(json.dumps(counts))
    except (ConfigurationError, SQLAlchemyError):
        print("Authentication cleanup failed; check configuration and database readiness. Values are omitted.")
        return 1
    finally:
        if database is not None:
            database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
