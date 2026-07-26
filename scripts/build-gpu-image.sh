#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

usage() {
    cat <<'EOF'
Build the PPX Linux GPU Docker image.

Usage:
  scripts/build-gpu-image.sh [--dry-run] [--push]

Environment:
  PPX_IMAGE_NAME                  Image repository/name. Default: hub.wenyinhulian.cn/docparser/ppx-gpu
  PPX_IMAGE_TAG                   Image tag. Default: build date in YYMMDD, for example 260702
  PPX_LOCAL_ALIAS                 Extra local moving alias. Default: ppx:gpu
  PPX_ADD_ALIAS                   Tag PPX_LOCAL_ALIAS too. Default: 1
  PPX_PUSH                        Push the formal image after build. Default: 0
  PPX_PLATFORM                    Docker platform. Default: linux/amd64
  PPX_DOWNLOAD_MODELS             Download models during build. Default: 0
  PPX_ONNXRUNTIME_GPU_VERSION     CUDA 12 compatible onnxruntime-gpu pin. Default: 1.23.2
  DEBIAN_MIRROR                   Optional build-only Debian mirror
  PYPI_INDEX_URL                  Optional build-only PyPI index

Examples:
  DEBIAN_MIRROR=https://mirrors.cloud.tencent.com/debian \
  PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
  scripts/build-gpu-image.sh

  scripts/build-gpu-image.sh --push

  PPX_IMAGE_TAG=260702 scripts/build-gpu-image.sh
EOF
}

DRY_RUN=0
PUSH="${PPX_PUSH:-0}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        --push)
            PUSH=1
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
    shift
done

IMAGE_NAME="${PPX_IMAGE_NAME:-hub.wenyinhulian.cn/docparser/ppx-gpu}"
PLATFORM="${PPX_PLATFORM:-linux/amd64}"
IMAGE_TAG="${PPX_IMAGE_TAG:-$(date +%y%m%d)}"
IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
LOCAL_ALIAS="${PPX_LOCAL_ALIAS:-ppx:gpu}"
ADD_ALIAS="${PPX_ADD_ALIAS:-1}"

PPX_DOWNLOAD_MODELS="${PPX_DOWNLOAD_MODELS:-0}"
PPX_ONNXRUNTIME_GPU_VERSION="${PPX_ONNXRUNTIME_GPU_VERSION:-1.23.2}"

build_args=(
    --platform "${PLATFORM}"
    --build-arg "PPX_GPU=cuda"
    --build-arg "PPX_DOWNLOAD_MODELS=${PPX_DOWNLOAD_MODELS}"
    --build-arg "PPX_ONNXRUNTIME_GPU_VERSION=${PPX_ONNXRUNTIME_GPU_VERSION}"
    -t "${IMAGE}"
)

if [ -n "${DEBIAN_MIRROR:-}" ]; then
    build_args+=(--build-arg "DEBIAN_MIRROR=${DEBIAN_MIRROR}")
fi

if [ -n "${PYPI_INDEX_URL:-}" ]; then
    build_args+=(--build-arg "PYPI_INDEX_URL=${PYPI_INDEX_URL}")
fi

printf 'Building PPX GPU image\n'
printf '  image: %s\n' "${IMAGE}"
printf '  platform: %s\n' "${PLATFORM}"
printf '  onnxruntime-gpu: %s\n' "${PPX_ONNXRUNTIME_GPU_VERSION}"
printf '  download models: %s\n' "${PPX_DOWNLOAD_MODELS}"
printf '  push: %s\n' "${PUSH}"

if [ "${DRY_RUN}" = "1" ]; then
    printf 'Dry run command:\n  docker build'
    printf ' %q' "${build_args[@]}" .
    printf '\n'
    if [ "${ADD_ALIAS}" = "1" ]; then
        printf 'Alias command:\n  docker tag %q %q\n' "${IMAGE}" "${LOCAL_ALIAS}"
    fi
    if [ "${PUSH}" = "1" ]; then
        printf 'Push command:\n  docker push %q\n' "${IMAGE}"
    fi
    exit 0
fi

docker build "${build_args[@]}" .

if [ "${ADD_ALIAS}" = "1" ]; then
    docker tag "${IMAGE}" "${LOCAL_ALIAS}"
    printf 'Tagged local alias: %s\n' "${LOCAL_ALIAS}"
fi

if [ "${PUSH}" = "1" ]; then
    docker push "${IMAGE}"
fi

printf 'Built image: %s\n' "${IMAGE}"
