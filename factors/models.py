"""Strict versioned research contracts; no executable formulas or trust upgrades.

``quality`` is the existing frozen shared availability result. Its VALID state
means input availability only, never factor efficacy or verified PIT history.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from market_data.quality import QualityResult
from .registry import BUILTIN_CALCULATOR_KEYS

Nonempty = Annotated[str, Field(min_length=1, pattern=r"\S")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Count = Annotated[int, Field(ge=0)]
PositiveCount = Annotated[int, Field(ge=1)]
PitGrade = Literal["FORWARD_OBSERVED", "RECONSTRUCTED"]
QueryMode = Literal["live", "strict_replay", "exploratory"]


def require_utc(value: datetime) -> datetime:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise ValueError("UTC_REQUIRED")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False,
                              revalidate_instances="always")


class FactorRef(StrictModel):
    factor_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")]
    version: Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


class FactorDefinition(StrictModel):
    ref: FactorRef
    name: Nonempty
    purpose: Nonempty
    market: Nonempty
    frequency: Nonempty
    unit: Nonempty
    formula_text: Nonempty
    calculator_key: str | None
    input_fields: list[Nonempty]
    min_history: dict[Nonempty, Count]
    missing_policy: Nonempty
    window: dict[Nonempty, Count]
    lag: dict[Nonempty, Count]
    direction: Nonempty

    @field_validator("calculator_key")
    @classmethod
    def controlled_calculator(cls, value):
        if value is not None and value not in BUILTIN_CALCULATOR_KEYS:
            raise ValueError("UNKNOWN_CALCULATOR")
        return value

    @field_validator("input_fields")
    @classmethod
    def distinct_inputs(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("INVALID_INPUT_FIELDS")
        return value


class FactorObservation(StrictModel):
    ref: FactorRef
    instrument_id: Nonempty
    observed_at: datetime
    source_published_at: datetime
    provider_available_at: datetime
    ingested_at: datetime
    computed_at: datetime
    effective_available_at: datetime
    value: float | None
    missing_reason: Nonempty | None
    pit_grade: PitGrade = "RECONSTRUCTED"
    source_ref: Nonempty
    input_hash: Sha256
    quality: QualityResult
    availability_basis: Nonempty
    dependency_available_ats: list[datetime] = Field(default_factory=list)
    input_refs: list[Nonempty]

    @field_validator("observed_at", "source_published_at", "provider_available_at",
                     "ingested_at", "computed_at", "effective_available_at")
    @classmethod
    def utc_times(cls, value):
        return require_utc(value)

    @field_validator("dependency_available_ats")
    @classmethod
    def utc_dependencies(cls, values):
        return [require_utc(value) for value in values]

    @field_validator("value", mode="before")
    @classmethod
    def finite_numeric_value(cls, value):
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("INVALID_FACTOR_VALUE")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError("INVALID_FACTOR_VALUE")
            # Numerically identical +0/-0 have one canonical content identity.
            if value == 0:
                return 0.0
        return value

    @field_validator("quality")
    @classmethod
    def shared_quality(cls, value):
        if value.state not in {"VALID", "OBSERVATION_ONLY", "UNAVAILABLE"}:
            raise ValueError("INVALID_QUALITY_RESULT")
        if (type(value.observation_ok) is not bool or type(value.confirmation_ok) is not bool
                or not isinstance(value.reason_codes, tuple)
                or any(not isinstance(code, str) or not code.strip() for code in value.reason_codes)):
            raise ValueError("INVALID_QUALITY_RESULT")
        for field in ("evaluated_at_ms", "sample_count"):
            if type(getattr(value, field)) is not int or getattr(value, field) < 0:
                raise ValueError("INVALID_QUALITY_RESULT")
        for field in ("valid_until_ms", "expected_bar_end_ms", "last_bar_end_ms"):
            number = getattr(value, field)
            if number is not None and (type(number) is not int or number < 0):
                raise ValueError("INVALID_QUALITY_RESULT")
        expected_state = ("VALID" if value.confirmation_ok else
                          "OBSERVATION_ONLY" if value.observation_ok else "UNAVAILABLE")
        if (value.state != expected_state or (value.confirmation_ok and (
                not value.observation_ok or value.reason_codes))
                or (value.observation_ok and (value.valid_until_ms is None
                    or value.valid_until_ms <= value.evaluated_at_ms))
                or (not value.observation_ok and value.valid_until_ms is not None)):
            raise ValueError("INVALID_QUALITY_RESULT")
        return value

    @property
    def actual_available_at(self) -> datetime:
        return max(self.source_published_at, self.provider_available_at, self.ingested_at,
                   self.computed_at, *self.dependency_available_ats)

    @model_validator(mode="after")
    def missing_and_time_semantics(self):
        if self.value is None:
            if self.missing_reason is None:
                raise ValueError("MISSING_REASON_REQUIRED")
            if self.quality.confirmation_ok:
                raise ValueError("MISSING_VALUE_CONFIRMATION")
        elif self.missing_reason is not None:
            raise ValueError("VALUE_WITH_MISSING_REASON")
        if not (self.observed_at <= self.source_published_at <= self.provider_available_at
                <= self.ingested_at <= self.computed_at):
            raise ValueError("IMPOSSIBLE_TIME_ORDER")
        historical_dependencies = max(self.observed_at, self.source_published_at,
                                      self.provider_available_at, *self.dependency_available_ats)
        earliest = self.actual_available_at if self.pit_grade == "FORWARD_OBSERVED" else historical_dependencies
        if self.effective_available_at < earliest:
            raise ValueError("AVAILABILITY_BEFORE_DEPENDENCY")
        if not self.input_refs or len(set(self.input_refs)) != len(self.input_refs):
            raise ValueError("INVALID_INPUT_REFS")
        return self


class DatasetManifest(StrictModel):
    dataset_id: Nonempty
    version: PositiveCount
    source_ref: Nonempty
    file_sha256: Sha256
    imported_at: datetime
    universe_version: Nonempty
    adjustment: Nonempty
    availability_basis: Nonempty
    row_count: Count
    provider: Nonempty
    permission_basis: Nonempty
    source_timezone: Nonempty
    corporate_action_basis: Nonempty
    history_version: Nonempty
    coverage_gaps: list[Nonempty]

    @field_validator("imported_at")
    @classmethod
    def utc_import(cls, value):
        return require_utc(value)

    @field_validator("source_timezone")
    @classmethod
    def timezone_exists(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("INVALID_SOURCE_TIMEZONE") from exc
        return value


class CommitResult(StrictModel):
    dataset_id: Nonempty
    version: PositiveCount
    inserted_rows: Count
    replayed: bool
    store_revision: Count
