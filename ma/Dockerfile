# Kronk's Music Assistant image: the stock release plus our patched Pandora
# provider package (MA_LOCAL_PANDORA_FEATURES_PLAN.md, option 2).
#
# Build context = the fork/clone checkout (ma/build.sh passes it). The hash
# guard refuses to build on any base whose Pandora files are not the ones the
# patch was written against, so a base bump that forgets the rebase fails
# here, not at 7 am in the kitchen.
ARG BASE=ghcr.io/music-assistant/server:2.11.0b5
FROM ${BASE}
ARG SP=/app/venv/lib/python3.14/site-packages/music_assistant/providers/pandora
ARG FORK_REV=unknown
# Every file in the stock package: the COPY below replaces the whole directory.
RUN cd $SP && printf '%s\n' \
  '6571b6b541491abbc1f09b74d96d3b4ec78a313057e88cc2599f1cffe81540bd  provider.py' \
  '8bfac3d5340d5b27b18d2922e13c4b0c94daf49c33ea1268a4d7b179dc9aae17  constants.py' \
  '7333f539580396f81d558b67ab2a1f8e2c9e678ef1985289bea284bd44f15b85  __init__.py' \
  'c968117766b71c3e3c050220aa058cccb3e30e39424b6a3858605258fba94981  fragments.py' \
  'a0a8ec09f9be0e0723e215450177bfd798707bde5281d938658e33294876e9ae  helpers.py' \
  'fbbe798b70b86f1b0aee022f5cf2d63e375dc3828a01912de62c982e7e567377  setup_flow.py' \
  '26af6c3f9ebf79a4a92177938f58b9f4094954ac970d9a864bb997c97c2b2b6d  manifest.json' \
  '409f9de915c96161f7238f1bedd5f9a907a8e414225f9fec1ca0aece1f50cccc  strings.json' \
  '8f4e1de040424f11b066aa30e53ff51c638abb6ff09acdf835768f5b14d066e3  icon.svg' \
  '205b02d35689b3637400ee33cf35d40772de9eb3472db41b1d3f956cb95488da  icon_monochrome.svg' \
  | sha256sum -c -
COPY music_assistant/providers/pandora/ $SP/
RUN /app/venv/bin/python -m compileall -q $SP
LABEL io.kronk.ma.patch="pandora: set_favorite -> station thumbs (FAVORITE_TRACKS_EDIT); seed radios + create on play + delete (LIBRARY_RADIOS_EDIT)" io.kronk.ma.fork_rev="${FORK_REV}"
