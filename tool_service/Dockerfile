FROM python:3.12-slim

# openssh-client: the /magicmirror routes shell out to ssh (forced-command
# key) to reach the MagicMirror Pi. See docs/plans/MAGICMIRROR_PLAN.md.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates graphviz openssh-client && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install uv

COPY requirements.txt requirements.lock ./
RUN uv pip install --system --require-hashes -r requirements.lock

COPY config_files.py config_loader.py corpus_check.py main.py music_decide.py music_resolve.py music_shadow.py netguard.py news.py ops.py settings_info.py solar.py taste.py telemetry.py ./
# The voice corpus and the Music Assistant snapshot, from the repo's tests/
# at the same commit (compose additional context `corpus`): the /settings
# page's corpus check (corpus_check.py) runs them offline before a
# music-policy save (sprint M21 step 2).
COPY --from=corpus voice_corpus.jsonl fixtures/music_snapshot.json /app/corpus/

# Non-root (tenet 10, operator directive 2026-07-07). UID 1000 matches the
# host operator: ./data writes land drew-owned, and the read-only MagicMirror
# SSH key (mode 600, drew-owned) stays readable. --create-home matters here —
# ssh wants a HOME even with BatchMode + explicit UserKnownHostsFile.
RUN useradd --uid 1000 --create-home kronk
USER kronk

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
