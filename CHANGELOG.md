# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-09-23

### Changed

- Discovery now looks for creators who are actually successful in your
  niche. A creator passes when they have 10,000 or more followers
  (`discover_min_followers`, was 1000), post a reel at least every 2 weeks
  (`discover_post_every_days`, default 14, any of 7 to 90 days), and at
  least 1 in 4 of their recent reels reach 5,000 views
  (`discover_min_views`). Passing creators come in two tiers, Established
  (50,000 or more followers) and Rising (10,000 to 50,000), ranked by the
  views 1 in 4 of their reels reach. The small-account bonus and the
  one-reel ranking are gone.
- Where creators come from: Claude's web search is required when it has
  one, Instagram keyword search finds the top reels for your phrases, your
  current watch list leads to Instagram's similar accounts, and hashtags are
  a fallback. The profile search that returned tiny business accounts is
  gone.
- Every account gets a quick profile check, then the best 20
  (`discover_shortlist`) get a full check of their last 15 reels.
  `.contentos/discovery.json` is now version 2, with tiers, the numbers
  behind each creator, what was left out and why, and the reels beating
  their creators' own average this month.
- Your search phrases also mark which accounts are in your niche. A phrase
  counts when all its words appear in a bio, caption, or hashtag, in any
  order or form, so `ai agents for business` matches "I help businesses
  automate with AI agents". Small words like "for" and "your" are skipped.
- The paid partnership filter also catches a brand's own partner tag, such
  as #higgsfieldpartner, #lovablepartner, or #replitpartners. Generic tags
  like #gympartner, #twitchpartner, or #studentambassador still pass.
- When discovery runs out of time, the accounts it did not reach say "not
  checked in time" or "not measured in time" instead of "not found", and it
  asks you to run it again.
- Discovery costs about $1 to $1.30 once at the defaults.

### Added

- A control panel. `/contentos discover` opens a small page in your browser,
  served from your own computer, where you set the bar, watch the cost
  change, run discovery, read the evidence for each creator, and tick the
  ones to keep. Saving adds them to your watch list. Chat still works when
  you prefer it.
- Reloading the control panel keeps your search: it picks up a run that is
  still going or shows its results, so a reload never starts a new paid run.
- `contentos.py ui` serves the panel, and `discover` gains `--seeds`.

### Upgrading

- Projects set up before 0.6.0 keep `discover_min_followers: 1000` in
  `.contentos/config.json`. Change it to 10000, or delete the line.

## [0.5.0] - 2026-09-19

### Added

- Creator discovery. `/contentos discover`, and "find them for me" during
  setup, finds accounts in your niche so you do not have to know your
  competitors. It scrapes recent reels under the hashtags you agree on, runs a
  profile search per keyword, and takes handles from a web search when Claude
  has one. A final profile check drops any account that does not exist or is
  private, so an invented handle never reaches your list. Accounts are ranked
  by their best reel and how many of your hashtags they showed up under, with
  a bonus for small accounts. Results land in `.contentos/discovery.json`. It
  works before setup, costs about $0.30 to $0.80 once, and goes through the
  same estimate, confirmation, and cost cap as research. New settings:
  `discover_reels_per_hashtag` (30), `discover_candidates` (25), and
  `discover_min_followers` (1000, applied to keyword and web finds only).
- `contentos.py accounts --competitors a,b,c [--format-accounts d,e]` replaces
  the account lists in `config.json` and `creator.md` of a project that is
  already set up, and touches nothing else. Typing handles by hand in setup
  works as before.
- Paid partnership filter. Research excludes a reel as `paid_partnership` when
  Instagram's own paid partnership label is set on the scraped reel, or when
  its caption or hashtags disclose it (`#ad`, `#sponsored`, "sponsored by",
  "in partnership with", and the like). `#adventure` and `#advice` never
  match. The content-director also flags a reel that discloses on screen or
  out loud, and `rank` leaves it out and lists it in `report.md` under
  "Skipped: paid partnership". The reel still counts toward its account's
  baseline. Set `exclude_paid_partnerships` to `false` to keep them.

### Changed

- The analysis schema gains an optional `paid_partnership` object. Older
  analyses without it still validate and rank.
- `03-briefs.json` gains `skipped_paid`, and the `rank` summary gains a
  `skipped_paid` count.

## [0.4.0] - 2026-09-19

### Added

- A weekly ideas list. `rank` now writes up to `briefs` (default 20) ideas per
  run instead of cutting to a handful, each tagged New, Carried over, or
  Format fill.
- Carry-over. An idea you have not picked comes back for up to two more weeks
  (`carry_weeks`, default 2), then expires. `carry_weeks` 0 turns carry-over
  off. `mark --state skipped` stops an idea from coming back. A new ledger,
  `.contentos/ideas.json`, remembers every idea across runs.
- Format fill. The synthesis also writes `03-fill.json` in each run: up to
  `fill_ideas` (default 8) ideas that apply a format that worked this week to
  one of your pillars. They only take slots left after every real idea.
  `fill_ideas` 0 turns format fill off.
- `idea_title`, a short topic-first name for each idea, shown ahead of the
  source account.
- `auto_scripts`: `--auto` now writes the top `auto_scripts` (default 3)
  ideas instead of every brief in the run.
- `briefs.md` opens with `# This week's ideas`, a numbered digest of the
  whole list, before the per-idea sections.

### Changed

- The outlier window is 14 days (`lookback_days`), not 90. A creator runs
  ContentOS once a week, and a 90-day window was hiding how few reels that
  actually covers.
- A new floor, `min_outlier_ratio` (default 2.0), drops reels that do not
  clear twice their account's usual plays, on top of the existing
  `min_plays` floor.
- The per-account cap (`max_per_account`) is now soft: a busy account's
  extra outliers rank after every account's capped reels instead of being
  dropped outright, so a real outlier still beats an empty slot on the list.
- `rank` will not re-rank a run that already has a script or a mark, because
  a re-rank renumbers the briefs and the script or mark would land on the
  wrong idea. `rank --force` re-ranks anyway.
- "What to do next" in `report.md` and `status --text` sums up the ideas with
  no script yet in one line instead of listing each one.
- `report.md`, `report.html`, `status`, and the intake questions title each
  brief by its idea title, so a format fill idea no longer shows its proof
  reel's title.

### Upgrade note

Projects set up before 0.4.0 pin `lookback_days: 90` and `briefs: 5` in
`.contentos/config.json`. Change them to 14 and 20 for the weekly list.
Projects upgrading from 0.3.0 start with an empty ideas ledger, so earlier
unpicked briefs do not carry over.

## [0.3.0] - 2026-09-18

### Added

- Transcripts. Each selected reel is transcribed locally with whisper-cpp when it is installed, or by an opt-in, priced Apify fallback. The director now hears spoken tool names, numbers, and steps. `transcribe` backfills an older run.
- The director records `specifics` (every named tool, repo, product, place, number, or step, with where it was seen or heard) and `steps`, and each brief carries them. A new `references/specificity.md` shows what "specific" means in eight niches.
- `## Inventory` in `creator.md` and a setup question for it, plus `lead_magnet`, which becomes an allowed claim.
- `intake` asks a brief's questions before writing. The orchestrator also writes a fact sheet with a cited source for every public fact, checked by `verify --stage facts`.
- QA scores `body_specificity` and checks `not_generic` (at least `min_specifics` named items, placeholders excluded) and `facts_sourced`. A placeholder no longer earns full proof credit, and `rules.md` outranks the default offer placement.
- A brief stuck at `needs_human` can get one more revision once its intake is answered.
- `report --html` writes a self-contained `report.html`. `status --text` prints a plain summary. `mark` and `history` keep a weekly log, and research skips source reels an earlier run already briefed.

### Changed

- `briefs.md` shows a Bet line and a Why line instead of one run-on hypothesis sentence. `report.md` opens with "What to do next", splits scores at the pass bar, and lists each placeholder with its beat and line.
- Claims now have three tiers: about you (from `creator.md` and the brief's intake), about the world (from the fact sheet or a public specific), and never (Forbidden claims).

## [0.2.1] - 2026-09-18

### Changed

- Adding the Apify key is now a guided, step-by-step task in the README and in the `/contentos` skill. Copy the token from the Apify Console, run one terminal command that asks for it with the paste hidden and saves it with owner-only permissions, then check it with `/contentos diagnose`. The token never passes through a chat, and the skill now tells Claude never to ask for it or write it to a file. The four places the key can live are unchanged.

## [0.2.0] - 2026-09-17

### Changed

- ContentOS is now for any creator, not only app founders. The profile is `creator.md`, with pillars, an audience, a voice, and an optional "What you promote" block; when nothing is promoted, scripts end on a follow, comment, save, or share ask.
- Sources are tagged. `competitors` are accounts in your niche; the new `format_accounts` list holds accounts from any niche whose formats travel. Every reel carries `source_kind`, the director scores niche reels on topic fit and format reels on transferability, and ranking caps format-account briefs at `max_format_briefs` (default 2).
- Renamed for the pivot: analysis fields `topic_shown`, `adaptation`, and `score_fit`; QA checks `payoff_present` and `consistent_with_profile`; the script section `## Payoff`; the prompt heading `## Creator rules`; the `diagnose` key `creator_md`. Nothing had shipped, so there is no migration.
- The gold example scripts, the reference files, the agent prompts, the setup interview, and the README are rewritten for creators, with a worked example of a run.

## [0.1.1] - 2026-09-17

### Fixed

- The Apify key saved with `/plugin` now works. Claude Code only shows plugin settings to hooks, never to the commands ContentOS runs, so the key was never found. A startup hook now copies it to `~/.config/contentos/plugin-option.env` (chmod 600), and clearing the setting deletes that copy. A new or changed key takes effect the next time Claude Code starts.

## [0.1.0] - 2026-09-17

### Added

- Stage 1, research: scrape competitor Instagram Reels with Apify, score each one against its own account's baseline, and select the outliers, with keyframes cut for the ones selected.
- Stage 2, direct: analyze each outlier's keyframes into a brief, synthesize the patterns across the run, and rank the briefs with a deterministic score.
- Stage 3, write: turn a chosen brief into a full script, with two labeled hooks, a beats table with visual cues, a demo moment, two CTAs, and a caption.
- Stage 4, qa: review a script against the product facts and a scoring rubric, send one revision back to the writer when it needs one, and flag anything that still needs a human.
- Three Claude subagents: `content-director`, `script-writer`, and `qa-reviewer`, each with Read and Write only, no Bash and no network.
- The `/contentos` skill: `setup`, `run`, `research`, `direct`, `write`, `qa`, `status`, and `diagnose`, run through a single Python CLI.
- Mock mode: a full pipeline run off committed fixtures, with no Apify key and no network, for a first look and for testing.
- Reference files distilled from three Ray Cfu guides on hooks, formats, scripting, QA, and scoring, each with a sources footer.
- A stdlib `unittest` suite covering every stage, the CLI, the reference files, the agent files, and the plugin's own file layout, plus a full mock end-to-end pipeline test.
