# Elucidating the Design Space of Multimodal Protein Language Models

**Cheng-Yen Hsieh**\*, **Xinyou Wang**\*, **Daiheng Zhang**†, **Dongyu Xue**, **Fei Ye**, **Shujian Huang**, **Zaixiang Zheng**‡, **Quanquan Gu**‡

*School of Computer Science, Nanjing University; Dept. of ECE, Rutgers University; ByteDance Seed*

---

## Abstract

Multimodal protein language models (PLMs) integrate sequence and token-based structural information, serving as a powerful foundation for protein modeling, generation, and design. However, the reliance on tokenizing 3D structures into discrete tokens causes substantial loss of fidelity about fine-grained structural details and correlations. In this paper, we systematically elucidate the design space of multimodal PLMs to overcome their limitations. We identify tokenization loss and inaccurate structure token predictions by the PLMs as major bottlenecks. To address these, our proposed design space covers improved generative modeling, structure-aware architectures and representation learning, and data exploration. Our advancements approach finer-grained supervision, demonstrating that token-based multimodal PLMs can achieve robust structural modeling. The effective design methods dramatically improve the structure generation diversity, and notably, folding abilities of our 650M model by reducing the RMSD from 5.52 to 2.36 on PDB testset, even outperforming 3B baselines and on par with the specialized folding models.

Project page and code: https://bytedance.github.io/dplm/dplm-2.1

---

## 1. Introduction

Proteins are the molecular machinery of life, encoded by amino acid sequences that fold into intricate three-dimensional structures to perform their biological functions. Existing approaches often treat sequence and structure as separate modalities, relying on disjoint models (e.g., ESM for sequences and AlphaFold for structures) that fail to capture the interplay between them. This limitation hinders the ability to jointly model, understand, and generate proteins in a unified framework, which is essential for tasks like protein design, folding, and functional annotation.

Recent efforts in multimodal protein language models such as ESM3 and DPLM-2 have demonstrated the potential of integrating sequence and structure within a single language model as a unified generative framework. In particular, DPLM-2 is a multimodal extension of diffusion protein language model (DPLM) with discrete diffusion framework, which naturally aligns with the discrete nature of protein sequences, enabling it to benefit from large-scale pre-training on sequence databases—a crucial factor for accurate structure prediction. Beyond sequence modeling, DPLM-2 extends its capabilities by tokenizing 3D coordinates into discrete tokens, thereby enabling direct language modeling of both modalities, hence comprehension and generation of them.

Despite the success, the structure tokenization process introduces structural information loss—obscuring fine-grained geometric relationships critical for accurate protein modeling. Consequently, even such state-of-the-art multimodal PLMs struggle to generate biologically plausible structures for complex tasks like structure folding or motif scaffolding, where precise structural correlations are crucial. The loss of nuanced variations due to tokenization also degrades the structure diversity in unconditional generation.

In this paper, we systematically explore the key pitfalls and the design space of token-based multimodal protein language models to bridge their limitations on structural modeling. In addition to the structural information loss from tokenization, we identify the primary challenges as the inaccuracies in language model's capability in structure (tokens) prediction, which could not be simply resolved by improving the reconstruction accuracy. We find that index-based structure tokens as supervised labels ignore correlations between semantically similar structure tokens, making the learning process particularly challenging.

In response, we build upon DPLM-2 to advance the design space spanning improved generative modeling, structure-aware architectures, representation learning, and data exploration. We achieve a finer-grained supervision through bitwise discrete modeling and a hybrid approach for data-space modeling. While these methods effectively guide the design of supervision targets, language model-based architectures still lack geometric inductive biases and structural learning objective. To mitigate this, we introduce geometry-aware modules and representation alignment techniques to refine the modeling of higher-order relationship between residues, which is essential as evidenced in protein folding. As existing multimodal PLMs are often trained solely on single-chain proteins, we explore the effects of multi-chain proteins (multimer), which introduces richer structural interactions crucial for robust modeling.

**Main contributions:**

- We conduct a comprehensive study revealing key pitfalls in structure token-based multimodal protein language models, and systematically elucidate their design space for robust structural modeling.
- Utilizing improved approaches such as bit-wise discrete modeling offers finer-grained supervision, significantly improving structure generative capability.
- Introducing representation-level learning and architectural innovations infuses geometric inductive biases and effectively refines generation diversity.
- We find that multimer and monomer modeling are deeply interconnected and leveraging multimer data advances the structural modeling for both single and multi-chain proteins.
- Our design methods allow multimodal PLMs to achieve robust structural understanding, improving the folding RMSD from 5.52 to 2.36 on the PDB date dataset, outperforming 3B folding baselines with only 650M parameters.

---

## 2. Revisiting Multimodal Protein Language Models: Capabilities & Constraints

The aim of generative protein modeling is to estimate the underlying distribution prot ~ q(prot) of all associated modalities of the protein data by learning a probabilistic model p_θ(prot). Here prot = (r₁, r₂, …, r_L) denotes a protein with L residues, where each residue rᵢ = (sᵢ, xᵢ) is represented by two major modalities: sᵢ ∈ {0,1}^|S| is a categorical variable for its amino acid type in S = {1,...,20}, and xᵢ ∈ ℝ^(N_atoms × 3) is the real-value Cartesian coordinates of its residue atoms (backbone atoms: [N, Cα, C, O] with N_atoms=4).

Multimodal generative approaches that jointly model structure and sequence can be mainly categorized into two paradigms: structure-centered diffusion/flow-based models, or sequence-centered language models. The latter is our main focus in this paper.

### 2.1 Multimodal Generative Learning for Proteins with Language Models and Structure Tokenization

Language models (LMs), parameterized by large-scale Transformers, have become the *de facto* choice dominating different domains. Among them, protein LMs have been serving as one of the AI foundations for protein sequence learning and generation.

**DPLM.** Diffusion protein language model (DPLM) shows excellent performance in both generation and representation learning of protein sequences, and even structures thanks to its multimodal extension DPLM-2. The DPLM family is grounded in the *absorbing* discrete diffusion framework, characterized by a forward and backward Markov process. The forward process defines a Markov process governed by a transition kernel that gradually perturbs the data into a stationary distribution. The learned *backward* process reversely denoises toward the data distribution. The learning objective can be simplified into weighted cross-entropies, resembling masked language modeling at arbitrary noise levels:

```
J_t = E_{q(x)} [ λ^(t) Σ_{1≤i≤L} bᵢ(t) · log p_θ(xᵢ | x^(t)) ]
```

For inference, DPLM generates amino acid sequences by a reverse iterative denoising process in a *mask-predict* manner, starting from an all-mask sequence and iterating towards a synthesized sequence.

**DPLM-2: A Multimodal Extension of DPLM.** DPLM-2 extends DPLM by introducing a token-based latent representation for protein structure via a two-stage approach:
1. A structure tokenizer converts 3D backbone coordinates **x** ∈ ℝ^(L × N_backb × 3) into a discrete structure token sequence **z** = (z₁, z₂, …, z_L) ∈ {0…|Z|}^L, where each token zᵢ represents a local structural element.
2. Given tokenized structure, DPLM-2 performs joint language modeling of structure tokens **z** with the corresponding amino acid sequence **s**.

**Structure Tokenization.** DPLM-2 employs an LFQ-based structure tokenizer, summarized as:

```
x → [encoder] → z_cont → [quantize] → z_quant ↔ z_index → [decoder] → x̃
```

1. A structure encoder encodes backbone coordinates into continuous structure tokens **z_cont** ∈ ℝ^(L×D).
2. An LFQ module quantizes **z_cont** dimension-wise into binary discrete tokens **z_quant** ∈ {−1,+1}^(L×D), converted to decimal index tokens **z_index** ∈ {0…|Z|}^L.
3. A structure decoder reconstructs 3D coordinates from the discrete tokens.

### 2.2 The Pitfalls of Modeling over Tokenized Structures

**Observation 1 (O1): Structure tokenization results in information loss.** Vector quantization converts continuous structure tokens (**z_cont**) into discrete tokens (**z_quant**), discarding residual information (**z_cont** − **z_quant**). As shown in Table 1, quantization significantly amplifies reconstruction errors (RMSD: 1.31 → 1.98; TMscore: 0.97 → 0.93). This suggests that learning to recover the lost residuals could enhance structure prediction accuracy.

**Table 1: Effects of feature quantization on structure tokenizer reconstruction.**

| Latent Feature | Struct Token Type | RMSD ↓ | TMscore ↑ |
|---|---|---|---|
| z_cont | (pre-quantized) continuous | **1.3127** | **0.9733** |
| z_index ↔ z_quant | (quantized) discrete | 1.9806 | 0.9385 |

**Observation 2 (O2): High reconstruction accuracy does not guarantee better structure generative performance.** As shown in Table 2, the ESM3 tokenizer achieves superior reconstruction accuracy (RMSD: 0.72, TMscore: 0.99) but the DPLM-2 tokenizer exhibits stronger protein folding performance. This suggests that greater emphasis should be placed on improving structure-aware generative modeling and architectural design.

**Table 2: Tokenizer reconstruction vs. language model generation (CAMEO 2022).**

| Tokenizer | rRMSD ↓ | rTMscore ↑ | RMSD ↓ | TMscore ↑ |
|---|---|---|---|---|
| DPLM-2 | 1.9806 | 0.9385 | **7.7025** | **0.7936** |
| ESM3 | **0.7248** | **0.9912** | 8.4424 | 0.7924 |

**Observation 3 (O3): Index-based structure tokens are predicted miserably wrong.** Direct index prediction is highly inaccurate (0.0864 accuracy on CAMEO). However, bit-based prediction accuracy reaches 0.7720, which aligns more closely with structural evaluation metrics. This suggests the model struggles to recover exact indices but effectively captures structural patterns at the bit level.

**Table 3: Language model structure token prediction accuracy.**

| Model | Testset | Index Acc ↑ | Bit Acc ↑ | RMSD ↓ | TMscore ↑ |
|---|---|---|---|---|---|
| DPLM-2 index-based | CAMEO 2022 | 0.0864 | 0.7720 | 7.7025 | 0.7936 |
| DPLM-2 index-based | PDB date split | 0.1188 | 0.7932 | 5.3071 | 0.8306 |
| DPLM-2 Bit-based | CAMEO 2022 | **0.1258** | **0.7958** | **6.4028** | **0.8380** |
| DPLM-2 Bit-based | PDB date split | **0.2641** | **0.8648** | **3.2213** | **0.9043** |

**Concluding Remarks.** We identify the primary bottlenecks as tokenization loss (O1) and ineffective structure modeling in sequence-based architectures (O2 & O3).

---

## 3. Improved Structure Prediction

### 3.1 Recovering Tokenization Loss with ResDiff

The vector quantizer introduces lossy compression, eliminating fine-grained structural details. To address this, we introduce **ResDiff**, a lightweight diffusion module to predict the residual information **r** = **z_cont** − **z_quant**, conditioned on the hidden states of the language model **h** and discrete structure tokens **z**:

```
L_φ = E_{q(r), ε~N(0,I), t} [ ||ε − ε_φ(r_t, t, h, z_quant)||² ]
```

The generation process first generates discrete structure tokens **z_index**, then feeds them and hidden states into ResDiff to generate residuals **r**, which are added to recover continuous tokens **z_cont** = **z_quant** + **r**, which are decoded to atomic structure.

**Results.** As shown in Table 4, ResDiff improves structural prediction accuracy by refining fine-grained structural variations and is model-agnostic, showing consistent improvements across DPLM-2 variants.

### 3.2 Bit-based Language Modeling

To bridge discrete and continuous structure tokens, we perform language modeling at the bit level rather than index level—reducing K-way classification (where K = 2^dims) to K binary classifications. The training objective becomes:

```
J_t^bit = E_{q(x,z)} [ λ^(t) Σᵢ bᵢ(t) ( log p_θ(sᵢ|·) + Σₖ log p_θ(z_{i,quant}^[k]|·) ) ]
```

**Results.** Bit-level supervised DPLM-2 achieves significant accuracy improvements and substantially reduces structural deviation (improved RMSD and TM-score), particularly on the PDB date test set.

### 3.3 Hybrid Generative Approach: Direct Data-space Modeling

The combination of the structure encoder, language model, and decoder functions as a denoising model for 3D structure:

```
x̄_denoised = decoder ∘ PLM ∘ encoder(x_t)
```

We incorporate this structure denoiser into a flow-based sampler with Euler integrator, treating it as a denoising process on data-space structure generation. Each Euler step interpolates:

```
x_s ← ((s−t)/(1−t)) · x̄_denoised + ((1−s)/(1−t)) · x_t
```

We fine-tune this model with flow matching (FM).

**Results.** Data-space sampling with flow matching enhances structure generation on the folding task, matching or surpassing ESMFold particularly on the PDB date split.

**Table 4: Evaluation of improved approaches for structure prediction.**

| Model | CAMEO RMSD ↓ | CAMEO TMscore ↑ | PDB RMSD ↓ | PDB TMscore ↑ |
|---|---|---|---|---|
| ESMFold (3B) | 3.9900 | 0.8500 | 2.8400 | 0.9300 |
| MultiFlow | 17.8400 | 0.5000 | 15.6400 | 0.5300 |
| ESM3 (1.4B) | 6.3300 | 0.8400 | 4.9003 | 0.8653 |
| DPLM-2 (650M) | 7.7025 | 0.7936 | 5.3071 | 0.8306 |
| + ResDiff | 7.2881 | 0.8087 | 5.1072 | 0.8430 |
| DPLM-2 (Bit-based) | 6.4028 | 0.8380 | 3.2213 | 0.9043 |
| + ResDiff | 6.1781 | 0.8428 | 3.0168 | 0.9076 |
| + FM | **6.1825** | 0.8414 | 2.8697 | 0.9099 |
| + FM + ResDiff | 6.0765 | 0.8456 | 2.7884 | 0.9146 |
| + FM + ResDiff + SFT | 5.8472 | 0.8442 | **2.3698** | **0.9270** |
| DPLM-2 (3B) + SFT | 5.9832 | 0.8443 | 3.1502 | 0.9012 |

---

## 4. Improved Structure-aware Architecture and Representation Learning

While bit-based modeling offers effective supervision targets, sequence-based models still lack geometric inductive biases and structural learning objectives.

### 4.1 GeoDPLM: Geometry-aware Model Architecture

Inspired by PairFormer in AlphaFold3, we introduce **GeoDPLM** with geometric modules operating on compact 2D pair representations to capture pairwise spatial dependencies of residues. We use a structure attention module to refine structure representations and pair representations through transition and triangle operations, followed by Seqstruct attention to blend pair representations with sequence and structural representations.

**Component-wise analysis** (Table 5) shows that introducing 2D pair representations reduces RMSD from 7.703 to 7.244 and increases TMscore to 0.8339. Transition layers for structure representations prove critical. Triangle update and attention operations do not yield notable benefits.

**Training efficiency.** Triangle operations are the most computationally intensive (triangle update: 3.9× slower; triangle attention: 6.8× slower). Transition layers for structure representations significantly boost structure modeling with minimal impact on training speed.

**Table 5: Ablation of geometry-aware modules (selected rows).**

| Method | SFT | PDB RMSD ↓ | PDB TM ↑ | CAMEO RMSD ↓ | CAMEO TM ↑ |
|---|---|---|---|---|---|
| DPLM-2 | ✗ | 5.521 | 0.8287 | 7.703 | 0.7936 |
| GeoDPLM (Base) | ✗ | 4.823 | 0.8521 | 7.244 | 0.8128 |
| GeoDPLM (ST) | ✗ | **3.883** | **0.8857** | **6.550** | **0.8339** |
| DPLM-2 | ✓ | 3.347 | 0.9008 | 6.612 | 0.8233 |
| GeoDPLM (ST) | ✓ | **3.021** | **0.9062** | 6.288 | 0.8393 |

### 4.2 Representation Alignment to Folding Model (REPA)

We adopt REPA by aligning the representations of the protein language model with representations from a specialized folding model (ESMFold). This enables smooth, informative, and high-dimensional learning, preserving finer structural nuances.

**Setup.** We precompute structure and pair representations from the ESMFold folding trunk (3 recycling iterations). We apply a learnable weight ensemble across all layers via softmax, and use a 3-layer MLP to project representations before alignment via negative cosine similarity:

```
L_REPA(θ,φ) = −(1/L) Σᵢ sim(yᵢ, hᵢ)
```

**Table 6: Representation alignment improves structure prediction.**

| Method | PDB RMSD ↓ | PDB TM ↑ | CAMEO RMSD ↓ | CAMEO TM ↑ |
|---|---|---|---|---|
| DPLM-2 | 5.521 | 0.8287 | 7.703 | 0.7936 |
| + REPA | 4.919 | 0.8508 | 7.344 | 0.8046 |
| GeoDPLM | 4.823 | 0.8521 | 7.244 | 0.8128 |
| + REPA | **4.340** | **0.8671** | **7.058** | **0.8217** |

REPA is compatible with both language model-based architectures and those incorporating geometric designs. Geometric designs and REPA significantly improve the low generation diversity of multimodal PLMs.

---

## 5. On the Orthogonality of Design Methods

**Table 7: Analysis of orthogonality.**

| Model | PDB RMSD ↓ | PDB TM ↑ | CAMEO RMSD ↓ | CAMEO TM ↑ | Diversity ↑ |
|---|---|---|---|---|---|
| DPLM-2 (650M) | 5.307 | .8306 | 7.703 | .7936 | 0.700 |
| Bit | 3.221 | .9043 | 6.403 | .8380 | 0.825 |
| Bit + FM | 2.870 | .9099 | 6.183 | .8418 | 0.525 |
| Bit + FM + ResDiff | 2.788 | .9146 | 6.077 | .8456 | 0.525 |
| + SFT | 2.370 | .9270 | 5.847 | .8442 | — |
| **Geo + Bit** | **2.551** | **.9254** | **5.955** | **.8520** | **0.900** |
| Geo + Bit + FM | 2.443 | .9261 | 6.172 | .8404 | 0.575 |
| Geo + Bit + REPA | 2.507 | .9264 | 6.192 | .8412 | 0.875 |
| + SFT | 2.404 | .9322 | 5.754 | .8424 | — |
| All* | 2.379 | .9297 | 6.200 | .8398 | — |

**Key findings:**

- **SFT** improves structure folding but sacrifices multimodal co-generation ability.
- **Recommended setting: Geo + Bit-based modeling.** Achieves comparable folding results to SFT fine-tuned models while improving unconditional generation quality and diversity, with better training efficiency.
- **REPA and Bit-based modeling** both enable smooth, high-dimensional learning signals; their effects are non-orthogonal, so combining them does not bring further improvement.
- **Hybrid modeling (FM)** accelerates sampling 10× but can reduce generation diversity due to its ODE nature.
- **ResDiff** provides fine-grained local structure improvement rather than large metric gains.

---

## 6. Structure Data: Multimer Exploration

Most existing protein language models are trained solely on single-chain proteins. We extend to multimers, which present diverse structural arrangements essential for a more general multimodal model.

**Table 8: PDB-Multimer dataset statistics.**

| Dataset | # Proteins (Train/Val) | # Chains | Protein Length | Chain Length |
|---|---|---|---|---|
| PDB-Multimer | 11614/291 | 2.88 ± 1.66 | 661.57 ± 416.37 | 229.39 ± 167.00 |

**Scaling monomer data improves reconstruction for multimer.** Increasing monomer data from 200K to 2M leads to substantial improvement on the PDB-Multimer validation set, indicating that monomer modeling closely relates to and benefits multimer modeling.

**Chain linker and position offset.** Applying position index offsets (product of chain index and a predefined value) in the relative position embedding improves reconstruction. Glycine (G) linkers of varying lengths improve folding metrics, with optimal performance at length 25.

**Table 9: Effects of monomer data scaling on multimer reconstruction.**

| Training Data | Size | Multimer RMSD ↓ | Multimer TM ↑ | CAMEO RMSD ↓ | CAMEO TM ↑ |
|---|---|---|---|---|---|
| PDB & SwissProt | 200K | 9.973 | 0.694 | 2.589 | 0.930 |
| + AFDB_Rep | +1.2M | **6.873** | **0.784** | **2.245** | **0.938** |

**Finetuning with multimer and monomer data.** Incorporating PDB-Multimer improves structure folding for both multimer and monomer, highlighting that multimer data is essential for robust structural modeling.

**Table 10: Effects of fine-tuning with multimer and monomer data.**

| PDB-Multimer | SwissProt | SFT | Multimer RMSD ↓ | Multimer TM ↑ | CAMEO RMSD ↓ | CAMEO TM ↑ |
|---|---|---|---|---|---|---|
| | ✓ | | 17.966 | 0.771 | 7.703 | 0.793 |
| | ✓ | ✓ | 19.615 | **0.799** | 6.612 | 0.823 |
| ✓ | | ✓ | **16.146** | 0.775 | 10.989 | 0.686 |
| ✓ | ✓ | ✓ | 16.674 | 0.798 | **6.410** | **0.831** |

---

## 7. Conclusions

We identify limitations in structural modeling for multimodal protein language models and propose an effective design space to bridge the gap. Tokenization quantization loss can be mitigated with bit-label supervision and flow-matching, significantly improving structure prediction accuracy. Geometric inductive biases through architectural design and representation learning refine generation diversity. Including multimers ensures broader 3D structural understanding. Our results show these designs allow multimodal models to achieve on-par or superior folding accuracy compared to larger, specialized folding models.

---

## Appendix

### A. Taxonomy of the Design Space

| Design Space | Design Choice | Traditional Choice | Motivation | Findings |
|---|---|---|---|---|
| Improved generative modeling | Bit-based modeling | Index-based modeling | Small bit-level changes can result in drastically different indices, making index-based learning challenging. Direct index prediction is highly inaccurate (8.64% on CAMEO). | Bit-level supervision improves accuracy at both index and bit levels, reducing structural deviation from ground truth. |
| Improved generative modeling | ResDiff | Decode tokens without residuals | Quantizing continuous tokens amplifies reconstruction errors; recovering lost residuals might enhance accuracy. | ResDiff performs fine-grained local structure refinements with consistent improvements across DPLM-2 variants. |
| Improved generative modeling | Hybrid data-space sampling | Predict discrete tokens | Discrete tokenization sacrifices atomic-level details. | Hybrid approach with flow matching improves folding and speeds up sampling by 10× (fewer steps). |
| Structure-aware approaches | Geometry-aware architecture | Sequence-based transformer | Protein structures require capturing higher-order residue relationships. | Geometric modules enhance folding and diversity; triangle layers provide little benefit while greatly slowing training. |
| Structure-aware approaches | Representation alignment to folding model | Discrete token supervision only | Discrete supervision may be less effective for fine atom-level details; alignment enables smooth, high-dimensional learning. | REPA boosts generation diversity and is compatible with both language model and geometric architectures. |
| Data | Multimer data exploration | Monomer data only | Multimer data presents diverse structural arrangements missing from monomer-only training. | Multimer and monomer modeling are deeply interconnected; multimer data advances structural modeling for both. |

### B. Discussions and Limitations

1. **Discrete representation bottleneck.** Our method still relies on discrete structure representations, which inherently introduce information loss. Future research could explore hybrid approaches combining discrete and continuous representations. Atomic-level precision remains a challenge, as current representations primarily operate at residue/backbone levels.

2. **Lack of physical constraints.** The framework lacks explicit physical constraints and energy-based priors crucial for generating physically plausible structures. Incorporating differentiable physics-based priors could improve structural realism.

3. **Scalability.** Analysis is conducted on relatively small models (up to 3B parameters); scalability of these design choices remains uncertain.

4. **Limited multimer data.** The sparsity of curated multimer datasets poses challenges for generalization. Future efforts should prioritize data augmentation and larger-scale multimodal datasets.

### C. Implementation Details

**ResDiff.** A lightweight diffusion module of 6 MLP layers (hidden size 1024) predicts residuals conditioned on discrete structure tokens and LM hidden states. The condition is computed as:

```
c = z_quant W_quant + Σᵢ aᵢ hᵢ
```

where aᵢ = softmax(wᵢ). Training: 100K steps, batch size 240, LR peak 1×10⁻⁴ with linear decay to 1×10⁻⁵.

**Bit-level Supervision.** The LM uses K binary classifiers to predict each bit of the K-bit structure token in parallel. Input projection: W_input ∈ ℝ^(K×H); output projection: W_output ∈ ℝ^(H×2K). Training: 300K steps, peak LR 1×10⁻⁴ with linear decay.

**Geometric Designs.** Pair representations initialized by cross-concatenating input hidden representations (L×D) into a 2D map (L×L×D) via 3-layer MLP. Feature dimensions: 1280 for structure representations, 128 for pair representations. PairFormer encoder blocks used for structure attention.

**Representation Alignment.** ESMFold selected as target encoder. Structure and pair representations precomputed with 3 recycling iterations. Multi-layer ensemble via learnable softmax weights avoids manual layer selection.

**Folding SFT.** Structure tokens are masked while sequence tokens remain unmasked. Fine-tuned on the pretrained model to further enhance folding performance.

### D. Additional Empirical Analysis

**Inverse Folding (CAMEO 2022).**

| Model | AAR ↑ | TMscore ↑ |
|---|---|---|
| DPLM-2 650M | 0.4962 | 0.8816 |
| DPLM-2 3B | 0.5236 | 0.8900 |
| DPLM-2 Bitwise | 0.5586 | 0.8907 |
| Geo + Bitwise | 0.5665 | 0.8886 |
| Geo + Bitwise + REPA | **0.5681** | **0.8909** |

**Structure-aware Representation Learning.**

| Model | HumanPPI Acc (%) | DeepLoc Acc (%) |
|---|---|---|
| SaProt | 86.41 | **85.57** |
| DPLM-2 | 84.44 | 82.98 |
| DPLM-2 bitwise | **88.89** | 83.39 |

**Training and Sampling Efficiency (Bit-based vs. Hybrid).**

| Approach | # Sampling Steps | Training Time (300K steps) |
|---|---|---|
| w/o FM | 100 | 46 hrs |
| w/ FM | 10 | 81 hrs |

---

*\* Equal contribution. † Core contributor. ‡ Project lead.*
