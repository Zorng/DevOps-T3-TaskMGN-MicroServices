"""Hash bearer tokens and translate database failures to safe contract errors."""

from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from identity_workspace.auth.service import token_hash
from identity_workspace.errors import APIError


class WorkspaceService:
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

    def list(self, token, limit=20, cursor=None):
        return self._database(self.store.list, token, limit, cursor)

    def create(self, token, name, key):
        return self._database(self.store.create, token, name, key, creating=True)

    def read(self, token, identifier):
        return self._database(self.store.read, token, identifier)

    def rename(self, token, identifier, name, matches):
        return self._database(self.store.rename, token, identifier, name, matches)

    def delete(self, token, identifier, matches):
        return self._database(self.store.delete, token, identifier, matches)
