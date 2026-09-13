# Evidence Contract v1

Pack object: object_id, known_at, source, currency, scale, dps_status?, metric, value, reporting_period ≠ known_at.

Status enum: VERIFIED | PARTIAL | UNKNOWN | FAILED | EXCLUDE | UNAVAILABLE

Missing never invent. DPS=0 requires dps_status ∈ {verified_non_payer, sourced_zero, UNAVAILABLE}.
