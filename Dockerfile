FROM python:3.11-slim-bookworm AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements.txt ./
RUN /opt/venv/bin/python -m pip install --upgrade pip \
    && /opt/venv/bin/python -m pip install -r requirements.txt \
    && /opt/venv/bin/python -m pip check \
    && /opt/venv/bin/python -c "import torch, torchaudio; assert torch.version.cuda is None and '+cpu' in torch.__version__; assert '+cpu' in torchaudio.__version__"
COPY pyproject.toml ./
COPY backend ./backend
COPY engine ./engine
RUN /opt/venv/bin/python -m pip install --no-deps .

FROM python:3.11-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 TORCH_NUM_THREADS=2 XDG_CACHE_HOME=/tmp/drum-cache \
    XDG_CONFIG_HOME=/tmp/drum-cache/config XDG_DATA_HOME=/tmp/drum-cache/data \
    NUMBA_CACHE_DIR=/tmp/drum-cache/numba
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg libsndfile1 musescore3 fontconfig fonts-freefont-ttf \
    libfontconfig1 libfreetype6 libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 \
    && fc-cache -f && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 drum-api \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin drum-api
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root backend ./backend
COPY --chown=root:root engine ./engine
COPY --chown=root:root scripts ./scripts
RUN mkdir -p storage/uploads storage/processed storage/exports storage/jobs models/adtof models/oaf \
    && chown -R drum-api:drum-api storage \
    && if command -v mscore3 >/dev/null; then ln -s "$(command -v mscore3)" /usr/local/bin/drum-musescore; \
       elif command -v musescore3 >/dev/null; then ln -s "$(command -v musescore3)" /usr/local/bin/drum-musescore; \
       else exit 1; fi
ENV MUSESCORE_BIN=/usr/local/bin/drum-musescore
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]