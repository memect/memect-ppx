# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ARG PPX_GPU=no
ARG PPX_DOWNLOAD_MODELS=0
ARG PPX_ONNXRUNTIME_GPU_VERSION=1.23.2
ARG DEBIAN_MIRROR=
ARG PYPI_INDEX_URL=

ENV RUNNING_IN_DOCKER=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN if [ -n "${DEBIAN_MIRROR}" ]; then \
        sed -i "s#http://deb.debian.org/debian#${DEBIAN_MIRROR}#g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -n "${PYPI_INDEX_URL}" ]; then \
        export PIP_INDEX_URL="${PYPI_INDEX_URL}" UV_INDEX_URL="${PYPI_INDEX_URL}"; \
    fi \
    && pip install uv

COPY pyproject.toml ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -n "${PYPI_INDEX_URL}" ]; then \
        export PIP_INDEX_URL="${PYPI_INDEX_URL}" UV_INDEX_URL="${PYPI_INDEX_URL}"; \
    fi \
    && uv pip install --system --no-cache -e . \
    && if [ "${PPX_GPU}" = "cuda" ]; then \
        python -m pip install \
            opencv-contrib-python-headless \
            "onnxruntime-gpu==${PPX_ONNXRUNTIME_GPU_VERSION}" \
            nvidia-cuda-runtime-cu12 \
            nvidia-cudnn-cu12 \
            nvidia-cublas-cu12 \
            nvidia-cufft-cu12 \
            nvidia-curand-cu12 \
            nvidia-cuda-nvrtc-cu12 \
            nvidia-nvjitlink-cu12; \
    else \
        ppx install --headless --gpu no; \
    fi \
    && mkdir -p /app/data /app/logs /app/conf

RUN if [ "${PPX_DOWNLOAD_MODELS}" = "1" ]; then ppx download; fi

EXPOSE 9527

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9527/health', timeout=3).read()" || exit 1

CMD ["ppx", "start", "--host", "0.0.0.0", "--port", "9527"]
