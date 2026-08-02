from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class MatchOutcome(StrEnum):
    EXACT = "EXACT"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"


class TagSuggestion(BaseModel):
    tag: str
    confidence: float = Field(ge=0, le=1)
    rationale: str
    rule_id: str | None = None
    field_path: str | None = None


class ClassificationResult(BaseModel):
    outcome: MatchOutcome
    suggestions: list[TagSuggestion] = Field(default_factory=list)
    rule_version: str
    trusted_auto_apply: bool = False
    evidence: dict = Field(default_factory=dict)
