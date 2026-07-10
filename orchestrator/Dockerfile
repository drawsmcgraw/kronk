FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY requirements.txt requirements.lock ./
RUN uv pip install --system --require-hashes -r requirements.lock

COPY *.py ./
COPY static/ static/

# Non-root (tenet 10, operator directive 2026-07-07). UID 1000 matches the
# host operator, so writes to the ./data bind mount land drew-owned.
RUN useradd --uid 1000 --create-home kronk
USER kronk

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
