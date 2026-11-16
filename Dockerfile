FROM python:3.11-slim-bookworm AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv "$VIRTUAL_ENV"
WORKDIR /build
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY engine ./engine
RUN python -m pip install --no-deps . \
    && python -m pip check

FROM python:3.11-slim-bookworm AS runtime

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_STORAGE_DIR=/app/storage \
    APP_MODELS_DIR=/app/models \
    HOME=/tmp \
    QT_QPA_PLATFORM=offscreen \
    XDG_CACHE_HOME=/tmp/cache \
    XDG_CONFIG_HOME=/tmp/config \
    XDG_DATA_HOME=/tmp/data \
    XDG_RUNTIME_DIR=/tmp/qt-runtime \
    NUMBA_CACHE_DIR=/tmp/numba

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates ffmpeg libsndfile1 musescore3 fontconfig fonts-freefont-ttf \
       libfontconfig1 libfreetype6 libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
RUN python -m pip check \
    && python -c 'import torch, torchaudio; assert torch.version.cuda is None and "+cpu" in torch.__version__; assert "+cpu" in torchaudio.__version__'
RUN groupadd --gid 10001 drum-api \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin drum-api \
    && install -d -o drum-api -g drum-api -m 0750 \
       /app/storage /app/storage/uploads /app/storage/processed \
       /app/storage/exports /app/storage/jobs \
    && install -d -m 0755 /app/models
WORKDIR /app
COPY scripts ./scripts
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]