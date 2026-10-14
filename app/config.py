from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_allowlist: list[str] = Field(default_factory=list)
    api_methods: list[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    max_body_bytes: int = Field(default=16_384, ge=0, le=1_000_000)
    capture_headers: bool = False
    max_pending_apis: int = Field(default=8, ge=1, le=64)
    api_timeout_ms: int = Field(default=3000, ge=100, le=30_000)
    root_selector: str = "body"
    ignore_selectors: list[str] = Field(default_factory=list)
    redact_selectors: list[str] = Field(default_factory=list)
    extra_headers: list[str] = Field(default_factory=list)
    debounce_ms: int = Field(default=400, ge=50, le=10_000)
    min_snapshot_interval_ms: int = Field(default=1500, ge=100, le=30_000)
    max_wait_ms: int = Field(default=3000, ge=100, le=60_000)
    max_html_chars: int = Field(default=80_000, ge=500, le=500_000)
    max_dom_nodes: int = Field(default=3000, ge=20, le=20_000)
    dom_budget_ms: int = Field(default=12, ge=2, le=100)
    watched_attributes: list[str] = Field(
        default_factory=lambda: [
            "hidden",
            "open",
            "disabled",
            "checked",
            "selected",
            "value",
            "href",
            "aria-hidden",
            "aria-expanded",
            "aria-checked",
            "aria-selected",
            "aria-disabled",
        ]
    )
    capture_frames: bool = False
    record_actions: bool = True
    record_errors: bool = False
    flush_interval_ms: int = Field(default=1000, ge=100, le=30_000)
    max_pending_events: int = Field(default=100, ge=10, le=1000)
    device: str | None = None
    viewport_width: int = Field(default=1280, ge=320, le=3840)
    viewport_height: int = Field(default=800, ge=320, le=3840)
    navigation_timeout_ms: int = Field(default=30_000, ge=1000, le=120_000)

    @field_validator("api_allowlist")
    @classmethod
    def absolute_patterns(cls, patterns):
        if any(
            not p.startswith(("https://", "http://")) or len(p.split("/")) < 4
            for p in patterns
        ):
            raise ValueError(
                "API patterns must be absolute HTTP(S) URL globs, including a path"
            )
        return patterns

    @field_validator("api_methods")
    @classmethod
    def normalize_methods(cls, methods):
        if any(not m.isalpha() for m in methods):
            raise ValueError("API methods must be HTTP method names")
        return [m.upper() for m in methods]

    @model_validator(mode="after")
    def validate_intervals(self):
        if self.max_wait_ms < max(self.debounce_ms, self.min_snapshot_interval_ms):
            raise ValueError(
                "max_wait_ms must be at least debounce_ms and min_snapshot_interval_ms"
            )
        return self
