"""Application boundary for membership operations; no provider or broker HTTP."""

from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.auth.service import token_hash
from identity_workspace.errors import APIError
from identity_workspace.memberships.models import search_text


class MembershipService:
    def __init__(self, store):
        self.store = store

    def _database(self, operation, token, *args):
        try:
            return operation(token_hash(token), *args)
        except SQLAlchemyError:
            raise APIError(503) from None

    def list(self, token, workspace_id, query=None):
        return self._database(self.store.list, token, workspace_id, search_text(query))

    def remove(self, token, workspace_id, user_id):
        return self._database(self.store.remove, token, workspace_id, user_id)

    def leave(self, token, workspace_id):
        return self._database(self.store.leave, token, workspace_id)

    def access_snapshot(self, token, workspace_id):
        return self._database(self.store.access_snapshot, token, workspace_id)
