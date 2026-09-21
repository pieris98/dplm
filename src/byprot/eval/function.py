"""Function-recovery evaluation metrics for ConditionalDPLM2.

Faithful ports of the CFP-Gen evaluation stack
(``cfpgen/eval/metrics/{spectrum,similarity,conditional}.py``) plus native
implementations of the CAFA protein-centric metrics (Fmax, and helpers for
Smin via the CAFA-evaluator tool). Everything here is dependency-light:
numpy + scikit-learn only; ``obonet`` is optional (GO-ancestor propagation).

Conventions
-----------
* Sequences are plain amino-acid strings.
* Labels are label-ID sets per sequence (e.g. ``{5, 27}``); the caller owns
  the integer↔accession mapping.
* Predictor outputs for a protein are ``{term_id: score}`` dicts (DeepGO-SE)
  or plain term sets (InterProScan).
"""
from __future__ import annotations

import itertools
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

AA_ALPHABET = "ARNDCEQGHILKMFPSTWYV"

# ---------------------------------------------------------------------------
# k-mer spectrum embedding (port of cfpgen metrics/spectrum.py)
# ---------------------------------------------------------------------------

def _make_kmer_trie(k: int) -> dict:
    kmers = ["".join(i) for i in itertools.product(AA_ALPHABET, repeat=k)]
    trie: dict = {}
    for i, kmer in enumerate(kmers):
        node = trie
        for aa in kmer:
            node = node.setdefault(aa, {})
            node.setdefault("kmers", []).append(i)
        # walk to the innermost dict for this kmer's bucket
        node = trie
        for aa in kmer:
            node = node[aa]
    return trie


_TRIE_CACHE: dict = {}


def spectrum_map(
    sequences: Iterable[str],
    k: int = 3,
    mode: str = "count",
    normalize: bool = True,
) -> np.ndarray:
    """Map sequences to L2-normalized k-mer count vectors [n_seqs, 20**k]."""
    sequences = [sequences] if isinstance(sequences, str) else list(sequences)
    if k not in _TRIE_CACHE:
        _TRIE_CACHE[k] = _make_kmer_trie(k)
    trie = _TRIE_CACHE[k]

    out = np.zeros((len(sequences), len(AA_ALPHABET) ** k), dtype=np.float32)
    for si, seq in enumerate(sequences):
        vec = out[si]
        for i in range(len(seq) - k + 1):
            node = trie
            try:
                for letter in seq[i : i + k]:
                    node = node[letter]
            except KeyError:
                continue
            for j in node["kmers"]:
                if mode == "count":
                    vec[j] += 1
                else:  # 'indicate'
                    vec[j] = 1
        if normalize:
            norm = np.sqrt(np.dot(vec, vec))
            if norm != 0:
                vec /= norm
    return out


# ---------------------------------------------------------------------------
# MMD (port of cfpgen metrics/similarity.py)
# ---------------------------------------------------------------------------

def _median_heuristic_gamma(emb: np.ndarray) -> float:
    from sklearn.metrics.pairwise import pairwise_distances

    dists = pairwise_distances(emb, metric="euclidean")
    sigma = np.median(dists)
    if sigma == 0:
        sigma = 1.0
    return 1.0 / (2 * sigma ** 2)


def mmd(
    seq1: Optional[Sequence[str]] = None,
    seq2: Optional[Sequence[str]] = None,
    emb1: Optional[np.ndarray] = None,
    emb2: Optional[np.ndarray] = None,
    mean1: Optional[np.ndarray] = None,
    mean2: Optional[np.ndarray] = None,
    kernel: str = "linear",
    gamma: Optional[float] = None,
    embedding_fn=None,
) -> float:
    """Maximum mean discrepancy between two sequence sets (or embeddings).

    ``embedding_fn`` defaults to :func:`spectrum_map`. With precomputed
    ``mean1``/``mean2`` only the linear-kernel closed form is available.
    """
    if embedding_fn is None:
        embedding_fn = spectrum_map
    if emb1 is None and mean1 is None:
        emb1 = embedding_fn(seq1)
    if emb2 is None and mean2 is None:
        emb2 = embedding_fn(seq2)

    if mean1 is not None and mean2 is not None:
        return float(
            np.sqrt(np.dot(mean1, mean1) + np.dot(mean2, mean2) - 2 * np.dot(mean1, mean2))
        )

    if kernel == "linear":
        x = np.mean(emb1, axis=0)
        y = np.mean(emb2, axis=0)
        return float(np.sqrt(np.dot(x, x) + np.dot(y, y) - 2 * np.dot(x, y)))
    elif kernel == "gaussian":
        x, y = np.asarray(emb1), np.asarray(emb2)
        m, n = x.shape[0], y.shape[0]
        if gamma is None:
            gamma = _median_heuristic_gamma(np.vstack([x, y]))
        from sklearn.metrics.pairwise import rbf_kernel

        kxx = rbf_kernel(x, x, gamma=gamma)
        kxy = rbf_kernel(x, y, gamma=gamma)
        kyy = rbf_kernel(y, y, gamma=gamma)
        return float(
            np.sqrt(
                np.sum(kxx) / m**2 - 2 * np.sum(kxy) / (m * n) + np.sum(kyy) / n**2
            )
        )
    raise ValueError(f"unknown kernel {kernel!r}")


# ---------------------------------------------------------------------------
# MRR (port of cfpgen metrics/conditional.py; hierarchy flags deferred)
# ---------------------------------------------------------------------------

def mrr(
    generated: Sequence[str],
    generated_labels: Sequence[Iterable[int]],
    reference: Sequence[str],
    reference_labels: Sequence[Iterable[int]],
    embedding_fn=None,
) -> Tuple[float, Dict[int, int]]:
    """Mean reciprocal rank of generated label-groups against reference groups.

    For every label ``L`` with generated sequences, the mean spectrum
    embedding of those sequences is ranked (by linear-kernel MMD) among the
    mean embeddings of all reference label-groups. MRR=1 means every
    generated group lands closest to its own reference group.

    Args:
        generated: generated sequences.
        generated_labels: per-sequence label-ID sets.
        reference: real (held-out) sequences.
        reference_labels: per-sequence label-ID sets.
        embedding_fn: optional custom embedding (default spectrum_map).

    Returns:
        (mrr, {label: rank})
    """
    if embedding_fn is None:
        embedding_fn = spectrum_map
    emb_gen = embedding_fn(generated)
    emb_ref = embedding_fn(reference)

    def group(embs, labels):
        groups: Dict[int, List[np.ndarray]] = {}
        for e, labs in zip(embs, labels):
            for lab in labs:
                groups.setdefault(int(lab), []).append(e)
        return groups

    groups_gen = group(emb_gen, generated_labels)
    groups_ref = group(emb_ref, reference_labels)

    means_gen = {t: np.mean(v, axis=0) for t, v in groups_gen.items()}
    means_ref = {t: np.mean(v, axis=0) for t, v in groups_ref.items()}

    ranks: Dict[int, int] = {}
    ref_terms = sorted(means_ref)
    for term, mean_g in means_gen.items():
        dists = [mmd(mean1=mean_g, mean2=means_ref[t2]) for t2 in ref_terms]
        order = np.argsort(dists)
        ranked_terms = [ref_terms[i] for i in order]
        ranks[term] = ranked_terms.index(term) + 1
    if not ranks:
        return float("nan"), ranks
    return float(np.mean([1.0 / r for r in ranks.values()])), ranks


# ---------------------------------------------------------------------------
# Multi-label set-match metrics (CFP-Gen eval_go/eval_ipr protocol)
# ---------------------------------------------------------------------------

def set_match_metrics(
    pred_sets: Sequence[Iterable],
    gt_sets: Sequence[Iterable],
) -> Dict[str, float]:
    """Micro/macro precision/recall/F1 over per-protein label sets.

    Mirrors ``cfpgen/eval/eval_go.py:116-131``: MultiLabelBinarizer over the
    union of observed labels, then sklearn micro/macro averages.
    """
    from sklearn.preprocessing import MultiLabelBinarizer
    from sklearn.metrics import (
        precision_score,
        recall_score,
        f1_score,
        roc_auc_score,
        average_precision_score,
    )

    mlb = MultiLabelBinarizer()
    mlb.fit(list(gt_sets) + list(pred_sets))
    y_true = mlb.transform(gt_sets)
    y_pred = mlb.transform(pred_sets)
    if y_true.shape[1] == 0:
        return {}
    out = {
        "precision_micro": precision_score(y_true, y_pred, average="micro", zero_division=0),
        "recall_micro": recall_score(y_true, y_pred, average="micro", zero_division=0),
        "f1_micro": f1_score(y_true, y_pred, average="micro", zero_division=0),
        "precision_macro": precision_score(y_true, y_pred, average="macro", zero_division=0),
        "recall_macro": recall_score(y_true, y_pred, average="macro", zero_division=0),
        "f1_macro": f1_score(y_true, y_pred, average="macro", zero_division=0),
    }
    # AUPR/AUC on binarized predictions (CFP-Gen computes these the same way)
    try:
        out["auc_roc_macro"] = roc_auc_score(y_true, y_pred, average="macro")
        out["auc_roc_micro"] = roc_auc_score(y_true, y_pred, average="micro")
        out["aupr_macro"] = average_precision_score(y_true, y_pred, average="macro")
        out["aupr_micro"] = average_precision_score(y_true, y_pred, average="micro")
    except ValueError:
        pass  # single-class edge cases
    return out


# ---------------------------------------------------------------------------
# CAFA protein-centric Fmax
# ---------------------------------------------------------------------------

def propagate_ancestors(
    term_sets: Sequence[Iterable[str]],
    parents: Dict[str, set],
) -> List[set]:
    """Expand each term set with all its GO ancestors.

    ``parents`` maps term → direct parent terms (from go.obo via obonet).
    """
    out = []
    for terms in term_sets:
        seen = set(terms)
        stack = list(terms)
        while stack:
            t = stack.pop()
            for p in parents.get(t, ()):  # breadth over the DAG
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        out.append(seen)
    return out


def fmax(
    scores: np.ndarray,
    gt_binary: np.ndarray,
    thresholds: Optional[Sequence[float]] = None,
) -> float:
    """CAFA protein-centric maximum F1 over prediction thresholds.

    Args:
        scores: [n_proteins, n_terms] per-term probabilities (any range).
        gt_binary: [n_proteins, n_terms] 0/1 ground truth (ancestor-expanded).
        thresholds: score cutoffs swept; default = 100 quantile-spaced points.

    Notes:
        Proteins with no prediction and no ground truth are excluded at each
        threshold (CAFA convention). For the full CAFA protocol including
        Smin, prefer the CAFA-evaluator tool; this covers Fmax natively.
    """
    scores = np.asarray(scores, dtype=np.float64)
    gt_binary = np.asarray(gt_binary, dtype=bool)
    if thresholds is None:
        thresholds = np.linspace(0.001, 0.999, 100)

    best = 0.0
    for t in thresholds:
        pred = scores >= t
        tp = (pred & gt_binary).sum(axis=1).astype(np.float64)
        n_pred = pred.sum(axis=1).astype(np.float64)
        n_gt = gt_binary.sum(axis=1).astype(np.float64)

        with np.errstate(divide="ignore", invalid="ignore"):
            precision = np.where(n_pred > 0, tp / n_pred, 0.0)
            recall = np.where(n_gt > 0, tp / n_gt, 0.0)
        undefined = (n_pred == 0) & (n_gt == 0)
        precision, recall = precision[~undefined], recall[~undefined]
        if precision.size == 0:
            continue
        f1 = np.where(
            precision + recall > 0, 2 * precision * recall / (precision + recall), 0.0
        )
        best = max(best, float(np.mean(f1)))
    return best
