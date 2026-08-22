"""PubMedBERT embeddings + semantic clustering of terms.

Used to group synonyms / related concepts so the ranked term list isn't
fragmented across surface variants. Optional: needs `bioleads[embed]`.

Clustering is HDBSCAN by default, so the number of groups comes from the data
instead of from a number someone had to guess before seeing the terms; KMeans
with an explicit k is still selectable via cfg.cluster_method.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .config import Config


@dataclass
class TermCluster:
    cluster_id: int
    terms: list[str]
    centroid_term: str
    # HDBSCAN leaves terms that sit in no dense region unassigned. They are
    # collected into one bucket with cluster_id -1 rather than dropped, so the
    # CSV, the scatter and the graph coloring still account for every term.
    is_noise: bool = False


@lru_cache(maxsize=2)
def _load_embedder(model_name: str):
    """Load and cache a tokenizer+model pair, like ner._load_scispacy does.

    Without this every call re-read ~400MB of weights from disk, so a single
    clustering run or relevance sweep paid the load cost dozens of times. Held
    for two models so switching cfg.embed_model once doesn't thrash; each entry
    keeps its weights resident.
    """
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name)
    model.eval()
    return tok, model


def _embed(texts: list[str], cfg: Config, *, max_length: int) -> np.ndarray:
    """Mean-pooled PubMedBERT embeddings for `texts`, truncated to max_length."""
    try:
        import torch

        tok, model = _load_embedder(cfg.embed_model)
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            'Embeddings need transformers + torch. Install: pip install "bioleads[embed]"'
        ) from e

    vecs = []
    with torch.no_grad():
        for i in range(0, len(texts), cfg.embed_batch_size):
            batch = texts[i : i + cfg.embed_batch_size]
            enc = tok(batch, padding=True, truncation=True,
                      max_length=max_length, return_tensors="pt")
            out = model(**enc).last_hidden_state          # (B, T, H)
            mask = enc["attention_mask"].unsqueeze(-1)     # (B, T, 1)
            pooled = (out * mask).sum(1) / mask.sum(1).clamp(min=1)
            vecs.append(pooled.cpu().numpy())
    return np.vstack(vecs)


def embed_terms(terms: list[str], cfg: Config | None = None) -> np.ndarray:
    """Return an (n_terms, hidden) array of mean-pooled PubMedBERT embeddings."""
    cfg = cfg or Config()
    return _embed(terms, cfg, max_length=32)


def embed_texts(texts: list[str], cfg: Config | None = None) -> np.ndarray:
    """Embed longer texts (titles + abstracts) for document-level similarity."""
    cfg = cfg or Config()
    return _embed(texts, cfg, max_length=256)


def _auto_min_cluster_size(n: int) -> int:
    """HDBSCAN's one real knob, derived from the term count instead of asked for.

    min_cluster_size is the smallest group HDBSCAN will call a cluster; it is
    also, indirectly, how many clusters you get. The point of clustering here is
    to collapse surface variants of one concept (`nmdar` / `nmda receptor`), and
    those groups are small, so this stays small: sqrt(n)/3, floored at 3 and
    capped at 5. A 200-term list gets 5, a 50-term list 3, a short list 3.

    The floor is 3 rather than 2 because pairs are not worth splitting a group
    over: at 2 any two terms that happen to sit slightly closer than their
    neighbors break off as their own "cluster", and one real concept comes back
    shattered into pairs.
    """
    return max(3, min(5, round(n ** 0.5 / 3)))


_DENSITY_DIMS = 10   # see _reduce_for_density


def _reduce_for_density(embeddings: np.ndarray, cfg: Config) -> np.ndarray:
    """Centre and PCA-reduce embeddings so density means something.

    Two properties of PubMedBERT term vectors break density clustering in the
    raw space, and both are fixed here:

    * They are extremely anisotropic — mean pairwise cosine ~0.93 over a
      typical ranked term list, i.e. one direction shared by all biomedical
      text (the same effect Config.relevance_center describes for the gate).
      Every term is then a near neighbour of every other, and HDBSCAN returns
      one giant blob: measured on a 74-term list, 67 terms in a single
      cluster. Subtracting the cloud's mean removes that shared direction.
    * They are 768-dimensional, where distances concentrate and any density
      estimate goes flat. Projecting onto the leading ~10 components is the
      usual working range for HDBSCAN; measured on the same list it is the
      difference between one blob and eleven readable groups (nmda receptor /
      glutamate receptor / ampa receptor together, blood pressure with aorta,
      the imaging methods with each other).

    KMeans has neither problem — it partitions whatever it is given — so it
    keeps the full normalized space.
    """
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import normalize

    X = np.asarray(embeddings, dtype=float)
    n, d = X.shape
    k = min(_DENSITY_DIMS, n - 1, d)
    if k < 2:                      # too few terms to project; use as-is
        return normalize(X)
    X = normalize(X - X.mean(axis=0))
    return normalize(PCA(n_components=k, random_state=cfg.seed).fit_transform(X))


def _hdbscan_labels(embeddings: np.ndarray, cfg: Config, say) -> np.ndarray | None:
    """Density-based labels, or None if no dense group exists (or no HDBSCAN).

    Euclidean distance on L2-normalized vectors is monotonic in cosine
    (|a-b|^2 = 2 - 2cos), so the density HDBSCAN sees is cosine density.
    """
    kwargs = {"copy": True}   # sklearn's would otherwise sort Z in place
    try:
        from sklearn.cluster import HDBSCAN            # scikit-learn >= 1.3
    except ImportError:                                # pragma: no cover
        try:
            from hdbscan import HDBSCAN                # the standalone package
        except ImportError:
            say("  HDBSCAN unavailable (needs scikit-learn >= 1.3); "
                "using KMeans instead.")
            return None
        kwargs = {}                                    # no copy= on that one

    n = embeddings.shape[0]
    mcs = cfg.min_cluster_size or _auto_min_cluster_size(n)
    mcs = max(2, min(mcs, n))          # 2 is HDBSCAN's own floor
    say(f"HDBSCAN: grouping {n} terms — min cluster size {mcs}, "
        "number of clusters inferred from the embedding density…")
    Z = _reduce_for_density(embeddings, cfg)
    # min_samples is what makes HDBSCAN conservative: left at its default (=
    # min_cluster_size) most of a term list lands in noise, because term
    # embeddings are a fairly uniform cloud. 1 is the least conservative
    # setting and keeps the unclustered bucket to the genuine loners.
    labels = HDBSCAN(min_cluster_size=mcs, min_samples=1, **kwargs).fit_predict(Z)
    if not (labels >= 0).any():
        say("  no dense group found; using KMeans instead.")
        return None
    return np.asarray(labels)


def _kmeans_labels(X: np.ndarray, cfg: Config, say) -> np.ndarray:
    """Fixed-k labels: every term assigned, k taken from cfg.n_clusters."""
    from sklearn.cluster import KMeans

    k = max(1, min(cfg.n_clusters, X.shape[0]))
    say(f"KMeans: grouping {X.shape[0]} terms into {k} cluster(s)…")
    return KMeans(n_clusters=k, random_state=cfg.seed, n_init=10).fit(X).labels_


def _group(terms: list[str], X: np.ndarray, labels) -> list[TermCluster]:
    """Turn per-term labels into TermClusters: real groups first, largest first,
    renumbered 0..k-1, each labeled by its medoid; unassigned terms last as -1.

    Renumbering matters for HDBSCAN, whose label numbers carry no order at all;
    doing it for both methods keeps `#0` meaning "the biggest group" whichever
    one ran.
    """
    labels = np.asarray(labels)
    groups = [
        (lab, np.where(labels == lab)[0])
        for lab in sorted(set(int(l) for l in labels))
        if lab >= 0
    ]
    groups.sort(key=lambda g: len(g[1]), reverse=True)

    clusters: list[TermCluster] = []
    for cid, (_lab, idx) in enumerate(groups):
        center = X[idx].mean(axis=0)
        medoid = terms[idx[int(np.argmin(np.linalg.norm(X[idx] - center, axis=1)))]]
        clusters.append(
            TermCluster(cluster_id=cid,
                        terms=[terms[i] for i in idx],
                        centroid_term=medoid)
        )

    noise = np.where(labels < 0)[0]
    if len(noise):
        # No centroid: the bucket is not a concept, it is what is left over.
        clusters.append(
            TermCluster(cluster_id=-1,
                        terms=[terms[i] for i in noise],
                        centroid_term="",
                        is_noise=True)
        )
    return clusters


def cluster_terms(
    terms: list[str], cfg: Config | None = None, embeddings: np.ndarray | None = None,
    progress=None,
) -> list[TermCluster]:
    """Group terms in PubMedBERT space; returns the grouped terms.

    Default is **HDBSCAN**, which reads the number of groups off the density of
    the embedding cloud rather than being told it, and leaves terms that belong
    to no group in an unclustered bucket (cluster_id -1, `is_noise`). Set
    cfg.cluster_method = "kmeans" for the old fixed-k behavior, where
    cfg.n_clusters is the target and every term is forced into some group.
    """
    cfg = cfg or Config()
    say = progress if callable(progress) else (lambda _msg: None)
    if not terms:
        return []
    if embeddings is None:
        say(f"Loading PubMedBERT ({cfg.embed_model}) — first run downloads the "
            "model, this can take a minute…")
        embeddings = embed_terms(terms, cfg)
        say(f"  embedded {len(terms)} term(s)")

    from sklearn.preprocessing import normalize

    method = (cfg.cluster_method or "hdbscan").lower()
    if method not in ("hdbscan", "kmeans"):
        raise ValueError(
            f"unknown cluster_method {cfg.cluster_method!r}; "
            'expected "hdbscan" or "kmeans"')

    embeddings = np.asarray(embeddings, dtype=float)
    # The clustering space and the labelling space differ on purpose: HDBSCAN
    # needs the centred, reduced one to see density at all, while the medoid
    # that names a cluster should be the term nearest its group in the cosine
    # space the embeddings actually live in.
    X = normalize(embeddings)
    labels = _hdbscan_labels(embeddings, cfg, say) if method == "hdbscan" else None
    if labels is None:
        labels = _kmeans_labels(X, cfg, say)

    clusters = _group(terms, X, labels)
    grouped = sum(len(c.terms) for c in clusters if not c.is_noise)
    n_real = sum(1 for c in clusters if not c.is_noise)
    say(f"  {n_real} cluster(s) over {grouped} term(s); "
        f"{len(terms) - grouped} unclustered")
    return clusters


def term_to_cluster(clusters: list[TermCluster]) -> dict[str, int]:
    """Flatten clusters into a {term: cluster_id} map (for graph coloring).

    Unclustered terms are included, mapped to -1, so a caller coloring a graph
    from this map still has an entry for every term rather than a hole.
    """
    return {t: c.cluster_id for c in clusters for t in c.terms}


def _reduce_2d(embeddings: np.ndarray, cfg: Config) -> np.ndarray:
    """Project (n, hidden) embeddings to (n, 2). UMAP if installed, else t-SNE,
    else PCA — chosen for robustness across corpus sizes."""
    from sklearn.preprocessing import normalize

    X = normalize(np.asarray(embeddings, dtype=float))
    n = X.shape[0]

    # Too few points for a manifold method — straight PCA (or pad).
    if n < 5 or X.shape[1] < 2:
        from sklearn.decomposition import PCA

        comp = min(2, X.shape[1], max(1, n - 1))
        Y = PCA(n_components=comp, random_state=cfg.seed).fit_transform(X) if comp >= 1 else X
        if Y.shape[1] < 2:                              # pad to two columns
            Y = np.column_stack([Y, np.zeros(n)])[:, :2]
        return Y

    try:
        import umap  # optional; nicer separation when available

        # min_dist=0.0 packs each neighborhood tight so the gaps BETWEEN clusters
        # widen — the "clean separation" knob. (Default 0.1 leaves them fluffy.)
        return umap.UMAP(
            n_components=2, min_dist=0.0, random_state=cfg.seed,
        ).fit_transform(X)
    except ImportError:
        from sklearn.manifold import TSNE

        perplexity = max(2, min(30, (n - 1) // 3))
        return TSNE(n_components=2, random_state=cfg.seed,
                    perplexity=perplexity, init="pca").fit_transform(X)


def write_cluster_scatter(
    clusters: list[TermCluster],
    path: str,
    cfg: Config | None = None,
    *,
    embeddings: np.ndarray | None = None,
    title: str = "bioleads term clusters",
) -> str | None:
    """Write an interactive 2D scatter of term embeddings, colored by cluster.

    Every clustered term becomes a point (centroids marked with a star and
    labeled); reduction is UMAP/t-SNE/PCA. `embeddings`, if given, must be row-
    aligned to the terms flattened in cluster order; otherwise they are recomputed
    with PubMedBERT. Returns the output path, or None if there's nothing to plot
    or plotly isn't installed (needs the `viz` extra).
    """
    cfg = cfg or Config()
    if not clusters:
        return None

    terms: list[str] = []
    cids: list[int] = []
    is_centroid: list[bool] = []
    for c in clusters:
        for t in c.terms:
            terms.append(t)
            cids.append(c.cluster_id)
            is_centroid.append(t == c.centroid_term)
    if not terms:
        return None

    if embeddings is None:
        embeddings = embed_terms(terms, cfg)          # ImportError -> needs embed extra
    coords = _reduce_2d(embeddings, cfg)

    try:
        import plotly.express as px
    except ImportError:
        print('[bioleads] plotly not installed; skipping term_clusters.html. '
              'Install with: pip install "bioleads[viz]"')
        return None

    import pandas as pd

    centroid_for = {c.cluster_id: c.centroid_term for c in clusters}
    # The unclustered bucket is not a cluster; name it and grey it so it reads
    # as background rather than as one more colored group.
    labels = {c: ("unclustered" if c < 0 else str(c)) for c in set(cids)}
    df = pd.DataFrame({
        "x": coords[:, 0],
        "y": coords[:, 1],
        "term": terms,
        "cluster": [labels[c] for c in cids],         # categorical colors
        "centroid term": [centroid_for[c] or "—" for c in cids],
        "label": [t if cen else "" for t, cen in zip(terms, is_centroid)],
        "size": [16 if cen else 9 for cen in is_centroid],
    })
    fig = px.scatter(
        df, x="x", y="y", color="cluster", text="label", size="size",
        size_max=16, title=title,
        color_discrete_map={"unclustered": "#bbbbbb"},
        hover_data={"term": True, "centroid term": True, "cluster": True,
                    "x": False, "y": False, "label": False, "size": False},
    )
    fig.update_traces(textposition="top center",
                      marker=dict(line=dict(width=0.5, color="#333")))
    fig.update_layout(legend_title_text="cluster",
                      xaxis_title=None, yaxis_title=None,
                      xaxis_showticklabels=False, yaxis_showticklabels=False)
    fig.write_html(path, include_plotlyjs=True, full_html=True)
    return path


def to_dataframe(clusters: list[TermCluster]):
    """One row per term: cluster id, its centroid, and whether it is the centroid."""
    import pandas as pd

    rows = [
        {
            "cluster_id": c.cluster_id,
            "centroid_term": c.centroid_term,
            "term": term,
            "is_centroid": term == c.centroid_term,
        }
        for c in clusters
        for term in c.terms
    ]
    return pd.DataFrame(rows)
