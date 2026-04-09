from pydantic import BaseModel, ConfigDict, Field


class OTelSpan(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    trace_id: str = Field(..., min_length=16, max_length=64, pattern=r'^[a-f0-9]+$')
    span_id: str = Field(..., min_length=8, max_length=32, pattern=r'^[a-f0-9]+$')
    name: str = Field(..., max_length=256)
    status_code: int = Field(..., ge=0, le=2)
    error_message: str | None = Field(None, max_length=4096)
    start_time_unix_nano: int = Field(..., gt=0)
    end_time_unix_nano: int = Field(..., gt=0)


class OTelResource(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    service_name: str = Field(..., max_length=256)


class OTelScopeSpans(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    spans: list[OTelSpan] = Field(default_factory=list)


class OTelResourceSpan(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    resource: OTelResource | None = None
    scope_spans: list[OTelScopeSpans] = Field(default_factory=list)


class OTelPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    resource_spans: list[OTelResourceSpan] = Field(default_factory=list)


class TraceResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    status: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    status: str
    db: str


class IncidentFeedback(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    corrected_root_cause: str = Field(..., min_length=10, max_length=2048)
    is_pinned: bool = Field(default=False)
    resolved: bool = Field(default=False)
