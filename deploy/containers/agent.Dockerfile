FROM node:24.16.0-bookworm-slim@sha256:2c87ef9bd3c6a3bd4b472b4bec2ce9d16354b0c574f736c476489d09f560a203 AS workflow
WORKDIR /runner
COPY apps/agent/workflow-runner/package*.json ./
RUN npm ci --ignore-scripts --omit=dev

FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
COPY apps/agent/pyproject.toml apps/agent/pyproject.toml
COPY apps/memory/pyproject.toml apps/memory/pyproject.toml
COPY tools/data-seed/pyproject.toml tools/data-seed/pyproject.toml
RUN uv sync --frozen --no-dev --package compass-agent --no-install-workspace
COPY apps/agent apps/agent
COPY --from=workflow /usr/local/bin/node /usr/local/bin/node
COPY --from=workflow /runner/node_modules /app/apps/agent/workflow-runner/node_modules
RUN uv sync --frozen --no-dev --package compass-agent
RUN useradd --create-home --uid 10001 compass
RUN install -d -o compass -g compass -m 700 /var/lib/compass/tool-results
USER compass
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app/apps/agent
EXPOSE 8102
CMD ["uvicorn", "compass_agent.main:app", "--host", "0.0.0.0", "--port", "8102", "--timeout-graceful-shutdown", "5"]
