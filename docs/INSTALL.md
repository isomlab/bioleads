# Install from scratch (first time)

This is the **one-time setup of your computer** so it can run bioleads. You don't need
to know how to code — do these two things once, in order. When you're done, go to
**[getting_started.md](getting_started.md)** to download and run the app.

> **Mac vs Windows:** steps are the same. Where Mac says **Terminal**, Windows users use
> **Miniforge Prompt**.

> **No GitHub account needed.** This is a public repository — anyone can
> download it without signing up for anything.

---

## 1. Install Miniforge (free)

Miniforge gives you a private copy of Python plus everything the app needs, without
disturbing anything else on your computer.

**Mac:**
1. Go to **[conda-forge.org/download](https://conda-forge.org/download/)**.
2. Choose the **macOS** installer matching your Mac — **Apple Silicon** (`arm64`) for
   M1/M2/M3/M4, **Intel** (`x86_64`) for older Macs. Use the **`.pkg`** installer.
3. Double-click it and click through, accepting the defaults.
4. **Quit Terminal, then open a fresh one.** Seeing **`(base)`** means it worked.

**Windows:** download the Windows installer from the same page, run it with the
defaults, then open **Miniforge Prompt** from the Start menu.

---

## 2. Install GitHub Desktop (optional, free)

1. Go to **[desktop.github.com](https://desktop.github.com)**, download, and install.
2. Open it — you can skip the sign-in, since this repo is public.

---

## You're set up

- ✅ **conda** (Python + the tools the app needs), and
- ✅ **GitHub Desktop** (optional).

Next, follow **[getting_started.md](getting_started.md)** to download the code and
launch bioleads.

---

### Trouble?

- **After installing Miniforge you don't see `(base)`** — quit Terminal fully and open a
  new window. On Mac, `source ~/miniforge3/bin/activate` once if it still doesn't show.
- **Already have Anaconda or Miniconda?** That works too; any `conda` on your PATH is fine.
