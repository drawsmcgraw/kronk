FROM python:3.12-slim

WORKDIR /app

# git: the /settings page commits each save in the live config directory
# (sprint M21 step 2; docs/sprint/M21-step2-build.md argues git in the image
# against a host-side helper). Debian 13's package, held to the 2.47 series:
# a security update within it is taken at the next build, a new series only
# by editing this line.
RUN apt-get update && apt-get install -y --no-install-recommends 'git=1:2.47.*' \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY requirements.txt requirements.lock ./
RUN uv pip install --system --require-hashes -r requirements.lock

COPY *.py ./
COPY static/ static/
# The voice corpus, from the repo's tests/ at the same commit (compose
# additional context `corpus`): the /settings page's routing check before a
# lead-in or music-policy save (sprint M21 step 2, settings_corpus.py).
COPY --from=corpus voice_corpus.jsonl /app/corpus/

# Non-root (tenet 10, operator directive 2026-07-07). UID 1000 matches the
# host operator, so writes to the ./data bind mount land drew-owned.
RUN useradd --uid 1000 --create-home kronk
USER kronk

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
