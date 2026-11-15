from unittest.mock import Mock

import psycopg
import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from identity_workspace.database import create_database


def test_database_creation_is_lazy_and_disposable(monkeypatch, settings):
    connect = Mock(side_effect=AssertionError("Database connection must remain lazy."))
    monkeypatch.setattr(psycopg, "connect", connect)
    database = create_database(settings)
    assert database.engine.url.drivername == "postgresql+psycopg"
    assert database.engine.pool.size() == settings.db_pool_size
    assert database.engine.pool.timeout() == settings.db_pool_timeout_seconds
    connect.assert_not_called()
    database.close()
    connect.assert_not_called()


def test_driver_receives_bounded_timeouts_and_service_identity(monkeypatch, settings):
    connect = Mock(side_effect=psycopg.OperationalError("Synthetic database outage."))
    monkeypatch.setattr(psycopg, "connect", connect)
    database = create_database(settings)
    try:
        with pytest.raises(OperationalError):
            database.engine.connect()
        assert connect.call_args.kwargs["connect_timeout"] == settings.db_connect_timeout_seconds
        assert connect.call_args.kwargs["options"] == f"-c statement_timeout={settings.db_statement_timeout_ms}"
        assert connect.call_args.kwargs["application_name"] == "identity-workspace"
        assert database.engine.hide_parameters is True
        assert database.engine.echo is False
    finally:
        database.close()


def test_transaction_commits_and_closes_without_eager_connection(monkeypatch, settings):
    connect = Mock(side_effect=AssertionError("No SQL is executed in this unit test."))
    monkeypatch.setattr(psycopg, "connect", connect)
    database = create_database(settings)
    committed = Mock()
    try:
        with database.transaction() as session:
            event.listen(session, "after_commit", committed)
            assert session.in_transaction()
        committed.assert_called_once()
        assert not session.in_transaction()
        connect.assert_not_called()
    finally:
        database.close()


def test_transaction_rolls_back_and_propagates_failure(monkeypatch, settings):
    connect = Mock(side_effect=AssertionError("No SQL is executed in this unit test."))
    monkeypatch.setattr(psycopg, "connect", connect)
    database = create_database(settings)
    rolled_back = Mock()
    try:
        with pytest.raises(RuntimeError, match="Synthetic application failure"):
            with database.transaction() as session:
                event.listen(session, "after_rollback", rolled_back)
                raise RuntimeError("Synthetic application failure")
        rolled_back.assert_called_once()
        assert not session.in_transaction()
        connect.assert_not_called()
    finally:
        database.close()


def test_close_disposes_the_pool(settings):
    database = create_database(settings)
    original_pool = database.engine.pool
    database.close()
    assert database.engine.pool is not original_pool
