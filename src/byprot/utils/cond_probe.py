"""Tier-1 in-training validation callback for ConditionalDPLM-2.

Implements the cheap, predictor-free probe layer from
plans/mode_collapse_debug_plan.md (Phase 3 / P3.1) and RESEARCH_STATE §4.2.4:
external predictors (InterProScan, DeepGO-SE) must never run inside the
training loop, but adapter failure has to be *visible* early. Everything here
runs on rank 0 only, in-process, and costs <1% of training time.

What it does
------------
Every ``probe_every_n_steps`` training steps (rank 0):

  1. **Generation probe** — 8 fixed label prompts x 4 sequences, fixed seed,
     short from-scratch denoising. Writes ``<probe_dir>/step_<n>/aatype.fasta``
     + ``metrics.json``.
  2. **Predictor-free metrics** from the probe sequences, all logged to the
     logger under ``probe/``:
     - ``probe/a_frac`` — mean alanine fraction (the poly-A detector; real
       proteins ~9%, the broken-model attractor was 78%).
     - ``probe/js_real`` — per-AA composition JS (bits) vs a fixed real
       reference subset (noise floor ~0.001 bits).
     - ``probe/mmd`` — linear-kernel k-mer MMD vs the same reference.
     - ``probe/mrr`` — probe label-group MRR vs real label groups
       (condition-sensitivity; chance ~0.07).
     - ``probe/diversity`` — unique-3mer fraction across the probe batch.
     - ``probe/adapter_w``, ``probe/adapter_w_max`` — mean/max per-layer
       adapter ``W_up`` norms (init std 1e-5).
     - ``probe/dh_mean``, ``probe/dh_max`` — adapter update magnitude
       ‖Δh‖/‖h‖ during the probe forward (compounding detector).
  3. **Pre-registered alarm** — ``probe/alarm`` = 1 when ``a_frac`` exceeds
     ``alarm.a_frac_threshold`` at ``alarm.consecutive_probes`` consecutive
     probes after ``alarm.start_step``. Loud warning printed; with
     ``alarm.abort=True`` the trainer stops (pre-registered abort rule).

Every validation (``on_validation_epoch_end``):

  4. **Loss-delta proxy** — the first ``loss_delta_num_batches`` validation
     batches, forward+loss under real conditions vs under the learned null
     condition → ``probe/loss_delta`` (condition-sensitivity curve; should be
     clearly > 0 once conditioning trains).
  5. Adapter gradient norm → ``probe/grad_norm`` (in ``on_before_optimizer_step``).

Reference assets are built lazily at train start from the datamodule's train
split (fixed seeded subset); the callback never touches external predictors.
"""

import json
import math
import os
import traceback
from typing import Dict, List, Optional

import numpy as np
import pytorch_lightning as pl
import torch

from byprot.eval.function import (
    mmd,
    mrr,
    sanitize_sequence,
    spectrum_map,
)

_AA = "ACDEFGHIKLMNPQRSTVWY"


def _aa_js_bits(counts_a: np.ndarray, counts_b: np.ndarray, alpha: float = 0.5) -> float:
    """Jensen-Shannon divergence (bits) between two AA count vectors."""
    p = (counts_a + alpha).astype(np.float64)
    q = (counts_b + alpha).astype(np.float64)
    p /= p.sum()
    q /= q.sum()
    m = 0.5 * (p + q)

    def kl(x, y):
        return float(np.sum(x * np.log2(x / y)))

    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def _aa_counts(seqs: List[str]) -> np.ndarray:
    c = np.zeros(len(_AA), dtype=np.int64)
    for s in seqs:
        for ch in s:
            idx = _AA.find(ch)
            if idx >= 0:
                c[idx] += 1
    return c


class ConditioningProbeCallback(pl.Callback):
    """See module docstring. Instantiate from configs/callbacks/cond_probe.yaml."""

    def __init__(
        self,
        probe_every_n_steps: int = 2000,
        loss_delta_num_batches: int = 2,
        n_labels: int = 8,
        seqs_per_label: int = 4,
        probe_len: int = 64,
        probe_max_iter: int = 64,
        n_reference: int = 256,
        probe_seed: int = 42,
        probe_dir: Optional[str] = None,
        max_probe_failures: int = 3,
        alarm_a_frac_threshold: float = 0.25,
        alarm_start_step: int = 5000,
        alarm_consecutive_probes: int = 2,
        alarm_abort: bool = False,
    ):
        super().__init__()
        self.probe_every_n_steps = int(probe_every_n_steps)
        self.loss_delta_num_batches = int(loss_delta_num_batches)
        self.n_labels = int(n_labels)
        self.seqs_per_label = int(seqs_per_label)
        self.probe_len = int(probe_len)
        self.probe_max_iter = int(probe_max_iter)
        self.n_reference = int(n_reference)
        self.probe_seed = int(probe_seed)
        self.probe_dir = probe_dir
        self.max_probe_failures = int(max_probe_failures)

        self.alarm_a_frac_threshold = float(alarm_a_frac_threshold)
        self.alarm_start_step = int(alarm_start_step)
        self.alarm_consecutive_probes = int(alarm_consecutive_probes)
        self.alarm_abort = bool(alarm_abort)

        # state
        self._ref = None            # dict with real reference embeddings/labels
        self._prompts = None        # list[(type, label_id, conditions_row)]
        self._val_batch_cache = []  # first K val batches for the loss-delta proxy
        self._consecutive_alarms = 0
        self._probe_failures = 0
        self._last_probe_step = -1

    # ------------------------------------------------------------------
    # asset construction
    # ------------------------------------------------------------------

    def _build_reference(self, trainer, pl_module):
        """Fixed real reference subset from the datamodule's train split."""
        dm = trainer.datamodule
        dataset = getattr(dm, "_train_dataset", None)
        if dataset is None:
            print("[cond_probe] no train dataset on datamodule; probes disabled")
            return False
        tok = dm.tokenizer
        rng = np.random.default_rng(self.probe_seed)
        n = min(self.n_reference, len(dataset))
        idxs = rng.choice(len(dataset), size=n, replace=False)

        seqs, labels = [], []
        for i in idxs:
            row = dataset.data[dataset._idx_map[int(i)]]
            seqs.append(sanitize_sequence(row["aa_seq"]))
            labs = [("ipr", int(v)) for v in (row.get("ipr_mapped") or [])]
            labs += [("go", int(v)) for v in (row.get("go_f_mapped") or [])]
            labels.append(labs)
        label_ids = [[self._label_key(t, v) for t, v in lab] for lab in labels]

        self._ref = {
            "seqs": seqs,
            "label_ids": label_ids,
            "emb": spectrum_map(seqs),                # [N, 20^3]
            "counts": _aa_counts(seqs),
        }
        print(f"[cond_probe] reference subset: {n} real sequences")

        # --- fixed probe prompts: top-frequency labels, interleaved types ---
        freq: Dict = {}
        for lab in labels:
            for key in lab:
                freq[key] = freq.get(key, 0) + 1
        top = sorted(freq.items(), key=lambda kv: -kv[1])
        picked = []
        seen_types = {"ipr": 0, "go": 0}
        # alternate ipr/go picks so both conditioning pathways are probed
        for (t, v), _ in top:
            if seen_types[t] < self.n_labels // 2 + self.n_labels % 2 and t == "ipr":
                picked.append((t, v))
                seen_types[t] += 1
            elif t == "go" and seen_types["go"] < self.n_labels // 2:
                picked.append((t, v))
                seen_types["go"] += 1
            if len(picked) == self.n_labels:
                break
        self._prompts = picked
        print(f"[cond_probe] probe prompts: {picked}")

        # Batch-level conditions: [n_seqs, max_labels] per type, aligned with
        # the generation order (prompt-major, seqs_per_label per prompt). A
        # type not carried by a prompt is an all -1 row (zero contribution),
        # matching how training treats empty label sets.
        n = self.n_labels * self.seqs_per_label
        batch_cond = {}
        for t in pl_module.model.cond_cfg.annotations.vocab_sizes:
            batch_cond[t] = torch.full((n, 1), -1, dtype=torch.long)
        for j, (t, v) in enumerate(picked):
            batch_cond[t][j * self.seqs_per_label:(j + 1) * self.seqs_per_label, 0] = v
        self._probe_conditions = {"annotations": batch_cond}
        return True

    @staticmethod
    def _label_key(t, v):
        # go and ipr id spaces overlap; offset go so MRR groups are unique
        return (1_000_000 + v) if t == "go" else v

    # ------------------------------------------------------------------
    # hooks
    # ------------------------------------------------------------------

    def on_train_start(self, trainer, pl_module):
        if not trainer.is_global_zero:
            return
        try:
            ok = self._build_reference(trainer, pl_module)
        except Exception:
            print("[cond_probe] reference build failed:\n" + traceback.format_exc())
            ok = False
        if self.probe_dir is None:
            self.probe_dir = os.path.join(
                trainer.default_root_dir or ".", "eval_probes"
            )
        os.makedirs(self.probe_dir, exist_ok=True)
        if not ok:
            self._ref = self._ref or {}

    def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
        if not trainer.is_global_zero or self._ref is None:
            return
        step = trainer.global_step
        if step < self.probe_every_n_steps or step % self.probe_every_n_steps != 0:
            return
        if step == self._last_probe_step:
            return
        self._last_probe_step = step
        try:
            self._run_probe(trainer, pl_module, step)
            self._probe_failures = 0
        except Exception:
            self._probe_failures += 1
            print(f"[cond_probe] probe failed at step {step} "
                  f"({self._probe_failures}/{self.max_probe_failures}):\n"
                  + traceback.format_exc())
            if self._probe_failures >= self.max_probe_failures:
                raise

    def on_before_optimizer_step(self, trainer, pl_module, optimizer):
        if not trainer.is_global_zero:
            return
        model = pl_module.model
        params = [p for layer in model.adapters_module
                  if layer is not None and not isinstance(layer, torch.nn.Identity)
                  for p in layer.parameters() if p.grad is not None]
        if params:
            total = torch.norm(torch.stack([p.grad.norm() for p in params]))
            pl_module.log("probe/grad_norm", total.item(), prog_bar=False)

    def on_validation_batch_end(self, trainer, pl_module, outputs, batch, batch_idx, dataloader_idx=0):
        if not trainer.is_global_zero:
            return
        if len(self._val_batch_cache) < self.loss_delta_num_batches:
            # keep annotations (step() pops them); clone to CPU
            self._val_batch_cache.append(batch)

    def on_validation_epoch_end(self, trainer, pl_module):
        if not trainer.is_global_zero or not self._val_batch_cache:
            return
        batches, self._val_batch_cache = self._val_batch_cache, []
        model = pl_module.model
        deltas = []
        track_means = {}
        model.eval()
        for batch in batches:
            if batch.get("annotations") is None:
                continue
            batch_real = dict(batch)  # step() pops annotations from the copy
            # Null pass must go through the model's own force_unconditional
            # path (one learned null token per type). Filling the batch's
            # padding slots with the null id would sum the null embedding
            # max_labels times — not the CFG null condition.
            # The force_unconditional patch below wraps ONLY the null pass:
            # loss_real must run unpatched (real conditions). Patching before
            # both passes made them bitwise identical and loss_delta exactly 0
            # forever — survived two fix attempts because the leftover outer
            # assignment kept re-applying the patch (live-run bug, caught at
            # step 7K of the 2026-10-26 8-node campaign).
            orig_encode = model._encode_conditions
            try:
                with torch.no_grad():
                    # identical seeding → both calls draw the same noised
                    # x_t / timesteps / masks, so the loss difference isolates
                    # the condition (otherwise it is dominated by the
                    # diffusion-noise variance, ~±2 at init).
                    torch.manual_seed(self.probe_seed)
                    loss_real, log_r = pl_module.step(batch_real)
                    model._encode_conditions = (
                        lambda conditions, force_unconditional=False,
                               _orig=orig_encode:
                        _orig(conditions, force_unconditional=True)
                    )
                    torch.manual_seed(self.probe_seed)
                    loss_null, log_n = pl_module.step(dict(batch))
            finally:
                model._encode_conditions = orig_encode
            deltas.append((loss_real - loss_null).item())

            # per-track deltas: conditions are function labels — the aatype
            # track is where conditioning should show up first; struct adds
            # most of the loss mass and can drown the signal in the total.
            def _track_val(log, track):
                for k, v in log.items():
                    if k.startswith(track) and "nll" in k and "fullseq" not in k:
                        return float(v)
                for k, v in log.items():
                    if k.startswith(track) and "loss" in k and "index" not in k:
                        return float(v)
                return None

            for track in ("aatype", "struct"):
                vr = _track_val(log_r, track)
                vn = _track_val(log_n, track)
                if vr is not None and vn is not None:
                    pl_module.log(f"probe/loss_delta_{track}", vr - vn)
                    track_means.setdefault(track, []).append(vr - vn)
        if deltas:
            pl_module.log("probe/loss_delta", float(np.mean(deltas)))
            print(f"[cond_probe] step {trainer.global_step}: "
                  f"loss_delta={np.mean(deltas):.4g} "
                  f"(per-batch {[f'{d:.4g}' for d in deltas]})")
            for track, vals in track_means.items():
                print(f"[cond_probe]   {track} delta: {np.mean(vals):.4g} "
                      f"(per-batch {[f'{d:.4g}' for d in vals]})")

    # ------------------------------------------------------------------
    # probe
    # ------------------------------------------------------------------

    def _run_probe(self, trainer, pl_module, step):
        model = pl_module.model
        device = next(model.parameters()).device
        was_training = model.training
        model.eval()

        # per-layer adapter ‖Δh‖/‖h‖ during the probe forward
        dh_vals: Dict[int, list] = {}

        def _hook(idx):
            def hook(module, inputs, output):
                h = inputs[0]
                ratio = (output.float().norm() / (h.float().norm() + 1e-8)).item()
                dh_vals.setdefault(idx, []).append(ratio)
            return hook

        handles = []
        for idx, layer in enumerate(model.adapters_module):
            if layer is not None and not isinstance(layer, torch.nn.Identity):
                handles.append(layer.register_forward_hook(_hook(idx)))

        try:
            # --- generation probe ---
            tok = model.tokenizer
            seq = tok.aa_mask_token * self.probe_len
            seq = tok.aa_cls_token + seq + tok.aa_eos_token
            n = self.n_labels * self.seqs_per_label
            x = tok.batch_encode_plus(
                [seq] * n, add_special_tokens=False, padding=True,
                return_tensors="pt",
            )["input_ids"].to(device)

            cond = {
                t: v.to(device) for t, v in self._probe_conditions["annotations"].items()
            }
            # conditions are grouped per label: rows are label-major — build
            # per-sequence label ids in the same order
            labels_per_seq = []
            for t, v in self._prompts:
                labels_per_seq += [self._label_key(t, v)] * self.seqs_per_label

            torch.manual_seed(self.probe_seed)
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                out = model.generate(
                    input_tokens=x, max_iter=self.probe_max_iter,
                    temperature=1.0,
                    unmasking_strategy="stochastic1.0",
                    sampling_strategy="annealing@2.0:0.1",
                    conditions={"annotations": cond}, cfg_scale=1.0,
                )
            gen_tokens = out["output_tokens"]
            gen_seqs = [sanitize_sequence(s.replace(" ", "")) for s in
                        tok.batch_decode(gen_tokens, skip_special_tokens=True)]

            # --- predictor-free metrics ---
            logs = {}
            logs["a_frac"] = float(np.mean(
                [s.count("A") / max(1, len(s)) for s in gen_seqs]))
            js = _aa_js_bits(_aa_counts(gen_seqs), self._ref["counts"])
            logs["js_real"] = js
            emb_gen = spectrum_map(gen_seqs)
            logs["mmd"] = mmd(emb1=emb_gen, emb2=self._ref["emb"], kernel="linear")
            mrr_val, _ = mrr(gen_seqs, [[l] for l in labels_per_seq],
                             self._ref["seqs"], self._ref["label_ids"])
            logs["mrr"] = mrr_val
            kmers = set()
            total_k = 0
            for s in gen_seqs:
                for i in range(len(s) - 2):
                    kmers.add(s[i:i + 3])
                    total_k += 1
            logs["diversity"] = len(kmers) / max(1, total_k)

            # adapter weight norms
            w_norms = []
            for layer in model.adapters_module:
                if layer is None or isinstance(layer, torch.nn.Identity):
                    continue
                for par in layer.adapters:
                    w_norms.append(par.linear_up.weight.float().norm().item())
            logs["adapter_w"] = float(np.mean(w_norms))
            logs["adapter_w_max"] = float(np.max(w_norms))

            # ‖Δh‖ per layer
            if dh_vals:
                per_layer = {i: float(np.mean(v)) for i, v in dh_vals.items()}
                logs["dh_mean"] = float(np.mean(list(per_layer.values())))
                logs["dh_max"] = float(np.max(list(per_layer.values())))
            else:
                per_layer = {}

            # --- log + persist ---
            for k, v in logs.items():
                pl_module.log(f"probe/{k}", v)

            # pre-registered alarm
            alarm = 0.0
            if (step >= self.alarm_start_step
                    and logs["a_frac"] > self.alarm_a_frac_threshold):
                self._consecutive_alarms += 1
            else:
                self._consecutive_alarms = 0
            if self._consecutive_alarms >= self.alarm_consecutive_probes:
                alarm = 1.0
                print(f"\n!!! [cond_probe] ALARM step {step}: a_frac="
                      f"{logs['a_frac']:.3f} > {self.alarm_a_frac_threshold} at "
                      f"{self._consecutive_alarms} consecutive probes "
                      f"(mmd={logs['mmd']:.3f}, js={logs['js_real']:.4f}, "
                      f"mrr={logs['mrr']:.3f}) !!!\n")
                if self.alarm_abort:
                    print("[cond_probe] alarm.abort=true → stopping training")
                    trainer.should_stop = True
            pl_module.log("probe/alarm", alarm)

            step_dir = os.path.join(self.probe_dir, f"step_{step}")
            os.makedirs(step_dir, exist_ok=True)
            with open(os.path.join(step_dir, "aatype.fasta"), "w") as f:
                for j, (s, lab) in enumerate(zip(gen_seqs, labels_per_seq)):
                    f.write(f">probe_{j}_label{lab} len={len(s)}\n{s}\n")
            with open(os.path.join(step_dir, "metrics.json"), "w") as f:
                json.dump({"step": step, **logs, "dh_per_layer": per_layer,
                           "alarm": alarm}, f, indent=2)
            print(f"[cond_probe] step {step}: " +
                  " ".join(f"{k}={v:.4f}" for k, v in logs.items()))
        finally:
            for h in handles:
                h.remove()
            if was_training:
                model.train()
