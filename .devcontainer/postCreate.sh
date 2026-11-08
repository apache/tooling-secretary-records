#!/bin/sh
# Post-create setup for devcontainer.
# Compatible with both Docker and Podman container runtimes.
# For Podman: set devcontainers.dockerPath to "podman" in VS Code settings.
# The workspace is mounted directly at /run/secretary-records (read-write),
# so all file edits are live — no copy/symlink needed.

set -eu

cd /run/secretary-records

# --- SSL certificates ---
# The cert volume (/run/cert) persists across container rebuilds.
# The state/ dir is on the host bind mount and may have stale certs.
mkdir -p /run/cert state
if [ -r /run/cert/cert.pem ] && [ -r /run/cert/key.pem ]; then
    echo "Found existing cert pair in volume cache"
    cp /run/cert/cert.pem /run/cert/key.pem state/
    [ -r /run/cert/rootCA.pem ] && cp /run/cert/rootCA.pem state/
else
    echo "Generating new SSL certificates"
    mkcert -cert-file state/cert.pem -key-file state/key.pem \
        localhost.apache.org localhost 127.0.0.1 ::1
    rm -f "$(mkcert -CAROOT)/rootCA-key.pem"
    cp state/cert.pem state/key.pem /run/cert/
    cp "$(mkcert -CAROOT)/rootCA.pem" /run/cert/
    cp "$(mkcert -CAROOT)/rootCA.pem" state/
fi

# --- Configuration ---
cp config.yaml.example config.yaml
sed -i 's|host: localhost|host: 0.0.0.0|' config.yaml

# --- Install dependencies ---
make sync
uv pip install debugpy
make init
make build

echo ""
echo "================================================"
echo "  Devcontainer setup complete!"
echo "  Run 'make dev' to start the application."
echo "  Access at: https://localhost (sign in here) or https://localhost:4500"
echo "================================================"
