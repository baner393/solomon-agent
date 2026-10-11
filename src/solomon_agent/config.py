import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    data_dir: Path
    sources: dict[str, Path] = field(default_factory=dict)
    max_output_tokens: int = 512
    history_char_budget: int = 6000
    model: str = ""
    base_url: str | None = None
    api_key: str = field(default="", repr=False)
    auth_mode: str = "api_key"
    reasoning_effort: str | None = None

    def __post_init__(self):
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ValueError("Unsupported reasoning_effort")
        if self.auth_mode not in {"api_key", "none"}:
            raise ValueError("auth_mode must be api_key or none")
        if not 1 <= self.max_output_tokens <= 16384 or not 0 <= self.history_char_budget <= 48000:
            raise ValueError("Invalid output or history budget")

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        raw = {}
        base = Path.cwd()
        if path:
            path = path.resolve()
            base = path.parent
            with path.open("rb") as handle:
                raw = tomllib.load(handle)

        def resolve(value: str) -> Path:
            candidate = Path(value).expanduser()
            return (candidate if candidate.is_absolute() else base / candidate).resolve()

        sources = {key: resolve(value) for key, value in raw.get("sources", {}).items()}
        data_dir = resolve(
            os.environ.get("SOLOMON_DATA_DIR", raw.get("data_dir", ".solomon-agent"))
        )
        for root in sources.values():
            if data_dir == root or root in data_dir.parents:
                raise ValueError("data_dir must be outside every read-only source root")
        auth_mode = os.environ.get("SOLOMON_AUTH_MODE", raw.get("auth_mode", "api_key"))
        api_key = ""
        if auth_mode == "api_key":
            api_key = os.environ.get(
                "SOLOMON_API_KEY", os.environ.get(raw.get("api_key_env", "SOLOMON_API_KEY"), "")
            )
        return cls(
            data_dir=data_dir,
            max_output_tokens=int(raw.get("max_output_tokens", 512)),
            history_char_budget=int(raw.get("history_char_budget", 6000)),
            sources=sources,
            model=os.environ.get("SOLOMON_MODEL", raw.get("model", "")),
            base_url=os.environ.get("SOLOMON_BASE_URL", raw.get("base_url")) or None,
            api_key=api_key,
            auth_mode=auth_mode,
            reasoning_effort=raw.get("reasoning_effort"),
        )

    def require_model(self) -> None:
        if not self.model.strip():
            raise ValueError("Real mode requires a model; use --demo explicitly for simulation")
        if self.auth_mode == "api_key":
            if not self.api_key:
                raise ValueError("api_key mode requires an API key")
        else:
            try:
                endpoint = urlsplit(self.base_url or "")
                valid = (
                    not any(char.isspace() for char in self.base_url or "")
                    and endpoint.scheme in {"http", "https"}
                    and endpoint.hostname
                    and endpoint.port != 0
                    and not endpoint.username
                    and not endpoint.password
                    and not endpoint.query
                    and not endpoint.fragment
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("none mode requires an explicit valid HTTP(S) base_url")
