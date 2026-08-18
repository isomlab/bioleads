# Benchmarking citation expansion

`tools/benchmark_expansion.py` measures the two things
[stage 2](how_it_works.md#2-grow-the-corpus-by-following-citations) asserts but
never tested:

1. **Is the asymmetry real?** Are papers that *cite* a seed more topically
   precise than the papers a seed *cites*?
2. **Does the relevance gate help, and at what `rocchio_gamma`?**

Both were argued from first principles. This turns them into numbers.

## How it measures

A systematic review's reference list is a curated, human-assembled answer to
"which papers are relevant to this topic?" — which makes it usable as ground
truth. The design follows [Sjögårde & Ahlgren
2024](https://arxiv.org/abs/2403.09295), who benchmarked seed-based retrieval on
the same NIH Open Citation Collection data that iCite serves:

1. Take a systematic review with at least `--min-refs` references.
2. Sample `--seeds` of them at random — these are the seeds an expansion starts from.
3. The **remaining** references are the target: what a perfect expansion would recover.
4. Run each arm from the seeds; score what it retrieves against that target.

Reported as **median** precision / recall / F1 across reviews, because the
per-review distributions are skewed and a mean would follow the outliers.

### The fairness correction

A paper published *after* the review cannot possibly appear in its reference
list. Counting such papers as false positives would penalise forward expansion
for doing the one thing it is guaranteed to do — surface recent work. So
candidates published after the review's year are dropped before scoring.

This is not cosmetic: it is the difference between a fair test of the asymmetry
and a rigged one. `--no-year-cutoff` shows how much it matters.

## The arms

| Arm | What it retrieves |
|---|---|
| `forward` | papers citing the seeds (one round, via iCite) |
| `backward` | papers the seeds cite (one round, via iCite) |
| `both` | the union — equivalent to `bfs` at one round |
| `relevance:<gamma>` | all forward citers, plus the top-K backward references gated at that `rocchio_gamma` |

The `relevance` arm mirrors `relevance_guided_expand` exactly: seeds + forward
citers form the profile, backward references are the gated candidates, and the
result is every forward citer plus the kept top-K. `relevance:0` is the
positive-only centroid, so it is the control arm for the negative term.

## Running it

```bash
python3 tools/benchmark_expansion.py --reviews 40 --seeds 5 --arms forward,backward,both
```

That answers the asymmetry question and needs only iCite. To also sweep the gate:

```bash
python3 tools/benchmark_expansion.py --reviews 25 --seeds 5 \
    --arms forward,backward,both,relevance --gammas 0,0.25,0.5 --out bench.csv
```

Everything fetched is cached under `--cache` (default `.benchmark_cache`, git
-ignored), so re-runs and added arms cost nothing.

## Cost, and what it trades away

The cheap arms need only iCite records. The `relevance` arms need *text* to
score, and by default they use the **titles iCite already returned**, so a full
sweep costs no extra requests.

Titles are thin. `--abstracts` fetches title + abstract from PubMed instead,
which is much closer to what the real pipeline scores on — and much slower, one
efetch per candidate. Treat a titles-only gamma as indicative, not final.

Two other honest limits:

- **A gamma sweep re-embeds.** Each `relevance:<gamma>` arm calls the scorer
  fresh, so a 3-gamma sweep embeds the same documents three times. Fine for
  tens of reviews; it will dominate the runtime at hundreds.
- **One round only.** Arms follow citation links a single hop, matching
  `expand_fwd_rounds=1` / `expand_back_rounds=1`. Multi-round drift is not
  measured here.

## Reading the output

The harness prints a verdict for each claim rather than leaving you to eyeball
the table — `SUPPORTED` / `NOT SUPPORTED` for the asymmetry, and the best gamma
by median F1 with its delta against `gamma=0`.

The design predicts **forward wins on precision and loses on recall**. The first
run at scale (40 reviews, 5 seeds) found the opposite: backward won on *both*, in
33 of 40 reviews. See [How bioleads works](how_it_works.md#measured-the-precision-half-of-this-claim-does-not-hold)
for the numbers and what follows from them.

The gamma sweep is measured in the same place. Headline: at the default
`rocchio_gamma=0.25` the negative term moves one document across twelve reviews,
and the gate as a whole governs only ~5% of what the relevance strategy returns
(the rest being ungated forward citers). Both results are titles-only; `--abstracts`
is the fidelity test.

If you re-run and get a different answer, the parameters that matter most are
`--seeds` (fewer seeds means a narrower profile), `--min-refs`, and whether the
year cutoff is on — check those before concluding the earlier run was wrong.
