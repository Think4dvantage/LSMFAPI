FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libeccodes-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root --no-interaction --no-ansi

COPY src/ ./src/
COPY static/ ./static/
RUN poetry install --only main --no-interaction --no-ansi

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=2 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "lsmfapi.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
