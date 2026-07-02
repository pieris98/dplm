# Conditional DPLM-2 via Parallel Adapters (+ optional FiLM)

## Context

DPLM-2 made structure a first-class token modality: amino-acid tokens and LFQ structure tokens share one vocabulary, are concatenated along the sequence dimension, and flow through a single self-attention stream with no cross-attention ([dplm2.py:220-270](../src/byprot/models/dplm2/dplm2.py#L220-L270), [dplm2_modeling_esm.py:259-352](../src/byprot/models/dplm2/modules/dplm2_modeling_esm.py#L259-L352)). This removed the GVP-Transformer + cross-attention structure adapter that DPLM-1 needed.

For function/annotation conditioning on top of DPLM-2, we therefore do not need a separate structure encoder or cross-attention into the backbone. We need a frozen-base way to inject discrete annotations (Pfam/GO/IPR/EC) and, later, continuous external-memory embeddings (retrieved fragments, text-encoder outputs).

Two candidate designs were compared:

- **CFP-Gen's AGFM** — FiLM modulation (shift/scale/gate) at every ESM layer's LayerNorms, condition via `nn.Embedding` lookup per annotation type, summed across types. Validated on DPLM-1 (diffusion LM, frozen backbone). Replaces `EsmLayer` with `AGFMLayer` end-to-end.
- **ProCALM's ParallelAdapterLayer** — low-rank bottleneck adapter attached in parallel at every transformer block, output summed into the residual stream before the residual connection. Condition via `ProjectionMLP` accepting arbitrary-dim vectors (one-hot, fingerprints, text embeddings). Validated on ProGen2 (autoregressive LM). Adapter-only fine-tuning.

**Decision: ProCALM-style parallel adapter is the primary mechanism.** AGFM-FiLM is offered as an optional config flag. The separate cross-attention adapter from the earlier draft is dropped — ProCALM's parallel adapter already serves the role of "external memory injected per layer."

## Why ProCALM parallel adapter over CFP-Gen AGFM

| Criterion | ProCALM adapter | CFP-Gen AGFM | Winner |
|---|---|---|---|
| **Invasiveness to `ModifiedEsmLayer`** | Adds a parallel module; layer forward unchanged | Replaces `EsmLayer` internals (LN→modulate→attn→modulate→FFN) | ProCALM |
| **Frozen-base cleanliness** | Strict residual with near-zero init; base graph untouched | Identity at zero shift/scale, but requires modified forward to exist | ProCALM |
| **Rotary/type_ids interaction** | None — operates on residual stream post-block | Modulates hidden states inside block; rotary unaffected but needs careful plumbing | ProCALM |
| **Multi-condition composition** | `ParallelAdapterLayer` stacks independent adapters, summed ([adapter.py:178-225](../../ProCALM/progen_conditional/model/adapter.py#L178-L225)) | Single summed embedding feeds one adaLN; conditions not separable | ProCALM |
| **External-retrieval / text-encoder readiness** | `ProjectionMLP` accepts arbitrary-dim continuous vectors ([adapter.py:7-45](../../ProCALM/progen_conditional/model/adapter.py#L7-L45)) | Discrete `nn.Embedding` lookups only | ProCALM |
| **Per-layer conditioning strength at frozen base** | Residual addition only — does not change intra-layer computation | Modulates both attention and FFN pre-LayerNorms | CFP-Gen |
| **Validated on diffusion LM** | No (ProGen2 AR) | Yes (DPLM-1 diffusion) | CFP-Gen |
| **Param count** | ~1.4M per condition (33 layers × ~41k) | ~9.8M (single shared adaLN) + small embedding tables | ProCALM |

ProCALM wins on architectural fit, frozen-base cleanliness, and future extensibility (Stage 5/6 retrieval and text). CFP-Gen wins on raw conditioning strength at strict frozen-base — which is recoverable by adding the FiLM path as an option.

## What to borrow from each

| Component | Borrow source | Build new in DPLM-2 | Why |
|---|---|---|---|
| `AdapterLayer` (low-rank bottleneck) | ProCALM `adapter.py:86-176` — math, near-zero init, `linear_up` init | Re-mount on `ModifiedEsmLayer` residual stream; param dims tuned for `d_model=1280` | ProCALM's adapter targets ProGen2's block; DPLM-2's block has the dual-stream rotary machinery we must not disturb |
| `ParallelAdapterLayer` (multi-condition) | ProCALM `adapter.py:178-225` — direct lift | Wrap to accept DPLM-2's `conditioning` config | Clean composition primitive |
| `ProjectionMLP` (condition encoder) | ProCALM `adapter.py:7-45` — direct lift | Add per-condition-type instantiation | Accepts arbitrary-dim condition vectors — works for one-hot EC, learned Pfam embeddings, retrieved-fragment embeddings, text-encoder outputs |
| Near-zero init scheme | ProCALM `adapter.py:136-138` (`weight_init=1e-5` on `linear_up`) | — | Identity-at-start invariant |
| `nn.Embedding` lookup for discrete tags (Pfam/GO/IPR/EC) | CFP-Gen `FuncTagEmbedder` `esm_cfpgen.py:210-223` | New module that *feeds into* `ProjectionMLP` | Bridges discrete annotation IDs to ProCALM's continuous-vector interface |
| (Optional) FiLM modulation | CFP-Gen `AGFMLayer` `esm_cfpgen.py:52-207` | Optional flag on `ModifiedEsmLayer`; off by default | Stronger per-layer conditioning when needed |
| Annotation data pipeline | CFP-Gen `uniprotKB.py` as reference for schema | New dataloader joining Pfam/InterPro/GO/EC labels onto `pdb_swissprot` records DPLM-2 already uses | CFP-Gen's collator is tied to its own format |

## Design

### 1. Adapter modules

New file: `src/byprot/models/dplm2/conditioning/parallel_adapter.py`

- `AdapterLayer` — low-rank bottleneck, near-zero init, mirrors [adapter.py:86-176](../../ProCALM/progen_conditional/model/adapter.py#L86-L176).
- `ParallelAdapterLayer` — stacks N `AdapterLayer`s, sums outputs, mirrors [adapter.py:178-225](../../ProCALM/progen_conditional/model/adapter.py#L178-L225).
- `ProjectionMLP` — projects arbitrary-dim condition vector to adapter input dim, mirrors [adapter.py:7-45](../../ProCALM/progen_conditional/model/adapter.py#L7-L45).

### 2. Annotation embedder (CFP-Gen-style, feeding into ProjectionMLP)

New file: `src/byprot/models/dplm2/conditioning/annotation_embedder.py`

- `AnnotationEmbedder({type_name: vocab_size}, embed_dim, p_dropout_uncond=0.1)` — `nn.Embedding` per annotation type (Pfam, GO, IPR, EC), multi-hot summation, CFG "unconditional" learnable token, per-batch label dropout. Output is a fixed-dim vector fed into `ProjectionMLP`, giving the ProCALM interface.

### 3. ModifiedEsmLayer patch (minimal)

Edit `src/byprot/models/dplm2/modules/dplm2_modeling_esm.py`:
- Add optional `layer_adapters: Optional[List[ParallelAdapterLayer]]` parameter to `ModifiedEsmEncoder.forward` (L367-464). For each layer `i`, after computing `layer_outputs[0]`, add `Σ adapter(h, s_j)` for `s_j` in that layer's parallel conditions. No edits to `ModifiedEsmLayer.forward` itself.
- (Optional, default off) `cond_film` kwarg on `ModifiedEsmLayer.forward` (L279-352) implementing CFP-Gen AGFM math. Behind a config flag.

### 4. DPLM-2 wrapper

New file: `src/byprot/models/dplm2/dplm2_conditional.py` subclassing `DPLM2`.

- `conditioning: ConditionConfig`:
  - `annotations: {type_name: {vocab_size, embed_dim}}` (Pfam / GO / IPR / EC)
  - `adapters: {n_parallel, adapter_dim, low_rank_dim, layer_indices, dropout, weight_init}`
  - `use_film: bool = False` (optional AGFM path)
- Constructor takes pretrained `airkingbd/dplm2_650m`, freezes base, instantiates annotation embedders, projection MLPs, and `ParallelAdapterLayer` per selected layer.
- `forward(input_ids, conditions=None, **kwargs)`:
  1. Encode each condition (annotation lookup OR external memory tensor) → `ProjectionMLP` → adapter input dim.
  2. Run base DPLM-2 forward; at each selected layer, apply parallel adapters and sum into residual stream.
  3. Loss computation in the four DPLM-2 modes (single/folding/inverse/joint) inherited unchanged from [dplm2.py:317-454](../src/byprot/models/dplm2/dplm2.py#L317-L454).
- Generation: reuse existing `generate` path ([dplm2.py:742-832](../src/byprot/models/dplm2/dplm2.py#L742-L832)); thread `conditions` into `forward_decoder` for every denoising step.

### 5. Data and config

- New datamodule: `src/byprot/datamodules/dataset/annotated_protein.py` and `annotated_protein_datamodule.py`. Joins Pfam/InterPro/GO/EC labels onto the existing `pdb_swissprot` records. Collator produces `batch["conditions"] = {type_name: multi-hot LongTensor [B, max_labels]}`.
- New experiment configs:
  - `configs/experiment/dplm2/cond_dplm2_650m_pfam.yaml` — Pfam-only annotation, single parallel adapter per layer. First milestone.
  - `configs/experiment/dplm2/cond_dplm2_650m_annotations.yaml` — Pfam + GO + IPR + EC, `n_parallel=4`.
  - `configs/experiment/dplm2/cond_dplm2_650m_external_memory.yaml` — accepts an external-memory tensor at inference (placeholder for retrieval/text-encoder in Stage 5/6).
- All configs: `model.conditioning.*`, `model.net.freeze_base=true`, pretrained `airkingbd/dplm2_650m`.

### 6. Critical files to modify or create

| File | Action |
|---|---|
| `src/byprot/models/dplm2/modules/dplm2_modeling_esm.py` | Edit: add `layer_adapters` (and optional `cond_film`) plumbing to `ModifiedEsmEncoder.forward` |
| `src/byprot/models/dplm2/dplm2.py` | Edit: thread `conditions` through `forward` and `forward_decoder`/`generate` |
| `src/byprot/models/dplm2/conditioning/parallel_adapter.py` | New: `AdapterLayer`, `ParallelAdapterLayer`, `ProjectionMLP` |
| `src/byprot/models/dplm2/conditioning/annotation_embedder.py` | New: `AnnotationEmbedder` (discrete-tag → vector interface) |
| `src/byprot/models/dplm2/conditioning/film.py` | New (optional, default off): `AgfilmModulation`, `modulate` |
| `src/byprot/models/dplm2/dplm2_conditional.py` | New: `ConditionalDPLM2` wrapper, `@register_model` |
| `src/byprot/models/dplm2/__init__.py` | Edit: export new classes |
| `src/byprot/datamodules/dataset/annotated_protein.py` | New: annotation-joined dataset |
| `src/byprot/datamodules/annotated_protein_datamodule.py` | New: datamodule |
| `configs/datamodule/annotated_protein.yaml` | New |
| `configs/experiment/dplm2/cond_dplm2_650m_pfam.yaml` | New |
| `configs/experiment/dplm2/cond_dplm2_650m_annotations.yaml` | New |
| `configs/experiment/dplm2/cond_dplm2_650m_external_memory.yaml` | New |
| `scripts/prepare_annotations.py` | New: builds Pfam/InterPro/GO/EC label files joined to `pdb_swissprot` ids |
| `src/byprot/tasks/lm/conditional_dplm2.py` | New: Lightning task with condition-aware `training_step`, mirroring [tasks/lm/dplm2.py](../src/byprot/tasks/lm/dplm2.py) |

## Verification

End-to-end test plan, executed in order:

1. **Frozen-base equivalence** — instantiate `ConditionalDPLM2` from `airkingbd/dplm2_650m` with empty `conditioning` config. Forward pass on a fixed batch. Confirm logits are bit-identical to plain `DPLM2` from the same checkpoint (no parameters added or reordered into the base graph).
2. **Adapter identity-at-init** — instantiate with Pfam adapter enabled, weight_init=1e-5. Forward pass without training. Confirm logits match base DPLM-2 within tolerance (adapter contributes ~0 at init).
3. **Frozen-base training step** — train one optimizer step on a 4-sample batch. Confirm only `AnnotationEmbedder` + `ProjectionMLP` + `ParallelAdapterLayer` parameters have non-zero grad; base DPLM-2 params unchanged.
4. **Generation parity** — run `generate` for `co_generation` and `sequence_generation` with no conditions. Confirm outputs match base DPLM-2 outputs within tolerance.
5. **Conditional generation** — run `generate` with a Pfam-family annotation. Confirm output sequences scan back to the requested family with HMMER (per [conditional_dplm_pfam.md](conditional_dplm_pfam.md)). Compare against unconditional DPLM-2 baseline.
6. **Multi-condition** — run with Pfam + IPR + EC + GO annotations. Confirm parallel adapters compose without degradation vs single-condition.
7. **External memory** — feed a dummy external-memory tensor via `conditions["external"]`. Confirm forward pass works and gradients flow only to the corresponding `ProjectionMLP` + adapter.
8. **Existing test suite** — run `pytest tests/` to confirm no regressions in DPLM-2 forward/generation paths.

Command reference:
- Smoke test: `bash scripts/reproduce/training/run_train.sh --recipe cond_dplm2_650m_pfam --fast-dev-run --max-steps 2 --name smoke/cond_dplm2_pfam`
- Full first run: same recipe, `--max-steps 100000 --name reproduce/cond_dplm2_650m_pfam`

## Risks and mitigations

- **Parallel adapter may under-condition at strict frozen base** — ProCALM uses adapter-only fine-tuning (the whole adapter + projection set is trained, base frozen). If conditioning signal is too weak, mitigation is the optional FiLM path (`use_film: true`), which gives the condition direct leverage over per-layer LayerNorms without unfreezing the base. If FiLM is also insufficient, add LoRA on attention as a config flag.
- **Diffusion-vs-AR regime mismatch** — ProCALM was validated on ProGen2 (AR); DPLM-2 applies the adapter at every denoising step (T=500). Repetitive application could amplify or attenuate the adapter effect. Verify in the Stage-2 smoke test; if attenuated, increase `weight_init` or train longer.
- **Rotary/type_ids interaction** — ProCALM adapter operates on the residual stream post-block, so it does not touch the dual-modality rotary embeddings ([dplm2_modeling_esm.py:28-86](../src/byprot/models/dplm2/modules/dplm2_modeling_esm.py#L28-L86)) or the `type_ids` plumbing. Verify via the Stage-1 frozen-base equivalence test.
- **Single-modality training mode** — DPLM-2's `single_modality` mode masks cross-modality attention via `attention_bias` ([dplm2.py:243-256](../src/byprot/models/dplm2/dplm2.py#L243-L256)). Adapter operates on post-attention residuals, so it remains active in both single-modality and joint modes; the annotation condition is independent of attention masking. Verify in smoke test.
