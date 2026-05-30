# Plex Compress — Containerized
# Supports NVIDIA NVENC on Linux and graceful fallback to software encoding.
# Apple VideoToolbox does NOT passthrough to Linux containers; use native
# execution on macOS (see scripts/docker-run-macos.sh) for hardware transcoding.
#
# Build:
#   docker build -t plex-compress:latest .
#   podman build -t plex-compress:latest .
#
# Multi-arch support:
#   docker buildx build --platform linux/amd64,linux/arm64 -t plex-compress:latest .

ARG FFMPEG_IMAGE=jrottenberg/ffmpeg:7.1-nvidia
ARG PYTHON_VERSION=3.12

# ------------------------------------------------------------------------------
# Stage 1: Base with ffmpeg + NVIDIA runtime libraries
# ------------------------------------------------------------------------------
FROM ${FFMPEG_IMAGE} AS ffmpeg-base

# jrottenberg/ffmpeg drops to 'ffmpeg' user; we need root to install packages
USER root

# Install Python and pip
ARG PYTHON_VERSION
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python${PYTHON_VERSION} \
    python3-pip \
    python${PYTHON_VERSION}-venv \
    libsqlite3-0 \
    ca-certificates \
    tini \
    && ln -sf /usr/bin/python${PYTHON_VERSION} /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Ensure ffmpeg binaries are on PATH (jrottenberg image already does this,
# but we make it explicit)
ENV PATH="/usr/local/bin:${PATH}"

# Verify ffmpeg has the encoders we care about
RUN ffmpeg -encoders 2>/dev/null | grep -E '(hevc_nvenc|libx265|hevc_videotoolbox)' || true

# ------------------------------------------------------------------------------
# Stage 2: Install application into a virtualenv
# ------------------------------------------------------------------------------
FROM ffmpeg-base AS app

WORKDIR /app

# Create a virtualenv to avoid PEP 668 externally-managed-environment issues
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install Python dependencies first (for layer caching)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Copy application code
COPY plex_compress/ ./plex_compress/
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Re-install to pick up package data
RUN pip install --no-cache-dir -e .

# Create directories for runtime mounts
RUN mkdir -p /data /config /tmp/plex_compress /mnt/plex

# Environment defaults
ENV PLEX_COMPRESS_ENCODER=auto
ENV PLEX_COMPRESS_QUALITY=28
ENV PLEX_COMPRESS_PRESET=medium
ENV PLEX_COMPRESS_PARALLEL=1
ENV PLEX_COMPRESS_TEMP_DIR=/tmp/plex_compress
ENV PLEX_COMPRESS_STATE_DB=/config/state.db
ENV PLEX_COMPRESS_LOG=/config/plex_compress.log
ENV PLEX_COMPRESS_LIBRARY_PATH=/mnt/plex
ENV PLEX_COMPRESS_BACKUP=0
ENV PLEX_COMPRESS_DRY_RUN=0
ENV PLEX_COMPRESS_VERBOSE=0
ENV PYTHONUNBUFFERED=1

# Health check uses the venv python explicitly
HEALTHCHECK --interval=60s --timeout=10s --start-period=5s --retries=3 \
    CMD /opt/venv/bin/python -m plex_compress --health-check --video-encoder ${PLEX_COMPRESS_ENCODER} || exit 1

# Web UI port
EXPOSE 8765

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["help"]
