---
name: yt-summary:setup
description: Set up the yt-summary plugin by installing its Python dependencies, or install them if another yt-summary skill fails with a missing-dependency or missing yt-dlp error
---

# Setup yt-summary Plugin

The user is setting up the yt-summary plugin, or one of its skills has failed
because a dependency is missing.

## Task

Install the plugin's Python dependencies:

1. Run `pip install -r {plugin_dir}/requirements.txt`, where `{plugin_dir}` is the
   plugin's installation directory. If that path isn't resolvable, fall back to
   `pip install youtube-transcript-api yt-dlp`.
2. Wait for the installation to finish.
3. Confirm success and tell the user the skills are ready: `summarize`,
   `transcript`, `ask`, `ingest`, `digest`, and `mass-ingest`.

## Notes

- **`ffmpeg` and `ffprobe`** are only needed by `yt-summary:ingest-slides`; every
  other skill works without them. They are not `pip`-installable — point the
  user at their platform's package manager or https://ffmpeg.org/download.html,
  and confirm with `ffmpeg -version` / `ffprobe -version`.
- **`youtube-transcript-api`** is the primary transcript source.
- **`yt-dlp`** is the fallback transcript source and how title/channel/date and
  channel listings are resolved. `ingest` and `mass-ingest` need it;
  `transcript`, `summarize`, and `ask` work without it as long as the API path
  succeeds.
- Optional: installing `curl_cffi` alongside `yt-dlp` enables
  `--impersonate`, which helps get past YouTube's throttling on the caption
  endpoint. The scripts detect it and use it if present. Verify with
  `yt-dlp --list-impersonate-targets`.
- **Behind a TLS-inspecting proxy** (corporate MITM, some AV suites): install with
  `pip install --cert <path-to-ca.pem> -r requirements.txt`.

  For fetching, appending the proxy's root CA to the certifi bundle
  (`python -m certifi` prints the path) covers three of the plugin's network
  paths in one step — `youtube_transcript_api`, and both of yt-dlp's own paths
  (metadata/listings, and caption downloads whether or not yt-dlp is
  impersonating a browser) — because all three default to that same bundle. A
  fourth path (the channel RSS feed and the oembed metadata fallback, both
  plain `urllib`) checks the OS's own certificate store instead, so the CA
  needs to land there too, or in `SSL_CERT_FILE`. `REQUESTS_CA_BUNDLE` /
  `CURL_CA_BUNDLE` reach `youtube_transcript_api` and an impersonated caption
  download, but not yt-dlp's own metadata/listing calls; see the `ingest` skill
  for the full table.

  Never disable certificate verification to work around this.
