#!/usr/bin/env python
import argparse
import gzip
import json
import random
from collections import Counter
from pathlib import Path

from pyhmmer import easel


RARE_TO_X = str.maketrans({aa: "X" for aa in "BZUOJ"})
ALLOWED = set("ACDEFGHIKLMNPQRSTVWYX")


def clean_sequence(sequence):
    sequence = sequence.replace("-", "").replace(".", "").upper().translate(RARE_TO_X)
    if any(aa not in ALLOWED for aa in sequence):
        return None
    return sequence


def family_id(accession):
    accession = accession.decode() if isinstance(accession, bytes) else accession
    return accession.split(".", 1)[0]


def as_text(value):
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


def iter_pfam_seed(path, min_len, max_len):
    alphabet = easel.Alphabet.amino()
    with easel.MSAFile(str(path), digital=True, alphabet=alphabet) as msas:
        for msa in msas:
            fid = family_id(msa.accession)
            rows = []
            for seq in msa.sequences:
                text_seq = seq.textize()
                sequence = clean_sequence(text_seq.sequence)
                if sequence is None or not (min_len <= len(sequence) <= max_len):
                    continue
                source_id = as_text(text_seq.name)
                rows.append(
                    {
                        "id": f"{fid}|{source_id}",
                        "sequence": sequence,
                        "family_id": fid,
                        "family_accession": as_text(msa.accession),
                        "family_name": as_text(msa.name),
                        "description": as_text(msa.description),
                        "clan": None,
                        "source_sequence_id": source_id,
                        "length": len(sequence),
                        "source": "pfam_a_seed",
                    }
                )
            yield fid, rows


def write_jsonl(path, rows):
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Prepare small Pfam family JSONL splits.")
    parser.add_argument("--input", default="data-raw/pfam/Pfam-A.seed.gz")
    parser.add_argument("--output-dir", default="data-bin/pfam_family")
    parser.add_argument("--max-families", type=int, default=8)
    parser.add_argument("--min-family-size", type=int, default=20)
    parser.add_argument("--max-examples-per-family", type=int, default=128)
    parser.add_argument("--min-len", type=int, default=50)
    parser.add_argument("--max-len", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    selected = []
    skipped = Counter()
    for fid, rows in iter_pfam_seed(args.input, args.min_len, args.max_len):
        dedup = {}
        for row in rows:
            dedup.setdefault(row["sequence"], row)
        rows = list(dedup.values())
        if len(rows) < args.min_family_size:
            skipped["too_small"] += 1
            continue
        rng.shuffle(rows)
        selected.append((fid, rows[: args.max_examples_per_family]))
        if len(selected) >= args.max_families:
            break

    train, valid, test = [], [], []
    family_vocab = {}
    family_metadata = {}
    per_family_counts = {}
    for idx, (fid, rows) in enumerate(selected):
        family_vocab[fid] = idx
        family_metadata[fid] = {
            "family_accession": rows[0]["family_accession"],
            "family_name": rows[0]["family_name"],
            "description": rows[0]["description"],
        }
        for row in rows:
            row["family_idx"] = idx
        n_valid = max(1, int(round(len(rows) * 0.05)))
        n_test = max(1, int(round(len(rows) * 0.05)))
        valid.extend(rows[:n_valid])
        test.extend(rows[n_valid : n_valid + n_test])
        train.extend(rows[n_valid + n_test :])
        per_family_counts[fid] = {
            "total": len(rows),
            "train": max(0, len(rows) - n_valid - n_test),
            "valid": n_valid,
            "test": n_test,
        }

    write_jsonl(output_dir / "train.jsonl", train)
    write_jsonl(output_dir / "valid.jsonl", valid)
    write_jsonl(output_dir / "test.jsonl", test)
    (output_dir / "family_vocab.json").write_text(json.dumps(family_vocab, indent=2, sort_keys=True) + "\n")
    (output_dir / "family_metadata.json").write_text(json.dumps(family_metadata, indent=2, sort_keys=True) + "\n")

    stats = {
        "source": args.input,
        "parser_backend": "pyhmmer",
        "split_strategy": "random_debug",
        "seed": args.seed,
        "min_len": args.min_len,
        "max_len": args.max_len,
        "min_family_size": args.min_family_size,
        "max_examples_per_family": args.max_examples_per_family,
        "num_families": len(selected),
        "num_train": len(train),
        "num_valid": len(valid),
        "num_test": len(test),
        "per_family_counts": per_family_counts,
        "skipped": dict(skipped),
    }
    (output_dir / "preprocess_stats.json").write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
    print(json.dumps(stats, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
