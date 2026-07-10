FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install uv

COPY requirements.txt requirements.lock ./
RUN uv pip install --system --require-hashes -r requirements.lock

COPY . .

# Non-root (tenet 10, operator directive 2026-07-07). UID 1000 matches the
# host operator — raw position exports saved to /data/raw land drew-owned
# instead of root-owned (the wart that triggered this pass).
RUN useradd --uid 1000 --create-home kronk
USER kronk

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]
