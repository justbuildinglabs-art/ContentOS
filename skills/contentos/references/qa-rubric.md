# QA rubric

The reviewer reads the script, the brief, the analysis, `03-patterns.md`, and `creator.md`, then fills
`qa.schema.json`. Every check gets pass, fail, or na. Every score gets 1 to 10. The verdict follows the
rules at the bottom, not your mood. Quote the offending line in every issue so the writer can find it.

The guides demand a 10 on every dimension. ContentOS sets the bar at `qa_pass_threshold`, default 8,
because the loop allows one revision before a human looks at it. Score honestly against the 10 anchor
anyway. A generous 8 wastes the revision.

## Checks

### hook_first_3s
Pass when the hook's spoken line is the first thing said, lands inside three seconds, and has an on-screen
text card that reads without sound. Fail on a greeting, a logo card, a setup sentence, or a hook that only
arrives in the second beat.

### hook_matches_brief
Pass when the hook uses the mechanism the brief named, in `hook_type` and `transferable_mechanism`. Fail
when the writer swapped the mechanism for an easier one, even if the new hook is good. A good hook off the
brief is still a fail, because the brief is what the outlier data supports.

### payoff_present
Pass when `## Payoff` names a concrete on-screen moment that delivers what the hook promised. When
`creator.md` has something under What you promote, the payoff must show that offer through a moment
listed under Payoff moments. Fail when the payoff is vague, invented, or missing, or when it pays off
something the hook never promised. Do not use na. Every format has a payoff, even the ones with no
screen recording in them.

### consistent_with_profile
Pass when every fact about the creator, the tools or topics covered, and anything promoted matches
`creator.md`. Fail on an invented fact, a wrong number, a tool that does something it does not do, or a
contradiction with Allowed claims or What you promote.

### no_fabricated_claims
Pass when every number and factual claim traces to Allowed claims, Proof assets, Payoff moments, or
What you promote in `creator.md`, or is written as a placeholder. Fail on any invented statistic,
rating, user count, or result. A `[NEED NUMBER]` is a pass.

### no_fake_testimonial
Pass when no quote, review, message screenshot, or customer story appears unless it is listed under Proof
assets. Fail on any invented person or paraphrased "one user told us".

### no_restricted_claims
Pass when the script makes no medical, health outcome, income, investment, or legal promise, guarantees no
result, and targets no minors. Fail otherwise. Cross-check the analysis `risk_flags`.

### not_a_clone
Pass when the mechanism is borrowed but the words, examples, and on-screen text are original. Fail when any
spoken line reads like the source reel's line or reuses its distinctive phrasing. Also fail when the script
lands on the `avoid` angle from the brief.

### cta_present
Pass when both CTAs exist, each under 20 words, the primary is a direct ask, and the backup is an open
loop. Fail when one is missing, over length, or when both are the same ask reworded.

### brand_voice
Pass when the script obeys the Brand voice section of `creator.md`: the three adjectives it should be, the
three it should not, the sentence rules, and the word lists. Fail on any off-limits word, and on any
creator rule the script breaks. Use na when the creator has not filled in the Brand voice section and the
prompt carries no creator rules.

### ai_tells
Pass when sentence length varies, contractions appear where speech would use them, numbers are textured or
placeheld, and none of the tells in `scripting.md` are present. Fail on any em dash, any word from the
banned vocabulary list, any throat-clearing opener, more than one hedge, or a spoken column where more
than four lines in five sit inside the 15 to 25 word band.

## Scores

Score 1 to 10 against the 10 anchor. The 7 and 4 anchors tell you what the middle and the floor look like.
While you score, keep a note of the single best line for `strongest_line` and up to three lines for
`weakest_lines`, each with what is wrong with it.

### hook_scroll_stop
10: a thumb stops on reflex. 7: interesting, but easy to skip past. 4: nothing in the first frame or the
first sentence asks anyone to stay.

### hook_specificity
10: an exact number, name, hour, or scene. 7: concrete but soft, "a lot of people", "pretty fast". 4:
category language that would fit any account in the niche.

### hook_emotional_charge
10: an involuntary reaction, recognition or irritation or surprise. 7: it registers as information and
nothing moves. 4: no reaction of any kind.

### hook_voice_match
10: indistinguishable from the sample sentences in `creator.md`. 7: generically professional, could be any
brand. 4: wrong register, or it uses an off-limits word.

### hook_differentiation
10: unlike anything else in this niche's feed, and clearly not the `avoid` angle. 7: a rival could run
the same line tomorrow. 4: it is the `avoid` angle.

### body_argument_clarity
10: after one watch the viewer could tell a friend why this matters to them. 7: they get the gist and miss
the point. 4: a list of things with no argument holding them together.

### body_emotional_arc
10: the viewer moves from frustration or curiosity to relief or confidence, and the turn is felt. 7: a
coherent line of thought with one flat note. 4: the same note from start to finish.

### body_proof_density
10: every claim carries a number, a comparison, a demonstration, or a placeholder. 7: one claim floating
without support. 4: mostly assertion, the thing is described rather than shown.

### body_pacing
10: no dead spots, every beat moves. 7: one beat drags or repeats the one before it. 4: a whole beat could
be deleted and nothing would be lost.

### cta_action_clarity
10: the next step is unmistakable and takes one tap. 7: the step is implied but never said. 4: the viewer
has to work out what to do.

### cta_friction
10: names the number one objection and removes it in the same breath. 7: easy to act on, but the objection
is ignored. 4: it adds friction, an account, a price surprise, or a form.

### cta_momentum
10: the natural end of the argument the body was making. 7: a small gear change into selling. 4: it
reads like the ending of a different video, glued on.

### cta_urgency
10: a real reason to act today, drawn from `creator.md`, or no honest reason to act today exists and the
CTA does not fake one. A plain follow, comment, save, or share ask with no false urgency scores 10. 7:
urgency that is generic or only implied, a "don't miss this" with nothing behind it. 4: manufactured
urgency, a deadline that does not exist or scarcity that is not real. Fake urgency also belongs in
`cringe_flags`.

## Filler questions (filler_cut_list)

Ask three questions of every line in the spoken column. Does it advance the argument. Does it carry
information a later line needs. Does it create a feeling. If the answer is no three times, it is filler.

Put it in `filler_cut_list` with the line quoted and the reason. Filler gets cut, not rewritten. Also cut:

- Anything that repeats a point already made.
- Fifteen words doing the job of seven.
- Habitual transitions that exist out of reflex, the "and that is not all" family.
- Hedges: basically, honestly, more or less, sort of, kind of. One survives at most.

## Length rule (length_check)

Count the words in the Beats table spoken or VO column plus the words in the on-screen text column.
Bracketed markers count as zero words: `[PAUSE]`, `[EMPHASIS]`, and every `[NEED ...]` placeholder. The
`[VISUAL CUE]` column is never counted. That total is `word_count`. Compare it with the `word_budget` in
the frontmatter, which must match the format's row in `formats.md`.

Within plus or minus 10 percent sets `within_tolerance` true. Over the top of the range is a fail and
forces a revision, with the cut list attached. Under the bottom of the range is also outside tolerance:
say which beat feels rushed and what genuine line would fill it. Do not pad.

## Verdict rules

- **reject** when `no_fabricated_claims`, `no_fake_testimonial`, `no_restricted_claims`, or
  `consistent_with_profile` failed. The one exception: if a single line carries the whole problem and
  deleting or rewriting that line fixes it, return `revise` instead and name the line.
- **revise** when any other check failed, when any score is below the pass threshold, when
  `within_tolerance` is false, or when your own `confidence` is below the threshold.
- **pass** when nothing above applies.

Every problem goes in `issues` with `check_or_score`, a severity of blocker, major, or minor, the detail
with the line quoted, and a concrete `fix` the writer can apply without guessing. `summary` is two or three
sentences the creator can read on its own. `confidence` is 1 to 10 on how sure you are about this review;
below the threshold, say in the summary what would raise it.

A second `revise` on the same brief sends it to a human. Say clearly what a human needs to decide.

## One watch test (one_watch_test)

Imagine one viewing at normal speed with the sound on, no replay. Write one or two sentences saying which
single thing stays with the viewer, and whether that thing is what makes this different rather than a
joke, a transition, or the music.

Then run the three-part version. Could the viewer say what the reel showed them, say why it beats what
they do now, and say what they would do next. If any of the three is missing, the body has a structural
problem, not a wording problem. Score `body_argument_clarity` no higher than 4 and add an issue against
it, `major`, saying the beats need rebuilding rather than a line edit. The verdict then follows the
verdict rules like every other finding: a score under the threshold is a `revise`.

## Spoken flow and cringe (spoken_flow_issues, cringe_flags)

Read the spoken column out loud as a performance, not as text.

- `spoken_flow_issues`: lines too long for one breath, tongue twisters, repeated sounds, numbers that are
  awkward to say, names that are hard to pronounce, sentences that only work written down.
- `cringe_flags`: trying too hard, overselling, fake urgency, slang the brand would not use, stacked
  exclamation marks, a joke that lands wrong, anything that sounds desperate.

Quote the line in each entry. Both arrays are empty when the script is clean.

## Placeholders

List every bracketed placeholder in `placeholders`, exactly as written, including `[NEED NUMBER]`,
`[NEED NAME]`, and the rest. Placeholders never fail a check, never lower a score, and never change the
verdict. They are the creator's to-do list in the final report. Never ask the writer to fill one in with an
estimate.

## Sources

Paraphrased from "The 20-Agent Script System: How to Build an AI Writing Pipeline That Actually Produces
Good Work" (the manager scoring dimensions, the filler questions, the character budget, the one watch test,
the spoken flow and cringe checks), "How to Make Your Writing Not Sound Like AI" (the tells behind the
`ai_tells` check), and "How to Build a 5-Agent Content Pipeline That Writes, Edits, and Publishes for You"
(the editor's evidence rule and banned vocabulary), all by Ray Cfu. The pass threshold of 8 and the verdict
rules are ContentOS decisions, not the guides'.
