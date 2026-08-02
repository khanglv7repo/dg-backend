from __future__ import annotations

from pydantic import BaseModel, Field


class VerificationCase(BaseModel):
    identity: str
    sql: str
    expected_allowed: bool


class DesiredPolicy(BaseModel):
    policy_key: str
    name: str
    description: str
    service: str
    resources: dict[str, list[str]]
    users: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    accesses: list[str]
    deny: bool = False
    verification_cases: list[VerificationCase] = Field(default_factory=list)
    source_version: str

    def ranger_document(self) -> dict:
        item_key = "denyPolicyItems" if self.deny else "policyItems"
        return {
            "service": self.service,
            "name": self.name,
            "description": self.description,
            "isEnabled": True,
            "resources": {
                key: {"values": values, "isExcludes": False, "isRecursive": False}
                for key, values in sorted(self.resources.items())
            },
            item_key: [
                {
                    "users": sorted(self.users),
                    "groups": sorted(self.groups),
                    "accesses": [
                        {"type": access, "isAllowed": True} for access in sorted(self.accesses)
                    ],
                    "conditions": [],
                    "delegateAdmin": False,
                }
            ],
        }
