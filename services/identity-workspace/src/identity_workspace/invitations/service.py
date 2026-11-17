"""Safe database boundary; no provider/broker lookup inside invitation operations."""
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from identity_workspace.auth.service import token_hash
from identity_workspace.errors import APIError


class InvitationService:
    def __init__(self, store):
        self.store = store

    def _database(self, operation, token, *args, creating=False):
        try:
            return operation(token_hash(token), *args)
        except DBAPIError as error:
            if creating and getattr(error.orig, "sqlstate", None) == "55P03":
                raise APIError(409, code="IDEMPOTENCY_IN_PROGRESS", retry_after=1) from None
            raise APIError(503) from None
        except SQLAlchemyError:
            raise APIError(503) from None

    def create(self, token, workspace_id, email, key):
        return self._database(self.store.create, token, workspace_id, email, key, creating=True)

    def list_owned(self, token, workspace_id, limit=20, cursor=None, status=None):
        return self._database(self.store.list_owned, token, workspace_id, limit, cursor, status)

    def list_received(self, token, limit=20, cursor=None, status=None):
        return self._database(self.store.list_received, token, limit, cursor, status)

    def read(self, token, identifier):
        return self._database(self.store.read, token, identifier)

    def respond(self, token, identifier, action):
        return self._database(self.store.respond, token, identifier, action)

    def cancel(self, token, identifier):
        return self._database(self.store.cancel, token, identifier)
