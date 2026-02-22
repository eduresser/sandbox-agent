#!/usr/bin/env bash
set -euo pipefail

USER_NAME="${SUDO_USER:-$USER}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo ./setup-docker.sh"
    exit 1
fi

# Install Docker if not present
if ! command -v docker &>/dev/null; then
    echo "Docker not found. Installing..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
    echo "Docker installed."
fi

# Ensure the daemon is running
if ! systemctl is-active --quiet docker; then
    echo "Starting Docker..."
    systemctl start docker
fi

# Add the user to the docker group
if ! id -nG "$USER_NAME" | grep -qw docker; then
    echo "Adding '$USER_NAME' to the docker group..."
    usermod -aG docker "$USER_NAME"
    echo "Group updated. The current session will be reloaded automatically."
else
    echo "User '$USER_NAME' is already in the docker group."
fi

# Build sandbox images (python, node, r, julia)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$SCRIPT_DIR/src/sandbox_agent/docker"

if [ -d "$DOCKER_DIR" ]; then
    echo ""
    echo "Building sandbox Docker images..."
    docker build -f "$DOCKER_DIR/Dockerfile.python" -t sandbox-python:latest "$DOCKER_DIR"
    docker build -f "$DOCKER_DIR/Dockerfile.node" -t sandbox-node:latest "$DOCKER_DIR"
    docker build -f "$DOCKER_DIR/Dockerfile.r" -t sandbox-r:latest "$DOCKER_DIR"
    docker build -f "$DOCKER_DIR/Dockerfile.julia" -t sandbox-julia:latest "$DOCKER_DIR"
    echo "All sandbox images built."
else
    echo "Warning: Docker directory not found ($DOCKER_DIR). Skipping image build."
fi

# Validate access
if sg docker -c "docker ps" &>/dev/null; then
    echo ""
    echo "Everything is ready. Run the agent with:"
    echo "  sg docker -c 'uv run sandbox-agent'"
    echo ""
    echo "Or open a new terminal (logout/login) and run normally:"
    echo "  uv run sandbox-agent"
else
    echo ""
    echo "Group configured. Logout/login and run:"
    echo "  uv run sandbox-agent"
fi
