# Formats

The director picks the `format` value that matches the source reel. The writer builds to that format's
budget and skeleton. QA checks the script against the same numbers. One table, ten formats, then one
section each.

`word_budget` is the hard ceiling for the Beats table: the words in the spoken or VO column plus the words
in the on-screen text column.
Bracketed markers count as zero words: `[PAUSE]`, `[EMPHASIS]`, and every `[NEED ...]` placeholder. The
`[VISUAL CUE]` column is never counted.

The number is about 2.5 spoken words per second of target length, which is the pace a person actually reads
at on camera. Over budget is a fail, not a rounding issue. The tolerance is plus or minus 10 percent.

## Format table

| format | target_length_s | word_budget | beats |
| --- | --- | --- | --- |
| talking_head | 30 | 75 | problem, solution, differentiator, proof |
| screen_demo | 40 | 100 | problem, solution on screen, differentiator, proof |
| voiceover_broll | 40 | 100 | problem, solution, differentiator, proof |
| skit | 30 | 75 | setup, conflict, turn, payoff |
| slideshow_text | 20 | 50 | hook card, problem card, solution card, proof card |
| ugc_review | 30 | 75 | problem, what I tried, what changed, proof |
| tutorial | 50 | 125 | problem, steps, differentiator, proof |
| trend_remix | 20 | 50 | trend beat, twist, product, proof |
| stitch | 30 | 75 | clip claim, response, solution, proof |
| other | 30 | 75 | problem, solution, differentiator, proof |

## Per format

### talking_head
- Budget: 30 seconds, 75 words.
- Beats: problem, then solution, then differentiator, then proof. Four rows, one idea per row.
- On-screen text: light. Three to five cards across the beats, six words or fewer each, placed where
  the eye is free. The hook card and the CTA card sit on top of that.
- CTA: last four seconds, spoken plus one text card. Cut the second the ask lands.
- Audio: one clean voice. No music under the first three seconds. A quiet bed after the first beat is fine.

### screen_demo
- Budget: 40 seconds, 100 words.
- Beats: problem, then the solution shown on screen, then differentiator, then proof. The screen carries
  the argument, the voice only names what the viewer is already seeing.
- On-screen text: medium. Label every tap or field the viewer must notice. Keep labels to five words.
- CTA: last five seconds, over the final screen state, never over a black card.
- Audio: voiceover on top of the recording. Keep the real tap and keyboard sounds low in the mix.

### voiceover_broll
- Budget: 40 seconds, 100 words.
- Beats: problem, then solution, then differentiator, then proof. Every beat needs its own shot. If you
  cannot name the shot, cut the beat.
- On-screen text: light. The b-roll is the evidence, the text is a caption, not a second script.
- CTA: last five seconds over the calmest shot in the set.
- Audio: voiceover leads. Music sits under it and drops out for the proof line.

### skit
- Budget: 30 seconds, 75 words.
- Beats: setup, then conflict, then turn, then payoff. The product is the turn, never the setup.
- On-screen text: minimal. One label to establish who is who, nothing else.
- CTA: after the payoff, said straight to camera so the viewer knows the bit is over.
- Audio: original dialogue, recorded close. No music over the punchline.

### slideshow_text
- Budget: 20 seconds, 50 words.
- Beats: hook card, then problem card, then solution card, then proof card. The text is the script.
- On-screen text: heavy, and it is the whole point. One idea per card, eight words or fewer, same position
  on every card so the eye does not move.
- CTA: the last card, plus the caption. Keep the card readable for two full seconds.
- Audio: trending sound with no vocals fighting the text, or silence. No voiceover.

### ugc_review
- Budget: 30 seconds, 75 words.
- Beats: the problem, then what I tried, then what changed, then proof. First person throughout.
- On-screen text: light and casual. Auto-caption style, not designed cards.
- CTA: last four seconds, said like a recommendation to a friend, not a pitch.
- Audio: phone mic, room tone left in. No music. Polish here reads as an ad.

### tutorial
- Budget: 50 seconds, 125 words.
- Beats: problem, then the steps as their own rows, then differentiator, then proof. Number the steps out
  loud. Three steps is the ceiling for a reel.
- On-screen text: medium to heavy. Every step gets a numbered card that stays up for the whole step.
- CTA: last five seconds, after the result is visible. Offer the next step, not a purchase.
- Audio: voiceover, steady pace, a short pause between steps so the viewer can follow.

### trend_remix
- Budget: 20 seconds, 50 words.
- Beats: the trend beat as the audience expects it, then the twist, then the product, then proof. The
  twist must land before the halfway mark.
- On-screen text: medium. Match the trend's own text convention or the remix reads as a miss.
- CTA: the caption carries it. On-screen, one short line at the end.
- Audio: the trending sound, unedited, at the original timing. Do not talk over the drop.

### stitch
- Budget: 30 seconds, 75 words.
- Beats: the borrowed clip claim, then your response, then the solution, then proof. Keep the borrowed clip
  under four seconds and make sure the rights are clear.
- On-screen text: light. One label naming what is being answered.
- CTA: last four seconds, in your own frame, never over the borrowed footage.
- Audio: the clip's own audio for the opening, then your voice, level matched.

### other
- Budget: 30 seconds, 75 words. Use these defaults when the source reel fits no named format.
- Beats: problem, then solution, then differentiator, then proof.
- On-screen text: light. Three to five cards across the beats, six words or fewer, plus the hook and
  CTA cards.
- CTA: last four seconds, spoken plus one text card.
- Audio: whatever the source used, named explicitly in the production notes so the founder can match it.

## Sources

Paraphrased from "The 20-Agent Script System: How to Build an AI Writing Pipeline That Actually Produces
Good Work" (the four-beat body, the word budget as a hard ceiling, the visual and emphasis markers) and
"How to Build a 5-Agent Content Pipeline That Writes, Edits, and Publishes for You" (per-platform
formatting rules, hook first and CTA last), both by Ray Cfu. Lengths and budgets here are ContentOS
defaults, not numbers from the guides.
