"""Standalone audit of the InterProScan zero-recovery result.

Recomputes, without going through score_function_eval.py:
  1. join integrity  — FASTA ids vs IPS-TSV ids vs manifest ids (staleness,
     renamed headers, dropped proteins);
  2. raw set overlap — hand-computed TP/FP/FN from accession sets, per arm;
  3. reproduction    — set_match_metrics() on the same inputs, to confirm the
     scorer's numbers (and that micro-AP has a nonzero baseline even for
     disjoint binary matrices — an artifact, not hidden overlap);
  4. what IPS *does* find — analysis sources, signature descriptions, unique
     accessions, per-protein annotation rates, sequence lengths;
  5. prompt-side stats — proteins with empty IPR prompts, GT set sizes;
  6. per-label recovery — whether any generation under label group L
     recovers any domain prompted for that group;
  7. optional composition control — writes a composition-preserving shuffled
     version of the real arm (domains destroyed) for an IPS baseline run.

Usage:
  python scripts/eval_function/diagnose_ips.py --evaldir eval_runs/fn_eval_v1 \
      [--arms ours_cond,ours_null,vanilla,real] [--shuffle-real DIR]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from byprot.eval.function import set_match_metrics  # noqa: E402
from byprot.eval.predictors import parse_interproscan_tsv  # noqa: E402


def read_fasta(path):
    seqs, name = {}, None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith(">"):
                name = line[1:].split()[0]
                seqs[name] = ""
            elif name is not None and line:
                seqs[name] += line
    return seqs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evaldir", required=True)
    ap.add_argument("--arms", default="ours_cond,ours_null,vanilla,real")
    ap.add_argument("--shuffle-real", default=None,
                    help="write a composition-preserved shuffled real FASTA here")
    ap.add_argument("--shuffle-arm", default=None,
                    help="write a composition-preserved shuffled FASTA of this arm here")
    args = ap.parse_args()

    evdir = Path(args.evaldir)
    manifest = json.load(open(evdir / "manifest.json"))
    go_inv = {v: k for k, v in manifest["label_map"]["go"].items()}
    ipr_inv = {v: k for k, v in manifest["label_map"]["ipr"].items()}

    def write_shuffled(seqs, dst_dir):
        rng = random.Random(0)
        out = []
        for sid, seq in seqs.items():
            l = list(seq)
            rng.shuffle(l)
            out.append(f">{sid}\n{''.join(l)}")
        Path(dst_dir).mkdir(parents=True, exist_ok=True)
        dst = Path(dst_dir) / "aatype.fasta"
        dst.write_text("\n".join(out) + "\n")
        print(f"wrote shuffled control: {dst} ({len(seqs)} seqs)")

    if args.shuffle_real:
        write_shuffled(read_fasta(evdir / "real" / "aatype.fasta"), args.shuffle_real)
        return
    if args.shuffle_arm:
        write_shuffled(read_fasta(evdir / args.shuffle_arm / "aatype.fasta"),
                       str(evdir / f"{args.shuffle_arm}_shuf"))
        return

    for arm in [a.strip() for a in args.arms.split(",") if a.strip()]:
        d = evdir / arm
        fasta, ips = d / "aatype.fasta", d / "ips.tsv"
        if not fasta.exists() or not ips.exists():
            print(f"== {arm}: missing fasta/ips, skipping")
            continue
        seqs = read_fasta(fasta)
        parsed = parse_interproscan_tsv(str(ips))
        recs = {r["seq_id"]: r for r in manifest["arms"].get(arm, [])}

        print(f"\n===== {arm} =====")
        # -- 1. join integrity ------------------------------------------------
        ids_fa, ids_ips, ids_man = set(seqs), set(parsed), set(recs)
        print(f"[join] fasta={len(ids_fa)}  ips={len(ids_ips)}  manifest={len(ids_man)}")
        print(f"[join] ips-not-in-fasta (stale ips?): {len(ids_ips - ids_fa)}"
              f"  fasta-not-in-ips (ips unannotated): {len(ids_fa - ids_ips)}"
              f"  fasta-not-in-manifest: {len(ids_fa - ids_man)}")
        if ids_ips - ids_fa:
            print(f"[join]   e.g. {sorted(ids_ips - ids_fa)[:3]}")

        # -- 2. raw overlap on accession sets ---------------------------------
        tp = fp = fn = 0
        n_gt_empty = n_prots_recovered = 0
        gt_sizes, recovered_examples = [], []
        per_label_gen = {}   # label -> Counter of predicted accessions
        per_label_gt = {}    # label -> set of prompted accessions
        for sid in seqs:
            pred = parsed.get(sid, {}).get("ipr", set())
            r = recs.get(sid)
            gt = {ipr_inv[int(t)] for t in (r["prompt_ipr"] if r else [])
                  if int(t) in ipr_inv} if r else set()
            gt_sizes.append(len(gt))
            if not gt:
                n_gt_empty += 1
            inter = pred & gt
            tp += len(inter)
            fp += len(pred - gt)
            fn += len(gt - pred)
            if inter:
                n_prots_recovered += 1
                if len(recovered_examples) < 5:
                    recovered_examples.append((sid, sorted(inter)[:3]))
            labs = r["prompt_go"] if r else []
            for t in labs:
                per_label_gen.setdefault(int(t), Counter()).update(pred)
                per_label_gt.setdefault(int(t), set()).update(gt)
        print(f"[raw ] GT: proteins with EMPTY ipr prompt = {n_gt_empty}/{len(seqs)};"
              f" mean GT size = {sum(gt_sizes)/max(len(gt_sizes),1):.2f}")
        print(f"[raw ] TP={tp}  FP={fp}  FN={fn}  -> precision={tp/max(tp+fp,1):.6f}"
              f"  recall={tp/max(tp+fn,1):.6f}  proteins-with-recovery={n_prots_recovered}")
        if recovered_examples:
            print(f"[raw ] recovered examples: {recovered_examples}")

        # -- 3. reproduce scorer numbers --------------------------------------
        ids = list(seqs)
        pred_sets = [parsed.get(s, {}).get("ipr", set()) for s in ids]
        gt_sets = [{ipr_inv[int(t)] for t in recs[s]["prompt_ipr"] if int(t) in ipr_inv}
                   if s in recs else set() for s in ids]
        m = set_match_metrics(pred_sets, gt_sets)
        print(f"[repr] set_match_metrics: F1micro={m.get('f1_micro'):.6f}"
              f" Pmicro={m.get('precision_micro'):.6f}"
              f" APmicro={m.get('aupr_micro'):.6f}"
              f" AUCmicro={m.get('auc_roc_micro'):.6f}")

        # -- 4. what IPS does find --------------------------------------------
        hits = [h for s in ids for h in _ips_rows(ips, s)]
        src = Counter(h["analysis"] for h in hits)
        sig = Counter((h["analysis"], h["sig_desc"][:40]) for h in hits)
        accs = Counter(h["ipr"] for h in hits if h["ipr"] != "-")
        n_ann = len({h["prot"] for h in hits})
        lens = [len(s) for s in seqs.values()]
        ann_lens = [len(seqs[p]) for p in {h["prot"] for h in hits} if p in seqs]
        print(f"[find] rows={len(hits)}  proteins-annotated={n_ann}/{len(seqs)}"
              f"  unique IPR={len(accs)}"
              f"  len(mean) all={sum(lens)/len(lens):.0f}"
              f" annotated={sum(ann_lens)/max(len(ann_lens),1):.0f}")
        print(f"[find] top sources: {src.most_common(5)}")
        print(f"[find] top signatures: {sig.most_common(5)}")
        print(f"[find] top IPR accs: {accs.most_common(8)}")

        # -- 6. per-label recovery --------------------------------------------
        rec_labels = [l for l in per_label_gt if per_label_gen.get(l, Counter())
                      and (per_label_gen[l].keys() & per_label_gt[l])]
        print(f"[label] label-groups with >=1 recovered prompted domain:"
              f" {len(rec_labels)}/{len(per_label_gt)}")


def _ips_rows(ips_path, prot=None):
    """Yield dicts for each IPS TSV row (optionally one protein)."""
    out = []
    with open(ips_path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 6:
                continue
            if prot is not None and f[0] != prot:
                continue
            out.append({"prot": f[0], "analysis": f[3], "sig_acc": f[4],
                        "sig_desc": f[5] if len(f) > 5 else "",
                        "ipr": f[11] if len(f) > 11 else "-"})
    return out


if __name__ == "__main__":
    main()
