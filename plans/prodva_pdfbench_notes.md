# ProDVA And PDFBench Notes For Conditional DPLM

## Why This Matters

ProDVA and PDFBench are directly relevant to the post-Pfam phase of conditional DPLM.

ProDVA provides a recent implementation pattern for text/function-guided protein design using dynamic protein vocabulary and retrieval-backed fragment candidates. PDFBench provides a recent benchmark taxonomy and evaluation harness for de novo protein design from function.

For our immediate Pfam-first plan, these do not replace HMMER/Pfam evaluation. They should shape the next phase: function keywords, InterPro/GO terms, and natural-language descriptions.

## ProDVA Summary

Repository:

```text
https://github.com/sornkL/ProDVa
```

Paper:

```text
Protein Design with Dynamic Protein Vocabulary
```

Hugging Face collection:

```text
https://huggingface.co/collections/nwliu/prodva
```

Available HF datasets:

- `nwliu/CAMEO`
- `nwliu/Molinst-SwissProtCLAP`

Available HF models:

- `nwliu/ProDVa-CAMEO`
- `nwliu/ProDVa-Molinst-SwissProtCLAP`

Each dataset includes:

- `training.json`
- `validation.json`
- `test.json`
- `phrases.json`
- `index/index.faiss`
- `index/index.pkl`

## ProDVA Implementation Pattern

ProDVA uses three model components:

- text encoder
- protein language model backbone
- phrase encoder

The central mechanism is dynamic vocabulary augmentation.

Static vocabulary:

```text
normal LM tokens
```

Dynamic vocabulary:

```text
retrieved/generated protein fragments encoded by a phrase encoder
```

During generation, the model builds a combined vocabulary:

```text
[static token embeddings] + [dynamic phrase embeddings]
```

The generated sequence may choose either normal tokens or whole protein-fragment phrase tokens.

The ProDVA forward pass concatenates text embeddings before sequence embeddings:

```text
[text embeddings] + [sequence/dynamic-vocabulary embeddings]
```

This is effectively a text-prefix conditioning strategy rather than explicit cross-attention.

## ProDVA Features Worth Borrowing

### Retrieval-Backed Candidates

ProDVA retrieves supporting documents using FAISS and a Hugging Face embedding model. It maps retrieved descriptions to known protein sequences, then samples protein fragments from those sequences.

For DPLM, this suggests a later retrieval-conditioned mode:

```text
function/text query -> retrieve related Pfam/UniProt examples -> extract domains/fragments -> condition DPLM
```

This can complement Pfam IDs without replacing them.

### Protein Fragment Mapping

ProDVA expects a mapping file like:

```json
{
  "sequence": "MKNCEY...",
  "phrases": [
    {
      "phrase": "KLKYCFTCKM...",
      "type": "DOMAIN",
      "name": "Palmitoyltrfase_DHHC",
      "description": "Palmitoyltransferase, DHHC domain"
    }
  ]
}
```

This maps very naturally to our Pfam/InterPro preprocessing:

```text
Pfam domain hits -> phrase/domain spans -> type/name/description
```

For our Pfam-first implementation, we should preserve enough metadata to later produce this fragment mapping.

### Auxiliary Losses

ProDVA includes:

- type classification loss for phrase type
- description alignment loss using InfoNCE between description embeddings and phrase embeddings

For conditional DPLM, analogous later losses are:

```text
L = L_diffusion + lambda_family * L_family_cls
```

Later text/function phase:

```text
L = L_diffusion + lambda_text * L_text_sequence_contrastive + lambda_domain * L_domain_type
```

### Fine-Tuning Modes

ProDVA supports:

- frozen backbone
- LoRA
- full fine-tuning

This matches our planned progression for DPLM and reinforces keeping these modes explicit in config.

## How This Changes Our DPLM Plan

### Immediate Pfam Phase

No major change.

Keep the first target as:

```text
Pfam family ID -> prefix-conditioned DPLM -> HMMER/Pfam evaluation
```

But preserve metadata needed for later ProDVA-style fragment mapping:

- family ID
- family accession
- family name
- description
- clan
- source sequence ID
- domain boundaries if available
- InterPro IDs if joined later

### Near-Term Architecture Change

Add a generic conditioning abstraction early:

```python
condition = condition_encoder(batch)
model(tokens, condition=condition)
```

Initial condition types:

- `family_prefix`
- `family_embedding`

Planned condition types:

- `text_prefix`
- `retrieved_fragment_prefix`
- `dynamic_fragment_candidates`
- `cross_attention_memory`

### Later Text/Function Phase

Use ProDVA as a reference for:

- retrieval over CAMEO or SwissProtCLAP instructions
- retrieved protein examples
- fragment/domain candidates
- text-prefix conditioning
- fragment-description contrastive alignment

Do not copy ProDVA’s causal LM generation directly into DPLM. DPLM is diffusion-based, so dynamic vocabulary and phrase tokens need a DPLM-specific design.

## Dynamic Vocabulary For DPLM

ProDVA’s dynamic vocabulary is natural for autoregressive decoding. For DPLM, it is less direct because DPLM denoises fixed-length token grids.

Possible DPLM adaptations:

### Option 1: Retrieved Fragment Prefixes

Encode retrieved fragments and use them as conditioning prefix tokens.

Pros:

- minimal change
- compatible with diffusion
- good first adaptation

Cons:

- does not allow direct insertion of whole fragment tokens

### Option 2: Fragment-Constrained Infilling

Use retrieved Pfam/InterPro fragments as motifs or span constraints during DPLM scaffolding.

Pros:

- very compatible with existing DPLM motif scaffolding
- biologically interpretable

Cons:

- needs span placement or motif selection logic

### Option 3: True Dynamic Fragment Tokens

Add phrase/domain tokens to the DPLM vocabulary for denoising.

Pros:

- closest to ProDVA

Cons:

- invasive tokenizer/model changes
- hard to reconcile with fixed amino-acid sequence output
- not recommended until prefix/retrieval approaches are understood

Recommended path:

```text
retrieved fragment prefixes -> fragment-constrained infilling -> true dynamic tokens only if needed
```

## PDFBench Summary

Repository:

```text
https://github.com/PDFBench/PDFBench
```

Paper:

```text
PDFBench: A Benchmark for De novo Protein Design from Function
```

PDFBench evaluates generated design results rather than controlling model inference directly.

Supported task formats:

- description-guided generation
- keyword-guided generation with GO and InterPro entries
- keyword-description-guided support planned

PDFBench input format for description-guided tasks includes:

```json
{
  "instruction": "natural language function description",
  "reference": "ground truth sequence",
  "response#1": "designed sequence",
  "response#2": "designed sequence",
  "response#3": "designed sequence"
}
```

Keyword-guided inputs include GO and InterPro annotations plus generated responses.

## PDFBench Metric Groups

### Plausibility

- perplexity using protein LMs such as ProGen2, ProtGPT2, RITA, ProteinGLM
- repetitiveness using Repeat and RepN

### Foldability

- ESMFold-based foldability

### Language And Function Alignment

- ProTrek Score
- EvoLlama Score
- GO Recovery using DeepGO-SE
- IPR Recovery using InterProScan
- Retrieval Accuracy

### Novelty

- sequence novelty using MMseqs2
- structure novelty using Foldseek

### Diversity

- sequence diversity
- structure diversity

### Similarity

- ground-truth identity
- ground-truth TM-score
- ESMScore

## How To Use PDFBench In Our Roadmap

For Pfam-first, keep HMMER/Pfam target-family hit rate as the primary metric.

For text/function-conditioned DPLM, export generated outputs to PDFBench-compatible JSON and run PDFBench metrics.

Recommended later benchmark datasets:

- `nwliu/CAMEO` for keyword/function-style generation
- `nwliu/Molinst-SwissProtCLAP` for text-description generation

Recommended output adapter:

```text
scripts/export_pdfbench_results.py
```

This script should convert DPLM generations into:

```json
{
  "instruction": "...",
  "reference": "...",
  "response#1": "...",
  "response#2": "...",
  "response#3": "..."
}
```

## Practical Caveats

PDFBench dependencies are heavy:

- InterProScan requires Java 11
- MMseqs2 and Foldseek databases are large
- UniProt MMseqs database can require hundreds of GB
- Foldseek AlphaFoldDB/SwissProt database can require tens of GB
- DeepGO-SE uses a separate environment

Therefore, PDFBench should be introduced after the Pfam/HMMER evaluation pipeline is stable.

## Recommended Integration Order

1. Finish Pfam-first DPLM and HMMER evaluation.
2. Preserve Pfam/InterPro metadata for fragment mapping.
3. Add generic condition encoder API.
4. Add external classifier reranking for Pfam.
5. Add ProDVA-inspired retrieval-conditioned prefix mode.
6. Add DPLM export to PDFBench JSON.
7. Benchmark on CAMEO and Molinst-SwissProtCLAP using PDFBench.
8. Only then consider true dynamic vocabulary or cross-attention if metrics justify the complexity.
