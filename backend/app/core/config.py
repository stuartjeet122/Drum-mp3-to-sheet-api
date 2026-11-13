"""Conservative single-host defaults with optional managed deployment paths."""

import os
from functools import lru_cache
from pathlib import Path
import shutil
from typing import ClassVar
from urllib.parse import urlsplit

from pydantic import RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Optional environment settings; no .env file is required."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    REDIS_URL: RedisDsn = RedisDsn("redis://localhost:6379/0")
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    APP_STORAGE_DIR: Path = Path("storage")
    APP_MODELS_DIR: Path = Path("models")

    MAX_UPLOAD_SIZE_MB: ClassVar[int] = 100
    ALLOWED_AUDIO_EXTENSIONS: ClassVar[tuple[str, ...]] = (".wav", ".mp3", ".flac", ".aiff")
    OMP_NUM_THREADS: ClassVar[str] = "2"
    MKL_NUM_THREADS: ClassVar[str] = "2"
    OPENBLAS_NUM_THREADS: ClassVar[str] = "2"
    TORCH_NUM_THREADS: ClassVar[int] = 2

    @property
    def BASE_STORAGE_DIR(self) -> Path:
        return self.APP_STORAGE_DIR

    @property
    def ADTOF_WEIGHTS_PATH(self) -> Path:
        return self.APP_MODELS_DIR / "adtof"

    @property
    def OAF_ONNX_PATH(self) -> Path:
        return self.APP_MODELS_DIR / "oaf/model.onnx"

    @property
    def UPLOAD_DIR(self) -> Path:
        return self.BASE_STORAGE_DIR / "uploads"

    @property
    def PROCESSED_DIR(self) -> Path:
        return self.BASE_STORAGE_DIR / "processed"

    @property
    def EXPORTS_DIR(self) -> Path:
        return self.BASE_STORAGE_DIR / "exports"

    @property
    def MUSESCORE_BIN(self) -> str:
        return next((name for name in ("mscore3", "musescore3", "drum-musescore", "mscore") if shutil.which(name)), "mscore3")

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        """Credentialed CORS accepts explicit HTTP origins, never wildcards or paths."""
        origins: list[str] = []
        for value in values:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.path not in {"", "/"}
                    or parsed.query or parsed.fragment or "*" in parsed.netloc
                    or parsed.port == 0):
                raise ValueError("CORS_ORIGINS must contain explicit HTTP(S) origins without paths.")
            origins.append(value.rstrip("/"))
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()


def apply_cpu_limits(settings: Settings | None = None) -> None:
    configuration = settings if settings is not None else get_settings()
    for variable in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "TORCH_NUM_THREADS",
    ):
        os.environ[variable] = str(getattr(configuration, variable))