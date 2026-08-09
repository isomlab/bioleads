#!/usr/bin/env python3
"""Check the links in this repository's README and docs/.

    python3 tools/check_doc_links.py              # relative links only, no network
    python3 tools/check_doc_links.py --external   # also the website and other external links

The default pass is offline and deterministic, so it can run on every push: it
confirms that every relative link points at a file that is actually in the
repository. A link to a file that exists only on someone's laptop looks fine
locally and 404s for everybody else, which is the failure this catches.

--external additionally confirms that the anchors this guide links to on the
shared website still exist. Those matter because the guide delegates the shared
steps to that page, and a renamed heading there would drop readers at the top of
it with no sign anything was wrong.

Links to other isomlab repositories are deliberately NOT verified. A token
scoped to this repository cannot see the private ones, so a 404 would mean
"private", not "missing", and the check would cry wolf on every run.
"""
import argparse
import glob
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "isom-lab-doc-link-check"}
SKIP_HOSTS = ("https://github.com/isomlab/",)      # see the module docstring

problems = []
pages = {}


def fetch(url):
    if url not in pages:
        try:
            parts = urllib.parse.urlsplit(url)
            safe = urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, urllib.parse.quote(parts.path), "", ""))
            pages[url] = urllib.request.urlopen(
                urllib.request.Request(safe, headers=UA), timeout=30).read().decode("utf-8", "replace")
        except Exception:                                        # noqa: BLE001
            pages[url] = ""
    return pages[url]


def head_ok(url):
    for _ in range(3):                                           # a flaky host is not a broken link
        try:
            parts = urllib.parse.urlsplit(url)
            safe = urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, urllib.parse.quote(parts.path), parts.query, ""))
            if urllib.request.urlopen(
                    urllib.request.Request(safe, headers=UA, method="HEAD"), timeout=30).status == 200:
                return True
        except urllib.error.HTTPError as e:
            if e.code in (403, 405, 429):
                continue
            return False
        except Exception:                                        # noqa: BLE001
            continue
    return False


def links_in(path):
    text = open(path, encoding="utf-8").read()
    out = re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text)
    out += re.findall(r'href="([^"]+)"', text)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--external", action="store_true")
    args = ap.parse_args()

    files = sorted(set(glob.glob("README.md") + glob.glob("docs/*.md")))
    if not files:
        print("no README.md or docs/*.md found — is this the repository root?")
        return 1

    n_rel = n_ext = n_skipped = 0
    for f in files:
        for link in links_in(f):
            if link.startswith(("mailto:", "#", "&#")):
                continue
            if link.startswith(("http://", "https://")):
                if link.startswith(SKIP_HOSTS):
                    n_skipped += 1
                    continue
                if not args.external:
                    continue
                n_ext += 1
                base, _, frag = link.partition("#")
                if "dangerisom.github.io" in base:
                    body = fetch(base)
                    if not body:
                        problems.append("%s: could not fetch %s" % (f, base))
                    elif frag and 'id="%s"' % frag not in body:
                        problems.append(
                            "%s: %s has no anchor #%s — the heading it points at was renamed"
                            % (f, base, frag))
                elif not head_ok(base):
                    problems.append("%s: external link looks broken: %s" % (f, base))
                continue
            n_rel += 1
            # a filename with spaces is written %20 in markdown, so decode before
            # looking on disk — otherwise a correct link reads as a missing file
            rel = urllib.parse.unquote(link.split("#")[0])
            target = os.path.normpath(os.path.join(os.path.dirname(f), rel))
            if not os.path.exists(target):
                problems.append("%s: relative link %s points at %s, which is not in this "
                                "repository" % (f, link, target))

    print("checked %d file(s): %d relative link(s)%s%s"
          % (len(files), n_rel,
             ", %d external" % n_ext if args.external else "",
             ", %d isomlab link(s) skipped by design" % n_skipped if n_skipped else ""))
    if problems:
        print("\n%d problem(s):" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    print("no broken links")
    return 0


if __name__ == "__main__":
    sys.exit(main())
