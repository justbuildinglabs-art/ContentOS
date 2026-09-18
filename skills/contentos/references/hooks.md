# Hooks

The director uses this file to label what a source reel did. The writer uses it to build two new hooks
that keep the mechanism and drop the wording. Read the Hook types first, then Writer approaches.

## Hook types

These are the values for `hook_type` in the director analysis. Pick the one that matches what the reel
actually does in its first seconds, not what the caption claims. When nothing fits, use `other` and
describe the mechanism in `transferable_mechanism`. Every example below is invented for teaching: half
name a made-up app, half come from made-up creators, and none of them is a real person or brand.

### bold_claim
A hard number or an unlikely result, stated with no wind-up, so the viewer stops to check whether
it can be true.
- "I cancelled six subscriptions in one sitting and got $214 back." (Loomi, budgeting)
- "The agent wrote 400 lines last night. I kept twelve of them." (a dev creator)

### pain_callout
Says the viewer's current annoyance back to them, in the words they would use themselves.
- "Your laundry pile has its own chair." (Rinse, laundry pickup)
- "It's 7pm, you have three onions, and no plan." (a cook)

### contrarian
Attacks something the category takes for granted and promises a better way.
- "Streaks are why you quit. They are not why you keep going." (Sprout, habits)
- "Your long runs are not the thing making you faster." (a run coach)

### story_open
Starts inside a scene already running, so the viewer stays to find out how it got there.
- "It's 11pm and I'm re-reading an invoice I sent in March." (Quartz, invoicing)
- "Period one, mid-titration, and the fire alarm goes off." (a chemistry teacher)

### question
Opens with a direct question the viewer answers in their head before they can scroll.
- "When did you last sleep through the night?" (Hush, sleep)
- "How many terminal tabs are open on your machine right now?" (a dev creator)

### curiosity_gap
States a result and withholds the mechanism, so the only way to close the gap is to keep watching.
- "I found $80 a month in a folder I never open." (Loomi, budgeting)
- "The thing that fixed my pan sauce was never in the pan." (a cook)

### pov
Frames the clip as a scene the viewer is inside, usually with a POV label burned on screen.
- "POV: you open the fridge at 6pm with no plan." (Marrow, meal planning)
- "POV: mile 18 and the watch is telling you to slow down." (a run coach)

### before_after
Shows the end state first, then rewinds to the start, or runs both states side by side.
- "Same desk, 40 seconds apart." (Deskpad, notes)
- "Same test suite. Before one prompt, after one prompt." (a dev creator)

### challenge
Sets a public test the creator commits to on camera, with a rule and a deadline.
- "One habit, 14 days, no streak counter. Starting now." (Sprout, habits)
- "A week of chemistry with a kettle, a spoon, and nothing else. Day one." (a chemistry teacher)

### other
Anything the nine labels above do not fit. Use it instead of forcing a bad match.
- A five second silent countdown over a messy desk, then the app opens. (Deskpad, notes)
- A silent split screen of two terminals racing, no voice for three seconds. (a dev creator)

## Writer approaches

The writer picks two of these four and labels each hook with the approach it used. The primary and the
backup must use different approaches, so you have a real alternative to film, not a reword.

1. **bold claim** (maps to `bold_claim`). Lead with the number or the result. Be specific enough that a
   skeptic could check it. If the number is not in `creator.md`, write `[NEED NUMBER]` and move on.
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
  emotional charge real or claimed. Could a competitor run the same line tomorrow. Does it match the brand
  voice in `creator.md`. Does it echo the source wording.
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
deliberately relaxes), both by Ray Cfu. Every example line here is original, and the apps and creators
in them are invented.
