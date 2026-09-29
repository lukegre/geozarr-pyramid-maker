#!/bin/sh
# Serve the GeoZarr viewer from the session's working directory (relative store paths resolve
# from there). RenkuLab sets RENKU_WORKING_DIR and RENKU_BASE_URL_PATH; both are optional.
set -e
cd "${RENKU_WORKING_DIR:-${PWD}}"
exec geozarr-pyramid preview \
    --host 0.0.0.0 \
    --port "${GEOZARR_VIEWER_PORT:-8888}" \
    --base-path "${RENKU_BASE_URL_PATH:-}" \
    "$@"
