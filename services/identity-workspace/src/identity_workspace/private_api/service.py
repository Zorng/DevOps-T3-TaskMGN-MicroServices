"""Validate both credentials/caller permissions before any authoritative read."""
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.auth.service import token_hash
from identity_workspace.errors import APIError
from identity_workspace.private_api.models import AccessContext, SessionIdentity


class PrivateIdentityService:
    def __init__(self, store, keys):
        self.store, self.keys = store, keys

    def _database(self, operation, *args):
        try:
            return operation(*args)
        except SQLAlchemyError:
            raise APIError(503) from None

    def introspect(self, service_keys, token):
        self.keys.authorize(service_keys, "introspect")
        return SessionIdentity.model_validate(self._database(self.store.introspect, token_hash(token))).model_dump()

    def workspace_access(self, service_keys, token, workspace_id):
        self.keys.authorize(service_keys, "workspace_access")
        return AccessContext.model_validate(self._database(self.store.workspace_access, token_hash(token), workspace_id)).model_dump()
