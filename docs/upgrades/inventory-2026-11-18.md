# Upgrade inventory, 2026-11-18 (sprint M18, check first)

Read-only: pins from the repo, what runs from `docker inspect`, `docker exec` (version commands), `systemctl --user` and the Whisper venv's dist-info; newest releases from anonymous GETs with the client name `upgrade-check/1.0`. Nothing was pulled, installed or changed. The build report (`docs/sprint/M18-build.md`) discusses each row.

Inventory read 2026-11-18T04:39+00:00 by `python3 scripts/upgrades.py inventory`.

| Component | Rule | Pin | Written in | Running | Newest minor (rule) | Newest of any kind | Source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| tempo | auto | 3.1.1 | docker-compose.telemetry.yml:51 | 3.1.1 (64e2fbd56d9c, same id as the tag) | none newer | 3.1.1 | github:grafana/tempo |
| grafana | auto | 13.2.4 | docker-compose.telemetry.yml:63 | 13.2.4 (dbbc38ea2110, same id as the tag) | 13.2.6 | 13.3.1 | github:grafana/grafana |
| wyoming-piper | auto | 2.2.2 | docker-compose.yml:208 | 2.2.2 (c874e4a04657, same id as the tag) | none newer | 2.5.4 | github:rhasspy/wyoming-piper |
| searxng | auto | 2026.11.9-57ff753ad | docker-compose.yml:31 | 2026.11.9-57ff753ad (287af61f1a13, same id as the tag) | 2026.11.17-b99cf7496 | 2026.11.17-b99cf7496 | dockerhub:searxng/searxng |
| nginx | auto | 1.29-alpine | docker-compose.yml:12 | 1.29.8 | none newer | 1.31.8-alpine | dockerhub:library/nginx |
| litellm | auto | v1.88.1 | docker-compose.yml:155 | v1.88.1 (c98c9395c56a, same id as the tag) | v1.88.6 | v1.110.0 | github:BerriAI/litellm |
| python-base-orchestrator | auto | 3.12-slim | orchestrator/Dockerfile:1 | 3.12.13 | 3.12.16-slim | 3.12.16-slim | dockerhub:library/python |
| python-base-tool_service | auto | 3.12-slim | tool_service/Dockerfile:1 | 3.12.13 | 3.12.16-slim | 3.12.16-slim | dockerhub:library/python |
| python-base-voice_tracer | auto | 3.12-slim | voice_tracer/Dockerfile:1 | 3.12.13 | 3.12.16-slim | 3.12.16-slim | dockerhub:library/python |
| uv | report | uv | orchestrator/Dockerfile:13 | 0.13.9 | - | 0.13.9 | pypi:uv |
| git-orchestrator | none | 1:2.47.* | orchestrator/Dockerfile:10 | 2.47.3 | no source | no source | - |
| homeassistant | auto | 2026.5.4 | docker-compose.ha.yml:41 | 2026.5.4 (ceb1202133a5, same id as the tag) | none newer | 2026.11.2 | github:home-assistant/core |
| lock-orchestrator | report | 41 packages | orchestrator/requirements.lock | matches the lock | 12 patch release(s) | 20 newer minor/major | pypi |
| lock-tool_service | report | 55 packages | tool_service/requirements.lock | matches the lock | 13 patch release(s) | 24 newer minor/major | pypi |
| lock-voice_tracer | report | 22 packages | voice_tracer/requirements.lock | matches the lock | 5 patch release(s) | 2 newer minor/major | pypi |
| llama.cpp | none | per unit (ExecStart build directory) | systemd/llama-gemma4-e4b.service | llama-gemma3-4b: llama-b9585-vulkan (active); llama-gemma4-e4b: llama-b9611-vulkan (active); llama-devstral-q4: llama-b9611-vulkan (active); llama-devstral: llama-gfx1151-rocm7 (inactive); llama-mistral-nemo: llama-gfx1151-rocm7 (inactive); llama-bonsai: bonsai-gfx1151-rocm7 (inactive); llama-talkie: talkie-gfx1151-rocm7 (inactive) | n/a (nothing is minor) | b12498 | github:ggml-org/llama.cpp |
| wyoming-faster-whisper | none | none in the repo | ~/services/wyoming-whisper/.venv | 3.1.0 | none newer | 3.9.0 | pypi:wyoming-faster-whisper |
| ma-blueprint | none | 20250404 | ha/blueprints/upstream/mass_assist_blueprint_en.yaml | HA (installed copy, see ha/blueprints) | n/a | 20250404 | raw.githubusercontent.com (upstream file) |
| music-assistant | excluded | 2.11.0b5-kronk.9 | docker-compose.ma.yml:53 | 2.11.0b5-kronk.9 (847b7601461f, same id as the tag) | none newer | upstream stable 2.10.12, pre-release 2.11.0.dev2026111803 | github:music-assistant/server |
| bgutil-pot-provider | excluded | 2.0.0 | docker-compose.ma.yml:82 | 2.0.0 (ed86b6fdd5e4, same id as the tag) | 2.0.2 | 2.0.2 | github:Brainicism/bgutil-ytdlp-pot-provider |

Notes:

- nginx: pin not exact; newest compared with what runs (1.29.8-alpine)
- python-base-orchestrator: pin not exact; newest compared with what runs (3.12.13-slim)
- python-base-tool_service: pin not exact; newest compared with what runs (3.12.13-slim)
- python-base-voice_tracer: pin not exact; newest compared with what runs (3.12.13-slim)
- lock-orchestrator: opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-orchestrator: opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-orchestrator: opentelemetry-instrumentation==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-orchestrator: opentelemetry-instrumentation-asgi==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-orchestrator: opentelemetry-instrumentation-fastapi==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-orchestrator: opentelemetry-instrumentation-httpx==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-orchestrator: opentelemetry-semantic-conventions==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-orchestrator: opentelemetry-util-http==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-tool_service: opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-tool_service: opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-tool_service: opentelemetry-instrumentation==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-tool_service: opentelemetry-instrumentation-asgi==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-tool_service: opentelemetry-instrumentation-fastapi==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-tool_service: opentelemetry-instrumentation-httpx==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-tool_service: opentelemetry-semantic-conventions==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-tool_service: opentelemetry-util-http==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-tool_service: python-dateutil==2.9.0.post0: not an exact X.Y[.Z] version; skipped
- lock-voice_tracer: opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-voice_tracer: opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
- lock-voice_tracer: opentelemetry-semantic-conventions==0.66b1: not an exact X.Y[.Z] version; skipped

lock-orchestrator: patch releases available: charset-normalizer 3.4.7 -> 3.4.9, click 8.3.2 -> 8.3.3, fastapi 0.115.0 -> 0.115.14, googleapis-common-protos 1.75.0 -> 1.75.5, opentelemetry-api 1.45.1 -> 1.45.2, opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2, opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2, opentelemetry-proto 1.45.1 -> 1.45.2, opentelemetry-sdk 1.45.1 -> 1.45.2, python-multipart 0.0.9 -> 0.0.33, pyyaml 6.0.2 -> 6.0.3, typing-inspection 0.4.2 -> 0.4.4.
Newer minor/major: annotated-types 0.7.0 -> 0.8.0, anyio 4.13.0 -> 4.16.0, certifi 2026.2.25 -> 2026.10.23, charset-normalizer 3.4.7 -> 3.5.2, click 8.3.2 -> 8.5.1, fastapi 0.115.0 -> 0.163.2, googleapis-common-protos 1.75.0 -> 1.76.0, httpx 0.27.2 -> 0.28.1, idna 3.11 -> 3.20, packaging 26.2 -> 26.3, protobuf 6.33.6 -> 7.36.2, pydantic 2.12.5 -> 2.14.3, pydantic-core 2.41.5 -> 2.50.4, pypdf 4.3.1 -> 7.1.0, python-dotenv 1.0.1 -> 1.2.4, starlette 0.38.6 -> 1.10.0, typing-extensions 4.15.0 -> 4.16.0, urllib3 2.7.0 -> 2.8.0, uvicorn 0.30.6 -> 0.54.0, wrapt 1.17.3 -> 2.5.2.

lock-tool_service: patch releases available: charset-normalizer 3.4.7 -> 3.4.9, click 8.3.2 -> 8.3.3, dateparser 1.4.0 -> 1.4.3, fastapi 0.115.0 -> 0.115.14, lxml 6.1.1 -> 6.1.3, lxml-html-clean 0.4.5 -> 0.4.6, opentelemetry-api 1.45.1 -> 1.45.2, opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2, opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2, opentelemetry-proto 1.45.1 -> 1.45.2, opentelemetry-sdk 1.45.1 -> 1.45.2, soupsieve 2.8.3 -> 2.8.4, typing-inspection 0.4.2 -> 0.4.4.
Newer minor/major: annotated-types 0.7.0 -> 0.8.0, anyio 4.13.0 -> 4.16.0, beautifulsoup4 4.12.3 -> 4.15.0, certifi 2026.2.25 -> 2026.10.23, charset-normalizer 3.4.7 -> 3.5.2, click 8.3.2 -> 8.5.1, courlan 1.4.0 -> 1.5.0, dateparser 1.4.0 -> 1.5.1, fastapi 0.115.0 -> 0.163.2, htmldate 1.10.0 -> 1.11.0, httpx 0.27.2 -> 0.28.1, idna 3.11 -> 3.20, pydantic 2.12.5 -> 2.14.3, pydantic-core 2.41.5 -> 2.50.4, pytz 2026.2 -> 2026.6, regex 2026.5.9 -> 2026.11.18, soupsieve 2.8.3 -> 4.1.1, starlette 0.38.6 -> 1.10.0, trafilatura 2.1.0 -> 2.3.1, typing-extensions 4.15.0 -> 4.16.0, tzlocal 5.3.1 -> 5.4.5, urllib3 2.7.0 -> 2.8.0, uvicorn 0.30.6 -> 0.54.0, websockets 16.0 -> 17.4.

lock-voice_tracer: patch releases available: opentelemetry-api 1.45.1 -> 1.45.2, opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2, opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2, opentelemetry-proto 1.45.1 -> 1.45.2, opentelemetry-sdk 1.45.1 -> 1.45.2.
Newer minor/major: httpx 0.27.2 -> 0.28.1, websockets 16.0 -> 17.4.

Hosts contacted: api.github.com, ghcr.io, hub.docker.com, pypi.org, raw.githubusercontent.com (83 requests).

