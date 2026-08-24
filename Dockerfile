FROM python:3.12-slim

WORKDIR /app

# git is needed to fetch the grading engine (mcp-probe) until it is published to PyPI.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# --- grading engine -------------------------------------------------------
# mcp-probe is the moat. Clone it and point MCP_PROBE_PATH at it; the app adds it to sys.path.
RUN git clone --depth 1 https://github.com/junaidshahid-dev/mcp-probe /opt/mcp-probe
ENV MCP_PROBE_PATH=/opt/mcp-probe

# --- app ------------------------------------------------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Hosted defaults: no arbitrary local command execution, no private-network access.
ENV MCPWATCH_ALLOW_STDIO=false \
    MCPWATCH_SSRF_ALLOW_PRIVATE=false \
    MCPWATCH_DB=/data/mcpwatch.db \
    PORT=8000

EXPOSE 8000
CMD ["python", "run.py"]
