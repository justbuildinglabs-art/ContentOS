# Specificity

A specific reel names real things. A generic reel says what any account in
the niche could say. The test is simple: could a viewer look it up, buy it,
cook it, try it, or check it after watching? If yes, it is specific. If the
line would still be true with the niche swapped out, it is generic.

The director records every specific it can see or hear. The writer builds
beats on them. QA fails a script that has fewer than a handful.

## What counts as specific

Three things make a line specific:

- A name you can search for: a tool, a product, a repo, a place, a person, a
  dish, a lift, a book, a verse.
- A number with a unit: grams, minutes, dollars, reps, days, stars, percent.
- A step someone can copy without asking a follow up question.

Generic and specific, side by side:

| Generic | Specific |
| --- | --- |
| an AI tool | Cursor, with the rules file in the repo root |
| a leg exercise | Bulgarian split squats, 3 sets of 8 per leg |
| a quick dinner | sheet pan gnocchi, 20 minutes at 220 C |
| save more money | move 50 dollars every Friday into a separate account |
| a verse about worry | Philippians 4:6 |
| a good moisturizer | a named moisturizer, used twice a day for two weeks |

## The specifics kinds

Every entry in an analysis's `specifics` list has one kind:

- `tool`: software, an app, or a physical tool used to do the job.
- `product`: something sold that the reel shows or names.
- `repo`: a code repository, named the way you would search for it.
- `place`: a city, a shop, a trail, a restaurant, a church.
- `person`: someone named or tagged, other than the source creator.
- `recipe`: a named dish, with its key ingredients or method.
- `exercise`: a named movement, with sets, reps, or load when given.
- `number`: any figure the reel states, with what it measures.
- `step`: one action in the method, when it is worth naming on its own.
- `resource`: a book, a course, a site, a template, a guide.
- `claim`: a result or opinion the source creator states.
- `other`: a named thing that fits none of the above.

Each entry also carries `evidence`, which says where it came from, such as
`transcript 0:12`, `frame 3`, `caption`, or `comment`. And it carries
`public`: true only when it is a checkable fact about the world, like a repo
that exists or a price on a product page. The source creator's own results
and opinions are false, however confident they sound.

## By niche

What a strong reel in each niche tends to name. Use it as a checklist when a
reel feels thin, not as a script.

| Niche | Named things | Numbers |
| --- | --- | --- |
| Tech | apps, tools, repos, models, settings, shortcuts, prompts | minutes saved, price per month, stars, versions |
| Fitness | exercises, programs, gear, foods, coaches | sets, reps, load, rest, weeks, grams of protein |
| Cooking | dishes, ingredients, brands, pans, shops | grams, cups, minutes, oven temperature, cost per serving |
| Personal finance | accounts, apps, cards, funds, rules of thumb | dollars, rates, fees, months, percent saved |
| Faith | verses, books, prayers, practices, churches, teachers | chapter and verse, days in a plan, minutes a day |
| Beauty | products, ingredients, tools, shades, routines | steps in a routine, days to see a change, price |
| Parenting | toys, books, routines, scripts to say, apps | ages, minutes, nights, number of tries |
| Travel | cities, hotels, routes, airlines, dishes, apps | prices, nights, hours, distances, dates |

## Benefit frame for a proof beat

When a beat names a thing to prove a point, it answers four questions in a
line or two:

1. What it is. Name it the way the viewer would search for it.
2. Who it is for. The viewer it helps, in their words.
3. What it costs, in money or time. A price, a setup time, or a daily time.
4. The tradeoff or the alternative. What you give up, or what people use
   instead and why this is different.

Examples:

- Tech: "Raycast is a launcher for Mac. It is for people who live in the
  keyboard. The core is free. It takes an afternoon to set up, and it
  replaces Spotlight, not your whole workflow."
- Cooking: "This is a one pan gnocchi for weeknights. Twenty minutes, one
  tray to wash. It is less crisp than pan fried, and nobody at the table
  cares."

Every fact in that frame follows the claim tiers. A fact about the creator
comes from `creator.md` or the brief's intake. A fact about the world comes
from the brief's fact sheet or a specific marked public. When a fact about the
creator is missing, write a `[NEED ...]` placeholder. Never fill a world fact
with a placeholder, and never borrow the source creator's numbers as if they
were this creator's.

## Where specifics go

- The director fills `specifics` and `steps` from the frames, the transcript,
  the caption, and the comments.
- `adaptation` names the concrete replacement: an item from the creator's
  Inventory, or a public specific from the source reel. "Swap in an AI tool"
  is a category. "Swap in the creator's own Notion weekly review" is a thing.
- `transferable_mechanism` stays free of any topic. The named things live in
  `specifics` and `adaptation`, not in the mechanism.

## Sources

ContentOS wording, written from `docs/superpowers/specs/2026-09-16-contentos-design.md`,
the "0.3.0 changes" section ("Specificity", the three claim tiers, and the
`references/specificity.md` bullet), after the first live run produced scripts
that dropped every named tool and number. The point that vague quantities read
as machine written is paraphrased from Ray Cfu's "How to Make Your Writing Not
Sound Like AI". The niche table and the examples are ContentOS defaults.
