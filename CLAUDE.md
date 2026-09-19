# ContentOS — developer notes

ContentOS is a Claude Code plugin: a four-stage Instagram Reels content pipeline (research → direct → write → qa) for creators; 0.2.0 renamed every product-specific field, check, and section (see the spec's "0.2.0 changes"). The design spec is `docs/superpowers/specs/2026-09-16-contentos-design.md` and is the binding authority.

Rules for anyone (human or agent) working in this repo:

- Python 3.9-compatible syntax only; every module starts with `from __future__ import annotations`.
- Standard library only. No pip dependencies. ffmpeg is optional at runtime (keyframes, transcript audio) and only touched by `lib/frames.py` and `lib/transcribe.py`. whisper-cpp is optional at runtime too (transcripts) and only touched by `lib/transcribe.py`.
- Tests: `python3 -m unittest discover -s tests -v`. Every test module subclasses `tests.helpers.NoNetworkTestCase`; tests never touch the network and never need real keys.
- Write the failing test first, then the code.
- Scripts live in `skills/contentos/scripts/`; run as `python3 skills/contentos/scripts/contentos.py <cmd> --project <dir>`. `lib/__init__.py` stays empty.
- Creator state lives under `<project>/.contentos/`, never in this repo.
- Reference files under `skills/contentos/references/` are paraphrased from the guides with a `## Sources` footer; never copy a passage over two sentences.
- Creator-facing text: plain language, short sentences, no em dashes.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
