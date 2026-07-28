"""PubMedBERT embeddings + semantic clustering of terms.

Used to group synonyms / related concepts so the ranked term list isn't
fragmented across surface variants. Optional: needs `bioleads[embed]`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config


@dataclass
class TermCluster:
    cluster_id: int
    terms: list[str]
    centroid_term: str


def _embed(texts: list[str], cfg: Config, *, max_length: int) -> np.ndarray:
    """Mean-pooled PubMedBERT embeddings for `texts`, truncated to max_length."""
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            'Embeddings need transformers + torch. Install: pip install "bioleads[embed]"'
        ) from e

    tok = AutoTokenizer.from_pretrained(cfg.embed_model)
    model = AutoModel.from_pretrained(cfg.embed_model)
    model.eval()

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


def cluster_terms(
    terms: list[str], cfg: Config | None = None, embeddings: np.ndarray | None = None,
    progress=None,
) -> list[TermCluster]:
    """KMeans-cluster terms in PubMedBERT space; returns grouped terms."""
    cfg = cfg or Config()
    say = progress if callable(progress) else (lambda _msg: None)
    if not terms:
        return []
    if embeddings is None:
        say(f"Loading PubMedBERT ({cfg.embed_model}) — first run downloads the "
            "model, this can take a minute…")
        embeddings = embed_terms(terms, cfg)
        say(f"  embedded {len(terms)} term(s)")

    from sklearn.cluster import KMeans
    from sklearn.preprocessing import normalize

    X = normalize(embeddings)
    k = min(cfg.n_clusters, len(terms))
    say(f"KMeans: grouping {len(terms)} terms into {k} cluster(s)…")
    km = KMeans(n_clusters=k, random_state=cfg.seed, n_init=10).fit(X)

    clusters: list[TermCluster] = []
    for cid in range(k):
        idx = np.where(km.labels_ == cid)[0]
        if len(idx) == 0:
            continue
        # centroid term = closest to cluster center
        center = km.cluster_centers_[cid]
        dists = np.linalg.norm(X[idx] - center, axis=1)
        centroid_term = terms[idx[int(np.argmin(dists))]]
        clusters.append(
            TermCluster(cluster_id=cid,
                        terms=[terms[i] for i in idx],
                        centroid_term=centroid_term)
        )
    return clusters


def term_to_cluster(clusters: list[TermCluster]) -> dict[str, int]:
    """Flatten clusters into a {term: cluster_id} map (for graph coloring)."""
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
    df = pd.DataFrame({
        "x": coords[:, 0],
        "y": coords[:, 1],
        "term": terms,
        "cluster": [str(c) for c in cids],            # categorical colors
        "centroid term": [centroid_for[c] for c in cids],
        "label": [t if cen else "" for t, cen in zip(terms, is_centroid)],
        "size": [16 if cen else 9 for cen in is_centroid],
    })
    fig = px.scatter(
        df, x="x", y="y", color="cluster", text="label", size="size",
        size_max=16, title=title,
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
