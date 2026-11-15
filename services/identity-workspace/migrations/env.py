"""Migration connection configuration; uses service settings, never INI secrets."""

from alembic import context

from identity_workspace.config import load_settings
from identity_workspace.database import create_database
from identity_workspace.persistence import SCHEMA, metadata


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == SCHEMA
    return True


def run_with_connection(connection):
    context.configure(
        connection=connection,
        target_metadata=metadata,
        include_schemas=True,
        include_name=include_name,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    # Integration tests can provide an isolated connection without exporting secrets.
    run_with_connection(context.config.attributes["connection"])
else:
    database = create_database(load_settings())
    try:
        with database.engine.connect() as connection:
            run_with_connection(connection)
    finally:
        database.close()
