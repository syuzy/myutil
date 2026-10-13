import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

REDACTED = "[REDACTED]"
SECRET_KEY = re.compile(
    r"password|passwd|authorization|cookie|token|api[-_]?key|secret|session|"
    r"credit[-_]?card|card[-_]?(number|no)|(?:^|[-_])(cvv|cvc|pan)(?:$|[-_])",
    re.IGNORECASE,
)
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
ASSIGNMENT = re.compile(
    r"""(?i)((?:password|passwd|authorization|access[_-]?token|refresh[_-]?token|api[_-]?key|secret|session[_-]?id|cvv|cvc)["']?\s*[:=]\s*["']?)([^\s&;,"'<>}]+)"""
)
BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
BASE64 = re.compile(r"(?<![\w/])[A-Za-z0-9+/]{120,}={0,2}(?!\w)")


class Config(BaseModel):
    extra_headers: list[str] = Field(default_factory=list)
    redact_selectors: list[str] = Field(default_factory=list)
    inline_body_bytes: int = Field(default=4096, ge=0, le=65536)
    max_body_bytes: int = Field(default=1_000_000, ge=0, le=10_000_000)


class Redactor:
    def __init__(self, config: Config):
        self.headers = {h.lower() for h in config.extra_headers}

    def text(self, text: str) -> str:
        text = re.sub(
            r"data:[^\s\"'<>]+;base64,[A-Za-z0-9+/=]+", "[BASE64 OMITTED]", text
        )
        text = BASE64.sub("[BASE64 OMITTED]", text)
        text = CARD.sub(REDACTED, text)
        text = JWT.sub(REDACTED, text)
        text = BEARER.sub(REDACTED, text)
        return ASSIGNMENT.sub(lambda m: m[1] + REDACTED, text)

    def url(self, value: str) -> str:
        try:
            parts = urlsplit(value)
            # Credentials in URL userinfo must never reach disk.
            host = parts.netloc.rsplit("@", 1)[-1]
            query = urlencode(
                [
                    (k, REDACTED if self.sensitive(k) else self.text(v))
                    for k, v in parse_qsl(parts.query, keep_blank_values=True)
                ]
            )
            return self.text(
                urlunsplit((parts.scheme, host, parts.path, query, parts.fragment))
            )
        except ValueError:
            return self.text(value)

    def sensitive(self, key: str) -> bool:
        return bool(SECRET_KEY.search(key)) or key.lower() in self.headers

    def clean(self, value):
        if isinstance(value, dict):
            return {
                str(k): REDACTED
                if self.sensitive(str(k))
                else self.url(v)
                if isinstance(v, str)
                and str(k).lower() in {"url", "href", "src", "action", "location"}
                else self.clean(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            return (
                self.url(value)
                if value.startswith(("http://", "https://"))
                else self.text(value)
            )
        return value
