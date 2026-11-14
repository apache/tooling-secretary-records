#!/bin/sh

STOPPING=0
PROXY_PID=""

# ASF OAuth refuses a redirect URI with a port, so signing in needs the app at https://localhost.
# A proxy that failed to start does not stop the app: it is still at https://localhost:4500.
start_proxy()
{
  if ! command -v caddy >/dev/null 2>&1; then
    echo "caddy is not installed: the app is at https://localhost:4500, where signing in with ASF OAuth does not work" >&2
    return
  fi

  caddy run --config scripts/Caddyfile --adapter caddyfile &
  PROXY_PID=$!

  # a proxy that cannot bind port 443 or read its files exits at once, and its own error scrolls past
  sleep 1
  if ! kill -0 "${PROXY_PID}" 2>/dev/null; then
    PROXY_PID=""
    echo "caddy exited, so the app is only at https://localhost:4500, where signing in does not work." >&2
    echo "  'permission denied' on :443: sudo setcap cap_net_bind_service=+ep \"\$(command -v caddy)\"" >&2
    echo "    or sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443" >&2
    echo "  'permission denied' on a file under ~/.local/share/caddy, left by an earlier sudo run:" >&2
    echo "    sudo chown -R \"\$USER\": ~/.local/share/caddy ~/.config/caddy" >&2
    return
  fi

  trap stop_proxy EXIT
  echo "Reverse proxy started: sign in at https://localhost"
}

stop_proxy()
{
  [ -n "${PROXY_PID}" ] && kill -TERM "${PROXY_PID}" 2>/dev/null
}

cleanup()
{
  if ps -o pid= -p "${PID}";
  then
    echo "Stopping spawned node process with pid ${PID}"
    kill -TERM "${PID}"
  else
    echo "Spawned node process with pid ${PID} not running anymore"
  fi
  STOPPING=1
}

SERVER_ONLY="false"

while getopts ":s" option; do
   case $option in
      s) SERVER_ONLY="true";;
      *) # Invalid option. Bare `exit` would carry the status of the echo above it, so a
         # mistyped flag would start nothing and still report success.
         echo "Error: Invalid option" >&2
         exit 2;;
   esac
done

if [ "${SERVER_ONLY}" = "false" ]; then
  trap cleanup TERM INT

  MANIFEST_FILE="./app/client/static/assets/manifest.json"
  rm "${MANIFEST_FILE}"

  cd assets && npm run dev &
  PID=$!

  # wait till the manifest file was generated before starting the app
  until [ -f "${MANIFEST_FILE}" ]
  do
    [ "${STOPPING}" = "1" ] && exit
    sleep 0.5
  done
fi

start_proxy

uv run python3 server.py

if [ "${SERVER_ONLY}" = "false" ]; then
  [ -d "/proc/${PID}" ] && echo "process running" || exit

  wait "${PID}"
  trap - TERM INT
  wait "${PID}"
fi
