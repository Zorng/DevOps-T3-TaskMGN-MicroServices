"""Remove expired workspace cursor and successful-create retry records in batches."""
import argparse
import json

from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.config import ConfigurationError, load_settings
from identity_workspace.database import create_database
from identity_workspace.workspaces.store import WorkspaceStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("limit must be between 1 and 10000")
    database = None
    try:
        database = create_database(load_settings())
        print(json.dumps(WorkspaceStore(database).cleanup(args.limit)))
    except (ConfigurationError, SQLAlchemyError):
        print("Workspace cleanup failed; check configuration and database readiness. Values are omitted.")
        return 1
    finally:
        if database is not None:
            database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
