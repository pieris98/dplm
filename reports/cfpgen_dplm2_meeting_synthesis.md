# CFP-Gen vs DPLM-2: What Already Exists — Meeting Synthesis

Presentation-ready synthesis across four questions. All claims are grounded in the papers (`cfpgen_paper.md`, `dplm2_paper.md`) and the code in `/home/cherry/dev/phd/{dplm,cfpgen}` (file:line citations inline). This describes **what exists** — no implementation proposals.

---

## Q1 — Methodology: how each adds a non-sequence modality

### CFP-Gen: function as a side-channel CONDITION (frozen backbone + adapters)

CFP-Gen conditions an amino-acid diffusion LM on function/structure/motif through three bolt-on modules. The model's **only output is the amino-acid sequence** — function, structure, and motif are inputs, never generated ([cfpgen_paper.md:242-250](../cfpgen/cfpgen_paper.md#L242-L250)).

| Module | Mechanism | Where |
|---|---|---|
| **AGFM** (Annotation-Guided Feature Modulation) | FiLM. Per-type `nn.Embedding` for GO/IPR/EC, **summed**, then one shared `adaLN` MLP regresses **6 params/layer** (shift/scale/gate for the self-attention sublayer **and** the FFN sublayer). Shift/scale zero-init, gate ones-init → identity at start. Applied at **every** ESM layer. | [esm_cfpgen.py:148-207](../cfpgen/src/byprot/models/lm/esm_cfpgen.py#L148-L207); paper §3.2 [cfpgen_paper.md:265-333](../cfpgen/cfpgen_paper.md#L265-L333) |
| **RCFE** (Residue-Controlled Functional Encoder) | ControlNet: a **trainable copy of the first half** of the ESM blocks (`deepcopy`), with **zero-init** `F_in`/`F_out` projections, added to the frozen main branch. Handles sequence motifs. Unlike DPLM/EvoDiff motif infilling (which **fix** motif residues), RCFE **dynamically updates** them during sampling. | [esm_cfpgen.py:21-49](../cfpgen/src/byprot/models/lm/esm_cfpgen.py#L21-L49); paper §3.3 [cfpgen_paper.md:335-403](../cfpgen/cfpgen_paper.md#L335-L403) |
| **GVPT** (structure modality) | A **GVP-Transformer** (frozen ESM-IF1) encodes backbone coords, injected via a **cross-attention adapter** that **reuses DPLM-1's pretrained structure adapter with NO further training**. | [modules/dplm_adapter.py](../cfpgen/src/byprot/models/lm/modules/dplm_adapter.py), [modules/gvp_transformer_encoder.py](../cfpgen/src/byprot/models/lm/modules/gvp_transformer_encoder.py); paper §3.4 [cfpgen_paper.md:467-480](../cfpgen/cfpgen_paper.md#L467-L480) |

Composition (paper Eq. 4): `h = A( RCFE( AGFM(x, c_anno), c_seq ), GVPT(c_str) )`, where `A` is the cross-attention layer. **Loss: `RDMCrossEntropyLoss` (weighted cross-entropy on AA tokens) — no contrastive / InfoNCE / alignment term anywhere in the repo.**

### DPLM-2: structure as a native GENERATIVE TOKEN (one unified model)

DPLM-2's central decision is the **opposite** of CFP-Gen's: structure is not injected via an adapter — it is **tokenized into the same discrete vocabulary** as amino acids and processed as first-class input tokens in a single self-attention stream. There is **no cross-attention** in DPLM-2 ([dplm2.py:263-268](src/byprot/models/dplm2/dplm2.py#L263-L268) never passes `encoder_hidden_states`; `add_cross_attention` is never enabled in any config).

- Structure tokens come from a frozen **LFQ tokenizer** (8192 codes, 13-bit binary — see Q3).
- `[struct | aa]` concatenated along the sequence dim ([dplm2.py:403](src/byprot/models/dplm2/dplm2.py#L403)), looked up in **one shared embedding table** ([dplm2.py:258-261](src/byprot/models/dplm2/dplm2.py#L258-L261)).
- Modality inferred from token-id range (`<33` = AA, `≥33` = struct) via `get_modality_type` — **no type embedding** ([dplm2.py:220-227](src/byprot/models/dplm2/dplm2.py#L220-L227)).
- `ModifiedRotaryEmbedding` halves the sequence length so struct-position `i` and aa-position `i` share the same rotary phase ([dplm2_modeling_esm.py:28-85](src/byprot/models/dplm2/modules/dplm2_modeling_esm.py#L28-L85)).
- Four training modes via masking + attention-bias (each 0.25; `independent`=0.0): single-modality, folding (`p(z|s)`), inverse-folding (`p(s|z)`), joint co-generation (`p(z,s)`) — [dplm2.py:317-392](src/byprot/models/dplm2/dplm2.py#L317-L392).
- **Generative / any-to-any**: the model co-generates seq + struct, enabling folding, inverse folding, co-generation, motif scaffolding from one model. Paper framing: "eliminating the need for a cascaded generation paradigm" ([dplm2_paper.md:308-312](dplm2_paper.md#L308-L312)).
- **Loss: `StructAARDMCrossEntropyLoss` — cross-entropy applied separately to struct and aa logits, summed. No contrastive term.** The whole model is (re)trained from DPLM init with LoRA (rank 16).

### The slide's punchline — side-by-side

| Dimension | **CFP-Gen** (function = side-channel condition) | **DPLM-2** (structure = native token) |
|---|---|---|
| Non-sequence modality: representation | Continuous vector: per-type `nn.Embedding` summed → FiLM; structure = external GVP encoder + cross-attention | Native **discrete token** in a unified vocab (frozen LFQ tokenizer) |
| Where it enters the network | Per-layer FiLM + ControlNet copy + cross-attention at the adapter layer | Input token sequence → standard self-attention |
| Generative vs conditioning | **Condition only** — cannot generate function/structure/motif | **Generative** — co-generates seq+struct (any-to-any) |
| Backbone retraining | **Frozen** + trained adapters (GVPT adapter reused with zero training) | **Whole model retrained** from DPLM init + LoRA |
| Contrastive/InfoNCE loss? | No — diffusion CE only | No — diffusion CE only |

**Synthesis (one line):** CFP-Gen treats function *exactly the way DPLM-1 treated structure* — as a side-channel condition injected into a frozen backbone via FiLM + cross-attention. DPLM-2 is precisely the *upgrade away* from that pattern: it removes cross-attention and makes the modality a native, generative token. So the design question for our project is whether function follows the **CFP-Gen adapter path** (fast, frozen-base, non-generative) or the **DPLM-2 tokenization path** (slower, requires a function tokenizer + retraining, but generative + unified). CFP-Gen's own paper lists "sequence–structure co-design" and "enriching annotations" as future work ([cfpgen_paper.md:771-776](../cfpgen/cfpgen_paper.md#L771-L776)) — the very things tokenization would give.

---

## Q2 — What data is actually in each dataset

### DPLM (sequence-only pretraining)
- **UniRef50** (clustered at 50% identity), **~45M sequences** ([dplm2_paper.md:345-346](dplm2_paper.md#L345-L346)).
- One example = **just the consensus AA string** ([uniref.py:217-230](src/byprot/datamodules/dataset/uniref.py#L217-L230)). No structure, no function.

### DPLM-2 (sequence + structure co-generation)
- **PDB (~20K clustered experimental) + AFDB-SwissProt (~200K AlphaFold-predicted)**, combined as `pdb_swissprot`, **~200K proteins after filtering** ([dplm2_paper.md:806-814](dplm2_paper.md#L806-L814)).
- Filtering: avg **pLDDT > 85** (SwissProt), **len ≤ 512** (50% of batches randomly crop), low-pLDDT ends cropped.
- ⚠️ **Correction worth knowing:** the paper says end-cropping at pLDDT **<50**, but the code crops at **70** ([tokenized_protein.py:56-62](src/byprot/datamodules/dataset/tokenized_protein.py#L56-L62)).
- One example = **`struct_tokens` (LFQ codes) + `aatype_tokens`** — both **discrete**. **The DPLM-2 language model never sees raw 3D coordinates.** Coords are used only (a) upstream to train the LFQ tokenizer and (b) downstream to decode predicted tokens at inference ([tokenized_protein.py:282-320](src/byprot/datamodules/dataset/tokenized_protein.py#L282-L320)).
- Loss mixture: single/folding/inverse/joint each **0.25** (`independent`=0.0) — same `(struct, aa)` pair in every sub-batch, only the noise schedule differs ([dplm2.py:322-392](src/byprot/models/dplm2/dplm2.py#L322-L392)).

### CFP-Gen (function-conditioned generation)
- **General dataset:** **103,939 sequences**, **375 GO + 1,154 IPR**, from Swiss-Prot + InterPro ([cfpgen_paper.md:501-504](../cfpgen/cfpgen_paper.md#L501-L504)).
- **Enzyme dataset:** **139,551 sequences**, **661 EC (4-level)**, from **SwissProt ∩ CARE** ([cfpgen_paper.md:504-505](../cfpgen/cfpgen_paper.md#L504-L505)).
- **Frequency filter: ≥100 sequences per GO/IPR term** (long-tail removal, ProteoGAN-style) ([cfpgen_paper.md:859-862](../cfpgen/cfpgen_paper.md#L859-L862)).
- One example = **sequence + multi-hot GO list + multi-hot IPR list + multi-hot EC list + motif span + (IF path only) backbone N/CA/C/O coords**. Labels are integer-ID lists **padded with `-1`** ([uniprotKB.py:188-212, 282-306](../cfpgen/src/byprot/datamodules/dataset/uniprotKB.py#L188-L306)).
- CFP-Gen's backbone coords come from **PDB + AFDB** — the same two sources as DPLM-2's structures ([cfpgen_paper.md:891-896](../cfpgen/cfpgen_paper.md#L891-L896)).

### Side-by-side

| Dataset | Source DB(s) | # seqs | Attached to each seq | Trains |
|---|---|---|---|---|
| DPLM (UniRef50) | UniRef50 | ~45M | **Sequence only** | DPLM seq pretraining |
| DPLM-2 (pdb_swissprot) | PDB + AFDB-SwissProt | ~200K | **Seq + LFQ struct tokens** (discrete; no coords at LM level) | DPLM-2 co-generation |
| DPLM-2 struct tokenizer | same PDB+SwissProt | ~200K | Continuous backbone coords (input) → LFQ tokens (target) | LFQ tokenizer pretraining |
| CFP-Gen general | Swiss-Prot + InterPro | 103,939 | Seq + GO + IPR + motif (+coords on IF path) | CFP-Gen function-conditioned gen |
| CFP-Gen enzyme | SwissProt ∩ CARE | 139,551 | Seq + GO + IPR + EC + motif (+coords on IF path) | CFP-Gen enzyme design |

**Slide takeaway:** **DPLM / DPLM-2 carry NO function annotations at all** — confirmed by inspecting every dataset class and the on-disk CSV fields. Function is entirely absent from the DPLM pipeline; that is the gap CFP-Gen fills by bolting SwissProt/InterPro/CARE labels onto a DPLM-derived backbone.

---

## Q3 — How the tokenizer is implemented

### DPLM-2 LFQ structure tokenizer — a LEARNED discrete quantizer (trained standalone, then frozen)

Pipeline ([structok_lfq.py:32-104](src/byprot/models/structok/structok_lfq.py#L32-L104); paper §3.3 [dplm2_paper.md:353-415](dplm2_paper.md#L353-L415)):

1. **Encoder** — frozen ESM-IF1 **GVP-Transformer** (`esm.pretrained.esm_if1_gvp4_t16_142M_UR50()`); input = backbone N/CA/C coords ([gvp_encoder.py:53-83](src/byprot/models/structok/modules/gvp_encoder.py#L53-L83)).
2. **pre_quant** MLP → `codebook_embed_dim = 13` ([structok_lfq.py:65-70](src/byprot/models/structok/structok_lfq.py#L65-L70)).
3. **LFQ quantizer** — *lookup-free*: each of 13 dims sign-binarized to **{-1, +1}**; token index = `Σ 2^(k-1) · bit` → implicit codebook **2^13 = 8192** entries. Straight-through estimator for gradients ([lfq.py:133, 293-296, 313-317](src/byprot/models/structok/modules/lfq.py#L293-L296); paper eq [dplm2_paper.md:399](dplm2_paper.md#L399)).
4. **post_quant** + **ESMFold decoder** (IPA / 4 EvoFormer layers, no MSA row attention) reconstructs backbone ([structok_lfq.py:75-87](src/byprot/models/structok/structok_lfq.py#L75-L87)).

Training ([task `StrucTok`](src/byprot/tasks/struct_tokenizer/structok.py#L40), config [structok_lfq_8k_pdb_swissprot_c512.yaml](configs/experiment/structok/structok_lfq_8k_pdb_swissprot_c512.yaml)):
- **Data:** same ~200K PDB+SwissProt structures, crop 512.
- **Loss `StructureVQLoss`** = reconstruction (**FAPE** primary + distogram + sequence-LM + violation + optional TM) + **commitment (w=0.25)** + **entropy (w=0.1, to keep the codebook utilized)** ([loss.py:1702-1768, 1812-1862](src/byprot/models/structok/modules/loss.py#L1702-L1768)).
- 200K steps, AdamW, encoder frozen; **trained standalone then frozen** when consumed by DPLM-2 (its params never enter the DPLM-2 optimizer).

### DPLM-2 main-model vocabulary & embeddings
- **Unified vocab = 33 (AA+special) + 8192 (struct) + 4 (struct special) = 8229** ([dplm2.py:30-34](src/byprot/models/dplm2/dplm2.py#L30-L34)).
- **ONE shared `nn.Embedding`** for AA and struct; AA rows init from pretrained DPLM, struct rows newly init ([dplm2_modeling_esm.py:488](src/byprot/models/dplm2/modules/dplm2_modeling_esm.py#L488)).
- **No separate type embedding** — modality inferred from token-id range.
- **Bit variant (cleaner):** struct tokens are **not** in the shared table. Instead a learned `quant2emb` Linear projects the 13-dim binary code → hidden, and `lm_head_struct` does **binary classification per codebook dim** (vocab = 13×2 = 26). The 8192-entry struct vocab never materializes as embedding/head rows → decouples model size from codebook size ([dplm2_bit.py:123-134, 188-193](src/byprot/models/dplm2/dplm2_bit.py#L123-L134); [dplm2_bit_modeling_esm.py:645-670](src/byprot/models/dplm2/modules/dplm2_bit_modeling_esm.py#L645-L670)).

### CFP-Gen's function "tokenizer" — NOT a learned quantizer, just label-embedding tables
- **`FuncTagEmbedder`** = plain `nn.Embedding(num_classes + 1, hidden)` per GO/IPR/EC (the `+1` is the CFG null token). Per-type embeddings **summed**, then fed to FiLM. No encoder, no codebook, no quantization ([esm_cfpgen.py:210-220, 314-336](../cfpgen/src/byprot/models/lm/esm_cfpgen.py#L210-L220)).
- **Vocabulary = the ontology label sets themselves** (GO=375/780, IPR=1154/4982, EC=661) — fixed by the external ontology.
- ⚠️ The paper text says "one-hot embeddings" ([cfpgen_paper.md:52-55](../cfpgen/cfpgen_paper.md#L52-L55)) but the code is a dense `nn.Embedding` lookup.
- CFP-Gen's **structure** branch uses DPLM-1's GVP + cross-attention adapter — **not** the LFQ tokenizer.

### Contrast table

| Dimension | DPLM-2 LFQ (structure) | CFP-Gen (function) |
|---|---|---|
| What's tokenized | 3D coords (continuous) | GO/IPR/EC IDs (already discrete) |
| Vocab source | **Learned** codebook, 8192 | **External ontology** (375/1154/661) |
| Quantization | Sign binarization, 13-bit | **None** — IDs used as table indices |
| Embedding | One shared table (or `quant2emb` in Bit) | Three separate tables, summed |
| Trained standalone? | **Yes** (own loss, 200K steps) then frozen | No — trains jointly |
| How it enters the model | **Input token** in the sequence | **FiLM condition** (never a token) |
| Generative over it? | **Yes** | **No** |

---

## Q4 — Evaluation experiments, metrics, validation datasets

### DPLM-2 — geometric / structural fidelity vs ground-truth coordinates

All generative tasks route through one evaluator ([evaluator_dplm2.py](src/byprot/utils/protein/evaluator_dplm2.py); paper §4 [dplm2_paper.md:440-702](dplm2_paper.md#L440-L702)).

| Experiment | Metric(s) | Validation set | Reproducible on `main`? |
|---|---|---|---|
| **Unconditional co-gen** (§4.1) | Designability **scTM≥0.5** (code-only threshold), pLDDT>70, diversity (inner-TM, Foldseek clusters @0.5), SS % vs natural PDB | Natural PDB | ✅ runnable; ⚠️ **pdb-TM novelty NOT in code** (only Foldseek clusters) |
| **Forward folding** (§4.2) | TM-score & RMSD to GT (`bb_tmscore_to_gt`, threshold ≥0.8) | **CAMEO 2022 + Multiflow PDB-date** | ✅ runnable (`generate_dplm2.py --task folding`) |
| **Inverse folding** (§4.3) | **AAR** (amino-acid recovery) + scTM (designability via `bb_rmsd≤2.0`) | CATH structures | ✅ runnable (`--task inverse_folding`) |
| **Motif scaffolding** (§4.4) | Success = **motif-RMSD <1Å AND scTM>0.8** | **24-problem Yim/FrameFlow benchmark** | ⚠️ generation works; **motif-RMSD gating NOT in code** (notebook-only) |
| **Representation learning** (§4.5) | SaProt downstream F1/AUC | task-specific | ❌ on separate `representationlearning` branch |

### CFP-Gen — functional-annotation recovery via external NEURAL PREDICTORS

Generated sequences are scored by running them back through predictors ([cfpgen_paper.md:544-664](../cfpgen/cfpgen_paper.md#L544-L664); eval code in [cfpgen/eval/](../cfpgen/eval/)).

| Predictor (evaluates) | Metric block | Validation set |
|---|---|---|
| **DeepGO-SE** (GO recovery) | MRR, MMD, MMD-Gauss, micro/macro F1, AUPR, AUC | GO held-out: 30 seqs/label → **8,309 seqs** |
| **InterProScan** (IPR recovery) | same | IPR held-out: 1/10 downsample → **831 seqs** |
| **CLEAN** (EC recovery) | same | EC held-out: 30 seqs/label → **16,187 seqs** |

- The **SOTA table** ([cfpgen_paper.md:407-441](../cfpgen/cfpgen_paper.md#L407-L441)) has ablation rows showing progressive condition addition (`w/ GO` → `w/ GO+IPR` → `w/ Motif` → `w/ GO+IPR+Motif`), with the full composable combo best on every metric.
- Baselines: **DPLM** (motif+struct), **ProteoGAN** (GO), **ProGen2** (GO+motif), **ZymCTRL** (EC), **ESM3** (IPR+seq+struct).
- **Functional inverse folding** ("inverse function task", §3.4/§4.3): given backbone **and** GO+IPR, maximize both AAR and function recovery. Metrics: AAR, MRR, F_max, scTM, pLDDT. Baselines ProteinMPNN / ESM-IF / LM-DESIGN / DPLM. Headline: +9.45% AAR, +16.10% MRR vs DPLM.
- **Multi-catalytic enzyme design** (§4.4): 6 enzymes (≥3 EC each); success = all ECs recovered by CLEAN **AND TM>0.7 AND pLDDT>0.7**.

### Validation datasets — side by side

| Aspect | DPLM-2 | CFP-Gen |
|---|---|---|
| Reference modality | Ground-truth **3D coordinates** | Functional **annotations** (GO/IPR/EC) |
| Folding benchmark | CAMEO 2022 + PDB-date | n/a |
| Inverse-folding benchmark | CATH (AAR vs native seq) | SwissProt general set (AAR + MRR + F_max vs labels) |
| Scaffolding benchmark | 24-problem Yim/FrameFlow | n/a |
| Function benchmark | n/a | SwissProt-derived GO/IPR/EC held-out sets |
| Novelty reference | Natural PDB | Training-set sequence identity |

### ⚠️ The key caveat for the slide

**CFP-Gen's headline metrics (MRR, MMD, F1, AUPR, AUC, F_max) are properties of (generated protein + predictor), not of the protein alone.** Every number is filtered through DeepGO-SE / InterProScan / CLEAN, and inherits their biases; the "positive control" is the predictor's own score on natural sequences, so the ceiling is predictor self-consistency, **not 1.0** ([cfpgen_paper.md:580-584](../cfpgen/cfpgen_paper.md#L580-L584)).

**DPLM-2's headline metrics (scTM, RMSD, AAR, motif-RMSD) are computed against ground-truth atomic coordinates or sequences.** The only neural component is ESMFold (for self-consistency folding), and even there the reference is a real structure.

**Consequence for our own conditional model:** a DPLM-2-style structural eval alone will **not** demonstrate that a function condition is satisfied. We will have to either (a) run the same neural-predictor gauntlet (DeepGO-SE / InterProScan / CLEAN) and accept its ceiling, or (b) hold out real labeled test sets and score label-recovery directly against ground-truth annotations (which CFP-Gen does *not* do — it scores predictor outputs vs prompts, not vs real assays). For Pfam specifically, HMMER (already in [scripts/evaluate_pfam_pyhmmer.py](scripts/evaluate_pfam_pyhmmer.py)) is the ground-truth-style evaluator, unlike the neural predictors above.
