# ContentOS

ContentOS is a Claude Code plugin for creators. It takes the Instagram Reels
that outperformed in your niche, and in any niche, and turns them into scripts
in your voice, in four stages. **Research** scrapes the accounts you name, works
out each one's normal reach, and picks the posts that beat it. **Direct** looks
at the keyframes of each winner and writes down why it worked and the part you
can reuse, then ranks the ideas into briefs. **Write** turns the briefs you pick
into full scripts: two hooks, a beats table with visual cues, a payoff, two
CTAs, and a caption. **QA** reviews each script against your own creator profile
and a scoring rubric, sends one revision back to the writer when it needs one,
and flags anything that needs you.

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
   `/plugin` lets you change it later. ContentOS reads it when a session
   starts, so after you set or change it, quit and reopen Claude Code.
   Claude Code keeps the setting in your system keychain, but only hooks can
   see it there, so a small startup hook copies it to
   `~/.config/contentos/plugin-option.env` (chmod 600) for the ContentOS
   scripts to read. Clearing the setting deletes that copy at the next start.
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

Claude asks about you, your viewer, what you promote if anything, your claims,
your voice, and the accounts to research: 3 to 8 handles in your niche, plus up
to 5 accounts from any niche whose formats travel. It writes
`.contentos/creator.md` and leaves the deeper sections marked TODO for you to
fill in by hand. Those sections are what make the scripts sound like you, so
they are worth an hour.

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

## How a run looks

Mara is invented, and so is everything she posts. She makes reels about AI
coding tools for indie developers. Here is one pass through the pipeline.

She runs `/contentos setup` once and answers: who she is and what she makes,
one sentence a stranger would get, her 3 to 5 pillars, who watches and their
number one frustration in their own words, what she promotes (a free
newsletter, with the objection "I already get too many"), the on-screen payoffs
she can show (a terminal running a skill end to end, a before and after diff),
what she may and may not claim, her voice, a default CTA, five handles in her
niche, and three format accounts from other niches. That writes
`.contentos/creator.md` and `.contentos/config.json`.

`/contentos run` estimates the Apify cost for eight accounts. She confirms.
Research scrapes the last 30 reels per account, works out each account's median
plays, and flags the outliers by their ratio to that median. A reel at 9x its
own account's median, from a dev creator with 12k followers, ranks above a big
account's average reel. The top 20 outliers are downloaded and eight keyframes
are pulled from each.

The director looks at each outlier's frames, caption, and comments, and writes
an analysis: hook type, format, why it worked, the transferable mechanism, how
Mara would adapt it in her niche, and the obvious copy to avoid. Niche reels are
scored on topic fit. Format reels are scored on how cleanly the mechanism
transfers to one of her pillars. A synthesis pass writes `03-patterns.md`: the
proven hooks across the set, the recurring formats, the saturated angles, and a
language bank taken from the comments.

Ranking writes `briefs.md`. B01 reads: source, a dev creator's "one command
replaced my whole morning routine" screen demo at 11x baseline; adaptation, the
same reveal structure with a coding-agent skill running end to end as the
payoff; hypothesis, "If we ... using the ... hook, we expect above-baseline
plays because ...". At most two of the five briefs come from format accounts.
Mara picks three.

For each brief the writer produces a shoot-ready script: two hooks under 25
words that take different approaches, a beats table with visual cues and
on-screen text, a payoff section naming the exact moment on screen, two CTAs
(the newsletter ask that answers "too many already", and an open loop), a
caption with hashtags, and production notes. QA scores it on the rubric, checks
it is not a clone of the source, that every claim traces back to `creator.md`,
and that the payoff is real. One revision round is allowed. `report.md` lists
the scripts, their scores, and the `[NEED NUMBER]` placeholders Mara fills in
with real figures.

## What a run costs

About $0.67 of Apify credit for eight accounts at the default 30 reels each,
and about $0.84 for ten. Both are one scrape of the reels plus one of the
profiles: accounts times reels times $0.0027, plus $0.0027 per account. Nothing
else in ContentOS costs money. The estimate is printed before anything is
spent, and a run stops on its own if the estimate goes over
`apify_max_charge_usd` in your config.

## Commands

| command | what it does |
| --- | --- |
| `/contentos setup` | Interview, then write `creator.md`, `config.json`, and `rules.md` |
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
├── creator.md            # your profile: pillars, audience, voice, what you promote, payoff moments, claims, CTA, accounts
├── rules.md              # your corrections, one per line
├── config.json           # niche accounts, format accounts, thresholds, cost cap, QA threshold
├── .env                  # optional Apify key, chmod 600
├── examples/<format>.md  # optional, your own gold script per format
└── runs/<YYYYMMDD-HHMMSS>/
    ├── run.json  01-reels.json  01-profiles.json  02-outliers.json
    ├── videos/  frames/         # gitignored, they get large
    ├── prompts/                 # gitignored, the exact prompt each subagent got
    ├── 03-analyses/  03-patterns.md  03-briefs.json  briefs.md
    ├── 04-scripts/<brief-id>.r<N>.md
    ├── 05-qa/<brief-id>.r<N>.json
    └── report.md
```

`prompts/` is worth knowing about when an output surprises you. It holds the
exact text each subagent was given, so you can read what it saw before you
decide the model got it wrong.

`setup` writes a `.contentos/.gitignore` that keeps your key file, the
downloaded videos, the keyframes, the dispatch prompts, and your setup answers
out of version control.

## Getting better output over time

Two files do this, and both are yours.

**`.contentos/rules.md`.** One correction per line. When you tell Claude that a
word is wrong, or that you never want a certain hook, it offers to add the line
here. Every later writer and reviewer prompt carries those lines as binding
style rules, so a correction you make once does not come back.

**`.contentos/examples/`.** One gold script per format. The writer reads the
example that matches the brief's format and imitates its shape. ContentOS ships
two of its own, `talking_head` and `screen_demo`. Add your own best script as
`.contentos/examples/<format>.md` in the same shape and every later script in
that format follows yours instead. Keep it in your project, not in the plugin:
the plugin's own `references/examples/` is replaced every time the plugin
updates, so anything you put there is lost.

Between them, the second run is better than the first, and the tenth is better
than the second.

## Privacy

The scraped reels, the keyframes, the briefs, and the scripts all stay on your
machine, under your own project directory. ContentOS uploads nothing. The one
outside service it calls is Apify, and all Apify sees is the list of handles you
asked it to scrape. Claude reads your creator profile and your run files the
same way it reads any other file you open in Claude Code, and the three
subagents that analyze, write, and review have no network access at all.

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
verdict rules, and the outlier research are ContentOS decisions.

## Development

```
python3 -m unittest discover -s tests -v
```

Standard library only, no network in tests, no real keys anywhere in the
repository. `CLAUDE.md` has the rules for working in this codebase, and
`docs/superpowers/specs/` has the design spec.

## License

License: TBD.
