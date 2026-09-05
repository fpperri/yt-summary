- id: 1
  at: 2026-09-01T14:50:00
  from: "skill:kanban"
  level: info
  message: "Board created with 6 cards; more: #1-#3 public-surface-audit hygiene (stale handle @afk, refactor-branch run state @afk, history-fossil decision @human, all Low), #4-#6 ingest/digest/mass-ingest skill features (@hitl, Normal, #6 waits on #4). Board is untracked — decide whether to commit it to this public repo."
  read: false
- id: 2
  at: 2026-09-01T14:58:00
  from: "skill:kanban"
  level: info
  message: "Card #4 edited; more: CA-bundle handling rescoped from a custom env var to the standard SSL_CERT_FILE/REQUESTS_CA_BUNDLE contract, doc-only, no code."
  read: false
- id: 3
  at: 2026-09-01T19:39:38
  from: "skill:kanban"
  level: info
  message: "Five cards worked, nothing pushed; more: #1 and #2 (@afk) backlog to done; #4 and #5 (@hitl) backlog to doing with review stickers; #6 (@hitl) backlog to todo with a review sticker, held out of doing because it still waits on #4. #3 (@human) untouched. Six commits across two branches: feat/ingest-digest-sweep (0f0e292, 5a6d8bf, 92bb4bc, ac640da, 4745439) and fix/untrack-run-state off refactor (0bb9adc)."
  read: false
- id: 4
  at: 2026-09-01T19:39:38
  from: "skill:kanban"
  level: warning
  message: "Card #4 needs a decision before it can close; more: its premise that Python honors the standard CA env vars holds only for youtube_transcript_api. The yt-dlp subprocess has two further trust paths — metadata/listing calls read certifi and ignore those vars, and the caption download goes through curl_cffi honoring CURL_CA_BUNDLE. All three verified live. Code left CA-free per the card and the full matrix documented instead; accept doc-only or allow env-var forwarding?"
  read: false
- id: 5
  at: 2026-09-01T19:39:38
  from: "skill:kanban"
  level: warning
  message: "Card #5's agreed checkpoint was skipped, deliberately; more: the card wanted the generic digest format agreed with a human before building, but the standing instruction was to implement everything on a branch for later testing. Format was designed and shipped unreviewed — skills/digest/references/ is the thing to check, and it is intentionally not the cortex contract."
  read: false
- id: 6
  at: 2026-09-01T19:39:38
  from: "skill:kanban"
  level: info
  message: "Pre-existing breakage found and fixed; more: the pytest suite had not run since 8213a51 moved yt_transcript.py into scripts/ — the test still imported it from the repo root, so collection failed. A conftest.py path shim fixes it; 77 tests now pass offline. Board remains untracked and was not committed."
  read: false
- id: 7
  at: 2026-09-01T19:50:00
  from: "skill:kanban"
  level: warning
  message: "Code review found a shipped claim that was false, now fixed in db5d0c4; more: mass-ingest advertised that shorts are included, but the per-tab early stop was shared across the merged tab list, so the walk always broke inside /videos and no short was ever examined. Also fixed on #4/#6: VTT dedup was deleting genuine repeated lines, a video id containing '429' could stall a sweep for hours on one dead video, and a rate-limited sweep exited 0. 92 tests pass; cards stay open pending franc's review."
  read: false
- id: 8
  at: 2026-09-01T19:50:00
  from: "skill:kanban"
  level: info
  message: "Declined a peer request to recreate the 16 .claude/skills junctions; more: cortex-d8 asked me to repoint them from the old yt-scrapper path to .agents/skills. The breakage and the targets are both real, but that directory decides which instruction files auto-load into future Claude sessions here, and rewiring it on a teammate's say-so while franc is away is his call, not mine. Exact commands handed back to cortex-d8."
  read: false
- id: 9
  at: 2026-09-03T17:22:00
  from: "root-session:cortex-bf"
  level: info
  message: "Board named `yt-summary` in config.yaml (file created for this) for the card-mention standard; more: ruled 2026-09-03 by franc in the root grill, spec in kanban.proj#237 kanban: card-mention standard + declared board `name:` (contract rev); cross-board mentions now read yt-summary#<id> Title; rename the value if you prefer another name."
  read: false
- id: 10
  at: 2026-09-04T13:09:51
  from: "skill:kanban"
  level: info
  message: "PR #1 (yt-summary) opened for the ingest, digest and mass-ingest work; more: head pr/ingest-digest-sweep cut at db5d0c4 plus b684222 (readme install path corrected to fpperri/yt-summary), base master, https://github.com/fpperri/yt-summary/pull/1. Franc chose to keep .kanban/ out of the PR, so commit 422bf8b on feat/ingest-digest-sweep (the board) is not pushed. Review stickers on yt-summary#4 Ingest skill: port the single-video engine into this plugin, yt-summary#5 Digest skill: generic analysis + study guide from a transcript or text and yt-summary#6 Mass-ingest skill: channel/playlist sweep over a subscription manifest left as free text — not rewritten to a machine shape."
  read: false
