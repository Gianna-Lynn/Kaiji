# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system kaiji && useradd --system --gid kaiji kaiji

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[service]"

USER kaiji
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2)"]

CMD ["uvicorn", "kaiji.service:app", "--host", "0.0.0.0", "--port", "8080"]
