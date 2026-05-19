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
