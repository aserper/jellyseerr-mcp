FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8000
EXPOSE 8000

CMD ["python", "main.py", "--transport", "sse", "--port", "8000"]
