"""Machine-readable contracts for Quant Detective."""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

EvidenceStatus = Literal["VERIFIED", "PARTIAL", "UNKNOWN", "FAILED", "EXCLUDE", "UNAVAILABLE"]
Domain = Literal["fundamental", "valuation_expectation", "event_assimilation", "risk", "data_model"]
DpsStatus = Literal["verified_non_payer", "sourced_zero", "UNAVAILABLE"]

class MoneyUnits(BaseModel):
    currency: Literal["USD", "CNY", "JPY", "EUR", "yen"]
    scale: Literal["raw", "million", "billion", "B", "亿元", "亿", "T", "trillion"]

class PackObject(BaseModel):
    object_id: str
    known_at: str
    source: str
    currency: Optional[str] = None
    scale: Optional[str] = None
    dps_status: Optional[DpsStatus] = None
    metric: str
    value: Any
    reporting_period: str
    ticker: Optional[str] = None
    reveal_only: bool = False
    status: Optional[EvidenceStatus] = None

    @model_validator(mode="after")
    def ledger_object_invariants(self):
        if self.reporting_period == self.known_at:
            raise ValueError("reporting_period must not equal known_at")
        if (self.currency is None) != (self.scale is None):
            raise ValueError("currency and scale must both be set when either is set")
        moneyish = (
            "price", "close", "open", "high", "low", "eps", "dps", "revenue", "sales",
            "income", "op", "nopat", "capex", "cash", "ev", "market_cap", "pe",
        )
        m = self.metric.lower()
        if any(k in m for k in moneyish) and isinstance(self.value, (int, float)):
            if self.currency is None or self.scale is None:
                raise ValueError(f"money metric {self.metric!r} requires currency+scale")
        if "dps" in m and self.value == 0 and self.dps_status is None:
            raise ValueError("DPS=0 requires dps_status")
        if self.dps_status is not None and "dps" not in m:
            raise ValueError("dps_status only allowed on DPS metrics")
        return self

class EvidencePack(BaseModel):
    pack_id: str
    simulation_date: str
    known_at_rule: str
    objects: list[PackObject]
    era: Optional[str] = None

    @model_validator(mode="after")
    def predict_pack_no_future(self):
        # If era/predict semantics: objects must have known_at <= simulation_date unless reveal_only
        for o in self.objects:
            if o.reveal_only:
                continue
            if o.known_at[:10] > self.simulation_date:
                raise ValueError(f"PIT: {o.object_id} known_at {o.known_at} > simulation_date {self.simulation_date}")
        return self

class ModelContract(BaseModel):
    model_id: str
    version: str
    inputs: Any
    units: dict[str, Any] = Field(default_factory=dict)
    as_of: str
    equations: list[str] | dict[str, Any] | str
    invariants: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    missing_data_policy: str = "UNAVAILABLE"
    failure_conditions: list[str] = Field(default_factory=list)
    validation_tests: list[str] = Field(default_factory=list)
    output_interpretation: str = "stress"

class ForecastItem(BaseModel):
    quantity: str
    domain: Domain
    point: Optional[float] = None
    prob_dist: Optional[dict[str, float]] = None
    units: MoneyUnits | dict[str, str]
    as_of: str
    pack_object_ids: list[str]
    falsifier_ids: list[str] = Field(default_factory=list)
    kernel_prepublish_ok: bool = False

    @field_validator("pack_object_ids")
    @classmethod
    def must_cite(cls, v):
        if not v:
            raise ValueError("uncited forecast: pack_object_ids required")
        return v

class Forecast(BaseModel):
    model_id: str
    version: str
    as_of: str
    pack_id: str
    forecasts: list[ForecastItem]
    assumptions: list[str] = Field(default_factory=list)
    falsifiers: list[dict[str, Any]] = Field(default_factory=list)
    known_at_rule: str
    lock_id: Optional[str] = None

class ExperimentManifest(BaseModel):
    experiment_id: str
    simulation_date: str
    pack_id: str
    model_id: str
    model_version: str
    code_commit: str
    phase: Literal["predict", "locked", "revealed", "scored"]
    truth_seal_path: str  # sealed until reveal
    predict_pack_path: str

class Score(BaseModel):
    lock_id: str
    domain_scores: dict[str, Any]
    error_taxonomy: list[str] = Field(default_factory=list)
    note: Optional[str] = None
