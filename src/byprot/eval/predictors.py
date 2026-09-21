"""Predictor drivers for function recovery: InterProScan and DeepGO-SE.

Both predictors are *external* tools — this module provides (a) subprocess
runners with configurable paths, and (b) tolerant TSV parsers whose outputs
feed :mod:`byprot.eval.function` metrics. Parse paths are unit-testable
without the tools installed.

InterProScan
------------
Run: ``interproscan.sh -i <fasta> -f TSV -goterms --disable-precalc``
TSV columns (tab-separated): 0 protein accession, 1 md5, 2 length, 3
analysis, 4 signature accession, 5 signature description, 6 start, 7 stop,
8 score, 9 status, 10 date, 11 InterPro accession, 12 InterPro description,
13+ GO terms (with -goterms).

DeepGO-SE
---------
Run the deepgo2/deepgose ``predict`` pipeline externally (conda env with
the pretrained MFO/BPO/CCO checkpoints), TSV lines:
``protein_id \t GO:XXXXXXX \t score``. CFP-Gen's ``eval_go.py`` consumes the
same format, so scores are cross-paper comparable.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# InterProScan
# ---------------------------------------------------------------------------

def run_interproscan(
    fasta_path: str,
    out_tsv: str,
    interproscan_path: str = "interproscan.sh",
    goterms: bool = True,
    threads: int = 8,
    timeout_s: Optional[int] = None,
) -> str:
    """Run InterProScan on a FASTA and write TSV output. Returns out_tsv."""
    cmd = [
        str(interproscan_path),
        "-i", str(fasta_path),
        "-f", "TSV",
        "-o", str(out_tsv),
        "-T", str(Path(out_tsv).parent / "ips_tmp"),
        "--disable-precalc",
        "-T", str(Path(out_tsv).parent / "ips_tmp"),
    ]
    if goterms:
        cmd.append("-goterms")
    cmd.extend(["-cpu", str(threads)])
    subprocess.run(cmd, check=True, timeout=timeout_s)
    return out_tsv


def parse_interproscan_tsv(
    tsv_path: str,
) -> Dict[str, Dict[str, set]]:
    """Parse an InterProScan TSV.

    Returns:
        ``{protein_id: {"ipr": {IPR accessions}, "go": {GO:XXXX terms}}}``
        Protein IDs are taken verbatim from column 0 (our FASTA headers
        control the format).
    """
    per_protein: Dict[str, Dict[str, set]] = {}
    with open(tsv_path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 5:
                continue
            prot = fields[0]
            entry = per_protein.setdefault(prot, {"ipr": set(), "go": set()})
            # InterPro accession lives in column 11 (only on InterPro rows).
            if len(fields) > 11 and fields[11] and fields[11] != "-":
                entry["ipr"].add(fields[11])
            # GO terms appear after column 12 with -goterms.
            for field in fields[12:]:
                if field.startswith("GO:"):
                    entry["go"].add(field)
    return per_protein


# ---------------------------------------------------------------------------
# DeepGO-SE
# ---------------------------------------------------------------------------

def run_deepgose(
    fasta_path: str,
    out_tsv: str,
    deepgose_dir: str,
    weights_dir: str,
    branch: str = "mfo",
    python_bin: str = "python",
    timeout_s: Optional[int] = None,
) -> str:
    """Run the deepgose prediction pipeline on a FASTA.

    ``deepgose_dir`` is the cloned deepgo2/deepgose repository; the exact
    entrypoint has historically been
    ``python <deepgose_dir>/predict.py <branch> <fasta> <out> <weights>`` —
    verify against the checkout used for ``weights_dir`` (setup is
    environment-specific; see deepgo2 README).
    """
    cmd = [
        python_bin,
        str(Path(deepgose_dir) / "predict.py"),
        branch,
        str(fasta_path),
        str(out_tsv),
        weights_dir,
    ]
    subprocess.run(cmd, check=True, timeout=timeout_s, cwd=str(deepgose_dir))
    return out_tsv


def parse_deepgose_tsv(tsv_path: str) -> Dict[str, Dict[str, float]]:
    """Parse a DeepGO-SE TSV: ``{protein_id: {GO:XXXX: score}}``.

    ID-convention: our driver writes FASTA headers as
    ``>{seq_id}`` where ``seq_id`` already carries arm/cfg/sample/uniprot
    information; the first tab field is used verbatim.
    """
    per_protein: Dict[str, Dict[str, float]] = {}
    with open(tsv_path) as f:
        for line in f:
            if not line.strip():
                continue
            fields = line.strip().split("\t")
            if len(fields) < 3:
                continue
            prot, go_id, score = fields[0], fields[1], float(fields[2])
            per_protein.setdefault(prot, {})[go_id] = score
    return per_protein


def deepgose_binary_matrix(
    per_protein_scores: Dict[str, Dict[str, float]],
    protein_ids: Sequence[str],
    terms: Sequence[str],
    threshold: float = 0.5,
) -> "np.ndarray":
    """Protein × term binary matrix at a score threshold (for Fmax inputs,
    use threshold=None and call :func:`byprot.eval.function.fmax` with raw
    scores instead)."""
    import numpy as np

    mat = np.zeros((len(protein_ids), len(terms)), dtype=np.float64)
    term_idx = {t: i for i, t in enumerate(terms)}
    for i, pid in enumerate(protein_ids):
        for term, score in per_protein_scores.get(pid, {}).items():
            j = term_idx.get(term)
            if j is not None:
                mat[i, j] = score if threshold is None else float(score >= threshold)
    return mat
