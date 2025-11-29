FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8797

CMD ["python", "-m", "jellyseerr_mcp"]
