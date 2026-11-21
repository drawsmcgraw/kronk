# Runbook: bump the SearXNG image pin

When: the weekly canary alert says the pin is behind, or search quality
fails. About 10 minutes. No HA/MA restart. Since sprint M18 the monthly
patch run (`docs/features/upgrade-cadence.md`) applies patch releases; this
runbook is for a bump by hand.

1. Newest tag: `curl -s "https://hub.docker.com/v2/repositories/searxng/searxng/tags?page_size=10&ordering=last_updated" | jq -r '.results[].name'`
   — take the newest `YYYY.M.D-<sha>`.
2. Edit `docker-compose.yml`: `image: searxng/searxng:<tag>`; add the date to
   the "Pinned" comment.
3. Commit the pin, tag, then `scripts/deploy_tag.sh plan <tag>` and
   `scripts/deploy_tag.sh deploy <tag>` (never `docker compose up` in the
   working tree: production runs from a release directory); wait for
   `(healthy)` in `docker ps`.
4. `docker logs --since 2m kronk-searxng-1 | grep -iE "warn|error"` — settings
   incompatibilities show up here (a renamed key, a removed engine).
5. Canary: `scripts/searxng_canary.sh`. Must PASS (≥1 engine, ≥2/5 relevant per
   query; a single engine is a warning).
6. Bing check (disabled 2026-10-05 for first-word junk; re-enabled 2026-11-09
   on the 2026.11.9 build, disabled again 2026-11-14 when the junk returned):
   from the tool_service container,
   `httpx.get("http://searxng:8080/search", params={"q": "Last Week Tonight
   John Oliver latest episode topic", "engines": "bing", "format": "json"})`
   — the top results must mention the show. Re-enable only if they do, and
   re-check a few days later: on 2026-11-09 bing passed, five days later it
   was first-word junk again. To flip it, set `disabled` for bing in
   `searxng/settings.yml` (+ `.example`; files are owned by uid 977: edit with
   `docker exec -u 0 -i kronk-searxng-1 python3 -`), `docker compose restart
   searxng`, canary again.
7. Probe sparingly: rapid queries CAPTCHA-suspend duckduckgo for 30 min.
8. Revert path: `scripts/deploy_tag.sh deploy <the previous tag>`.
9. Record: the tag in the compose comment; anything odd in `docs/incidents/`.
