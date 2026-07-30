FROM python:3.11-slim

# No apt libeccodes: the eccodeslib wheel ships the C library matching the
# eccodes binding. Debian's copy tracks the base image suite (trixie moved it to
# 2.41.0), which silently drifts out of range of the COSMO definitions and makes
# ecCodes abort the process on the first GRIB parse.
WORKDIR /app

RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-interaction --no-ansi

COPY src/ ./src/
COPY static/ ./static/
RUN poetry install --only main --no-interaction --no-ansi

EXPOSE 8000

# start-period=60s: load_cache() JSON-parses ~170 MB and np.load()s two npz files before
# uvicorn serves anything — 10s was thin enough to risk a false-unhealthy on a cold start.
HEALTHCHECK --interval=10s --timeout=5s --retries=2 --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

CMD ["uvicorn", "lsmfapi.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
