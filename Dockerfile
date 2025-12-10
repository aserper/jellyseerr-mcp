FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FASTMCP_HOST=0.0.0.0
ENV PORT=8000
# Jellyseerr Configuration
ENV JELLYSEERR_URL=""
ENV JELLYSEERR_API_KEY=""
ENV JELLYSEERR_TIMEOUT=15.0
# SSE Authentication Configuration
ENV MCP_AUTH_ISSUER_URL=""
ENV MCP_AUTH_RESOURCE_SERVER_URL=""
ENV MCP_AUTH_REQUIRED_SCOPES=""

EXPOSE 8000

CMD ["python", "main.py", "--transport", "sse"]
