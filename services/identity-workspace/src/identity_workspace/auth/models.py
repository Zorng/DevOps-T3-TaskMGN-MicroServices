from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from identity_workspace.validation import ContractModel, UUIDValue


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class GoogleExchange(ContractModel):
    challenge_id: UUIDValue
    id_token: str = Field(min_length=1, max_length=16384, repr=False)


class User(ContractModel):
    id: str
    display_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=1, max_length=254)
    created_at: str


class Challenge(ContractModel):
    challenge_id: str
    nonce: str = Field(min_length=32, max_length=128, repr=False)
    expires_at: str


class Session(ContractModel):
    access_token: str = Field(min_length=32, max_length=256, repr=False)
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: str
    user: User


class Me(ContractModel):
    user: User
    owned_workspace_count: int = Field(ge=0, le=10)


def public_user(row) -> User:
    return User(
        id=str(row["id"]), display_name=row["display_name"], email=row["email"],
        created_at=timestamp(row["created_at"]),
    )
