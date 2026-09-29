# GeoZarr viewer for RenkuLab session launchers (and any container host).
# Starts the blank viewer on port 8888; RenkuLab's URL prefix comes from $RENKU_BASE_URL_PATH.
# Build: docker build -t lukegre/geozarr-viewer:latest .
# Run:   docker run --rm -p 8888:8888 -v "$PWD:/home/renku/work" lukegre/geozarr-viewer:latest
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv

# rasterio's manylinux wheel links against the system libexpat, which -slim does not ship
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /opt/app
# dependencies first (cached layer), then the project itself
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra cloud --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra cloud --no-editable && rm -rf /root/.cache \
    && geozarr-pyramid version

COPY docker/entrypoint.sh /usr/local/bin/geozarr-viewer
RUN chmod 0755 /usr/local/bin/geozarr-viewer \
    && useradd --uid 1000 --gid 100 --create-home --shell /bin/bash renku \
    && mkdir -p /home/renku/work && chown -R 1000:100 /home/renku

USER 1000:100
ENV HOME=/home/renku
WORKDIR /home/renku/work
EXPOSE 8888
ENTRYPOINT ["/usr/local/bin/geozarr-viewer"]
