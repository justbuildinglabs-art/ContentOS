# Stages

This file is the map of the pipeline: what each command reads and writes, where
a run's files live on disk, what every exit code means, and the external
numbers ContentOS was built against. No subagent loads this file. It is here
for whoever is reading the code, the tests, or a run directory and wants to
know what produced a given file.

## Stage table

Every command is `python3 skills/contentos/scripts/contentos.py <command> --project <dir> ...`.
`direct` is one stage with four commands, run in order.

| stage | command | inputs | outputs |
| --- | --- | --- | --- |
| research | `research [--yes] [--estimate-only] [--no-download] [--resume <id>]` | `.contentos/config.json`, the Apify key or `--mock` | `run.json`, `01-reels.json`, `01-profiles.json`, `02-outliers.json`, the downloaded videos and frames |
| frames | `frames --run <id> [--refresh-expired]` | `02-outliers.json`, the downloaded videos | `frames/<shortCode>/`, an updated `02-outliers.json` |
| direct | `direct-prompt --run <id> --shortcode <sc>` | one selected reel's frames and metadata, `creator.md`, the director references | a dispatch prompt on stdout; the subagent writes `03-analyses/<sc>.json` |
| direct | `verify --run <id> --stage direct --shortcode <sc>` | `03-analyses/<sc>.json` | the file coerced in place, `analysis_status` recorded on the reel |
| direct | `synth-prompt --run <id>` | every analysis, `creator.md` | a dispatch prompt on stdout; the subagent writes `03-patterns.md` |
| direct | `rank --run <id> [--mock]` | the selected reels and their valid analyses | `03-briefs.json`, `briefs.md` |
| write | `write-prompt --run <id> --brief B01 [--revision N]`, then `verify --stage write` | the brief, its analysis and frames, `creator.md`, `rules.md`, the writer references | `04-scripts/B01.r<N>.md` |
| qa | `qa-prompt --run <id> --brief B01 [--revision N]`, then `verify --stage qa` | the script, the brief, the analysis, `creator.md`, `rules.md`, `qa-rubric.md` | `05-qa/B01.r<N>.json` |
| report | `report --run <id>` | every stage's output for one run | `report.md` |

## Run directory layout

Creator state lives in the creator's own project, never inside this plugin:

```
<project>/.contentos/
├── creator.md            # who you are, audience profile, brand voice, claims, CTA, payoff moments
├── rules.md              # creator corrections, one per line
├── config.json           # competitors[], thresholds, cost cap, qa_pass_threshold
├── .env                  # optional APIFY_API_TOKEN, must be chmod 600
└── runs/<YYYYMMDD-HHMMSS>/
    ├── run.json                  # stages, timings, cost estimate and actual, apify run ids, warnings
    ├── 01-reels.json  01-profiles.json
    ├── 02-outliers.json          # scored reels, selected top-K, backfill pool, exclusions, video/frames status
    ├── videos/<shortCode>.mp4    # gitignored
    ├── frames/<shortCode>/f01.jpg ... f08.jpg  (+ cover.jpg)
    ├── 03-analyses/<shortCode>.json
    ├── 03-patterns.md            # proven hooks, recurring formats, saturated angles to avoid
    ├── 03-briefs.json  briefs.md
    ├── 04-scripts/<brief-id>.r<N>.md
    ├── 05-qa/<brief-id>.r<N>.json
    └── report.md
```

## Exit codes

Every subcommand returns one of these. 0 is the only success code; the rest
tell the skill (or the creator) what to do next.

| code | meaning |
| --- | --- |
| 0 | ok |
| 1 | subcommand stub, not implemented yet |
| 2 | usage error: bad arguments, no such run, a required file is missing |
| 3 | confirmation required: an estimate was printed, re-run with `--yes` |
| 4 | missing key: no Apify token resolved and not `--mock` |
| 5 | upstream failure: Apify run or dataset fetch failed |
| 6 | cost cap: the estimate is over `apify_max_charge_usd` |
| 7 | verification failed: a subagent's file did not pass `verify` |

## Verified constants

- Apify actor: `apify~instagram-scraper`. Written `apify/instagram-scraper` in
  plain prose; Apify's REST paths swap the slash for a tilde, so the code
  constant carries the tilde form.
- Runs path: `/acts/apify~instagram-scraper/runs`, appended to
  `https://api.apify.com/v2`.
- Price: $0.0027 per result, on the free tier.
- Free credit: $5 a month.
- Fixture handles: `sproutapp`, `habitlab`, `dailywins`, `ghostaccount`. The
  mock research run, and its tests, are built around these four: the first
  three are healthy sample accounts, the fourth simulates an account Apify
  cannot find.
- Claude vision limits for frames: JPEG, PNG, GIF, or WebP only, never video.
  Frames are resized to a 1024 px long edge before the director reads them.

Live smoke: Apify runs path verified: pending
Live smoke: the /plugin setting reaches the Bash tool: no. Claude Code passes
plugin settings to hooks only, so the SessionStart hook in `hooks/hooks.json`
runs `contentos.py sync-plugin-key`, which copies the key to
`~/.config/contentos/plugin-option.env` (chmod 600). `diagnose` reports it as
`apify_source: "plugin_option"`. The full `diagnose` key map is `apify`,
`apify_source`, `project_dir`, `creator_md`, `rules_md`, `config_json`,
`python`, `ffmpeg`, `skill_root`, `env_perms_ok`, `warnings`, `mock`, and
`apify_live`.
Live smoke: Instagram CDN status for an expired signed URL: pending (expected 403)

## Sources

Written from `docs/superpowers/specs/2026-09-16-contentos-design.md`: the
Architecture, External API facts, Stage specs, and Global Constraints
sections. The exact constants above are cross-checked against the code that
implements them, not restated from memory.
