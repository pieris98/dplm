**Short Report**

ProCALM’s paper uses conditional adapters for three main conditioning modalities:

- **EC/function conditioning**: EC numbers are encoded as either hierarchical one-hot vectors, DRFP reaction fingerprints, or CREEP text-description embeddings. The paper says EC one-hot is 630d, DRFP is 2048d, and CREEP comes from a contrastive protein/reaction/text model. See [procalm_paper.md](</home/cherry/dev/phd/ProCALM/procalm_paper.md:1099>).
- **Taxonomy conditioning**: taxonomy is simplified to a 4-way kingdom one-hot vector: bacteria, eukaryota, archaea, viruses. See [procalm_paper.md](</home/cherry/dev/phd/ProCALM/procalm_paper.md:1121>).
- **Natural-language/function text conditioning**: the paper reports a ProteinCLAP/ProteinDT-style text-guided experiment using ProteinDT’s training data, not just enzyme data. See [procalm_paper.md](</home/cherry/dev/phd/ProCALM/procalm_paper.md:423>).

Datasets/splits in the paper:

- **UniRef EC data**: 29.4M EC-associated sequences, 5222 ECs.
- **SwissProt EC data**: SwissProt Train, Heldout 90%, Heldout 70%, Heldout ECs, plus sampled Train Common, Train Rare, and Heldout EC generation sets. See [procalm_paper.md](</home/cherry/dev/phd/ProCALM/procalm_paper.md:976>).
- SwissProt is filtered to ECs with CARE reactions and resampled by 50% MMseqs2 clusters before split construction. See [procalm_paper.md](</home/cherry/dev/phd/ProCALM/procalm_paper.md:1085>).
- Text-guided generation uses the same ProteinDT training data; the local repo does not appear to include the full ProteinDT text dataset/config.

**How ProCALM Implements This**

The adjacent ProCALM repo implements the paper’s adapter design directly.

- The base model is loaded from pretrained ProGen2/ProCALM weights and frozen by default: [model.py](</home/cherry/dev/phd/ProCALM/progen_conditional/model/model.py:815>).
- A conditioning encoder is created per condition key from `encoding_dimensions`: [model.py](</home/cherry/dev/phd/ProCALM/progen_conditional/model/model.py:820>).
- Each transformer layer gets a `ParallelAdapterLayer`: [model.py](</home/cherry/dev/phd/ProCALM/progen_conditional/model/model.py:848>).
- The adapter itself normalizes the LM hidden state and condition vector, low-rank projects the hidden state, concatenates or sums the condition, runs an MLP, then projects back to model hidden size: [adapter.py](</home/cherry/dev/phd/ProCALM/progen_conditional/model/adapter.py:86>).
- During the transformer block forward pass, the adapter output is added to the normal attention+MLP update before the residual connection: [model.py](</home/cherry/dev/phd/ProCALM/progen_conditional/model/model.py:333>).
- Joint EC+taxonomy is implemented either by a shared combined adapter or by separate parallel adapters. The published joint config uses separate adapters via `conditions_shared_adapter: False`: [ec-onehot+tax-swissprot.yml](</home/cherry/dev/phd/ProCALM/config/long-final/ec-onehot+tax-swissprot.yml:5>).
- Training chooses either adapter-only fine-tuning or full fine-tuning. Default is adapter/projection training only; `full_finetuning: True` unfreezes all model parameters: [train.py](</home/cherry/dev/phd/ProCALM/progen_conditional/composer/train.py:57>).
- Data collation maps `ec` and `tax` strings to precomputed tensors, repeats them across sequence length, and places them in `batch["adapter_input"]`: [prepare.py](</home/cherry/dev/phd/ProCALM/progen_conditional/data/prepare.py:63>).

**What Is Missing Or Partial Locally**

In the ProCALM repo:

- EC and taxonomy conditioning are present and wired through configs/data/model code.
- CREEP text-description-as-EC conditioning is present as `data/ec2CREEP_text.pt`.
- The general defaults mention `stability`, but the actual batch preparation only handles `ec` and `tax`, so stability conditioning is not really wired through: [defaults.py](</home/cherry/dev/phd/ProCALM/progen_conditional/defaults.py:43>) and [prepare.py](</home/cherry/dev/phd/ProCALM/progen_conditional/data/prepare.py:99>).
- I did not find a full local implementation/config for the paper’s ProteinCLAP/ProteinDT natural-language prompt experiment. The local code can accept arbitrary condition tensors in principle, but the shipped data path is mainly `ec`/`tax`.

In the current DPLM repo:

- There is a **Pfam soft-prefix conditional DPLM** scaffold: family IDs are embedded, projected to learned prefix embeddings, concatenated before sequence tokens, and logits are sliced back to protein positions. See [conditional_dplm.py](</home/cherry/dev/phd/dplm/src/byprot/models/dplm/conditional_dplm.py:19>) and [conditional_dplm.py](</home/cherry/dev/phd/dplm/src/byprot/models/dplm/conditional_dplm.py:85>).
- That DPLM prefix model freezes the base DPLM by default: [conditional_dplm.py](</home/cherry/dev/phd/dplm/src/byprot/models/dplm/conditional_dplm.py:58>).
- However, it is not imported in `src/byprot/models/dplm/__init__.py`, so its registry decorators may not fire unless imported manually: [__init__.py](</home/cherry/dev/phd/dplm/src/byprot/models/dplm/__init__.py:5>).
- The active `cond_dplm_*` configs are actually **structure-conditioned inverse-folding** configs using CATH + a GVP encoder, not EC/tax/text function conditioning: [cond_dplm_650m.yaml](</home/cherry/dev/phd/dplm/configs/experiment/dplm/cond_dplm_650m.yaml:18>).
- That structure adapter replaces only the final ESM layer with a cross-attention adapter over encoder features and freezes non-adapter parameters: [dplm_adapter.py](</home/cherry/dev/phd/dplm/src/byprot/models/dplm/modules/dplm_adapter.py:41>) and [dplm_adapter.py](</home/cherry/dev/phd/dplm/src/byprot/models/dplm/modules/dplm_adapter.py:189>).
- There is a Pfam preprocessing script that writes debug JSONL splits and `family_vocab.json`, but I did not find a datamodule/training config that feeds `family_idx` into the Pfam prefix model yet: [prepare_pfam_dataset.py](</home/cherry/dev/phd/dplm/scripts/prepare_pfam_dataset.py:71>).

Bottom line: ProCALM’s local repo mostly implements the paper’s EC/tax adapter system around pretrained ProGen2. The current DPLM repo has useful conditional-generation scaffolds, but not a ProCALM-equivalent EC/tax/text adapter implementation; it currently has structure-conditioned inverse folding plus an unfinished Pfam family-prefix path.

---

# Addendum (2026-10-13): claims inventory, what worked/failed in ProGen2, and the exhaustive ProCALM→DPLM-2 difference list

Sources: paper transcription `/home/cherry/dev/phd/ProCALM/procalm_paper.md` (refs `pp:NNN`), repo `/home/cherry/dev/phd/ProCALM/` (`model.py` = `progen_conditional/model/model.py`), released per-condition CSVs under `ProCALM/results/`. The transcription carries figure captions only — all numbers below come from the released CSVs (mean over 24 ECs per split, the paper's protocol); checkpoint↔tokens: `ba11000`=1.5B, `ba21000`=3B, `ba42000`=6B, `ba63000`=9B (`README.md:35`).

## A. Evidence-status inventory

**Theoretical/proven content: NONE.** No equation, derivation, or analysis anywhere in the paper. In particular **identity-at-initialisation is never claimed or discussed in the paper** — it exists only in code: `linear_up.weight ~ N(0, 1e-5)`, zero bias (`adapter.py:136-138`), with a one-line code comment. Low-rank+concat design, gradient flow, OOD mechanism: all unargued.

**Empirically demonstrated (key results):**
- In-distribution EC conditioning works: Correct-EC 0.79 (1.5B tok) → 0.94 (9B tok) vs ZymCTRL 0.973; ProCALM wins diversity (90%-clusters 0.48–0.70 vs 0.85) and cost (40–240 A100-h vs ZymCTRL's 15,000) (`pp:344-394`).
- **Parallel adapters ≫ shared adapter for joint conditioning**: Correct-EC 0.612 vs 0.288 at 3B (`pp:400-404`, Fig. A.8).
- **Concat ≫ sum of the condition**: 0.791 vs 0.662 in-distribution; sum loses *all* rare-EC capability (0.043 vs 0.000) (Fig. A.7).
- PEFT ≫ full finetuning: full-FT memorizes (Correct-EC 0.898 but MeanMaxID 0.992, 90%Clust 0.142 vs 0.709/0.674) (Figs. A.3–A.4).
- **Training longer: correctness ↑, diversity ↓** (`pp:354-355`) — the direct precedent for our checkpoint-sweep hypothesis (meeting point 3).
- Smaller adapter ("Small": half dims, 2-layer MLP): worse everywhere, worst OOD (rare-EC 0.000 vs 0.043).
- Held-out ECs (0 training sequences): valid enzymes 0.64–0.70 but **exact-condition 0.000–0.001** for all three encodings; only partial-credit (EC hierarchy levels) shows signal (`pp:460-504`).
- Unseen-taxonomy (bacteria held out): prompting with bacteria ≫ other taxa (both-correct 0.066 vs 0.028–0.041).
- LM scale-up 764M→2.7B→6.4B: better loss, **no generation-quality gain** (`pp:392-394`).

**Asserted without evidence (strict list — relevant ones):**
1. **Portability**: "we expect that it can be extended to a wide range of generative language models" (`pp:48-50`); "could easily be applied to other autoregressive PLMs" (`pp:581-582`). Zero experiments outside ProGen2. *Our DPLM-2 port rests on this untested assertion.*
2. **OOD mechanism**: conditioning "in continuous space rather than through initial tokenization" (`pp:270-273`) — asserted, and undercut by their own result that continuous DRFP/CREEP did *not* beat discrete OH OOD (`pp:497`).
3. "Matches or exceeds the performance of existing methods" (`pp:45-47`): on the headline in-distribution metric ProCALM *trails* ZymCTRL (0.79–0.94 vs 0.973); "matches" holds only for validity/diversity/cost.
4. "Can generalize to rare and unseen functions" (`pp:46-48`): exact-condition accuracy is 0.000–0.001; what's shown is partial-credit similarity. (Their own discussion is more honest: "performance is limited", `pp:555-556`.)
5. "No data leakage in the CREEP representation" (`pp:500-502`): asserted twice, no verification described.
6. Finer taxonomy encodings "were explored and were worse" (`pp:1123-1124`): experiment claimed, no number/figure/config shown.
7. Latent-space-geometry claims (`pp:284-285`, `pp:322-324`): closest evidence is an indirect DRFP-cosine correlation (Fig. A.9).

## B. What worked / didn't in the ProGen2 setting

**Worked:** in-distribution EC + taxonomy conditioning (0.84–0.95 validity, 0.6–0.85 correct-condition); joint conditioning at a ~18-pt correctness cost; parallel adapters; concat; PEFT; text conditioning qualitatively beating ProteinDT; extreme compute efficiency.

**Didn't work:** exact conditioning on unseen/rare functions (≈0); continuous encodings (DRFP/CREEP) failing to beat one-hot incl. OOD; summed condition (rare capability wiped out); shared adapters for multi-condition; small adapter; full finetuning (memorization); finer taxonomy encodings; LM scaling; the rarest class (viruses: taxonomy accuracy 0.000).

**Architecture ablations: only three exist** (Small, Summed, shared-vs-parallel). **NOT FOUND anywhere in the paper:** adapter placement (all 27 layers get one, `model.py:856`, no option), where in the residual stream the output is injected (only "merged at the end of each transformer layer", `pp:1157`), **the adapter input source h**, any layer-wise contribution/magnitude analysis, **any discussion of adapter outputs compounding across layers**, bottleneck-size sweep (16 stated once), weight_init, dropout (code: 0.1 config + 0.1 hardcoded internal), no significance tests or seeds.

## C. Exhaustive difference list: ProCALM (ProGen2) vs our DPLM-2 port

| # | aspect | ProCALM | our port | consequence |
|---|---|---|---|---|
| 1 | model class | AR decoder (GPT-J-style *parallel* attn+MLP blocks, no ln_2) | discrete diffusion (ModifiedEsmLayer, sequential post-LN) | conditioning re-applied every **token** step (with KV cache) vs every **denoising** step for us; no ProCALM precedent for our CFG |
| 2 | **adapter input h** | `attn_output + feed_forward_hidden_states` — the branch sum **before** the residual add (`model.py:337-342`); residual (and hence previous adapters' outputs) reaches `h` only *indirectly* through the next layer's ln_1 + branches | **fused post-residual stream** — [dplm2_modeling_esm.py:432-446](../src/byprot/models/dplm2/modules/dplm2_modeling_esm.py#L432-L446) adds each adapter into the residual stream, so adapter *l* directly reads all accumulated adapter outputs | **our port compounds strictly more than ProCALM's.** The paper never discusses input source; no knob exists in their repo either. Their GPT-J parallel block has no exact analog in our sequential layer — a ProCALM-faithful variant for us = adapter reads the layer update pre-residual-add. Upgrades meeting point 4a / hypothesis H2 |
| 3 | injection point | third parallel branch, added to branch sum, before residual add | summed into residual stream after layer output | related to #2 |
| 4 | condition content | EC: 630-d one-hot / DRFP 2048-d / CREEP text embedding; taxonomy: 4-kingdom one-hot; 1–2 conditions, near-mutually-exclusive | GO-F + IPR multi-hot (hundreds of co-occurring terms) via learned embedder | granularity + co-occurrence untested in ProCALM; their hierarchy-encoding failure (`pp:1123-1124`) is the nearest data point |
| 5 | null/missing condition | zeros vector (`runner.py:80-90`) — a run-time fallback, *not* used for guidance | learned/embedder null used as CFG base | our CFG extrapolation has no analog in their pipeline |
| 6 | guidance/sampling | **none**; top-p 0.95, temp 0.3 (temp 1.0 for text task); fixed seeds | diffusion iterative denoising + CFG w∈{1..8} | non-monotone w-curve (JS 0.49/0.20/0.17/0.25) is uncharted for this method family |
| 7 | adapter capacity | standard: c_s 256, bottleneck 16, 4-layer MLP (configs; paper text says 3 — discrepancy); ~70M params on 764M frozen | c_s 128, bottleneck 16, 2-layer MLP | **we sit at/below their "Small" variant, which was the ablated negative** — cheap capacity bump (c_s 256, 4 layers) is a candidate lever under H3 |
| 8 | training | Swissprot 152k seqs, 25–150 epochs (1.5–9B tokens), packed 144k-token batches, DecoupledAdamW lr 5e-4, wd 5e-6, cosine to 10%, clip 0.8, bf16; `'1'+seq+'2'` + **random reversal p=0.5** augmentation (paper never mentions it, `prepare.py:54-61`) | CFP-Gen subset, 100K steps, our hyperparams | reversal augmentation absent in ours (probably immaterial); their epoch counts show adapters trained to saturation |
| 9 | output modalities | sequence only | aa + **struct tracks**; adapters also steer struct logits | no precedent for keeping a co-generating track on-manifold |
| 10 | eval protocol | BLASTp validity oracle, EC/tax oracles, enrichment vs train prevalence, ESMFold pLDDT, MMseqs2 novelty/diversity, perplexity-across-splits | IPS set-F1 (dead for our arms), DeepGO-SE, MRR, MMD, ESM-space UMAP | oracle validity (BLAST hit ≥80% cov) is a metric we lack; their perplexity-flatness check is the analog of our loss monitoring |
| 11 | regime mapping | train_common / train_rare / heldout-EC label regimes | our eval labels are in-distribution *label-vocab* (held-out proteins only) → **their train_common regime is our comparison class** (in-dist Correct-EC 0.79–0.94) | our recovery (MRR 0.11–0.13, IPR 0) sits far below their in-distribution conditioning performance; our IPR-zero failure resembles their *heldout-EC* mode, but we are not in that regime — something in the port underperforms |
| 12 | paper-vs-code discrepancies + gotchas | 3-layer (paper) vs 4-layer (configs) adapter MLP; "256-d two-layer" encoder vs 128→256 code; `adapter_c_hidden` config knob is dead (hardwired 16); two independent dropouts; `full_fineturning` typo | our port passes `c_hidden` explicitly (dead-knob bug not inherited); single dropout on h and s | documented so the paper draft doesn't propagate their typos |

**Consequences queued:** (i) the fused/ProCALM-faithful/base adapter-input ablation is now a three-way comparison, motivated by #2; (ii) capacity bump per #7 is the cheap lever if H3; (iii) checkpoint sweep per point 3 has direct precedent (correctness↑ diversity↓ with training); (iv) paper draft must not describe ProCALM as theoretically grounded — it is empirical, and its portability claim was asserted, so our port is a genuine test of it (good framing for the paper).
