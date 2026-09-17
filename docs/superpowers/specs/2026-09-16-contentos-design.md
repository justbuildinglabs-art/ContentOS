# ContentOS — Design Spec (v1)

Approved 2026-09-16. The implementation task list lives in the plan file; this spec is the binding authority for every task.

## Context

Leslie (Just Building Labs) wants a Content Operating System: a four-agent pipeline that turns competitors' outlier short-form videos into vetted, ready-to-shoot scripts. The four agents are competitor research (Apify), a content director that filters viral ideas, a script writer, and a QA reviewer.

Decisions made during brainstorming on 2026-09-16:

- **Product for other app founders**, shipped as a Claude Code plugin installed from a GitHub repo. Founders bring one key, `APIFY_API_TOKEN`. Every LLM step (director, writer, QA) runs as a Claude subagent on the founder's Claude Code plan, on whatever model they have selected. No Gemini, no Anthropic key.
- **Instagram Reels first.** Competitors are given as account handles.
- **Runtime: Claude Code skill + subagents.** Deterministic stages (scrape, score, download, keyframes, rank) are Python 3 stdlib-only scripts. No agent frameworks (wiki: `frameworks-are-fluff`, `model-agnostic-codebase`).
- **The director looks at keyframes, not video.** Claude accepts images, not video (verified against the vision docs on 2026-09-16), so a script pulls 6–8 keyframes per reel with ffmpeg when installed, else the cover image. Reels burn captions into frames, so hook text and structure survive; beat timestamps and spoken-line transcripts are the known loss versus a video model.
- **Three Ray Cfu guides shape the prompts** (read from Google Drive on 2026-09-16): "How to Make Your Writing Not Sound Like AI", "5-Agent Content Pipeline", and "The 20-Agent Script System". They are distilled into `references/*.md` files that the subagent prompts load, with paraphrased rules and source footers, never long verbatim passages, because the plugin is redistributed.
- The repo `/Users/lesliezhang/git/ContentOS` is empty (no commits, no files).

Wiki grounding (2ndBrain, cite in the spec): `vsc-framework` supplies the director's rubric (viral proof from small accounts, scalable to 100+ variants, convertible = product-intent comments). `copy-viral-formats` gives QA its "change 10–20%, keep the structure" test. `hypothesis-tagged-experimentation` means every brief carries a hypothesis. `ai-content-commoditization` says taste and safety review stay scarce, so the founder picks briefs and QA is strict. `spytalk` is the research stage's analog.

## What the guides contribute (and where it lands)

| Guide idea | Where it lands |
|---|---|
| Strategist returns a brief, not content: angle, hook, subpoints, "Avoid this: the angle everyone else is taking" | Director analysis gains `emotion_lead` and `avoid` (the obvious copy everyone will make); a set-level `03-patterns.md` synthesis names proven hooks, recurring formats, and saturated angles across the run |
| Handoff protocol: "take the above as input, execute your role, do not re-run the previous stage" | Every dispatch prompt opens with a HANDOFF block and closes with the `WROTE <path>` sentinel |
| Hook writer: four approaches (bold claim, pain call-out, contrarian, story opener), under 25 words, draft → diagnosis → redraft | Writer produces a primary and backup hook using two different approaches, runs the diagnosis loop internally, keeps each under 25 words |
| Body: 3–4 beats (problem, solution, differentiator, proof), every line earns its place, hard word budget, show don't describe, write from evidence | Beats table skeleton per format in `formats.md`; word budget per format (≈ 2.5 spoken words per second); writer must cite `product.md` for every claim |
| CTA: direct ask plus open loop, under 20 words, address the #1 objection, urgency only if genuine | Script has a primary and backup CTA; objection comes from the audience profile in `product.md` |
| Managers score on named 1–10 dimensions and fail anything below the bar | QA returns scored dimensions for hook, body, and CTA, plus the compliance checks; pass threshold is configurable (default 8, the guides' 10 is available) because the loop allows one revision before a human |
| Filler detection (advance the argument, essential information, emotional response), hedges, habitual transitions; character budget as a hard ceiling | QA `filler_cut_list` and `length_check` (target ± 10 %, over is a fail) |
| Novelty and intensity per line, strongest and weakest lines, one-watch test, cringe check, spoken-flow check, confidence 1–10 | QA `strongest_line`, `weakest_lines`, `one_watch_test`, `spoken_flow`, `cringe`, `confidence` |
| Assembler markers `[VISUAL CUE]`, `[EMPHASIS]`, `[PAUSE]`, read time | Beats table columns and production notes; `verify` computes read time from word count |
| AI tells: uniform sentence length, throat-clearing openers, banned vocabulary, em-dash pileups, relentless positivity, vague quantities; fixes: vary length, contractions, textured numbers, `[NEED NUMBER]` instead of inventing | `scripting.md` voice rules; writer writes `[NEED NUMBER]` placeholders rather than fabricating; QA `ai_tells` check; `verify` surfaces placeholders to the founder as to-dos, not failures |
| Audience profile (person, pain in their words, tried and failed, fear, desire, top objections, proof that flips them) and brand voice guide (3 adjectives on and off, sentence rules, 10 on-brand and 10 off-limits words, rhythm, sample sentences) | `product-template.md` sections; `setup` asks the short version and leaves the rest as headings to fill |
| Correction rule, example rule, voice rule | `.contentos/rules.md` (founder-owned, appended to writer and QA prompts), `references/examples/<format>.md` gold scripts, voice lives in `product.md` |
| Research across YouTube, Reddit, X for a language bank | Out of scope for v1 (research is competitor outliers); listed under Later |

Deliberate deviations, stated in the reference files: hooks may be questions when the outlier data favors them (the 5-agent guide bans question hooks); the QA bar is 8 not 10 by default; the 20-agent research and strategy phases collapse into the director because ContentOS starts from observed outliers, not from a topic.

## Architecture

```
ContentOS/
├── .claude-plugin/plugin.json          # name contentos, version 0.1.0, userConfig: APIFY_API_TOKEN (sensitive)
├── .claude-plugin/marketplace.json     # name contentos, plugins:[{name:"contentos", source:"./"}]
├── skills/contentos/
│   ├── SKILL.md                        # /contentos orchestrator (user-invoked only)
│   ├── references/                     # loaded by the subagent prompts; guides are distilled here
│   │   ├── hooks.md  formats.md  scripting.md  qa-rubric.md  scoring.md  stages.md  product-template.md
│   │   └── examples/<format>.md        # gold scripts (at least talking_head and screen_demo in v1)
│   └── scripts/
│       ├── contentos.py                # CLI: diagnose|setup|research|frames|direct-prompt|synth-prompt|rank|write-prompt|qa-prompt|verify|report|status
│       ├── lib/{env,store,http,apify,instagram,outliers,video,frames,director,research,agents,report}.py
│       └── schemas/{analysis,qa}.schema.json
├── agents/content-director.md          # Claude subagent, tools: Read, Write
├── agents/script-writer.md             # Claude subagent, tools: Read, Write
├── agents/qa-reviewer.md               # Claude subagent, tools: Read, Write
├── fixtures/                           # apify samples, tiny mp4 + frames, sample analysis/patterns/script/qa
├── tests/                              # stdlib unittest, no network ever
├── docs/superpowers/specs/2026-09-16-contentos-design.md
├── README.md  CLAUDE.md  CHANGELOG.md  .gitignore
```

Per-founder state lives in their project, never in the plugin:

```
<project>/.contentos/
├── product.md            # product facts, audience profile, brand voice, allowed/forbidden claims, CTA, demo moments
├── rules.md              # founder corrections, one per line, appended to writer and QA prompts (correction rule)
├── config.json           # competitors[], thresholds, cost cap, qa_pass_threshold
├── .env                  # optional APIFY_API_TOKEN, must be chmod 600
└── runs/<YYYYMMDD-HHMMSS>/
    ├── run.json                  # stages, timings, cost estimate + actual, apify run ids, warnings
    ├── 01-reels.json  01-profiles.json
    ├── 02-outliers.json          # scored reels, selected top-K, backfill pool, exclusions, video/frames status
    ├── videos/<shortCode>.mp4    # gitignored
    ├── frames/<shortCode>/f01.jpg … f08.jpg  (+ cover.jpg)
    ├── 03-analyses/<shortCode>.json
    ├── 03-patterns.md            # set-level synthesis: proven hooks, recurring formats, saturated angles to avoid
    ├── 03-briefs.json  briefs.md
    ├── 04-scripts/<brief-id>.r<N>.md
    ├── 05-qa/<brief-id>.r<N>.json
    └── report.md
```

Data flow: `research` (Apify → normalize → per-account baselines → outlier selection → download top-K → keyframes) → per reel: `content-director` subagent → `verify` → one `content-director` synthesis dispatch → `rank` (deterministic scoring → ranked briefs) → founder picks briefs → per brief: `script-writer` → `verify` → `qa-reviewer` → `verify` → at most one revision → `report`.

Conventions verified on this machine: plugin manifests as in `superpowers` and `last30days`; agent frontmatter as in `claude-ads/agents/*.md` (`tools` comma-separated string, `maxTurns`); subagents dispatched as `subagent_type: "contentos:<name>"`; key precedence and `--diagnose`/`--mock` patterns from `~/.claude/plugins/marketplaces/last30days-skill/scripts/lib/env.py`; outputs under `.<plugin>/runs/<id>/` as in `claude-ads`. Target Python 3.9 syntax (founders' system Python), no pip dependencies. ffmpeg is optional at runtime (keyframes) and only required to build the test fixture once.

## External API facts (verified 2026-09-16)

**Apify** actor `apify/instagram-scraper`, $0.0027 per result on the free tier, $5 free monthly credit.
- Reels: `{"directUrls":["https://www.instagram.com/<handle>/"],"resultsType":"reels","resultsLimit":N,"onlyPostsNewerThan":"365 days","skipPinnedPosts":true}`. Items carry `shortCode, url, caption, hashtags, mentions, ownerUsername, timestamp, likesCount (-1 = hidden), commentsCount, videoPlayCount, videoViewCount, videoDuration, videoUrl (CDN, expires), displayUrl (cover jpg), productType ("clips"), isPinned, latestComments[≤10], musicInfo`.
- Profiles: same actor, `"resultsType":"details"` → `username, followersCount, postsCount, verified, private`. Result types cannot be mixed, so two runs.
- REST: `POST https://api.apify.com/v2/acts/apify~instagram-scraper/runs` (body = input; query `maxTotalChargeUsd`, `maxItems`, `timeout`, `waitForFinish≤60`), header `Authorization: Bearer <token>`. Poll `GET /v2/actor-runs/{id}` until `SUCCEEDED` (statuses: READY, RUNNING, SUCCEEDED, FAILED, TIMING-OUT, TIMED-OUT, ABORTING, ABORTED). Items: `GET /v2/datasets/{defaultDatasetId}/items?clean=true&format=json&limit=1000&offset=`. The sync endpoint 408s at 300 s, so never use it. Keep the path in one constant and confirm in the live smoke (docs also show `/v2/actors/`).

**Claude vision (for the director subagent):** JPEG/PNG/GIF/WebP only, no video. Images cost `⌈w/28⌉×⌈h/28⌉` visual tokens, so frames are resized to a 1024 px long edge (≈ 800 tokens each; 8 frames ≈ 6.5K tokens per reel). Subagents read frames with the Read tool, which renders images.

## Stage specs

### Config defaults (`store.load_config` merges over `.contentos/config.json`)
`competitors[]`, `lookback_days 90`, `baseline_lookback_days 365`, `reels_per_account 30`, `min_reels_for_median 8`, `outlier_threshold 3.0`, `min_plays 5000`, `top_k_videos 20`, `backfill_pool 10`, `max_per_account 4`, `small_account_followers 50000`, `apify_max_charge_usd 3.0`, `apify_timeout_s 900`, `poll_interval_s 5`, `max_video_mb 40`, `max_video_seconds 180`, `frames_per_reel 8`, `frame_long_edge_px 1024`, `briefs 5`, `parallel_agents 3`, `qa_pass_threshold 8`, `length_tolerance 0.10`, `video_source "cdn"`.

### Stage 1 — research (`contentos.py research [--yes] [--estimate-only] [--no-download] [--resume <run-id>]`)
1. Estimate cost = accounts × reels_per_account × $0.0027 + accounts × $0.0027. Over `apify_max_charge_usd` → exit 6. Without `--yes` print the estimate JSON and exit 3 (the skill confirms with the founder, then re-runs with `--yes`).
2. Start the reels run and the details run with `maxTotalChargeUsd`, `maxItems`, `timeout`; record run ids in `run.json` so `--resume` can re-poll after a Bash timeout. Poll every `poll_interval_s`.
3. Normalize to the canonical Reel: `{shortCode, url, ownerUsername, timestamp, caption, hashtags, mentions, plays, plays_source, likes (null when -1), comments, duration_s, videoUrl, displayUrl, isPinned, latestComments, musicInfo}`. `plays` prefers `videoPlayCount`, then `videoViewCount`, else null (excluded from medians and ranking). Record per-account status: ok | private | not_found | empty | error.
4. Baselines per account from the last N reels over 365 days: median of `plays`. Fewer than `min_reels_for_median` → blend with the pooled median and mark `baseline_confidence: low`; fewer than 3 → `none`, excluded with an `exclusion_reason`. Accounts with no plays at all fall back to a likes baseline (flagged).
5. Score every reel: `outlier_ratio = plays / max(median, 1)`, `reach_ratio = plays / followers` (null without followers), `engagement_rate = (likes or 0 + comments) / plays`, `small_account_proof = followers < small_account_followers and outlier_ratio ≥ outlier_threshold`, `viral_proof = clamp(2.5 · log2(max(outlier_ratio, 1)), 0, 10)` (+1 with small-account proof; capped at 6 when confidence is low; 0 when none). Ratio 3 → 4.0, 8 → 7.5, 16 → 10.
6. Select: only reels inside `lookback_days` with `plays ≥ min_plays`, ranked by `outlier_ratio` (tie-break outlier_ratio, then shortCode), at most `max_per_account`, top `top_k_videos` as `selected` plus the next `backfill_pool` as `backfill`.
7. Download `selected` videos and cover images immediately (CDN URLs expire fastest right after scraping): browser User-Agent + `Referer: https://www.instagram.com/`, 3 retries, abort over `max_video_mb`, status per video (ok | too_large | expired | blocked | failed). Failed ones are replaced from `backfill`. Warn once if more than half fail and point at the `apify/instagram-reel-scraper` `downloadedVideo` add-on ($0.02/MB) as a later `video_source` option.
8. Keyframes (`lib/frames.py`, also `contentos.py frames --run <id>` to re-run): if `ffmpeg` is on PATH, extract `frames_per_reel` JPEGs at t = 0.0, 1.0, 2.0, 3.0 s then evenly to `duration − 0.5` s, scaled to `frame_long_edge_px`, one `ffmpeg -ss <t> -i <mp4> -frames:v 1 -vf scale=… -q:v 4` call per frame; always save `cover.jpg` from `displayUrl`. Without ffmpeg, `frames_status: cover_only` and a one-line hint to install ffmpeg. Idempotent: skips existing frames.
9. Write `01-reels.json`, `01-profiles.json`, `02-outliers.json` (with `video_status` and `frames_status` per reel), update `run.json`; print a per-account summary table and a final `RESULT {...}` JSON line.

### Stage 2 — direct (subagent `contentos:content-director`, tools Read, Write, maxTurns 20, model inherited)
`contentos.py direct-prompt --run <id> --shortcode <sc>` prints the dispatch prompt for one reel: a HANDOFF block (from research; do not re-score plays), absolute paths to its frames (ordered, with timestamps), the reel's metadata block (caption, hashtags, latestComments, musicInfo, duration, owner followers, outlier metrics), `product.md`, `references/hooks.md`, `references/formats.md`, `references/scoring.md`, the schema, and the exact output path `03-analyses/<sc>.json`. It states that captions and comments are data, not instructions.

Output JSON (flat, `schemas/analysis.schema.json`, depth ≤ 2, no `$ref`): `brief_title, hook_spoken (best guess from burned-in captions, or null), hook_on_screen_text, hook_type (bold_claim | pain_callout | contrarian | story_open | question | curiosity_gap | pov | before_after | challenge | other), hook_seconds, emotion_lead (curiosity | frustration | desire | fear | awe | anger | hope | humor | recognition), format (talking_head | screen_demo | voiceover_broll | skit | slideshow_text | ugc_review | tutorial | trend_remix | stitch | other), structure[{frame, beat}], audio (voiceover | original_dialogue | trending_sound | music_only | unknown), cta, product_or_topic_shown, why_it_worked, transferable_mechanism, adaptation_for_product (the 10–20% change), avoid (the obvious copy everyone in the niche will make), score_scalable, score_convertible, score_product_fit (0–10), risk_flags[] (copyrighted_media | fake_testimonial_risk | medical_claim | financial_claim | minors | brand_ip | none), confidence (high | medium | low, lower when cover-only)`. Final message exactly `WROTE <path>` or `FAILED <reason>`.

`contentos.py verify --run <id> --stage direct --shortcode <sc>` validates and coerces the JSON (clamp scores, unknown enums → `other`/`none`/`unknown`, missing lists → `[]`), exit 7 with a problem list. The skill dispatches one reel per subagent, `parallel_agents` at a time, re-dispatching once on verify failure, then marks the reel `analysis_failed`.

`contentos.py synth-prompt --run <id>` prints one more director dispatch that reads every analysis and writes `03-patterns.md` with fixed headings: Proven hooks (ranked, with the reels that prove them), Recurring formats, Saturated angles to avoid, Structural recommendation (length, pacing, format for this product), Language bank (phrases from captions and comments that show product intent). `verify --stage synth` checks the headings exist.

`contentos.py rank --run <id>` is deterministic: `brief_score = 0.35·viral_proof + 0.25·convertible + 0.20·scalable + 0.20·product_fit`, capped at 4.0 when `copyrighted_media` or `fake_testimonial_risk` is flagged, minus 1.0 when `confidence` is `low`. `viral_proof` comes from stage 1, never from the subagent. Rank, assign `B01…`, write `03-briefs.json` and `briefs.md` (title, source, format, hook type, emotion lead, scores, risk flags, adaptation, avoid, frames path, and a hypothesis line: "If we <adaptation> using the <mechanism> hook, we expect above-baseline plays because <why_it_worked>").

### Stage 3 — write (subagent `contentos:script-writer`, tools Read, Write, maxTurns 15, model inherited)
`contentos.py write-prompt --run <id> --brief B01 [--revision 1]` prints the dispatch prompt: HANDOFF block, absolute paths to the brief, the analysis, its frames, `03-patterns.md`, `product.md`, `rules.md` (if non-empty), `references/hooks.md`, `formats.md`, `scripting.md`, the matching `examples/<format>.md` if present, the prior script and QA JSON on revision 1, the word budget for the format, and the exact output path `04-scripts/B01.r<N>.md`.

Contract: one file, frontmatter `brief_id, format, target_length_s, word_budget, hypothesis, source_shortcode, revision`, sections:
- `## Hook`: primary and backup, each labeled with its approach (two different approaches), spoken line under 25 words plus on-screen text; the mechanism must match the brief, the wording must not copy the source.
- `## Beats`: table `t | [VISUAL CUE] | spoken / VO | on-screen text`, ≥ 3 rows, following the format skeleton (problem → solution → differentiator → proof where length allows); `[EMPHASIS]` and `[PAUSE]` markers allowed in the spoken column.
- `## Demo moment`: the on-screen product moment, taken from `product.md` Demo moments.
- `## CTA`: primary (direct ask, addresses the #1 objection from the audience profile) and backup (open loop), each under 20 words, urgency only if genuine.
- `## Caption`: caption plus 5–8 hashtags.
- `## Production notes`: audio choice, shot list, text style, estimated read time.
- `## What changed vs source`: the 10–20% adaptation and what was kept.
Rules from the guides: execute the brief without changing the hook mechanism; every claim must exist in `product.md` (Core features, Allowed claims, Proof assets); write `[NEED NUMBER]` rather than invent a statistic; no testimonials unless listed under Proof assets; vary sentence length, use contractions, no banned vocabulary, no em dashes, no throat-clearing, at most one hedge; run the draft → diagnosis → redraft loop internally and deliver only the final; on revision 1 apply the editing pass to the QA issues and change nothing else. Final message exactly `WROTE <path>` or `FAILED <reason>`.

### Stage 4 — qa (subagent `contentos:qa-reviewer`, tools Read, Write, maxTurns 12)
`contentos.py qa-prompt --run <id> --brief B01 [--revision N]` prints the dispatch prompt (script, brief, analysis, `03-patterns.md`, `product.md`, `rules.md`, `references/formats.md`, `qa-rubric.md`, `qa.schema.json`); output `05-qa/B01.r<N>.json`:
```
{brief_id, revision, verdict (pass | revise | reject),
 checks{hook_first_3s, hook_matches_brief, demo_present, consistent_with_product, no_fabricated_claims,
        no_fake_testimonial, no_restricted_claims, not_a_clone, cta_present, brand_voice, ai_tells: pass | fail | na},
 scores{hook_scroll_stop, hook_specificity, hook_emotional_charge, hook_voice_match, hook_differentiation,
        body_argument_clarity, body_emotional_arc, body_proof_density, body_pacing,
        cta_action_clarity, cta_friction, cta_momentum, cta_urgency: 1–10},
 length_check{word_count, word_budget, within_tolerance},
 filler_cut_list[{line, reason}], placeholders[] (every [NEED …]),
 strongest_line, weakest_lines[≤3], one_watch_test, spoken_flow_issues[], cringe_flags[],
 issues[{check_or_score, severity (blocker | major | minor), detail, fix}], summary, confidence 1–10}
```
Verdict rules: any compliance check failed on `no_fabricated_claims`, `no_fake_testimonial`, `no_restricted_claims`, or `consistent_with_product` → `reject` unless the fix is a single line → `revise`; any other failed check, any score below `qa_pass_threshold`, over-budget length, or confidence below `qa_pass_threshold` → `revise`; otherwise `pass`. Placeholders never fail a script; they are listed in the report for the founder. `verify --stage qa` enforces the schema and that a `pass` verdict has no failed checks and no score below threshold.

`contentos.py verify --run <id> --stage write|qa --brief B01` validates the file the subagent claims to have written (exit 7 with a list of problems). For scripts it also computes word count and read time and flags placeholders. Loop: writer → verify → QA → verify → if revise, one revision → QA → final. A second revise verdict marks the brief `needs_human`. `contentos.py report --run <id>` renders `report.md` (per-brief verdict, scores, placeholders to fill, paths, cost, timings, needs_human list); `status` prints the machine-readable state.

### Reference files (task 0b distills the guides into these)

| File | Loaded by | Content |
|---|---|---|
| `hooks.md` | director, writer | taxonomy matching `hook_type` with one-line definitions and two examples each; the four approaches the writer must choose from; under-25-words rule; first-3-seconds rules; note that question hooks are allowed when the outlier data favors them |
| `formats.md` | director, writer, QA | per format: target length, word budget (≈ 2.5 words/s), beat skeleton, on-screen text density, CTA placement, audio norms; for all ten `format` values |
| `scripting.md` | writer | execute-the-brief rules, beats skeleton, show-don't-describe, evidence-only claims and `[NEED NUMBER]`, CTA rules, caption rules, the AI-tells list and fixes, the internal draft → diagnosis → redraft loop, the revision editing pass |
| `qa-rubric.md` | QA | every check and score with its 10 / 7 / 4 anchors, filler questions, length rule, verdict rules, what goes in `one_watch_test` |
| `scoring.md` | director (context), docs | the deterministic formulas above |
| `examples/<format>.md` | writer | one gold script per format in the exact output contract; v1 ships talking_head and screen_demo, generated in task 0b and hand-edited |
| `product-template.md` | setup | Product, One-liner, Audience profile (who specifically, #1 frustration in their words, what they tried, the fear, the desired outcome, top 3 objections, proof that flips them), Core features, Demo moments, Allowed claims, Forbidden claims, Proof assets, Brand voice (3 adjectives on, 3 off, sentence rules, 10 on-brand words, 10 off-limits words, 3 sample sentences), CTA, Hashtag seeds, Competitors |
| `stages.md` | docs | stage table, run-dir layout, exit codes, verified API constants |

Each file ends with a `Sources` footer naming the guide it draws on. Rules are paraphrased checklists; no passage over two sentences is copied.

### Skill `skills/contentos/SKILL.md`
- Frontmatter: `name: contentos`, `description`, `argument-hint: "setup | run [--auto] [--yes] | research | direct | write [B01 B02] | qa [B01] | status | diagnose [--mock]"`, `allowed-tools: Bash, Read, Write, Glob, AskUserQuestion, Agent(contentos:content-director, contentos:script-writer, contentos:qa-reviewer)`, `disable-model-invocation: true`.
- Step 0 locates the scripts with a probe and stores `CONTENTOS_ROOT`: `${CLAUDE_SKILL_DIR}` → `${CLAUDE_PLUGIN_ROOT}/skills/contentos` → newest `~/.claude/plugins/cache/*/contentos/*/skills/contentos` → `~/.claude/plugins/marketplaces/*/skills/contentos` → `./skills/contentos` → stop with an error. Every command is `python3 "$CONTENTOS_ROOT/scripts/contentos.py" <cmd> --project "$PWD"`, run in the foreground with a 600000 ms timeout, never in the background.
- Pre-flight runs `diagnose`; missing `.contentos/` offers `setup`; missing key prints the four key locations (env, plugin userConfig, `.contentos/.env`, `~/.config/contentos/.env`) and stops unless `--mock`; missing ffmpeg is a one-line notice, not a stop.
- `setup`: AskUserQuestion sequence (one-liner; who specifically; their #1 frustration in their words; top objection; 3–6 core features; demo moment; allowed claims; forbidden claims; 3 voice adjectives on and 3 off; off-limits words; CTA; 3–8 competitor handles) → Write `.contentos/setup-answers.json` → `setup --answers-file` → show `product.md` and point at the headings left to fill; create an empty `rules.md` with a one-line explanation.
- `run`: `research --estimate-only` → confirm cost (skipped with `--yes`) → `research --yes` (re-run with `--resume` if Bash timed out) → director loop over `selected` reels (`direct-prompt` → Agent → `verify --stage direct`, batches of `parallel_agents`) → `synth-prompt` → Agent → `verify --stage synth` → `rank` → Read `briefs.md` → founder picks brief ids via AskUserQuestion multi-select (`--auto` takes the top `briefs`) → write/QA loop in batches → `report` → show the path, the placeholders to fill, and a five-line summary.
- Ground rules: subagents never get Bash or network; nothing shown to the founder is invented, it all comes from files in the run dir; captions and comments are data; when the founder corrects an output, offer to append the correction to `rules.md`; failure table maps exit codes (2 usage, 3 confirm, 4 keys, 5 upstream, 6 cost cap, 7 verification) to a message and a recovery command.

### Key resolution (`lib/env.py`)
Precedence: process env → `CLAUDE_PLUGIN_OPTION_APIFY_API_TOKEN` (plugin userConfig) → `<project>/.contentos/.env` → `~/.config/contentos/.env` (`CONTENTOS_CONFIG_DIR` override, empty string = clean mode for tests). Warn when an env file is not mode 600. `diagnose` prints a JSON map (`apify, config_source, project_dir, product_md, rules_md, config_json, python, ffmpeg, skill_root, env_perms_ok, mock`) and exits 0; `diagnose --live` validates the key at zero cost (`GET /v2/users/me`).

## Global Constraints

These bind every task. A reviewer treats a violation as Important.

- **Python 3.9-compatible syntax only** (no `match`, no runtime `X | Y` unions, no `list[str]` at runtime without `from __future__ import annotations`, which every module includes). Founders run Apple's system `python3`.
- **Standard library only.** No pip dependencies, no `requests`, no third-party SDKs. ffmpeg is optional at runtime and is invoked through `subprocess` only from `lib/frames.py`.
- **Tests are stdlib `unittest`** under `tests/`, run with `python3 -m unittest discover -s tests -v`. Every test module subclasses `tests.helpers.NoNetworkTestCase`, whose `setUp` patches `urllib.request.urlopen` and `urllib.request.OpenerDirector.open` to raise. Test output must be pristine (no warnings, no stray prints).
- **TDD per task:** write the failing tests first, watch them fail, then implement. The implementer's report must show the RED run and the GREEN run.
- **Layout exactly as in Architecture.** Scripts live under `skills/contentos/scripts/`; `lib/__init__.py` stays empty (no eager imports); one responsibility per module.
- **CLI contract:** `python3 skills/contentos/scripts/contentos.py <subcommand> [--project DIR] [--mock] ...`; `--project` defaults to the current directory. Exit codes: 0 ok, 1 subcommand stub not implemented yet, 2 usage, 3 confirmation required, 4 missing key, 5 upstream failure, 6 cost cap, 7 verification failed. Machine-readable output (JSON, prompts) goes to stdout; progress and warnings to stderr.
- **Run-dir layout and file names exactly as in Architecture.** JSON files are written atomically (temp file in the same directory, then `os.replace`).
- **Config keys and defaults exactly as in "Config defaults".** Formulas exactly as in the stage specs.
- **No network in tests, no real keys anywhere in the repo.** Fixtures use fake URLs and fake tokens.
- **Commits:** small, one task per commit (or a few focused commits per task); every commit message ends with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **Reference prose** (`skills/contentos/references/*.md`): paraphrased checklists with a `## Sources` footer; never copy a passage over two sentences from the guides.
- **Founder-facing text** (README, SKILL.md, agent prompts, reference files): plain language, short sentences, no em dashes.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| CDN video and cover URLs expire, 403, or IP-block | Download at the end of `research`; UA + Referer; 3 retries; backfill pool; `direct --refresh-expired`; one warning pointing at the paid `downloadedVideo` option |
| Founder has no ffmpeg | cover-only mode with `confidence: low` penalty in ranking; README and `diagnose` say how to install it |
| Frames miss the hook when captions are not burned in | first four frames at 0–3 s plus `hook_spoken` allowed to be null; `confidence` field lets `rank` demote guesses |
| Accounts with few reels give unstable medians | 365-day baseline window, pooled-median blending, confidence flag caps `viral_proof`, `< 3` excluded with reason |
| `videoPlayCount` is 0 | fall back to `videoViewCount`, else exclude; account-level likes baseline flagged |
| QA too strict for a one-revision loop | threshold defaults to 8 with the guides' 10 available; placeholders never fail; second revise → `needs_human` with the QA notes attached, not a silent drop |
| QA too lenient | `verify --stage qa` refuses a `pass` that contradicts its own checks or scores; compliance failures always block |
| Cost runaway | Apify `maxTotalChargeUsd`/`maxItems`/`timeout` plus confirmed estimate; `maxTurns` on agents; one revision max; no other paid API |
| `${CLAUDE_SKILL_DIR}` may not reach Bash | probe chain in SKILL.md, `diagnose` echoes `skill_root` |
| Subagents reply instead of writing | `Read, Write` only, exact output path in the prompt, `verify` after every dispatch, one re-dispatch, then `analysis_failed` / `needs_human` |
| Director context bloat | one reel per dispatch, 8 frames at 1024 px (≈ 6.5K tokens), metadata trimmed to the fields listed; synthesis dispatch reads JSON analyses, not frames |
| Bash 10-minute cap | `research --resume`, `frames --run`, `status`-driven loops |
| Founders on Python 3.9 | 3.9 syntax only, `from __future__ import annotations`, README states 3.9+ |
| Prompt injection via captions/comments | director, writer, and QA prompts treat reel text as data |
| Redistributing guide content | references are paraphrased checklists with source footers, no passage over two sentences copied |

## Verification

1. Unit tests: `python3 -m unittest discover -s tests -v` (also once under `/usr/bin/python3` if it is 3.9).
2. Mock end-to-end in the scratchpad: `setup --answers-file fixtures/setup-answers.sample.json --project "$S"` → `research --mock --yes` (copies fixture mp4, cover, frames) → `direct-prompt --shortcode <fixture>` (inspect the prompt) → copy fixture analyses and patterns → `verify --stage direct` and `--stage synth` → `rank --run latest` → `write-prompt --brief B01` → `verify --stage write --brief B01` (exit 7 until a script exists) → `report --run latest`. Expect the full run-dir layout.
3. Live smoke with a real key in `.contentos/.env` (chmod 600): `diagnose --live` → `research` on 2–3 accounts with `--reels-per-account 10 --top-k 3 --yes` (≈ $0.06) → inspect `02-outliers.json`, `videos/`, `frames/` → in Claude Code, dispatch `contentos:content-director` for one reel with the printed prompt and confirm `verify --stage direct` passes → synthesis dispatch → `rank` → read `briefs.md`. Record the verified Apify path constant in `references/stages.md`.
4. Plugin install in a fresh Claude Code session in another directory: `/plugin marketplace add /Users/lesliezhang/git/ContentOS` → `/plugin install contentos@contentos` → `/contentos diagnose --mock` (exercises the root probe from the cache path) → `/agents` lists the three subagents → `/contentos run --mock --auto` completes and writes `report.md`. Repeat from GitHub once pushed.
5. Manual quality check: run the director on two real reels and compare its `format`/`hook_type` to your own read; run the writer and QA on a fixture brief; confirm QA rejects a script with an invented feature, flags a fabricated statistic, and lists a `[NEED NUMBER]` placeholder without failing it; confirm a script that copies the source hook verbatim fails `not_a_clone`.

## Cut from v1 (YAGNI) and later

Cut: any video-model backend, SessionStart hook (pre-flight `diagnose` covers it), transcripts (Apify add-on), HTML/PDF report, threaded downloads, ffprobe (duration comes from Apify), cross-run caching, comment sentiment, `addProfileStatistics`, TikTok, per-line novelty and intensity scoring (folded into strongest/weakest lines), separate hook/body/CTA manager agents (folded into one QA pass with scored dimensions).

Later: TikTok via an Apify TikTok actor behind the same Reel schema; hashtag/keyword discovery; Reddit and X voice-of-customer mining into the language bank (the 20-agent research phase); optional Gemini or transcript backend for founders who want spoken-line fidelity (the analysis schema already fits); Airtable or Notion export; results feedback loop (the founder's own reel metrics re-weight `brief_score`, closing the hypothesis loop); hosted version with a shared research cache; `video_source: apify_download`; multi-language scripts; `/contentos rule "…"` helper that appends to `rules.md`.

Outside this repo, from a vault session: log ContentOS under `wiki/domains/products/` in 2ndBrain (the products hub currently has no active products), and consider ingesting the three guides as sources.
