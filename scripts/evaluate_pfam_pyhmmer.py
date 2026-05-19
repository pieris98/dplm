#!/usr/bin/env python
import argparse
import json
from pathlib import Path

from pyhmmer import easel, plan7


def load_jsonl(path):
    with Path(path).open() as handle:
        return [json.loads(line) for line in handle]


def family_id(accession):
    accession = accession.decode() if isinstance(accession, bytes) else accession
    return accession.split(".", 1)[0]


def build_hmms(seed_path, train_rows):
    train_ids_by_family = {}
    for row in train_rows:
        train_ids_by_family.setdefault(row["family_id"], set()).add(row["source_sequence_id"])

    alphabet = easel.Alphabet.amino()
    builder = plan7.Builder(alphabet)
    background = plan7.Background(alphabet)
    hmms = {}

    with easel.MSAFile(str(seed_path), digital=True, alphabet=alphabet) as msas:
        for msa in msas:
            fid = family_id(msa.accession)
            if fid not in train_ids_by_family:
                continue
            keep = []
            for idx, seq in enumerate(msa.sequences):
                name = seq.textize().name.decode() if isinstance(seq.textize().name, bytes) else seq.textize().name
                if name in train_ids_by_family[fid]:
                    keep.append(idx)
            if len(keep) < 2:
                continue
            train_msa = msa.select(keep)
            hmm, _, _ = builder.build_msa(train_msa, background)
            hmms[fid] = hmm
            if len(hmms) == len(train_ids_by_family):
                break
    return alphabet, hmms


def make_sequence_block(alphabet, row):
    seq = easel.TextSequence(name=row["id"].encode(), sequence=row["sequence"])
    block = easel.DigitalSequenceBlock(alphabet)
    block.append(seq.digitize(alphabet))
    return block


def main():
    parser = argparse.ArgumentParser(description="Evaluate held-out Pfam rows with pyhmmer-built train HMMs.")
    parser.add_argument("--seed-msas", default="data-raw/pfam/Pfam-A.seed.gz")
    parser.add_argument("--train", default="data-bin/pfam_family/train.jsonl")
    parser.add_argument("--eval", default="data-bin/pfam_family/valid.jsonl")
    parser.add_argument("--output", default="data-bin/pfam_family/pyhmmer_eval.json")
    args = parser.parse_args()

    train_rows = load_jsonl(args.train)
    eval_rows = load_jsonl(args.eval)
    alphabet, hmms = build_hmms(args.seed_msas, train_rows)
    pipeline = plan7.Pipeline(alphabet)

    per_family = {}
    rows = []
    for row in eval_rows:
        block = make_sequence_block(alphabet, row)
        best = None
        for fid, hmm in hmms.items():
            hits = pipeline.search_hmm(hmm, block)
            if not hits:
                continue
            hit = hits[0]
            candidate = {
                "family_id": fid,
                "score": float(hit.score),
                "evalue": float(hit.evalue),
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
        predicted = None if best is None else best["family_id"]
        is_hit = predicted == row["family_id"]
        rows.append(
            {
                "id": row["id"],
                "target_family_id": row["family_id"],
                "predicted_family_id": predicted,
                "target_hit": is_hit,
                "best_score": None if best is None else best["score"],
                "best_evalue": None if best is None else best["evalue"],
            }
        )
        stats = per_family.setdefault(row["family_id"], {"total": 0, "target_hits": 0, "no_hits": 0})
        stats["total"] += 1
        stats["target_hits"] += int(is_hit)
        stats["no_hits"] += int(best is None)

    total = len(rows)
    target_hits = sum(row["target_hit"] for row in rows)
    no_hits = sum(row["predicted_family_id"] is None for row in rows)
    result = {
        "method": "pyhmmer_train_msa_hmm_smoke_eval",
        "note": "Builds one HMM per selected family from train split seed alignments and scans held-out rows against those HMMs. This is a real Pfam/pyhmmer smoke evaluation, not the final full Pfam-A hmmscan benchmark.",
        "num_train_rows": len(train_rows),
        "num_eval_rows": total,
        "num_hmms": len(hmms),
        "target_family_hit_rate": 0.0 if total == 0 else target_hits / total,
        "no_hit_rate": 0.0 if total == 0 else no_hits / total,
        "wrong_family_hit_rate": 0.0 if total == 0 else (total - target_hits - no_hits) / total,
        "per_family": per_family,
        "rows": rows,
    }
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
