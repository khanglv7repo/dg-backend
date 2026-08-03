from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header

from app.core.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class Actor:
    subject: str
    display_name: str
    roles: frozenset[str]

    def has_any_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


async def actor_from_headers(
    settings: Annotated[Settings, Depends(get_settings)],
    x_actor_id: str | None = Header(default=None),
    x_actor_name: str | None = Header(default=None),
    x_actor_roles: str | None = Header(default=None),
) -> Actor:
    # Header-based identity is for a trusted local/proxy boundary only.
    # Critically, an omitted header must never become governance-admin.
    if not settings.trusted_identity_headers:
        return Actor(
            subject="anonymous",
            display_name="Anonymous",
            roles=frozenset(),
        )

    roles = frozenset(
        part.strip()
        for part in (x_actor_roles or "").split(",")
        if part.strip()
    )
    subject = (x_actor_id or "anonymous").strip() or "anonymous"
    display_name = (x_actor_name or subject).strip() or subject
    return Actor(
        subject=subject,
        display_name=display_name,
        roles=roles,
    )
