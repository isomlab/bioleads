# Getting started with bioleads

bioleads mines biomedical literature for enriched terms, co-occurrence networks and
Swanson-style hypothesis leads.

Everything the lab tools have in common — downloading, launching, updating, and what
to do when something goes wrong — is on one shared page: **[Getting started with a lab
tool](https://dangerisom.github.io/Isom-Lab/getting-started/)**. This guide covers
only what is specific to bioleads.

---

## Before you start

Your computer needs **conda** (Miniforge, Miniconda, or Anaconda).

> **First time on this computer?** Do the one-time **[Setting up your
> computer](https://dangerisom.github.io/Isom-Lab/setup/)** first, then come back here.
> bioleads's own install notes are in **[INSTALL.md](INSTALL.md)**.

> **Want to know what the program actually does with your papers?**
> **[How bioleads works](how_it_works.md)** walks through every stage of the
> pipeline in plain language.

---

## Get it and launch it

**1. Download it** from **[github.com/isomlab/bioleads](https://github.com/isomlab/bioleads)**.
   It is **public**, so no account or password is needed: **Download ZIP**,
   **GitHub Desktop**, or `git clone`. Step by step:
   **[Get the code](https://dangerisom.github.io/Isom-Lab/getting-started/#public-tools)**.

**2. Open the `launchers` folder inside it and double-click:**

- **Mac:** `Launch bioleads.command`
- **Windows:** `Launch bioleads.bat`

The **first** launch takes a few minutes while it builds a private, isolated conda
environment named `bioleads` containing Python and everything the app needs. Every
launch after that opens straight away. You don't need to type anything.

New to this? **[Launch
it](https://dangerisom.github.io/Isom-Lab/getting-started/#launch-it)** walks through
what you will see, and **[the first-time
hiccups](https://dangerisom.github.io/Isom-Lab/getting-started/#the-first-time-hiccups)**
covers macOS blocking the file, Windows SmartScreen, and a double-click that does
nothing.

---

## Use it

1. **Point it at your papers** — a PubMed query, a list of PMIDs, or a
   reference-manager export.
2. **Run the pipeline.** It extracts entities (genes, diseases, chemicals,
   phenotypes), ranks the terms that carry the most weight in the corpus, and
   builds a co-occurrence network.
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

Refresh the folder the way you got it — see **[Updating
later](https://dangerisom.github.io/Isom-Lab/getting-started/#updating-later)**. If a
release changes what the tool depends on, delete its environment and let the launcher
rebuild it on the next double-click:

```bash
conda env remove -n bioleads
```

---

## If something goes wrong

Most problems are one of a handful of things — start with **[Try these
first](https://dangerisom.github.io/Isom-Lab/getting-started/#try-these-first)**, then
the rest of **[If something goes
wrong](https://dangerisom.github.io/Isom-Lab/getting-started/#if-something-goes-wrong)**.

Stuck? Send Dan (<a href="&#109;&#97;&#105;&#108;&#116;&#111;&#58;&#100;&#105;&#115;&#111;&#109;&#64;&#109;&#105;&#97;&#109;&#105;&#46;&#101;&#100;&#117;">&#100;&#105;&#115;&#111;&#109;<span>&#64;</span>&#109;&#105;&#97;&#109;&#105;<span>&#46;</span>&#101;&#100;&#117;</a>) the exact command you ran and the message you got — the shared page lists **[what to include](https://dangerisom.github.io/Isom-Lab/getting-started/#still-stuck)**.
