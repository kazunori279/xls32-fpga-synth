# Listening sessions

Votes from the blind listening pages, one file per session, grouped by what was compared:

```
listening/<incumbent>-vs-<challenger>/ab_<comparison>_<question>_<listener>.json   ab_check.html
listening/consolidate-128-to-64/sub_<listener>.json                               sub_check.html
```

Read a directory, never a single file:

```
uv run python ab_tally.py  listening/prev128-vs-soundfont
uv run python sub_tally.py listening/consolidate-128-to-64
```

A **session** is one question, one listener, one pass over the counterbalanced sequence. Adding a
second ear means adding a file, not re-running and replacing; `ab_tally.py` pools whatever is in the
directory and reports per session as well. That is the whole storage design.

Some rules the files encode, and why:

- **Two hearings per pair, one each way round.** Only a pair that names the same bank both times
  counts. A discordant pair is not a tie — a tie is the listener saying the two sound alike, a
  discordant pair is the protocol failing to ask. `ab_render.py` builds the sequence with at least
  `MIN_SEP` trials between the two hearings, so the second one is not answered from memory of the
  first answer.
- **`"void": true`** marks a run kept as evidence about the protocol rather than about the banks.
  `ab_tally.py` skips it and says why. `prev128-vs-soundfont/ab_votes_r1_void.json` is the one:
  before counterbalancing, the listener picked the first-played clip in 19 of 22 decided trials
  (p = 0.001) while the bank split sat at 13–9 (p = 0.52). Balancing which bank plays first keeps a
  position preference from *favouring* a bank; it leaves it free to *decide* every trial. Playing
  the target immediately before each candidate is what removed it (re-run: 19–18, p = 1.000).
- **One question per session.** Asked side by side on one screen, `closer` and `better` came back
  identical on all 48 hearings — two questions with the same buttons and the same answer every time
  are one question. They are separate sittings with different audio now: `closer` is a single note
  with the target played immediately before each candidate, `better` a six-note phrase with no
  target at all.
- **Pre-#20 files** have one hearing per pair and both questions on each row. `ab_tally.py` still
  reads them, fans them into one pseudo-session per question, and refuses to compute concordance
  from them. `stft-vs-clapstft` (the `$LOSS` choice, 18–1) and `armbase-vs-armfx` (#16, the
  search-space widening, 9–7 null) are both of that vintage: neither controls for playback order.

- **`consolidate-128-to-64`** is not an A/B: each trial is one dropped preset against the surviving
  slot that shares its name, asking whether the survivor covers it (#22). Same two rules — two
  hearings a pair, one question a session — and `consolidate_check.py` additionally draws from the
  presets no stored session has asked about, so a second sitting extends the coverage instead of
  re-testing memory. Pool on the preset name, not the trial id: stems are per-render, so `s00` is a
  different pair in every set. `sub_votes_r1.json` predates all of that and asked once.

The WAV the votes refer to is not here. `ab_render.py` and `consolidate_check.py` regenerate
`webui/ab/` and `webui/sub/` from the banks in about a minute each, and their manifests are the
answer key, which is why those directories are gitignored and this one is not.
