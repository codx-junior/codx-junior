#!/bin/bash
# Dynamically set CODX_JUNIOR_PATH to the parent directory of the script
export CODX_JUNIOR_PATH="$(cd "$(dirname "$0")/.." && pwd)"
source ${CODX_JUNIOR_PATH}/set_env.sh

echo "CODX_JUNIOR_PATH: $CODX_JUNIOR_PATH"
echo "Starting client USER: ${USER} HOME: ${HOME}"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion

cd ${CODX_JUNIOR_PATH}/client

CLIENT_DIST="${CODX_JUNIOR_CLIENT_DIST:-${CODX_JUNIOR_PATH}/client/dist}"
WEB_PORT="${CODX_JUNIOR_WEB_PORT:-19981}"

# ---------------------------------------------------------------------------
# Production: serve the prebuilt SPA from disk with nginx.
#
# This used to run `vite preview`, a Node process that parses the whole Vite
# toolchain before it binds the port. On a loaded host that took ~60 s, on top
# of the `nvm install v24.12.0` network round-trip (~70 s) that ran on every
# start. Traefik publishes the container as soon as it starts and does not wait
# for a listener, so every restart answered 502 Bad Gateway for minutes:
#   dial tcp 172.18.0.2:19981: connect: connection refused
# nginx binds the port in milliseconds and serves the same `dist` directory, so
# a restart no longer has a window where the port is closed.
# ---------------------------------------------------------------------------
run_static_server() {
  local nginx_conf="/tmp/codx-junior-client-nginx.conf"
  local tmp_dir="/tmp/codx-junior-client-nginx"

  mkdir -p "${tmp_dir}/client_body" "${tmp_dir}/proxy" "${tmp_dir}/fastcgi" \
           "${tmp_dir}/uwsgi" "${tmp_dir}/scgi"

  # The container runs as an unprivileged user: every path nginx writes to
  # (pid, logs, request bodies, proxy buffers) lives under /tmp. `daemon off`
  # keeps it in the foreground so docker logs and signals work.
  cat > "${nginx_conf}" <<NGINX
worker_processes 1;
pid ${tmp_dir}/nginx.pid;
error_log /dev/stderr warn;
events {
    worker_connections 1024;
}
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log /dev/stdout;
    sendfile on;
    tcp_nopush on;
    keepalive_timeout 65;
    client_body_temp_path ${tmp_dir}/client_body;
    proxy_temp_path ${tmp_dir}/proxy;
    fastcgi_temp_path ${tmp_dir}/fastcgi;
    uwsgi_temp_path ${tmp_dir}/uwsgi;
    scgi_temp_path ${tmp_dir}/scgi;
    gzip on;
    gzip_types text/plain text/css application/json application/javascript
               application/xml application/wasm image/svg+xml;
    gzip_min_length 1024;
    server {
        listen ${WEB_PORT};
        server_name _;
        root ${CLIENT_DIST};
        index index.html;
        location /assets/ {
            expires 1y;
            add_header Cache-Control "public, immutable";
            try_files \$uri =404;
        }
        location = /index.html {
            add_header Cache-Control "no-store";
        }
        location / {
            try_files \$uri \$uri/ /index.html;
        }
    }
}
NGINX

  echo "*********** codx-junior client PRODUCTION (nginx :${WEB_PORT}) ************"
  exec nginx -c "${nginx_conf}" -g 'daemon off;'
}

# `DEBUG=0` is a value, not "unset": the old test ([ "$DEBUG" != "" ]) treated
# every non-empty value as debug, so a container configured for production ran
# `npm install` and the Vite dev server on every restart. That is what blocks a
# restart: the install needs the network and the UI stays down until it ends.
case "${DEBUG:-0}" in
  1|true|TRUE|yes|on)
    echo "*********** codx-junior client DEBUG ************"
    # Dependencies belong to the image/build, not to every start. Install only
    # for a fresh checkout that has no node_modules yet.
    if ! command -v npm >/dev/null 2>&1; then
      echo "ERROR: DEBUG=1 needs node/npm in the image." >&2
      echo "       Use the dev image (codxjuniorai/codx-junior:dev) or the" >&2
      echo "       nginx production mode (DEBUG=0)." >&2
      exit 1
    fi
    [ -d node_modules ] || npm install
    npm run dev
    ;;
  *)
    if [ ! -f "${CLIENT_DIST}/index.html" ]; then
      echo "ERROR: ${CLIENT_DIST}/index.html is missing." >&2
      echo "       Build it once with: (cd client && npm run build-only)" >&2
      exit 1
    fi
    if command -v nginx >/dev/null 2>&1; then
      run_static_server
    else
      # Old client image without nginx: keep serving, but never silently.
      echo "WARNING: nginx is not installed; falling back to \`vite preview\`," >&2
      echo "         whose slow start is the cause of the 502s. Rebuild the" >&2
      echo "         client image to get the nginx server." >&2
      npm run preview
    fi
    ;;
esac
