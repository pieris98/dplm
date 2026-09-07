# ConditionalDPLM2 — Technical Reference

**Implementation and architecture documentation**
Companion to the progress report ([conditional_dplm2_progress_report.md](conditional_dplm2_progress_report.md)); this document is the deep-dive. Every claim is anchored to source with `file:line` references in the style of generated API docs.

---

## Table of contents

1. [Module map](#1-module-map)
2. [Architecture](#2-architecture)
3. [Conditioning modules](#3-conditioning-modules)
4. [The ConditionalDPLM2 wrapper](#4-the-conditionaldplm2-wrapper)
5. [Encoder integration](#5-encoder-integration)
6. [Data pipeline](#6-data-pipeline)
7. [Training task](#7-training-task)
8. [Generation pipeline](#8-generation-pipeline)
9. [Configuration reference](#9-configuration-reference)
10. [Invariants and proofs](#10-invariants-and-proofs)
11. [Known limitations](#11-known-limitations)

---

## 1. Module map

New modules (all committed) and their dependencies:

```
byprot/
├── models/dplm2/
│   ├── conditioning/
│   │   ├── parallel_adapter.py      ProjectionMLP, AdapterLayer,
│   │   │                            _MLP, ParallelAdapterLayer
│   │   └── annotation_embedder.py   AnnotationEmbedder
│   ├── dplm2_conditional.py         ConditionalDPLM2 + 5 config dataclasses
│   └── modules/dplm2_modeling_esm.py  (patched: adapter kwargs plumbing)
├── datamodules/
│   ├── dataset/annotated_protein.py   AnnotatedProteinDataset, AnnotatedProteinCollater
│   └── annotated_protein_datamodule.py AnnotatedProteinDataModule, _length_based_sampler
└── tasks/lm/conditional_dplm2.py      ConditionalDPLM2TrainingTask

generate_conditional_dplm2.py          CLI: ckpt loading, label parsing, sampling
configs/experiment/dplm2/cond_dplm2_650m_cfpgen{,_meluxina}.yaml
configs/trainer/meluxina_ddp_bf16.yaml
```

Dependency direction (arrows = "imports"):

```
ConditionalDPLM2TrainingTask ──▶ ConditionalDPLM2 ──▶ conditioning.parallel_adapter
        │                               │      └─────▶ conditioning.annotation_embedder
        ▼                               ▼
AnnotatedProteinDataModule ──▶ AnnotatedProteinDataset/Collater ──▶ DPLM2Tokenizer (shared)
```

---

## 2. Architecture

### 2.1 Placement in the transformer

Vanilla DPLM-2 processes a `[struct | aa]` token stream (length `2L`) through 33 `ModifiedEsmLayer` blocks with rotary embeddings that assign the same positional phase to `struct_i` and `aa_i`. There is no cross-attention anywhere.

ConditionalDPLM2 keeps that stream, those layers, and every pretrained weight untouched, and adds one **parallel adapter branch per layer**:

```
                    ┌──────────────────────────────────────────────────┐
                    │              ModifiedEsmEncoder                   │
                    │                                                  │
 input_ids ──▶ Embeddings ──▶ ┌──────────────── Layer ℓ = 0..32 ─────┐│
 (2L tokens)                  │                                      ││
                              │   h ──────────────┬──────────────┐    ││
                              │   │               │              │    ││
                              │   ▼               ▼              │    ││
                              │ ModifiedEsmLayer  ParallelAdapterLayer│
                              │ (FROZEN,          (TRAINABLE)     │    ││
                              │  pretrained)      h ──▶ Δ(h, s)   │    ││
                              │   │               ▲              │    ││
                              │   │               │ s_ipr, s_go  │    ││
                              │   ▼               │              │    ││
                              │ h_out = h + Δ ────┘              │    ││
                              │   │                              │    ││
                              └───┼──────────────────────────────────┘│
                                  ▼                                   │
                          (next layer ℓ+1)                            │
                                                                      │
                         s_ipr, s_go computed ONCE per batch ─────────┘
                         (identical at every layer)
```

The adapter output `Δ` is **added to the residual stream** after the frozen layer's output — the transformer's own computation path is never entered.

### 2.2 End-to-end tensor shapes

For batch `B`, protein length `L`, hidden size `H=1280`, condition dim `c_s=128`:

| Stage | Tensor | Shape |
|---|---|---|
| Input | `input_ids` | `[B, 2L]` |
| Embedding | `input_embeds` | `[B, 2L, 1280]` |
| Per-layer | frozen layer output `h` | `[B, 2L, 1280]` |
| Adapter input | condition `s_t` (per type `t`) | `[B, 128]` (broadcast) |
| Adapter internal | bottleneck `x` | `[B, 2L, 16+128]` |
| Adapter output | `Δ_t` | `[B, 2L, 1280]` |
| Residual sum | `h + Σ_t Δ_t` | `[B, 2L, 1280]` |
| Output | `logits` | `[B, 2L, 8229]` |

### 2.3 Parameter budget

| Component | Count |
|---|---|
| Frozen base (`net`) | 650M |
| `AnnotationEmbedder` (IPR 1155×128 + GO 376×128) | 195,872 |
| `ProjectionMLP` × 2 | 2 × 34,432 |
| `ParallelAdapterLayer` × 33 × 2 conditions | 33 × 568,960 ≈ 18.8M |
| **Total trainable** | **19.5M (3.0% of base)** |

---

## 3. Conditioning modules

### 3.1 `AnnotationEmbedder` — discrete labels → vectors

[annotation_embedder.py:23](../../src/byprot/models/dplm2/conditioning/annotation_embedder.py#L23)

```
 labels = {"ipr": LongTensor[B, max_n_ipr],     e.g. [[10, 11, 12, -1]]
           "go":  LongTensor[B, max_n_go]}      e.g. [[ 5, -1, -1, -1]]
        (-1 = padding)
              │
              ▼ per type t ──────────────────────────────────────────┐
   ┌──────────────────────────────────────────────────────────────┐ │
   │ tables[t] = nn.Embedding(vocab_t + 1, embed_dim)             │ │
   │   rows 0..vocab_t-1  : learned label embeddings              │ │
   │   row  vocab_t       : learned UNCONDITIONAL (null) token    │ │
   └──────────────────────────────────────────────────────────────┘ │
              │                                                     │
              ▼                                                     ▼
   valid = (x >= 0)                                    drop ~ Bernoulli(p_uncond)
   emb = tables[t](x.clamp(min=0))                     per SAMPLE (training only)
   summed_t = Σ_labels emb·mask          [B, embed_dim]                │
              └──────────────── merge ◀── torch.where(drop, uncond, summed_t) ──┘
```

Semantics:
- **Multi-label summation**: a protein with IPR labels {10, 11, 12} gets the *sum* of those three embeddings — the CFP-Gen convention ([FuncTagEmbedder]).
- **CFG dropout** ([annotation_embedder.py:100-106](../../src/byprot/models/dplm2/conditioning/annotation_embedder.py#L100-L106)): during training, each sample's whole label set is replaced by the null-token embedding with probability `p_dropout_uncond=0.1`. This trains the model to model both `p(x|c)` and `p(x)` — the prerequisite for classifier-free guidance at sampling time.
- `force_unconditional=True` returns the null embedding for every sample (used by the `--labels null` baseline; will be used by CFG sampling).

### 3.2 `ProjectionMLP` — condition interface adapter

[parallel_adapter.py:20](../../src/byprot/models/dplm2/conditioning/parallel_adapter.py#L20)

```
 input_dim ──▶ Linear(input_dim, hidden) ──▶ ReLU ──▶ [Linear(hidden,hidden) ──▶ ReLU]*
                                    ──▶ Linear(hidden, output_dim)
```

Per annotation type, one instance maps `embed_dim(128) → c_s(128)`. Its purpose is interface normalization: **anything that can produce a fixed-width vector** (annotation embeddings today; retrieved fragments, text-encoder outputs tomorrow, via `ExternalMemoryConfig`) plugs into the same adapter slot without architectural change.

### 3.3 `AdapterLayer` — the low-rank bottleneck

[parallel_adapter.py:54](../../src/byprot/models/dplm2/conditioning/parallel_adapter.py#L54). This is the per-condition computational unit (ported from ProCALM with the condition-broadcast fix, §10.2):

```
 h [B, 2L, c_h]                       s [B, c_s]
 │                                     │
 ▼                                     ▼
 LayerNorm(c_h) → Dropout              LayerNorm(c_s) → Dropout
 │  (computed in fp32)                 │
 │                                     │  broadcast: [B, c_s] → [B, 2L, c_s]
 │                                     │  (unsqueeze+expand; no copy)
 ▼                                     │
 └──────────────┬──────────────────────┘
                ▼
        x = h  (low_rank_cond=True path)
                │
                ▼
        linear_down: c_h → c_hidden          [B, 2L, 16]
                │
                ▼
        x = concat([s, x], dim=-1)           [B, 2L, 16+128]
                │
                ▼
        _MLP: (c_hidden+c_s) → 2× → (c_hidden+c_s)     [B, 2L, 144]
                │
                ▼
        ReLU
                │
                ▼
        linear_up: (c_hidden+c_s) → c_h      [B, 2L, 1280]
                │
                ▼
        Δ  (near-zero at init: weight ~ N(0, 1e-5²), bias = 0)
```

Key design points:
- **Condition fused inside the bottleneck** (`low_rank_cond=True`): `s` is concatenated to the *16-dim* compressed representation, not to the 1280-dim `h` — the condition modulates the low-rank code, keeping parameter count minimal.
- **fp32 LayerNorm** on `h` (line 134) — bf16 training stability; cast back to input dtype after.
- **Near-zero init** (lines 116-118): `Δ ≈ 0` at step 0, giving bit-exact frozen-base behavior (proof in §10.1).

Dimensions: `c_h=1280`, `c_hidden=16`, `c_s=128` → per-adapter params = `1280·16 + 144·288 + 288·144 + 144·1280 + norms ≈ 284K`.

### 3.4 `ParallelAdapterLayer` — multi-condition composition

[parallel_adapter.py:194](../../src/byprot/models/dplm2/conditioning/parallel_adapter.py#L194)

```
                h ────────────────┬────────────────┐
                                  │                │
                     AdapterLayer₀(h, s_ipr)   AdapterLayer₁(h, s_go)
                                  │                │
                                  ▼                ▼
                                Δ_ipr            Δ_go
                                  └───────┬────────┘
                                          ▼
                              Δ = Δ_ipr + Δ_go    (elementwise sum)
```

Each condition gets an **independent** adapter sharing the same `h`; outputs are summed. This preserves condition separability (unlike CFP-Gen's pre-summed FiLM embedding): ablating a condition at inference = dropping its adapter term. `n_parallel = n_annotation_types + external.enable`.

---

## 4. The ConditionalDPLM2 wrapper

[dplm2_conditional.py:93](../../src/byprot/models/dplm2/dplm2_conditional.py#L93). Inherits `MultimodalDiffusionProteinLanguageModel` (the vanilla DPLM-2); config via `ConditionalDPLM2Config(DPLM2Config)` which adds one field: `conditioning: ConditioningConfig`.

### 4.1 Construction sequence

```
ConditionalDPLM2(cfg)
 │
 ├─ 1. super().__init__(cfg)          → loads pretrained net/tokenizer (parent path)
 │
 ├─ 2. build AnnotationEmbedder(vocab_sizes={ipr,go})
 │     build annotation_projectors[t] = ProjectionMLP per type
 │     (external_projector if external.enable)
 │
 ├─ 3. build layer_adapters: List[Optional[ParallelAdapterLayer]], len 33
 │     layer_indices = all (or cfg.adapter.layer_indices)
 │     register adapters_module = ModuleList (state_dict/.to()/eval() propagate)
 │
 └─ 4. if freeze_base: for p in net.parameters(): p.requires_grad_(False)
```

### 4.2 `_encode_conditions` — condition broadcast

[dplm2_conditional.py:213](../../src/byprot/models/dplm2/dplm2_conditional.py#L213)

```
conditions = {"annotations": {"ipr": [B, n₁], "go": [B, n₂]}}
        │
        ▼
AnnotationEmbedder → {"ipr": [B,128], "go": [B,128]}
        │
        ▼ per type
annotation_projectors[t] → s_t [B, c_s=128]
        │
        ▼
layer_adapter_inputs = [ [s_ipr, s_go] if layer ℓ has an adapter else None, ℓ=0..32 ]
```

The **same** condition vectors feed every adapter-equipped layer (ProCALM convention — one embedding per sample, broadcast across depth). Early-exit returns `None` if no conditions and no external input → no adapter fires anywhere.

---

## 5. Encoder integration

The only change to vanilla modeling code: optional `layer_adapters` / `layer_adapter_inputs` kwargs threaded through `ModifiedEsmEncoder.forward` → `ModifiedEsmModel.forward` → `EsmForDPLM2.forward` ([dplm2_modeling_esm.py](../../src/byprot/models/dplm2/modules/dplm2_modeling_esm.py)). The per-layer hook:

```python
hidden_states = layer_outputs[0]                    # frozen layer done
if (layer_adapters is not None
        and layer_adapter_inputs is not None
        and layer_adapters[i] is not None
        and layer_adapter_inputs[i] is not None):
    adapter_update = layer_adapters[i](hidden_states, layer_adapter_inputs[i])
    hidden_states = hidden_states + adapter_update
```

Guards make it a strict no-op when unset — vanilla DPLM-2 executes a byte-identical path.

**One non-obvious constraint:** the parent `DPLM2.forward` hardcodes the kwargs it forwards to `self.net(...)` and would silently drop the adapter kwargs. The wrapper therefore **replicates the parent's 20-line forward body** (attention-bias construction, single-modality masking, embedding) and passes `layer_adapters`/`layer_adapter_inputs` explicitly ([dplm2_conditional.py:297-324](../../src/byprot/models/dplm2/dplm2_conditional.py#L297-L324)). This is the single point where the wrapper does not call `super()` — documented in-source.

---

## 6. Data pipeline

### 6.1 On-disk format

`joined_train_safe.parquet` — DPLM-2's schema plus label columns:

| Column | Type | Source |
|---|---|---|
| `pdb_name`, `struct_seq`, `aa_seq`, `length`, `avg_plddt`, `plddt`, … | DPLM-2 schema | DPLM-2 shipped parquet (verbatim struct tokens) |
| `uniprot_id` | string | join key |
| `ipr_mapped` | list&lt;int64&gt; | CFP-Gen InterPro label IDs (vocab 1154) |
| `go_f_mapped` | list&lt;int64&gt; | CFP-Gen GO molecular-function IDs (vocab 375) |

45,696 rows = CFP-Gen general train ∩ DPLM-2 train by UniProt accession.

### 6.2 `AnnotatedProteinDataset`

[annotated_protein.py:26](../../src/byprot/datamodules/dataset/annotated_protein.py#L26)

```
parquet ──▶ to_pylist ──▶ drop rows with empty labels ──▶ deterministic train/val split
                                                            (rng(seed=0), 5% val)
__getitem__(i):
  row = data[idx_map[i]]
  crop = random window if length > max_len (shared start/stop for struct AND aa)
  return {
    "struct_tokens": "<cls_struct>" + joined codes + "<eos_struct>",
    "aatype_tokens": "<cls_aa>" + aa string       + "<eos_aa>",
    "length": cropped_len + 2,
    "ipr_mapped": [int,...], "go_f_mapped": [int,...],
    "pdb_name", "uniprot_id"
  }
```

The struct/aa crops use the **same window** to preserve the position-by-position correspondence the rotary embeddings rely on (vanilla behavior, replicated).

### 6.3 `AnnotatedProteinCollater`

[annotated_protein.py:122](../../src/byprot/datamodules/dataset/annotated_protein.py#L122)

```
raw_batch ──▶ tokenizer.batch_encode_plus(struct strings, padding=True)  ─▶ {"targets", "attention_mask"}
           ──▶ tokenizer.batch_encode_plus(aa strings,     padding=True)  ─▶ {"targets", "attention_mask"}
           ──▶ _pad_labels(ipr lists, pad=-1)  ─▶ LongTensor[B, max_n_ipr]
           ──▶ _pad_labels(go lists,  pad=-1)  ─▶ LongTensor[B, max_n_go]
           ─▶ batch = {"struct_tokens":…, "aatype_tokens":…, "annotations": {"ipr":…, "go":…}}
```

Two hard-won details: `padding=True` (not `"longest"` — the EsmTokenizer subclass silently fails to pad the latter, producing ragged tensors); and label padding value `-1` interpreted by `AnnotationEmbedder` as "no label", matching CFP-Gen's collator exactly.

### 6.4 `AnnotatedProteinDataModule`

[annotated_protein_datamodule.py:49](../../src/byprot/datamodules/annotated_protein_datamodule.py#L49) — wraps the above in a LightningDataModule with `_length_based_sampler`: a greedy token-budget batch sampler (`max_tokens=4000`: batches grow until `(B+1)·max_L > budget`, mirroring the vanilla `ApproxBatchSampler` semantics without its cluster-sorting extras).

---

## 7. Training task

`ConditionalDPLM2TrainingTask` ([conditional_dplm2.py:15](../../src/byprot/tasks/lm/conditional_dplm2.py#L15)) subclasses `DPLM2TrainingTask` and overrides exactly one method, `step`:

```
training_step / validation_step          (inherited: logging, metrics, NaN guard)
        │
        ▼
step(batch):                                        ← ONLY override
    conditions = batch.pop("annotations")            # {"ipr":…, "go":…}
    conditions = {"annotations": {k: v.to(device)}}
    logits, targets, masks, weights =
        model.compute_loss(batch, weighting, conditions=conditions)
    loss, logging = criterion(logits, targets, masks, weights, …)   (inherited StructAARDMCE)
    + index accuracies (inherited helpers)
```

And on the model side, `compute_loss(conditions=)` ([dplm2_conditional.py:326](../../src/byprot/models/dplm2/dplm2_conditional.py#L326)) uses the stash pattern:

```
compute_loss(batch, weighting, conditions):
    stash(_encode_conditions(conditions))            # set
    try:  return super().compute_loss(batch, weighting)   # parent calls self.forward(...) internally
    finally: restore stash                            # unset
```

The parent's internal `self.forward(input_ids=x_t, single_modality=…)` picks the stashed adapter inputs through the forward-override's fallback branch. Everything downstream — the four-way loss mixture (single/folding/inverse/joint, 0.25 each), diffusion timestep weighting, criterion — is inherited unchanged. **No new loss terms.**

Optimizer/scheduler/criterion are configured identically to the vanilla task; only the trainable set differs (19.5M params), which is why the full-run lr is 3e-4 vs vanilla's 1e-4.

---

## 8. Generation pipeline

### 8.1 The stash mechanism

The parent's 70-line `forward_decoder` (modality logit masking, top-p, annealing) calls `self.forward(input_ids=…)` with no conditions kwarg. Reimplementing it would be brittle; instead `generate(conditions=…)` ([dplm2_conditional.py:349](../../src/byprot/models/dplm2/dplm2_conditional.py#L349)):

```
generate(input_tokens, conditions, …):
    prev = self._active_layer_adapter_inputs
    self._active_layer_adapter_inputs = self._encode_conditions(conditions)   # encoded ONCE
    try:
        return super().generate(input_tokens, …)   # full parent denoising loop
    finally:
        self._active_layer_adapter_inputs = prev    # restore
```

Conditions are token-independent → encode once, apply at **every** denoising step (~`max_iter` forward passes).

### 8.2 CLI: `generate_conditional_dplm2.py`

```
--ckpt path.ckpt ──▶ torch.load
                      ├─ hyper_parameters.model.conditioning ──▶ cfg_override (SELF-CONFIGURING:
                      │                                            vocab sizes read from ckpt)
                      └─ state_dict, strip "model." prefix ──▶ load_state_dict(strict=False)
                                                               └─ error if any TRAINABLE key missing

--labels go:5,27 --labels ipr:10,11,12 ──▶ parse_labels
                      └─ unspecified trained types auto-filled with dummy label 0
                         (AnnotationEmbedder requires every trained type present;
                          true unconditional = --labels null → conditions=None)

task=sequence_generation   input = all-<mask_aa> aa track
task=co_generation         input = [<mask_struct> | placeholder-aa] concat (2L)

model.generate(input_tokens, conditions, max_iter, …)
    └─▶ outputs["output_tokens"] [B, 2L]
            └─▶ chunk into struct|aa halves ──▶ batch_decode ──▶ aatype.fasta (+ struct_token.fasta)
                                                     (identical format to generate_dplm2.py)
```

Output path: `<saveto>/<task>/len_<L>/<cond_tag>/` where `cond_tag` encodes the labels (e.g. `cond_go-5+ipr-10_11_12` or `uncond`).

---

## 9. Configuration reference

`configs/experiment/dplm2/cond_dplm2_650m_cfpgen_meluxina.yaml` (full run):

```yaml
model:
  _target_: conditional_dplm2
  training_stage: continue_train_from_dplm2
  lora.enable: false                    # frozen base; adapters only
  net: {name: airkingbd/dplm2_650m, pretrain: false}   # weights via from_pretrained at load
  conditioning:
    annotations:
      vocab_sizes: {ipr: 1154, go: 375}
      embed_dim: 128
      p_dropout_uncond: 0.1             # CFG dropout (training)
    adapter:
      layer_indices: null               # null = all 33 layers
      c_s: 128
      c_hidden: 16
      weight_init: 1.0e-5
      dropout_rate: 0.1
      adapter_nlayers: 2
      proj_hidden_dim: 128
    external: {enable: false, input_dim: 0}   # future retrieval/text hook
    freeze_base: true
task:
  _target_: lm/conditional_dplm2
  criterion: StructAARDMCrossEntropyLoss (inherited)
  optimizer: adamw lr=3e-4, betas [0.9,0.98], wd 0.01
  lr_scheduler: polynomial, warmup 1000, total 100K steps
trainer (meluxina_ddp_bf16):
  strategy: ddp_find_unused_parameters_true   # REQUIRED: frozen base → unused params
  precision: bf16, devices: 4, num_nodes: parameterized
  sync_batchnorm: true, gradient_clip_val: 0.5
datamodule:
  _target_: annotated_protein
  parquet_path: …/joined_train_safe.parquet
  max_tokens: 4000, max_len: 512, val_ratio: 0.05
```

Design notes: `ddp_find_unused_parameters_true` is not optional — with `freeze_base`, the majority of parameters receive no gradient and vanilla DDP would error; lr 3e-4 sits between the smoke's 1e-3 (aggressive, fine for 200 steps) and vanilla DPLM-2's 1e-4 (tuned for full-model training).

---

## 10. Invariants and proofs

### 10.1 Frozen-base equivalence (verified bit-exact)

**Claim:** with adapters at init (`weight_init=1e-5`) and no conditions, `ConditionalDPLM2.forward` produces bit-identical logits to vanilla DPLM-2 from the same checkpoint.

**Argument:** the encoder hook adds `hidden_states + Δ` where `Δ = linear_up(ReLU(mlp(concat[s, down(h)])))`. At init `linear_up.weight ~ N(0, 1e-5²)` and `linear_up.bias = 0`, so `‖Δ‖∞ ≈ 1e-4 · ‖x‖` — and with `conditions=None` the hook does not fire at all (guard on `layer_adapter_inputs[i] is not None`). All other model code paths are provably untouched (the encoder patch is guarded; the wrapper's forward replication is line-for-line the parent's computation).

**Empirical:** verified at two levels —
1. Random AA tokens: max logit diff `0.00e+00`; weights bit-identical into the subclass.
2. **Real `[struct|aa]` batches** from the dataset (exercises dual-stream rotary, modality typing, full attention bias): struct-half `0.00e+00`, aa-half `0.00e+00`.
3. With conditions at init: diff 4.5e-04 — the adapter fires but perturbs minimally, as designed.

### 10.2 Gradient isolation (verified)

**Claim:** backward through `compute_loss(conditions=…)` touches only adapter/embedder/projector params.

**Argument:** `freeze_base` sets `requires_grad=False` on all of `net` (including the shared embedding and lm_head); the adapter branch's only inputs from the base graph are `hidden_states` (used as values, gradient stops at the frozen subgraph boundary... rather: gradients flow *through* `h` into the adapter's `linear_down`, but no *parameter* of the base accumulates because none has `requires_grad=True`).

**Empirical:** after one backward: **0 base params** with non-zero grad; **802 adapter/embedder/projector** params with grad.

### 10.3 Condition sensitivity (verified)

**Claim:** conditions measurably steer both training and generation.
- Training: `compute_loss` loss differs conditioned vs not (12.74 vs 11.44 nats on a fixed batch).
- Generation: same seed + same 200-step ckpt, conditional (`go:5`) vs `--labels null` produce **entirely different amino-acid distributions** (Gly/Ala-rich vs Val/Glu/Met-rich).

### 10.4 Ported-code fix: condition broadcast

ProCALM's original `AdapterLayer.forward` concatenates `s [B, c_s]` with the down-projected `x [B, L, c_hidden]` — a shape error unless `s` is pre-broadcast. Our port broadcasts explicitly (`s.unsqueeze(1).expand(...)` when `s.dim() == h.dim()-1`, [parallel_adapter.py:141-147](../../src/byprot/models/dplm2/conditioning/parallel_adapter.py#L141-L147)), and accepts an already-per-position condition unchanged. One condition per sample shared across positions is the intended semantics (function is a protein-level property).

---

## 11. Known limitations

1. **No CFG sampling yet.** Training does CFG dropout (`p=0.1`) but `generate()` samples only `p(x|c)`; a guidance scale (combining `p(x|c)` and `p(x|∅)` passes) is the top TODO.
2. **Partial-label conditions coerce to label 0.** Supplying only `--labels go:5` fills IPR with dummy label 0 — that conditions on IPR-0's embedding rather than "no IPR". True per-type nulls would need `force_unconditional` granularity per type.
3. **Adapters at every layer** (`layer_indices: null`). Selective placement (e.g. last-K layers) is config-supported but untested; could cut params ~3× if early layers prove redundant.
4. **Single shared condition across denoising steps.** Conditions can't vary with `t` (e.g. time-dependent guidance strength) under the current encode-once stash.
5. **Diffusion-regime risk (inherited from ProCALM's AR validation).** The adapter is applied ~`max_iter` times per sample; signal attenuation/amplification across steps is possible and only the full training run answers it.
6. **The stash is instance state.** Concurrent `generate()` calls on one model instance would race on `_active_layer_adapter_inputs`. Irrelevant for current single-threaded use; a context manager would make it safe.
7. **Tokenization mismatch residual (data-side).** The 36K AFDB-recovered proteins carry ~5-10% single-bit struct-token noise vs DPLM-2's shipped tokens (environmental numerical differences in the LFQ binarizer; lengths verified matching). Shelved from training; the 45K shipped-token dataset is authoritative.

---

*Source of truth for line references: commit `444432c` + subsequent conditional-run commits on `main`.*
