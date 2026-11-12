"""Trusted local service configuration; constructing settings starts no clients."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ServiceSettings:
    root: Path
    data_directory: Path | None = None
    artifacts: Path | None = None
    ui_directory: Path | None = None
    port: int = 8766
    development_origins: tuple[str, ...] = ("http://127.0.0.1:5173",)
    event_retention: int = 1024
    recovery_seconds: float = 15

    def __post_init__(self):
        root = Path(self.root).resolve()
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "data_directory", Path(self.data_directory or root / "artifacts/studio/service").resolve())
        object.__setattr__(self, "artifacts", Path(self.artifacts or root / "artifacts/studio/runs").resolve())
        if self.ui_directory is not None:
            object.__setattr__(self, "ui_directory", Path(self.ui_directory).resolve())
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("invalid_service_port")
        if not 0 < self.recovery_seconds <= 30:
            raise ValueError("invalid_recovery_budget")
        for origin in self.development_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password
                    or parsed.port is None):
                raise ValueError("invalid_development_origin")

    @property
    def origins(self):
        return frozenset((f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}",
                          f"http://[::1]:{self.port}", *self.development_origins))

    @property
    def authorities(self):
        return frozenset(urlsplit(origin).netloc.lower() for origin in self.origins)
