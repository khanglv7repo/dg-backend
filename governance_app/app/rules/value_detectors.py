from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ConfigurationError
from app.schemas.classification import TagSuggestion


@dataclass(frozen=True, slots=True)
class ValueMatchResult:
    detector_id: str
    tag: str
    confidence: float
    total_samples: int
    matched_samples: int
    match_ratio: float
    rationale: str


class ValueDetector:
    def __init__(self, rule: dict[str, Any]) -> None:
        self.id = str(rule["id"])
        self.detector_type = str(rule.get("detector_type", "regex"))
        self.tag = str(rule["tag"])
        self.min_match_ratio = float(rule.get("min_match_ratio", 0.3))
        self.min_samples = int(rule.get("min_samples", 5))
        self.rationale = str(rule.get("rationale", f"Matched detector {self.id}"))
        pattern_str = rule.get("regex")
        if not pattern_str:
            raise ConfigurationError(f"detector rule {self.id} must specify regex")
        self.pattern = re.compile(pattern_str, re.IGNORECASE)

    def evaluate(self, samples: list[str]) -> ValueMatchResult | None:
        valid_samples = [s.strip() for s in samples if isinstance(s, str) and s.strip()]
        total = len(valid_samples)
        if total < self.min_samples:
            return None

        matched = sum(1 for s in valid_samples if self.pattern.search(s))
        ratio = matched / total if total > 0 else 0.0

        if ratio >= self.min_match_ratio:
            confidence = min(1.0, round(ratio * 0.9 + 0.1, 2))
            return ValueMatchResult(
                detector_id=self.id,
                tag=self.tag,
                confidence=confidence,
                total_samples=total,
                matched_samples=matched,
                match_ratio=round(ratio, 4),
                rationale=f"{self.rationale} ({matched}/{total} samples matched)",
            )
        return None


class EmailValueDetector(ValueDetector):
    pass


class PhoneValueDetector(ValueDetector):
    pass


class NationalIdValueDetector(ValueDetector):
    pass


class PaymentCardValueDetector(ValueDetector):
    pass


DETECTOR_CLASSES = {
    "email": EmailValueDetector,
    "phone": PhoneValueDetector,
    "national_id": NationalIdValueDetector,
    "payment_card": PaymentCardValueDetector,
}


class DataValueDetectorEngine:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        self.configuration_version = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        raw_detectors = document.get("detectors", [])
        if not isinstance(raw_detectors, list):
            raise ConfigurationError("detectors must be a list in data_value_scan.yaml")

        self.detectors: list[ValueDetector] = []
        for raw in raw_detectors:
            dtype = raw.get("detector_type", "regex")
            cls = DETECTOR_CLASSES.get(dtype, ValueDetector)
            self.detectors.append(cls(raw))

    @classmethod
    def from_path(cls, path: Path) -> DataValueDetectorEngine:
        if not path.exists():
            raise ConfigurationError(f"data value scan file not found: {path}")
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(document)

    def scan_column_samples(
        self, field_path: str, samples: list[str]
    ) -> tuple[list[TagSuggestion], dict[str, Any]]:
        suggestions: list[TagSuggestion] = []
        metrics: dict[str, Any] = {"detectors": {}, "total_samples_evaluated": len(samples)}

        for detector in self.detectors:
            res = detector.evaluate(samples)
            if res:
                suggestions.append(
                    TagSuggestion(
                        tag=res.tag,
                        confidence=res.confidence,
                        rationale=res.rationale,
                        rule_id=res.detector_id,
                        field_path=field_path,
                    )
                )
                metrics["detectors"][detector.id] = {
                    "matched": res.matched_samples,
                    "total": res.total_samples,
                    "ratio": res.match_ratio,
                    "tag": res.tag,
                }

        return suggestions, metrics
