FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates age && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install uv

COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt

# Pre-download embedding model so first query is instant
RUN python -c "from fastembed import TextEmbedding; list(TextEmbedding('BAAI/bge-small-en-v1.5').embed(['warmup']))"

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004"]
