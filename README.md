# ContentOS

ContentOS is a Claude Code plugin for app founders. It turns competitor
Instagram Reels into Reel scripts for your own app, in four stages. **Research**
scrapes the accounts you name, works out each one's normal reach, and picks the
posts that beat it. **Direct** looks at the keyframes of each winner and writes
down why it worked and the part you can reuse, then ranks the ideas into briefs.
**Write** turns the briefs you pick into full scripts: two hooks, a beats table
with visual cues, a demo moment, two CTAs, and a caption. **QA** reviews each
script against your own product facts and a scoring rubric, sends one revision
back to the writer when it needs one, and flags anything that needs you.

Everything runs on your machine. You type `/contentos run` and read the report.

## Requirements

- Claude Code, with plugins enabled.
- Python 3.9 or newer. Apple's system `python3` is fine. No pip packages.
- An Apify account for the Instagram scraping. The free tier includes $5 of
  credit a month, which is plenty for a few runs.
- ffmpeg, optional. With it, the director reads eight keyframes per reel.
  Without it, it sees the cover image only and rates its own confidence lower.
  `brew install ffmpeg` on a Mac.

## Install

```
/plugin marketplace add <owner>/ContentOS
/plugin install contentos@contentos
```

Replace `<owner>` with the GitHub owner of this repository. You can also point
the first command at a local checkout path instead.

## The Apify key

ContentOS reads `APIFY_API_TOKEN`. Get one at apify.com, under Settings then
Integrations. It can live in any of four places, and the first one found wins:

1. The `APIFY_API_TOKEN` environment variable.
2. The plugin setting. Claude Code asks for it when you install ContentOS, and
   `/plugin` lets you change it later.
3. `<your project>/.contentos/.env`, one line: `APIFY_API_TOKEN=apify_api_...`
   Then `chmod 600 .contentos/.env`, and ContentOS will warn you if you forget.
4. `~/.config/contentos/.env`, the same line, shared by every project.

Run `/contentos diagnose` any time to see which one was found. It never prints
the key itself.

## First run

In your project directory:

```
/contentos setup
```

Claude asks about your product, your audience, your claims, your voice, and the
three to eight competitor accounts to research. It writes `.contentos/product.md`
and leaves the deeper sections marked TODO for you to fill in by hand. Those
sections are what make the scripts sound like you, so they are worth an hour.

```
/contentos run
```

That runs all four stages. It shows you the Apify estimate first and waits for
your yes before it spends anything.

To try the whole pipeline with no key and no network, on the committed sample
data:

```
/contentos run --mock --auto
```

## What a run costs

About $0.84 of Apify credit for ten competitor accounts at the default 30 reels
each, which is one scrape of the reels plus one of the profiles. Nothing else in
ContentOS costs money. The estimate is printed before anything is spent, and a
run stops on its own if the estimate goes over `apify_max_charge_usd` in your
config.

## Commands

| command | what it does |
| --- | --- |
| `/contentos setup` | Interview, then write `product.md`, `config.json`, and `rules.md` |
| `/contentos run` | All four stages, end to end. `--auto` skips the brief question, `--yes` skips the spend question, `--mock` uses fixtures |
| `/contentos research` | Stage 1 only: scrape, score, select, download, keyframes |
| `/contentos direct` | Stage 2 only: analyze each selected reel, find the patterns, rank the briefs |
| `/contentos write B01 B02` | Stage 3 only: write the named briefs |
| `/contentos qa B01` | Stage 4 only: review, one revision, then the report |
| `/contentos status` | Where the latest run got to |
| `/contentos diagnose` | Key, Python, ffmpeg, and your project state |

## Where files go

Your state lives in your own project, never in the plugin:

```
<your project>/.contentos/
├── product.md            # your product facts, audience, voice, claims, CTA
├── rules.md              # your corrections, one per line
├── config.json           # competitors, thresholds, cost cap, QA threshold
├── .env                  # optional Apify key, chmod 600
└── runs/<YYYYMMDD-HHMMSS>/
    ├── run.json  01-reels.json  01-profiles.json  02-outliers.json
    ├── videos/  frames/         # gitignored, they get large
    ├── 03-analyses/  03-patterns.md  03-briefs.json  briefs.md
    ├── 04-scripts/<brief-id>.r<N>.md
    ├── 05-qa/<brief-id>.r<N>.json
    └── report.md
```

`setup` writes a `.contentos/.gitignore` that keeps your key file, the
downloaded videos, the keyframes, and your setup answers out of version
control.

## Getting better output over time

Two files do this, and both are yours.

**`.contentos/rules.md`.** One correction per line. When you tell Claude that a
word is wrong, or that you never want a certain hook, it offers to add the line
here. Every later writer and reviewer prompt carries those lines as binding
style rules, so a correction you make once does not come back.

**`skills/contentos/references/examples/`.** One gold script per format. The
writer reads the example that matches the brief's format and imitates its shape.
ContentOS ships two, `talking_head` and `screen_demo`. Add your own best script
as `<format>.md` in the same shape and every later script in that format follows
it.

Between them, the second run is better than the first, and the tenth is better
than the second.

## Privacy

The scraped reels, the keyframes, the briefs, and the scripts all stay on your
machine, under your own project directory. ContentOS uploads nothing. The one
outside service it calls is Apify, and all Apify sees is the list of competitor
handles you asked it to scrape. Claude reads your product facts and your run
files the same way it reads any other file you open in Claude Code, and the
three subagents that analyze, write, and review have no network access at all.

## Credits

The writing, editing, and review rules are distilled from three guides by
Ray Cfu:

- "The 20-Agent Script System: How to Build an AI Writing Pipeline That Actually
  Produces Good Work"
- "How to Build a 5-Agent Content Pipeline That Writes, Edits, and Publishes for
  You"
- "How to Make Your Writing Not Sound Like AI"

They are paraphrased as checklists in `skills/contentos/references/`, each file
with a `Sources` footer naming what it drew on. The scoring formulas, the QA
verdict rules, and the competitor outlier research are ContentOS decisions.

## Development

```
python3 -m unittest discover -s tests -v
```

Standard library only, no network in tests, no real keys anywhere in the
repository. `CLAUDE.md` has the rules for working in this codebase, and
`docs/superpowers/specs/` has the design spec.

## License

License: TBD.
