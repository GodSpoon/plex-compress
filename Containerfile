# Plex Compress — Containerfile for Podman
# Syntax-identical to Dockerfile; kept separate for Podman conventions on w7.
# Build: podman build -t plex-compress:latest -f Containerfile .

ARG FFMPEG_IMAGE=jrottenberg/ffmpeg:7.1-nvidia
ARG PYTHON_VERSION=3.12

FROM ${FFMPEG_IMAGE} AS ffmpeg-base
USER root

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

ENV PATH="/usr/local/bin:${PATH}"
RUN ffmpeg -encoders 2>/dev/null | grep -E '(hevc_nvenc|libx265|hevc_videotoolbox)' || true

FROM ffmpeg-base AS app
WORKDIR /app

# Create a virtualenv to avoid PEP 668 externally-managed-environment issues
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY plex_compress/ ./plex_compress/
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN pip install --no-cache-dir -e .

RUN mkdir -p /data /config /tmp/plex_compress /mnt/plex

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

HEALTHCHECK --interval=60s --timeout=10s --start-period=5s --retries=3 \
    CMD /opt/venv/bin/python -m plex_compress --health-check --video-encoder ${PLEX_COMPRESS_ENCODER} || exit 1

EXPOSE 8765

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["help"]
