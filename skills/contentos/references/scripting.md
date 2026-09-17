# Scripting

Rules for the script writer. The brief decides what to say. This file decides how to say it. Read the
matching format section in `formats.md` and the gold script in `examples/` before you write a line.

## Execute the brief

- The brief is the strategy. Do not redo it, argue with it, or improve it.
- Keep the hook mechanism named in the brief. You may write new words. You may not switch a pain call-out
  into a bold claim because the claim came out easier.
- Change 10 to 20 percent of the source. That is the adaptation line in the brief: the product, the
  setting, the example, the number. Everything else about the structure stays.
- Under 10 percent changed is a clone. Over 20 percent and you have thrown away the thing that worked.
- Do not re-research. Everything you are allowed to say is already in `product.md`, the brief, the
  analysis, and `03-patterns.md`.
- Read the `avoid` line in the brief before you start. That is the obvious version everyone in the niche
  will post this month. If your draft matches it, start again.

## Beats skeleton

The Beats table header is exactly:

`| t | [VISUAL CUE] | spoken / VO | on-screen text |`

Three rows minimum, four is the normal shape for a 30 second reel. One idea per row.

1. **Problem.** The status quo, in the viewer's language from the audience profile. No product yet.
2. **Solution.** The product arrives as the obvious next move, not as an ad break.
3. **Differentiator.** The one thing that is genuinely different. Not a feature list.
4. **Proof.** A number, a comparison, a result, or a demonstration. Skip this beat only when the target
   length cannot hold it, and say so in the production notes.

Every line earns its place. A line stays only if it advances the argument, carries information the next
line needs, or creates a feeling. If it does none of the three, cut it rather than rewrite it.

Use `[VISUAL CUE]` in the cue column for the shot. Use `[EMPHASIS]` before a word to stress and `[PAUSE]`
where silence helps, both inside the spoken column. Keep the markers rare enough that they still mean
something.

## Show, don't describe

Never write that the product is simple, fast, or easy. Show the thing that makes a viewer conclude it.

- Weak: the setup is quick and painless.
- Better: you type the habit name, hit done, and the setup screen is gone.
- Weak: it is great for busy mornings.
- Better: it fits between the kettle going on and the toast coming out.

For a screen demo the rule is harder. Say only what the screen is already showing. If the voice describes
something the viewer cannot see, either shoot it or cut the line.

## Evidence only

- Every claim must trace to `product.md`: Core features, Allowed claims, or Proof assets. If it is not
  there, it does not go in the script.
- Never invent a statistic, a rating, a user count, or a funding number. Write `[NEED NUMBER]` in place of
  the figure and keep the sentence. The founder fills it in later. Placeholders are expected and never
  count against the script.
- Use the same convention for other missing facts: `[NEED NAME]`, `[NEED SCREENSHOT]`, `[NEED DATE]`.
- No testimonial, quote, review, or message screenshot unless it is listed under Proof assets.
- Nothing under Forbidden claims, and no medical, income, or legal promise even when the founder would
  like one.
- When the brief pushes you toward a claim the product cannot support, write the beat without the claim
  and note it in `## What changed vs source`.

## Hook rules

- Two hooks. Primary and backup. Different approaches from the four in `hooks.md`, labeled with the
  approach name.
- Each spoken line under 25 words.
- Each hook gets an on-screen text line that is shorter than the spoken line.
- The mechanism comes from the brief. The wording is yours and must not echo the source reel.
- The hook is the first thing said. See the first three seconds rules in `hooks.md`.

## CTA rules

- Two CTAs. The primary is the direct ask. The backup is the open loop.
- Each under 20 words.
- The direct ask names the exact next step and answers the number one objection from the audience
  profile in the same breath. If the objection is "another app I will delete", say something about that.
- The open loop leaves a question that only the action resolves. Good for cold audiences.
- Urgency only when it is real. No invented deadlines, no fake scarcity.
- The CTA must feel like the end of the argument, not a different script stapled on.

## Caption rules

- Two or three short lines. The caption repeats the promise for the sound-off viewer and adds the one
  detail the video had no room for.
- The caption may carry the honest limitation that keeps the whole thing credible.
- End the caption with one line of 5 to 8 hashtags, nothing after it. Use the seeds in `product.md`, mix
  one broad tag with narrow ones, and skip anything that reads like a bot wrote it.

## AI tells and fixes

These are the tells that make a script feel machine made. The QA `ai_tells` check looks for exactly these.

| Tell | Fix |
| --- | --- |
| Every sentence lands between 15 and 25 words | Vary length on purpose. Write a four word line. Then let one run long and double back the way a person does when they are working it out. This fix matters more than the rest combined. |
| Every beat is the same size | Make them uneven. Let one beat be a single line. |
| Throat-clearing openers like "in today's world" or "it is important to note" | Start on the actual point. Delete the runway. |
| Stock vocabulary: delve, tapestry, leverage, seamless, robust, landscape, navigate, underscore, realm, testament, elevate, unlock, harness, foster, unleash, groundbreaking, game-changer | Say the plain word out loud and write that one instead. |
| Em dashes | None. Use a comma or a period. |
| Relentless positivity | One honest limitation is allowed and usually helps. Say the part that is slow, awkward, or not for everyone. |
| Vague quantities: significantly, a wide range, numerous | A textured number, or `[NEED NUMBER]`. Odd and precise beats round and soft. |
| Hedges: kind of, sort of, in a way, to be honest, basically | At most one hedge in the whole script. Zero is better. |
| Formal register with no contractions | Use contractions everywhere speech would. Start a line with And, But, or Because when the emphasis needs it. |

Read the spoken column out loud before you deliver. If you run out of breath, the line is too long. If you
stumble on a word, the viewer will too.

## Draft, diagnosis, redraft

Run the loop internally for the hooks, the body, and the CTAs. Deliver the final version only. Never ship
the working notes.

- **Draft.** Write the whole thing without editing. Editing while writing produces careful, dead lines.
- **Diagnosis.** Name the weakest line and say why. Where does the energy dip. Where does it stop sounding
  like content and start sounding like an ad. Is every line earning its place. Does the rhythm vary. Does
  it sound like the voice in `product.md`.
- **Redraft.** Fix what you named. Do not rewrite what was working.

Two rounds is normal, three is the cap. Then count words against the format budget and cut to fit. Cut
filler first, never the proof.

## Revision pass

On revision 1 you get the previous script and the QA JSON.

- Fix only what QA flagged. Work the blockers first, then major, then minor.
- Leave every other line exactly as it was. A revision that rewrites clean lines is a new draft, and the
  founder loses the version they already approved of.
- Keep the same structure, the same section order, and the same hook mechanism unless QA failed
  `hook_matches_brief`.
- Placeholders stay as placeholders. Do not fill `[NEED NUMBER]` with a guess to make the report cleaner.
- Set `revision: 1` in the frontmatter and write to the revision path you were given.
- If a QA issue cannot be fixed without breaking a rule in this file, say so in `## What changed vs source`
  and leave the line alone.

## Sources

Paraphrased from "How to Make Your Writing Not Sound Like AI" (the tells, the fixes, the number
placeholder convention, the one honest complaint), "The 20-Agent Script System: How to Build an AI Writing
Pipeline That Actually Produces Good Work" (execute the brief, the beat structure, show do not describe,
the word budget, the draft and diagnosis loop), and "How to Build a 5-Agent Content Pipeline That Writes,
Edits, and Publishes for You" (the writer executes and does not re-research, evidence before claims),
all by Ray Cfu.
