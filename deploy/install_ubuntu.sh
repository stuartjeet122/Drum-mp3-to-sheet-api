#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

if [[ ${EUID} -ne 0 ]]; then
    printf 'Run this installer as root on the target Ubuntu server.\n' >&2
    exit 1
fi
if [[ $(uname -m) != x86_64 ]]; then
    printf 'This deployment is pinned for the M73 x86_64 CPU.\n' >&2
    exit 1
fi
source /etc/os-release
if [[ ${ID} != ubuntu ]]; then
    printf 'This installer requires Ubuntu Server; Ubuntu 24.04 LTS is recommended.\n' >&2
    exit 1
fi
if [[ ! -d /run/systemd/system || ! -f /sys/fs/cgroup/cgroup.controllers ]] || ! grep -qw memory /sys/fs/cgroup/cgroup.controllers; then
    printf 'A running systemd host with the cgroup-v2 memory controller is required for enforced memory/swap limits.\n' >&2
    exit 1
fi

SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
APP_DIR=/opt/drum-transcription
for unit in drum-api.service drum-worker.service; do
    if systemctl cat "${unit}" >/dev/null 2>&1; then
        systemctl stop "${unit}"
    fi
done
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates git rsync build-essential \
    ffmpeg libsndfile1 musescore3 fontconfig fonts-freefont-ttf \
    libfontconfig1 libfreetype6 libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 \
    redis-server python3 python3-venv python3-dev

if [[ -z ${PYTHON_BIN:-} ]]; then
    if apt-cache show python3.11-venv >/dev/null 2>&1; then
        apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-dev
        PYTHON_BIN=python3.11
    else
        PYTHON_BIN=python3
    fi
fi
"${PYTHON_BIN}" -c 'import sys; assert (3, 11) <= sys.version_info[:2] < (3, 14), "Use Python 3.11-3.13 (Ubuntu 24.04 supplies 3.12); provision a supported interpreter first."'
MSCORE_BIN=$(command -v mscore3 || command -v musescore3 || command -v mscore || true)
if [[ -z ${MSCORE_BIN} ]]; then
    printf 'MuseScore 3 executable is missing after package installation.\n' >&2
    exit 1
fi
getent group drum-api >/dev/null || groupadd --system drum-api
id -u drum-api >/dev/null 2>&1 || useradd --system --gid drum-api --home-dir "${APP_DIR}" --shell /usr/sbin/nologin drum-api
install -d -m 0755 "${APP_DIR}"
if [[ ${SOURCE_DIR} != "${APP_DIR}" ]]; then
    rsync -a --exclude='.git/' --exclude='.venv/' --exclude='.env' \
        --exclude='storage/' --exclude='models/' --exclude='__pycache__/' \
        --exclude='.pytest_cache/' --exclude='*.egg-info/' "${SOURCE_DIR}/" "${APP_DIR}/"
    install -d "${APP_DIR}/models"
    rsync -a --ignore-existing "${SOURCE_DIR}/models/" "${APP_DIR}/models/"
fi
install -d -o drum-api -g drum-api -m 0750 \
    "${APP_DIR}/storage" "${APP_DIR}/storage/uploads" "${APP_DIR}/storage/processed" \
    "${APP_DIR}/storage/exports" "${APP_DIR}/storage/jobs"
install -d -m 0755 "${APP_DIR}/models/adtof" "${APP_DIR}/models/oaf"
if [[ ! -x ${APP_DIR}/.venv/bin/python ]]; then
    "${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/python" -m pip install --no-cache-dir -r "${APP_DIR}/requirements.txt"
"${APP_DIR}/.venv/bin/python" -m pip install --no-deps "${APP_DIR}"
"${APP_DIR}/.venv/bin/python" -m pip check
"${APP_DIR}/.venv/bin/python" -c 'import torch, torchaudio; assert torch.version.cuda is None and "+cpu" in torch.__version__; assert "+cpu" in torchaudio.__version__; print("Verified CPU-only PyTorch:", torch.__version__)'
fc-cache -f
systemctl enable --now redis-server.service

chown -R root:drum-api "${APP_DIR}"
chmod -R g+rX,g-w "${APP_DIR}"
chmod 0750 "${APP_DIR}"
if [[ -f ${APP_DIR}/.env ]]; then
    chmod 0640 "${APP_DIR}/.env"
fi
chown -R drum-api:drum-api "${APP_DIR}/storage"
chmod -R u+rwX "${APP_DIR}/storage"
install -d -o drum-api -g drum-api -m 0750 /var/cache/drum-transcription
install -m 0644 "${APP_DIR}/deploy/systemd/drum-api.service" /etc/systemd/system/drum-api.service
install -m 0644 "${APP_DIR}/deploy/systemd/drum-worker.service" /etc/systemd/system/drum-worker.service
systemctl daemon-reload
systemctl enable drum-api.service drum-worker.service
systemctl restart drum-api.service drum-worker.service
printf '\nInstalled. Swagger: http://127.0.0.1:8000/docs\n'
printf 'Model files must be present in models/adtof and models/oaf/model.onnx before transcription.\n'
printf 'Job state is stored in storage/jobs; no database or .env file is required.\n'
printf 'Logs: journalctl -u drum-api -u drum-worker -f\n'