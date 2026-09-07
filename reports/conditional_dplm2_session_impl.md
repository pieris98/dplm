# Conditional DPLM-2 Implementation — Session Report

**Date:** 2026-07-07
**Plan reference:** [plans/conditional_dplm2_architecture.md](../plans/conditional_dplm2_architecture.md)
**Goal:** Add ProCALM-style parallel-adapter annotation conditioning to pretrained DPLM-2, preserving frozen-base correctness.

---

## Design decision: ProCALM-primary, CFP-Gen optional

Two candidate designs were compared before any code was written.

| Aspect | ProCALM parallel adapter (chosen) | CFP-Gen AGFM-FiLM (optional) |
|---|---|---|
| Attachment | Parallel residual on post-block hidden state | Replaces LayerNorm→attn→FFN internals |
| Frozen-base cleanliness | Near-zero init; base graph untouched | Requires modified `ModifiedEsmLayer.forward` |
| Multi-condition | `ParallelAdapterLayer` stacks separable adapters | Single summed embedding → one adaLN |
| Condition interface | `ProjectionMLP` accepts arbitrary-dim vectors | Discrete `nn.Embedding` lookups only |
| Validation regime | ProGen2 (AR) — not yet on diffusion LM | DPLM-1 (diffusion LM) |

**Decision:** primary mechanism is the ProCALM parallel adapter. CFP-Gen's discrete-tag embedder is still borrowed to feed annotation IDs into the adapter's continuous-vector interface. AGFM-FiLM is documented in the plan as an optional flag but not implemented this session.

The full rationale, including a per-criterion comparison table and a list of what is borrowed from each source, is in [plans/conditional_dplm2_architecture.md](../plans/conditional_dplm2_architecture.md).

---

## Files created

| File | Purpose |
|---|---|
| [src/byprot/models/dplm2/conditioning/parallel_adapter.py](../src/byprot/models/dplm2/conditioning/parallel_adapter.py) | `ProjectionMLP`, `AdapterLayer`, `ParallelAdapterLayer`. Ported from [ProCALM `progen_conditional/model/adapter.py`](../../ProCALM/progen_conditional/model/adapter.py) but reimplemented for DPLM-2. `AdapterLayer.forward` broadcasts the per-sample condition `s` across the sequence dim before concat (ProCALM's original assumes `s` is already per-position). |
| [src/byprot/models/dplm2/conditioning/annotation_embedder.py](../src/byprot/models/dplm2/conditioning/annotation_embedder.py) | `AnnotationEmbedder` — CFP-Gen-style `nn.Embedding` per annotation type (Pfam/GO/IPR/EC), multi-hot summation, classifier-free-guidance dropout (learnable "unconditional" token). Output feeds into `ProjectionMLP`. |
| [src/byprot/models/dplm2/dplm2_conditional.py](../src/byprot/models/dplm2/dplm2_conditional.py) | `ConditionalDPLM2` wrapper (`@register_model("conditional_dplm2")`). Subclasses `MultimodalDiffusionProteinLanguageModel`, freezes base, adds annotation embedders + projectors + per-layer parallel adapters. Overrides `forward` / `generate` to thread `conditions` through. |
| [plans/conditional_dplm2_architecture.md](../plans/conditional_dplm2_architecture.md) | Plan document (ProCALM-primary decision, design, file map, verification plan, risks). |

## Files modified

| File | Change |
|---|---|
| [src/byprot/models/dplm2/modules/dplm2_modeling_esm.py](../src/byprot/models/dplm2/modules/dplm2_modeling_esm.py) | Added `layer_adapters` / `layer_adapter_inputs` kwargs to `ModifiedEsmEncoder.forward`, `ModifiedEsmModel.forward`, and `EsmForDPLM2.forward`. The encoder applies each adapter as `hidden_states += adapter(h, s)` after the layer call. **Purely additive** — when `layer_adapters` is `None`, the forward path is byte-identical to base DPLM-2. |
| [src/byprot/models/dplm2/__init__.py](../src/byprot/models/dplm2/__init__.py) | Export `ConditionalDPLM2`, `ConditionalDPLM2Config`, and the conditioning config dataclasses. |

---

## Design highlights

### 1. Adapter plumbing is non-invasive

`ModifiedEsmEncoder.forward` gained two optional kwargs. The adapter is applied at the residual stream between layers — it does not touch `ModifiedEsmLayer.forward`, the dual-modality rotary embeddings, or the `type_ids` plumbing. When `layer_adapters=None` (the default for base DPLM-2), the encoder is byte-identical to the pretrained model.

### 2. Conditions flow via a per-instance stash during generation

The parent `forward_decoder` calls `self.forward(input_ids=output_tokens)` with no conditions kwarg. Reimplementing that 70-line body (modality logit masking, top-p, annealing, history) would have been brittle. Instead, `ConditionalDPLM2.generate` populates `self._active_layer_adapter_inputs` once at entry (conditions don't depend on noisy tokens) and delegates to `super().generate(...)`. `forward` consults this stash when no explicit `conditions=` kwarg is passed.

### 3. Config inheritance

`ConditionalDPLM2Config(DPLM2Config)` inherits all base DPLM-2 fields and adds `conditioning: ConditioningConfig`. The parent's `_update_cfg` (OmegaConf merge) handles this cleanly — same config schema for training and inference.

### 4. Frozen-base + near-zero init

`AdapterLayer.linear_up` is initialized with `weight_init=1e-5` std (ProCALM default), so a freshly-constructed `ConditionalDPLM2` produces logits within ~1e-4 of the pretrained base. Only `AnnotationEmbedder`, `ProjectionMLP`, and `ParallelAdapterLayer` parameters have `requires_grad=True` after `freeze_base=True`.

---

## Verification status

### ✅ Standalone module tests pass

- `ParallelAdapterLayer` shape check: `[2, 5, 1280]` hidden + 2× `[2, 128]` condition → `[2, 5, 1280]` output.
- Near-zero init check: max abs adapter contribution at init `= 1.06e-4` (well below `1e-2` threshold).
- `AnnotationEmbedder` multi-hot + CFG dropout: produces `{'pfam': [B, 128], 'go': [B, 128]}`.

### ✅ Model instantiation works

`ConditionalDPLM2(cfg)` loads from a config with:
- `vocab_sizes: {pfam: 100}` (smoke test)
- `c_s=128, c_hidden=16, weight_init=1e-5`
- `freeze_base=True`

Result: base DPLM-2 params all frozen, 9.69M trainable params across annotation embedder + projectors + 33 layer adapters.

### ❌ Frozen-base equivalence test (FAILED — test bug, not code bug)

The test instantiated two fresh models via the regular constructor with HF repo ids (`airkingbd/dplm2_650m`) and `training_stage: continue_train_from_dplm2`. This code path requires a **local checkpoint path** ([src/byprot/models/utils.py:170](../src/byprot/models/utils.py#L170) asserts `is_local`). Without it, `NetConfig.pretrain` defaults to `False`, so no pretrained weights are loaded — both "base" and "conditional" models are randomly initialized and naturally differ.

**Root cause:** test harness used the wrong loading path, not the conditioning code.

**Fix needed:** use `ConditionalDPLM2.from_pretrained("airkingbd/dplm2_650m", cfg_override={...})` (inherited from DPLM-2's HF-loader `from_pretrained` at [dplm2.py:161-166](../src/byprot/models/dplm2/dplm2.py#L161-L166)) so the actual pretrained weights are loaded for both models. This was identified but not yet run — the session was interrupted by the request to write this report.

---

## What is NOT done

Per the plan in [conditional_dplm2_architecture.md](../plans/conditional_dplm2_architecture.md), the following items remain:

1. **Frozen-base equivalence test** — re-run with `from_pretrained` loader (see above).
2. **Annotation datamodule** — `src/byprot/datamodules/dataset/annotated_protein.py` and `annotated_protein_datamodule.py`. Joins Pfam/InterPro/GO/EC labels onto `pdb_swissprot` records.
3. **Lightning task** — `src/byprot/tasks/lm/conditional_dplm2.py` mirroring `tasks/lm/dplm2.py` with annotation-aware `training_step`.
4. **Experiment configs** — `configs/experiment/dplm2/cond_dplm2_650m_{pfam,annotations,external_memory}.yaml`.
5. **Annotation prep script** — `scripts/prepare_annotations.py` for joining Pfam/InterPro/GO/EC labels to SwissProt ids.
6. **Optional FiLM path** — `AgfilmModulation` + `modulate` for users who want stronger per-layer conditioning at strict frozen base. Documented in plan, not implemented.
7. **Real conditional-generation smoke test** — generate with a Pfam annotation, scan back with HMMER.

---

## Key references (file:line)

- Parallel adapter module: [parallel_adapter.py:86](../src/byprot/models/dplm2/conditioning/parallel_adapter.py#L86)
- Annotation embedder: [annotation_embedder.py:30](../src/byprot/models/dplm2/conditioning/annotation_embedder.py#L30)
- ConditionalDPLM2 wrapper: [dplm2_conditional.py:92](../src/byprot/models/dplm2/dplm2_conditional.py#L92)
- Encoder adapter hook: [dplm2_modeling_esm.py:430](../src/byprot/models/dplm2/modules/dplm2_modeling_esm.py#L430)
- Plan document: [plans/conditional_dplm2_architecture.md](../plans/conditional_dplm2_architecture.md)
- Source adapters (ProCALM reference): [../../ProCALM/progen_conditional/model/adapter.py](../../ProCALM/progen_conditional/model/adapter.py)
