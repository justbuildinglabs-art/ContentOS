# Hooks

The director uses this file to label what a source reel did. The writer uses it to build two new hooks
that keep the mechanism and drop the wording. Read the Hook types first, then Writer approaches.

## Hook types

These are the values for `hook_type` in the director analysis. Pick the one that matches what the reel
actually does in its first seconds, not what the caption claims. When nothing fits, use `other` and
describe the mechanism in `transferable_mechanism`. All examples below are invented for teaching.

### bold_claim
A specific, surprising result or number said flat, so the viewer thinks "wait, really".
- "I cancelled six subscriptions in one sitting and got $214 back." (Loomi, budgeting)
- "This fern went 40 days alone in an empty flat." (Fernback, plant care)

### pain_callout
Names the exact frustration the viewer is feeling right now, in the viewer's own words.
- "You've started the same morning routine four times this year." (Sprout, habits)
- "Your laundry pile has its own chair." (Rinse, laundry pickup)

### contrarian
Attacks something the category takes for granted and promises a better way.
- "Streaks are why you quit. They are not why you keep going." (Sprout, habits)
- "Meal prep Sunday is a trap." (Marrow, meal planning)

### story_open
Drops the viewer into the middle of a scene with no setup, raising a question they need answered.
- "It's 11pm and I'm re-reading an invoice I sent in March." (Quartz, invoicing)
- "The trail marker was gone. So was my signal." (Trailhead, hiking)

### question
Opens with a direct question the viewer answers in their head before they can scroll.
- "How many habit apps are on your phone right now?" (Sprout, habits)
- "When did you last sleep through the night?" (Hush, sleep)

### curiosity_gap
States a result and withholds the mechanism, so the only way to close the gap is to keep watching.
- "The setting that fixed my sleep wasn't in the sleep app." (Hush, sleep)
- "I found $80 a month in a folder I never open." (Loomi, budgeting)

### pov
Frames the clip as a scene the viewer is inside, usually with a POV label burned on screen.
- "POV: day three of the habit you swore you'd keep." (Sprout, habits)
- "POV: you open the fridge at 6pm with no plan." (Marrow, meal planning)

### before_after
Shows the end state first, then rewinds to the start, or runs both states side by side.
- "Same desk, 40 seconds apart." (Deskpad, notes)
- "These are the same boots." (Cobbler, shoe repair)

### challenge
Sets a public test the creator commits to on camera, with a rule and a deadline.
- "One habit, 14 days, no streak counter. Starting now." (Sprout, habits)
- "I'm cooking from an empty fridge for a week." (Marrow, meal planning)

### other
Anything the nine labels above do not fit. Use it instead of forcing a bad match.
- A five second silent countdown over a messy desk, then the app opens. (Deskpad, notes)
- A stitched clip of a stranger's spreadsheet, no words for two seconds. (Loomi, budgeting)

## Writer approaches

The writer picks two of these four and labels each hook with the approach it used. The primary and the
backup must use different approaches, so the founder has a real alternative to film, not a reword.

1. **bold claim** (maps to `bold_claim`). Lead with the number or the result. Be specific enough that a
   skeptic could check it. If the number is not in `product.md`, write `[NEED NUMBER]` and move on.
2. **pain call-out** (maps to `pain_callout`). Say the frustration back to the viewer in their language,
   taken from the audience profile and the language bank in `03-patterns.md`. No sympathy, no preamble.
3. **contrarian** (maps to `contrarian`). Name the thing the category believes, then say it is wrong.
   Only use it when the script can actually back the disagreement.
4. **story opener** (maps to `story_open`). Start mid-scene at a specific hour, place, or object. No
   setup sentence, no "so last week".

When the brief names a mechanism outside these four (`pov`, `before_after`, `challenge`, `curiosity_gap`,
`question`, `other`), keep that mechanism and deliver it through the closest of the four approaches. The
mechanism comes from the brief. The words are yours.

Rules for both hooks:
- Under 25 words spoken. Count them.
- On-screen text is shorter than the spoken line. Six words or fewer.
- Never reuse the source reel's phrasing. Same mechanism, new sentence.
- No greeting, no "in this video", no brand name before the point.

Run this loop in your own head and ship only the final pair:
- **Draft.** Write the hook fast, do not edit while writing.
- **Diagnosis.** Be blunt with yourself. Is it specific or soft. Would a thumb stop or drift. Is the
  emotional charge real or claimed. Does it sound like every other ad in this category. Does it match the
  brand voice in `product.md`. Does it echo the source wording.
- **Redraft.** Fix the named weakness only. Two rounds is usually enough. Three is the cap.

## First three seconds

- The hook is the first thing said. Nothing goes in front of it.
- Something on screen changes before the third spoken word.
- The on-screen text card lands inside the first second and reads without sound.
- Frame one is a thumbnail. It must make sense paused.
- Say the number, the name, or the scene in sentence one, not sentence two.
- No logo card, no "hey guys", no throat-clearing.
- If the format opens on a screen recording, the screen is already doing something.

## Question hooks

The five-agent guide bans question hooks outright and requires a statement. ContentOS keeps the softer
rule on purpose. A question hook is allowed when the outlier data favors it: the source reel opened with a
question and beat its own baseline, or several reels in `03-patterns.md` share that pattern. Otherwise
write a statement. Never open with a question the viewer can answer "no" to, because that answer ends the
watch.

## Sources

Paraphrased from "The 20-Agent Script System: How to Build an AI Writing Pipeline That Actually Produces
Good Work" (hook approaches, the draft and diagnosis loop, scroll-stop thinking) and "How to Build a
5-Agent Content Pipeline That Writes, Edits, and Publishes for You" (the statement-hook rule this file
deliberately relaxes), both by Ray Cfu. All example lines here are original and use invented products.
