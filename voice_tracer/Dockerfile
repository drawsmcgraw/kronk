FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY requirements.txt requirements.lock ./
RUN uv pip install --system --require-hashes -r requirements.lock

COPY main.py convert.py marks.py ./
# The spoken command forms routing reads (sprint M12): one file, shared with
# the orchestrator. The compose service passes ./orchestrator as the build
# context named "orchestrator" (additional_contexts).
COPY --from=orchestrator command_forms.py ./

# Non-root (tenet 10). UID 1000 matches the host operator so the state file
# under the ./data bind mount lands drew-owned.
RUN useradd --uid 1000 --create-home kronk
USER kronk

CMD ["python", "-u", "main.py"]
