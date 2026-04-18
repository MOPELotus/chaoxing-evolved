#!/usr/bin/env bash

set -euo pipefail

TAG="local"
TARGET_OS=""
ARCHITECTURE=""
OUTPUT_DIR="build"
RELEASE_DIR="release"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)
            TAG="$2"
            shift 2
            ;;
        --os)
            TARGET_OS="$2"
            shift 2
            ;;
        --arch)
            ARCHITECTURE="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --release-dir)
            RELEASE_DIR="$2"
            shift 2
            ;;
        *)
            echo "未知参数: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$TARGET_OS" || -z "$ARCHITECTURE" ]]; then
    echo "必须同时指定 --os 和 --arch" >&2
    exit 1
fi

case "$TARGET_OS" in
    macos|linux)
        ;;
    *)
        echo "不支持的目标系统: $TARGET_OS" >&2
        exit 1
        ;;
esac

case "$ARCHITECTURE" in
    x64|arm64)
        ;;
    *)
        echo "不支持的目标架构: $ARCHITECTURE" >&2
        exit 1
        ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/${OUTPUT_DIR}"
RELEASE_DIR_PATH="${REPO_ROOT}/${RELEASE_DIR}"
JOBS="$(python -c 'import os; print(max(os.cpu_count() or 1, 1))')"
PYTHON_MACHINE="$(python -c 'import platform; print(platform.machine().lower())')"

case "$ARCHITECTURE" in
    x64)
        if [[ "$PYTHON_MACHINE" != "x86_64" && "$PYTHON_MACHINE" != "amd64" ]]; then
            echo "当前 Python 架构为 ${PYTHON_MACHINE}，与目标架构 ${ARCHITECTURE} 不一致" >&2
            exit 1
        fi
        ;;
    arm64)
        if [[ "$PYTHON_MACHINE" != "arm64" && "$PYTHON_MACHINE" != "aarch64" ]]; then
            echo "当前 Python 架构为 ${PYTHON_MACHINE}，与目标架构 ${ARCHITECTURE} 不一致" >&2
            exit 1
        fi
        ;;
esac

mkdir -p "$RELEASE_DIR_PATH"

pushd "$REPO_ROOT" >/dev/null

if [[ "$TARGET_OS" == "macos" ]]; then
    PLATFORM_LABEL="macOS Intel"
    if [[ "$ARCHITECTURE" == "arm64" ]]; then
        PLATFORM_LABEL="macOS Apple Silicon"
    fi

    python -m nuitka \
        --standalone \
        --assume-yes-for-downloads \
        --enable-plugin=tk-inter \
        --plugin-no-detection \
        --macos-create-app-bundle \
        --lto=no \
        --jobs="$JOBS" \
        --progress-bar=none \
        --include-module=websockets.asyncio.server \
        --include-module=websockets.server \
        --include-data-dir=resource=resource \
        --output-dir="$BUILD_DIR" \
        --output-filename=ChaoxingDesktop \
        desktop_app.py

    APP_BUNDLE="$(find "$BUILD_DIR" -maxdepth 1 -type d -name '*.app' | head -n 1)"
    if [[ -z "$APP_BUNDLE" ]]; then
        echo "未找到 macOS 应用包输出目录: $BUILD_DIR" >&2
        exit 1
    fi

    python scripts/prepare_release.py \
        --source-path "$APP_BUNDLE" \
        --release-dir "$RELEASE_DIR_PATH" \
        --tag "$TAG" \
        --ref-name "local" \
        --sha "$(git rev-parse HEAD)" \
        --platform-label "$PLATFORM_LABEL" \
        --artifact-name "chaoxing-evolved-macos-${ARCHITECTURE}-${TAG}"
else
    python -m nuitka \
        --standalone \
        --assume-yes-for-downloads \
        --enable-plugin=tk-inter \
        --plugin-no-detection \
        --lto=no \
        --jobs="$JOBS" \
        --progress-bar=none \
        --include-module=websockets.asyncio.server \
        --include-module=websockets.server \
        --include-data-dir=resource=resource \
        --output-dir="$BUILD_DIR" \
        --output-filename=chaoxing-desktop \
        desktop_app.py

    DIST_DIR="$(find "$BUILD_DIR" -maxdepth 1 -type d -name '*.dist' | head -n 1)"
    if [[ -z "$DIST_DIR" ]]; then
        echo "未找到 Linux standalone 输出目录: $BUILD_DIR" >&2
        exit 1
    fi

    python scripts/package_linux_release.py \
        --dist-dir "$DIST_DIR" \
        --release-dir "$RELEASE_DIR_PATH" \
        --tag "$TAG" \
        --arch "$ARCHITECTURE"
fi

popd >/dev/null
