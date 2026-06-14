# BNB Agent — single-image Dockerfile for the public online deploy.
#
# Builds a Python 3.12 image with the bnbagent package + all runtime
# deps, then runs the dashboard backend (FastAPI) on port 8000. The
# MCP SSE server can be exposed on 8765 by setting BNBAGENT_MCP_PORT.
#
# Build:    docker build -t bnbagent .
# Run:      docker run --rm -p 8000:8000 \
#               -v bnbagent_data:/home/bnbagent/.bnbagent \
#               -e BNBAGENT_AUTH_ENABLED=true \
#               -e BNBAGENT_AUTH_SECRET=... \
#               -e JUDGE_PASSWORD=... \
#               -e ADMIN_PASSWORD=... \
#               -e MINIMAX_API_KEY=sk-cp-... \
#               bnbagent
#
# In Coolify, the volumes + env are configured in the service UI; this
# Dockerfile just runs the dashboard (which is the entry point for
# judges + the operator).

FROM python:3.12-slim

# System deps: git (for `git rev-parse` provenance in replay JSONs),
# curl (for x402 base RPCs + handy for healthcheck).
RUN apt-get update -y && apt-get install -y --no-install-recommends \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for runtime (the dashboard, the agent, the signer all
# expect a writable home at $HOME/.bnbagent/).
RUN useradd --create-home --shell /bin/bash bnbagent
WORKDIR /home/bnbagent
USER bnbagent

# Install the package + test extras. We use --no-cache-dir to keep the
# image lean. The 'test' extras are not required for the running app
# but pulling them in costs ~5MB and means pytest works in the
# container for on-the-fly verification.
COPY --chown=bnbagent:bnbagent pyproject.toml ./
COPY --chown=bnbagent:bnbagent setup.cfg ./ 2>/dev/null || true
COPY --chown=bnbagent:bnbagent README.md ./
COPY --chown=bnbagent:bnbagent agents ./agents
COPY --chown=bnbagent:bnbagent backtest ./backtest
COPY --chown=bnbagent:bnbagent config ./config
COPY --chown=bnbagent:bnbagent connectors ./connectors
COPY --chown=bnbagent:bnbagent core ./core
COPY --chown=bnbagent:bnbagent dashboard ./dashboard
COPY --chown=bnbagent:bnbagent docs ./docs
COPY --chown=bnbagent:bnbagent jobs ./jobs
COPY --chown=bnbagent:bnbagent policy ./policy
COPY --chown=bnbagent:bnbagent scripts ./scripts
COPY --chown=bnbagent:bnbagent strategies ./strategies
COPY --chown=bnbagent:bnbagent tests ./tests
COPY --chown=bnbagent:bnbagent bnbagent ./
COPY --chown=bnbagent:bnbagent install.sh ./

# The MiniMax API endpoint (and others) require networking in the
# container. --default-timeout caps the pip install so a stuck
# network doesn't hang the build.
RUN pip install --no-cache-dir --default-timeout=60 -e ".[test]" 2>&1 | tail -5

# Default port. Coolify / docker-compose can override.
EXPOSE 8000

# Healthcheck: hit /api/healthz (the public no-auth endpoint) every
# 30s. If 3 consecutive failures, the container is marked unhealthy
# and Coolify restarts it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/healthz || exit 1

# The bnbagent launcher is a bash script that activates the venv
# and runs the dashboard. In Docker we don't have a venv (everything
# is installed in the system site-packages), so we bypass the venv
# check and call uvicorn directly.
#
# Why not use `bash bnbagent`? Because that script insists on a venv
# at .venv/ or /tmp/venv/. In Docker we have neither — pip install -e
# put everything in /usr/lib/python3.12/site-packages.
#
# So we shell into uvicorn directly, which is the same thing
# `bash bnbagent` would do once the venv check passes.
ENTRYPOINT ["python3", "-m", "uvicorn", "dashboard.backend.main:app", \
            "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
