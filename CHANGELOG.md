# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
