# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
