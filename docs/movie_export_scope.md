# Scoping a movie export

What it would take for bioleads to write an `.mp4` of a network tour itself, rather than
leaving you to screen-record the in-page tour. Written 2026-10-03 at Dan's request. **Nothing
here is built.**

## What exists today

The network pages carry a **guided tour**: *Play tour* flies the camera to the most connected
node, zooms, shows its details, and steps on every four seconds. **Screen-recording that
produces the movie now, with no dependencies at all.** Everything below is about removing the
screen-recording step.

## Recording the tour, which is what to do today

The same text is on the **ⓘ** beside the tour buttons in every network page, so it is there
at the moment you want it rather than in a document you have to go and find.

| System | How |
|---|---|
| **macOS** | **Shift-Command-5**, choose *Record Selected Portion*, then *Record*. The file lands on the Desktop. QuickTime's *File → New Screen Recording* does the same thing |
| **Windows 11** | **Windows-Alt-R** opens Game Bar's recorder, or use the **Snipping Tool**'s record button |
| **Linux, GNOME** | **Ctrl-Alt-Shift-R**, built in. KDE has **Spectacle** |
| **Any of them** | **OBS Studio**, if you want a region, a cursor highlight, or a webcam inset |

**Three things that make the recording better:**

1. **Press *Pause layout* first** if the graph is still drifting, or the movie opens on a
   settling graph rather than on the network.
2. **Start recording before pressing *Play tour***, so the first flight is in the file.
3. **A full tour of 10 nodes takes about 1 minute 50 seconds** at the current pacing — 11
   seconds on each node, which is set by `citations.TOUR_DWELL_MS`. *Next* and *Reset view*
   let you drive it by hand instead if you would rather narrate.

Trimming afterwards needs no editor: `ffmpeg -i in.mov -ss 3 -t 95 -c copy out.mov`.

## What a recorder actually has to do

1. Open the generated HTML in a real browser — the layout is computed by vis.js and there is
   no way to get it without running vis.js.
2. Wait for stabilization to finish.
3. Drive the tour, which already exists as JavaScript.
4. Capture frames.
5. Encode to `.mp4`.

Steps 1-4 need a browser. Step 5 needs **ffmpeg**.

## Three ways to do it

| | How | New dependencies | Quality | Effort |
|---|---|---|---|---|
| **A. Browser video capture** | Playwright's own `record_video`, then ffmpeg to transcode | Playwright + a Chromium download | Browser-timed, variable frame pacing, fixed resolution | **~half a day** |
| **B. Deterministic frames** | Playwright steps the tour one stop at a time and screenshots; ffmpeg assembles at a chosen frame rate | Playwright + Chromium | **Best.** Reproducible frame for frame, any resolution, any pacing | **~1 day** |
| **C. No browser at all** | Lay the graph out in Python, draw frames with matplotlib, ffmpeg assembles | ffmpeg and matplotlib only | Different picture from the HTML, by construction | **~1 day, plus a real risk** |

## The dependency arithmetic, which is the whole decision

- **Playwright** is a small wheel but `playwright install chromium` pulls **a few hundred
  megabytes**. For a package whose point is `conda install bioleads`, that is a large thing
  to add.
- **ffmpeg is a system binary, not a Python package.** It is on conda-forge, so a conda
  dependency is possible; a pip install would have to find it on `PATH` and say so clearly
  when it cannot. **It is already present on this machine** (8.1.2), which is not an argument
  that it will be present on anyone else's.
- **Bioconda would notice.** Shipping a browser in a bioinformatics recipe invites review
  questions and is the sort of thing that stalls a PR.

**So: an optional extra, never core.** `pip install "bioleads[movie]"`, detected at runtime,
with a message naming exactly what is missing rather than a traceback. The tour keeps working
for everyone else.

## On option C, honestly

A server-side layout **was tried on 2026-10-03 and failed badly**: `nx.spring_layout`
collapsed a 150-node network onto a diagonal line of overlapping nodes, and it was reverted.

**That failure was the parameters, not the idea.** A properly tuned force layout — ForceAtlas2
with the same constants vis.js uses, or spring_layout with a sensible `k` and far more
iterations — could produce a usable picture. But it would be **a different picture from the
one in the HTML**, and two renderings of the same corpus that disagree is worse than one
rendering plus a screen recording. **C is only worth it if the movie is allowed to look
different from the page.**

## What to decide

1. **Is the screen recording actually a problem?** It works, it costs nothing, and the tour
   was built to be recorded. If the answer is "it is fine", this document is the answer.
2. If a file is wanted, **A or B**: A is quick and adequate for showing someone a network; B
   is reproducible and belongs in a figure.
3. **Who has to be able to run it?** If only this machine, the dependency argument mostly
   evaporates and B is clearly right. If anyone installing from Bioconda, it stays an extra.

## Recommendation

**Do nothing yet.** The tour covers the stated need, and the next time a movie is wanted the
question of whether it must be reproducible — a figure, not a demonstration — will answer A
against B without guessing.
