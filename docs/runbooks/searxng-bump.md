# Runbook: bump the SearXNG image pin

When: the weekly canary alert says the pin is behind, or search quality
fails, or on update day (ROADMAP 9). About 10 minutes. No HA/MA restart.

1. Newest tag: `curl -s "https://hub.docker.com/v2/repositories/searxng/searxng/tags?page_size=10&ordering=last_updated" | jq -r '.results[].name'`
   — take the newest `YYYY.M.D-<sha>`.
2. Edit `docker-compose.yml`: `image: searxng/searxng:<tag>`; add the date to
   the "Pinned" comment.
3. `docker compose pull searxng && docker compose up -d searxng`; wait for
   `(healthy)` in `docker ps`.
4. `docker logs --since 2m kronk-searxng-1 | grep -iE "warn|error"` — settings
   incompatibilities show up here (a renamed key, a removed engine).
5. Canary: `scripts/searxng_canary.sh`. Must PASS (≥2 engines, ≥3/5 relevant).
6. Bing re-test (disabled since 2026-10-05 for first-word junk): from the
   tool_service container, `httpx.get("http://searxng:8080/search",
   params={"q": "Last Week Tonight John Oliver latest episode topic",
   "engines": "bing", "format": "json"})` — if the top results now mention
   the show, re-enable bing in `searxng/settings.yml` (+ `.example`; files are
   owned by uid 977: edit with `docker exec -u 0 -i kronk-searxng-1 python3 -`),
   `docker compose restart searxng`, canary again.
7. Probe sparingly: rapid queries CAPTCHA-suspend duckduckgo for 30 min.
8. Revert path: put the previous tag back, `up -d searxng`.
9. Record: the tag in the compose comment; anything odd in `docs/incidents/`.
