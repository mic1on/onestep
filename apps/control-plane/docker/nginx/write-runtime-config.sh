#!/bin/sh
set -eu

api_base_url="${ONESTEP_CP_UI_API_BASE_URL:-/}"
escaped_api_base_url="$(printf '%s' "$api_base_url" | sed 's/[\\"]/\\&/g')"

cat > /usr/share/nginx/html/app-config.js <<EOF
window.__APP_CONFIG__ = {
  apiBaseUrl: "${escaped_api_base_url}"
};
EOF
