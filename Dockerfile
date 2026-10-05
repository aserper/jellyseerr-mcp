FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY jellyseerr_mcp ./jellyseerr_mcp
COPY arrchestra_mcp ./arrchestra_mcp
RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 arrchestra

USER arrchestra
ENTRYPOINT ["arrchestra-mcp"]
