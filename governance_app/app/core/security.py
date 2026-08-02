from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header


@dataclass(frozen=True, slots=True)
class Actor:
    subject: str
    display_name: str
    roles: frozenset[str]

    def has_any_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


async def actor_from_headers(
    x_actor_id: str = Header(default="local-user"),
    x_actor_name: str = Header(default="Local User"),
    x_actor_roles: str = Header(default="governance-admin"),
) -> Actor:
    roles = frozenset(part.strip() for part in x_actor_roles.split(",") if part.strip())
    return Actor(subject=x_actor_id.strip(), display_name=x_actor_name.strip(), roles=roles)
