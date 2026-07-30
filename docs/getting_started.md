# Getting started with bioleads

This guide gets bioleads running on your computer. You do **not** need to know how to
code — follow the steps and copy-paste where asked.

> On Windows everything below works the same — use **Miniforge Prompt**
> wherever this says "Terminal".

---

## Before you start

This guide assumes your computer already has **conda**.

> **First time?** Do the one-time
> **[install-from-scratch guide → INSTALL.md](INSTALL.md)** first, then come back here.

---

## Step 1 — Get the code

bioleads is a **public** repository, so no account or password is needed.

**Option A — Download ZIP (fastest, nothing to install).**
1. Open **[github.com/isomlab/bioleads](https://github.com/isomlab/bioleads)**.
2. Green **`Code ▾`** button → **Download ZIP**.
3. Unzip it somewhere easy, like your **Documents**. The folder is called `bioleads-main`.

**Option B — GitHub Desktop.**
1. **File ▸ Clone repository… ▸ URL** → paste `https://github.com/isomlab/bioleads` → pick a folder → **Clone**.

**Option C — `git clone` in Terminal.** The repo is public, so this needs no password:

```bash
cd ~/Documents
git clone https://github.com/isomlab/bioleads.git
```

Either way you now have a bioleads folder on your computer.

---

## Step 2 — Launch it

**Open the `launchers` folder inside that folder and double-click:**

- **Mac:** `Launch bioleads.command`
- **Windows:** `Launch bioleads.bat`

That's it. The **first** launch takes a few minutes: it builds a private, isolated
conda environment (named `bioleads`) containing Python, the app, and its full pipeline (PDF input, PubMed fetching, graph viz), then opens the app.
**Every launch after that opens straight away.**

> **Mac note:** the first time, macOS may say the file is from an unidentified developer.
> Right-click (or Control-click) the file → **Open** → **Open**. You only do this once.

> **Mac, if double-clicking does nothing:** the file may have lost its executable flag
> when unzipped. In Terminal, run `chmod +x "<your folder>/launchers/Launch bioleads.command"` once,
> then double-click again.

---

## Step 3 — Use it

1. **Point it at your papers** — local PDFs, a PubMed query, or both.
2. **Run the pipeline.** It extracts entities (genes, diseases, chemicals,
   phenotypes), ranks the terms that are over-represented against a background
   corpus, and builds a co-occurrence network.
3. **Explore the output** — ranked term tables, the co-occurrence graph, and
   Swanson-style ABC hypothesis leads.

Two heavy extras are opt-in: `pip install -e ".[ner]"` for scispaCy biomedical NER
(plus a model download) and `pip install -e ".[embed]"` for PubMedBERT term
clustering. See the **Environment** section in the README.

If you'd rather work in a Terminal:

```bash
conda activate bioleads
bioleads --help
bioleads-gui
```

---

## Updating later

- **GitHub Desktop (Option B):** open it and click **Fetch / Pull origin**.
- **`git clone` (Option C):** `cd ~/Documents/bioleads && git pull`.
- **Downloaded the ZIP (Option A):** download a fresh ZIP and replace the old
  folder's contents (keep the same folder name and location).

The environment installs the code in "editable" mode, so an update takes effect the next
time you launch. If a release changes the dependencies, delete the environment and let
the launcher rebuild it:

```bash
conda env remove -n bioleads
```

---

## If something goes wrong

- **"conda: command not found"** — close and reopen Terminal after installing conda. On
  Mac, `source ~/miniforge3/bin/activate` once if it still isn't found.
- **The launcher says it can't find conda** — install it from the
  [install-from-scratch guide](INSTALL.md), then double-click again.
- **Setup failed partway through** — remove the half-built environment with
  `conda env remove -n bioleads` and launch again.
- **The window doesn't appear** — check the Terminal window the launcher opened; any
  error is printed there.


Stuck? Send Dan the exact command you ran and the message you got.
