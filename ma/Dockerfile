# Kronk's Music Assistant image: the stock release plus our patched Pandora
# provider package (MA_LOCAL_PANDORA_FEATURES_PLAN.md, option 2).
#
# Build context = the fork/clone checkout (ma/build.sh passes it). The hash
# guard refuses to build on any base whose Pandora files are not the ones the
# patch was written against, so a base bump that forgets the rebase fails
# here, not at 7 am in the kitchen.
ARG BASE=ghcr.io/music-assistant/server:2.11.0b2
FROM ${BASE}
ARG SP=/app/venv/lib/python3.14/site-packages/music_assistant/providers/pandora
ARG FORK_REV=unknown
RUN cd $SP && printf '%s\n' \
  '5ce725904292a8490621dde2e7dbaf5e6a2eadddeabc5f6a28a99c8776ed9226  provider.py' \
  '8bfac3d5340d5b27b18d2922e13c4b0c94daf49c33ea1268a4d7b179dc9aae17  constants.py' \
  '7333f539580396f81d558b67ab2a1f8e2c9e678ef1985289bea284bd44f15b85  __init__.py' \
  'c968117766b71c3e3c050220aa058cccb3e30e39424b6a3858605258fba94981  fragments.py' \
  'a0a8ec09f9be0e0723e215450177bfd798707bde5281d938658e33294876e9ae  helpers.py' \
  | sha256sum -c -
COPY music_assistant/providers/pandora/ $SP/
RUN /app/venv/bin/python -m compileall -q $SP
LABEL io.kronk.ma.patch="pandora: feedback (+ create_station)" io.kronk.ma.fork_rev="${FORK_REV}"
