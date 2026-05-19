# Introduction {#sec:intro}

Proteins are macromolecules that execute crucial roles in every living
organism. They are characterized by their amino acid sequences and
three-dimensional structure, where the sequence determines the
structure, which in turn governs the protein's function. Generative
modeling for proteins has made significant strides in recent years.
Among them, diffusion models [@ho2020ddpm; @song2020sde] exhibit great
success in protein structure-based generative
modeling [@watson2023RFdiffusion; @yim2023framediff]. Meanwhile,
large-scale protein language models [@rives2019esm; @lin2022esmfold],
trained on evolutionary-scale sequence database, have become one of the
most important cornerstones in sequence-based foundation models for
protein sequence representation learning and generation. Remarkably,
DPLM [@wang2024diffusion], a discrete diffusion [@austin2021structured]
based protein language models, has exhibited the state-of-the-art
performance in both sequence generation and understanding, addressing a
wide range of sequence-oriented applications.

Many protein engineering applications, *e.g*..,
motif-scaffolding [@watson2023RFdiffusion; @yim2024improved] and
antibody
design [@jin2021iterative; @kong2022conditional; @zhou2024abdpo],
require jointly determine both structure and sequence. However, the
aforementioned approaches mostly employ generative models for one
modality (either sequence or structure) and resort to separate
models [@jumper2021AF2; @dauparas2022proteinmpnn] for the other. This
highlights the pressing need for multimodal protein generative models
that can integrate both sequence and structure, enabling a more
comprehensive understanding of protein behaviors and functions. This,
therefore, raises the following question:

::: center
*Can we build a multimodal protein foundation model to simultaneously\
model, understand, and generate both sequences and structures?*
:::

To pursue this goal, Multiflow [@campbell2024generative] is a recent
effort for structure-sequence co-generation that incorporates sequences
into structure-based generative models using multimodal flow matching.
Despite its impressive structure generation capability, Multiflow
exhibits suboptimal performance in co-generating structurally-compatible
sequences and consequently resorts to instance-level knowledge
distillation from ProteinMPNN [@dauparas2022proteinmpnn]. Furthermore,
it completely falls short in protein folding for given sequences,
showing Mulitflow's inadequacy in sequence understanding. We argue that
this bottleneck arises from the absence (co-)evolutionary inductive bias
derived from massive pre-training from sequence database, as prior
studies have demonstrated that the evolutionarily-informed
representations learned by pre-trained protein language models
implicitly capture structural information enables direct structure
prediction [@lin2022esmfold]. As a consequence, the limitation in
sequence understanding and generation renders Multiflow inadequate as a
multimodal protein generative foundation.

::: figure*
![image](figures/main.pdf){width="\\linewidth"}
:::

Inspired by the connection between evolutionary knowledge and spatial
interactions, we deem that sequence-based generative language models
like DPLM, with their strong sequence generation and predictive
abilities, hold great promise as a foundation for multimodal learning
for proteins. Despite its exciting potential, this approach presents two
key challenges: (1) language models cannot directly handle continuous
data like structure; and (2) language models heavily necessitate
sufficient scale of data and compute resources while structure data is
much smaller compared to sequence databases.

In this paper, we address the aforementioned questions by introducing
DPLM-2, a multimodal protein foundation model that advances the
state-of-the-art discrete diffusion-based protein language model
(*i.e*.., DPLM) to accommodate both sequences and structures. By
training on both experimental and high-quality synthetic structures,
DPLM-2 learns the joint distribution of sequence and structure, as well
as their marginals and conditionals. We present several key recipes to
facilitate multimodal learning in DPLM-2: (1) the core difficulty lies
in enabling the language model to learn structural information, which is
challenging and remains elusive, for which we develop a lookup-free
quantization [LFQ, @yu2023language] structure tokenizer to convert 3D
coordinates to discrete tokens and vice versa
(Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}A,
§[3.3](#sec:method-tokenizer){reference-type="ref"
reference="sec:method-tokenizer"}); (2) we implement an efficient
warm-up strategy to exploit the connection between large-scale
evolutionary data and structural inductive biases from pre-trained
sequence-based DPLM (Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}B, §[3.2](#sec:method-train){reference-type="ref"
reference="sec:method-train"}); and (3) we also address the exposure
bias problem in discrete diffusion for sequence
learning [@ranzato2015sequence; @bengio2015scheduled] by a self-mixup
training strategy that leads to enhanced generation quality and
diversity.

We highlight our main contributions and findings as follows:

::: compactitem
We present [DPLM-2]{.smallcaps}, a multimodal protein generative
language model that aims to simultaneously model, understand and
generate protein structure and sequence. We show that it can be fairly
efficient and effective to obtain a mulitmodal protein model with
moderate amount of high-quality data, a decent structure tokenizer and
publicly-accessible sequence-only pre-trained language models.

As a mulitmodal generative model, [DPLM-2]{.smallcaps} enables
unconditional co-generation of designable and diverse proteins that
guarantees consistency between structure and
sequence (Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}C(1)). Our empirical evaluation shows that
[DPLM-2]{.smallcaps} attains competitive co-generation performance
compared to structure-based generative approaches, while the proteins
generated by [DPLM-2]{.smallcaps} have a better alignment with the
characteristics of natural proteins in secondary structure statistics
(§[4.1](#sec:exp-uncond){reference-type="ref"
reference="sec:exp-uncond"}).

In addition, [DPLM-2]{.smallcaps} supports various conditional
generation tasks by its multimodal nature, ranging from
(sequence-conditioned)
folding (Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}C(3), §[4.2](#sec:exp-folding){reference-type="ref"
reference="sec:exp-folding"}), (structure-conditioned)
inverse-folding (Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}C(4), §[4.3](#sec:exp-invfold){reference-type="ref"
reference="sec:exp-invfold"}), to more successful motif-scaffolding
given multimodal motif
conditioning (Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}C(5), §[4.4](#sec:exp-motif){reference-type="ref"
reference="sec:exp-motif"}).

Last but not least, we demonstrate that the structure-aware protein
representation learned by [DPLM-2]{.smallcaps} brings additional benefit
for a range of protein predictive tasks
(Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}C(2), §[4.5](#sec:exp-repr){reference-type="ref"
reference="sec:exp-repr"}).
:::

**Concurrent work.** During the development of [DPLM-2]{.smallcaps}, we
became aware of the recently proposed multimodal generative protein
language model, ESM3 [@hayes2024esm3], which also jointly models
tokenized structure and sequence using a generative masked language
model. While both models aim for similar goals, [DPLM-2]{.smallcaps}
differs from ESM3 in several key aspects: **(1)** *Multimodal protein
generation:* [DPLM-2]{.smallcaps} treats structure and sequence
modalities equally by design and emphasizes the simultaneous
co-generation of compatible protein sequence and structure, whereas ESM3
is a sequence-first model (other modalities are subject to dropout
during training) and generates in cascaded modality-by-modality manner.
**(2)** *Data and compute efficiency:* ESM3 seeks to perform mulimodal
pre-training from scratch using a huge amount of synthetic data, with
modal size ranging from 1.4B to 98B. With strict license and absence of
training infrastructure, this prohibits community from replicating for
customized purposes. In contrast, [DPLM-2]{.smallcaps} leverages much
smaller datasets (PDB + SwissProt) and builds on open-source,
pre-trained sequence-based DPLM (150M/650M/3B), which leverages DPLM's
learned evolutionary knowledge and inherits strong sequence
understanding and generation capabilities. We are also committed to
open-source our models, training and inference code to democratize
multimodal generative protein LMs to benefit the community. Overall, we
believe [DPLM-2]{.smallcaps} provides unique contributions to the
community.

# Preliminaries {#sec: preliminary}

## Generative Modeling for Protein

::: wraptable
r0.28
:::

The aim of generative protein modeling is to estimate the underlying
distribution $\mathrm{prot} \sim q(\mathrm{prot})$ of the protein data
of our interest by learning a probabilistic model
$p_\theta(\mathrm{prot})$. Here
$\mathcal{\mathrm{prot}} = (r_1, r_2, \dots, r_L)$ denotes a protein
with $L$ residues, where each residue $r_i = (s_i, x_i)$ is represented
by two major modalities, *i.e*.., $s_i \in \{0,1\}^{ |\mathcal{S}|}$ is
a categorical variable for its amino acid type in
$\mathcal{S} = \{1,..., 20\}$, and
$x_i \in \mathbb{R}^{N_\text{atoms} \times 3}$ is the real-value
Cartesian coordinates of its residue atoms (we only consider backbone
atoms herein, *i.e*..,
$[\text{N}, \text{C}_{\alpha}, \text{C}, \text{O}]$ with
$N_\text{atoms}=4$). Namely, $$\begin{aligned}
    p_\theta(\mathrm{prot}) = p_\theta(s_1, s_2,\dots, s_L,~ x_1, x_2, \dots, x_L) = p_\theta(\mathbf{s}, \mathbf{x}) \nonumber
\end{aligned}$$ As a result, most of protein tasks can be viewed as
specifying their input conditioning and output between these two
modalities (Tab. [\[tab:task\]](#tab:task){reference-type="ref"
reference="tab:task"}), including (1) sequence-conditioned structure
prediction [folding,
@jumper2021AF2; @lin2022esmfold; @huguet2024foldflow2], (2)
structure-conditioned sequence generation [inverse folding or
fixed-backbone design,
@dauparas2022proteinmpnn; @hsu2022esmif; @zheng2023structure], (3)
sequence learning or generation
 [@rives2019esm; @nijkamp2022progen2; @alamdari2023protein; @wang2024diffusion],
(4) structure
generation [@yim2023framediff; @watson2023RFdiffusion; @ingraham2023chroma],
and (5) sequence-structure
co-generation [@jin2021iterative; @shi2022protein; @campbell2024generative].
These further enable various conditional applications by allowing single
or mixed-modal conditioning for partial generation, *e.g*..,
motif-scaffolding and antibody design.

## Diffusion Protein Language Model (DPLM)

Language models (LMs), typically parameterized by
Transformers [@vaswani2017attention] have become the *de facto* choice
dominating different domains with scalable and performing
expressiveness [@openai2023gpt4]. Among them, protein LMs have been
serving as one of the AI foundation for protein sequence
learning [@rives2019esm; @lin2022esmfold] and
generation [@nijkamp2022progen2; @alamdari2023protein].

Diffusion protein language model [DPLM, @wang2024diffusion], in
particular, shows excelling performance in both generation and
representation learning of protein sequences. DPLM is grounded in
*absorbing* discrete diffusion
framework [@austin2021structured; @zheng2023reparameterized], which is
characterized by a forward and backward Markov process. Let
$\texttt{Cat}(\mathbf{x};\mathbf{p})$ be a categorical distribution on
protein sequence $\mathbf{y}$ parameterized by a vector $\mathbf{p}$ on
$(|\mathcal{V}|-1)$-dimensional probability simplex. The forward process
of discrete diffusion defines a Markov process governed by the
transition kernel
$q(\mathbf{x}^{(t)}|\mathbf{x}^{(t-1)})=\texttt{Cat}\big(\mathbf{x}^{(t)}; \beta_t\mathbf{x}^{(t-1)} + (1-\beta_t)\mathbf{q}_{\text{noise}}\big)$
that gradually perturb the data
$\mathbf{x}^{(0)}\sim q(\mathbf{x}^{(0)})$ into a stationary
distribution $\mathbf{x}^{(T)} \sim \mathbf{q}_{\text{noise}}$. For
absorbing diffusion, $\mathbf{q}_{\text{noise}}$ is the point mass with
all of the probability on the absorbing (mask) state. The learned
*backward* process
$p_{\mathbf{\theta}}(\mathbf{x}^{(t-1)}|\mathbf{x}^{(t)})$ reversely
denoises the $\mathbf{x}^{(T)}$ towards the data distribution
$\mathbf{x}^{(0)}$, which is typically optimized by the variational
bound of the log-likelihood [@ho2020ddpm]: $$\begin{aligned}
      & \mathbb{E}_{q(\mathbf{x}^{(0)})}\big[\log p_\theta(\mathbf{x}^{(0)})\big]  \geq \mathbb{E}_{q(\mathbf{x}^{(0:T)})} \bigg[\log \frac{p_{\theta}(\mathbf{x}^{(0:T)})}{q(\mathbf{x}^{(1:T)}|\mathbf{x}^{(0)})}\bigg] \nonumber\\[-5pt]
      & = \mathbb{E}_{q(\mathbf{x}^{(0)})}\Big[\log p_{\theta} (\mathbf{x}^{(0)} | \mathbf{x}^{(1)}) 
      + \textstyle{\sum_{t=2}^{T}} \underbrace{-\text{KL}\big[q(\mathbf{x}^{(t-1)}|\mathbf{x}^{(t)}, \mathbf{x}^{(0)})\|p_{{\theta}}(\mathbf{x}^{(t-1)}|\mathbf{x}^{(t)})\big]\Big]}_{\mathcal{J}_t} + \text{const.}, \nonumber
\end{aligned}$$ where $\mathcal{J}_t$ is the learning objective. The
learning objective of discrete diffusion can be further simplified into
reweighted cross-entropies [@zheng2023reparameterized], resembling
masked language modeling at arbitrary noise levels: $$\begin{aligned}
\mathcal{J}_t & = \mathbb{E}_{q(\mathbf{x}^{(0)})}-\text{KL}\big[q(\mathbf{x}^{(t-1)}|\mathbf{x}^{(t)}, \mathbf{x}^{(0)})\|p_{{\theta}}(\mathbf{x}^{(t-1)}|\mathbf{x}^{(t)})\big] \nonumber \\[-1pt]
& = \mathbb{E}_{q(\mathbf{x}^{(0)})} \Big[\lambda^{(t)}  \textstyle{\sum_{1 \leq i \leq L}} b_i(t) \cdot \log p_{\theta}(x^{(0)}_i|\mathbf{x}^{(t)})\Big], 
\label{eq:reparam_obj}
\end{aligned}$$ where $\lambda^{(t)}$ is a weighting coefficient induced
from the specific noising schedule. For inference, DPLM is able to
generate amino acid sequences by the reverse iterative denoising process
of discrete diffusion [@hoogeboom2021argmax; @austin2021structured] from
the following distribution, $$\begin{aligned}
    p_\theta(\mathbf{x}^{(t-1)} | \mathbf{x}^{(t)}) = \textstyle\sum_{\Tilde{\mathbf{x}}^{(0)}} q(\mathbf{x}^{(t-1)} |\mathbf{x}^{(t)}, \Tilde{\mathbf{x}}^{(0)} p_\theta(\Tilde{\mathbf{x}}^{(0)}| \mathbf{x}^{(t)}). \nonumber
\end{aligned}$$ Specifically, at time $t$, it first generates
$\Tilde{\mathbf{x}}^{(0)}$ from $p_\theta(\cdot| \mathbf{x}^{(t)})$,
then a less noisy $\mathbf{x}^{(t-1)}$ is sampled by
$q(\cdot |\mathbf{x}^{(t)},\mathbf{x}^{(0)} = \Tilde{\mathbf{x}}^{(0)})$.
Within absorbing diffusion, the generation process can be viewed as an
iterative *mask-predict* approach. For sequence representation for
predictive tasks, it can be obtained by simply letting DPLM take the
sequence as input.

# DPLM-2: A Multimodal Diffusion Protein Language Model

## Overview

Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"} illustrates [DPLM-2]{.smallcaps}'s overall
architecture. [DPLM-2]{.smallcaps} is built on the state-of-the-art
sequence-based generative protein LM, *i.e*..,
DPLM [@wang2024diffusion], using a discrete diffusion probabilistic
framework to concurrently model both protein sequences and their
corresponding structures. To facilitate structure learning in language
models, we introduce a token-based representation for protein structure
via a tokenizer that converts
$\mathbf{x} \in \mathbb{R}^{L \times N_\text{backb} \times 3}$, the 3D
coordinates of the protein backbone into a discrete structure token
sequence, denoted as
$\mathbf{z} = (z_1, z_2, \dots, z_L) \in \{0,1\}^{L \times |\mathcal{Z}|}$,
where each token $z_i$ represents a local structural element of the
$i$-th residue. Given tokenized structure, [DPLM-2]{.smallcaps}
processes mulitmodal input by concatenating the structure token sequence
$\mathbf{z}$ with the corresponding amino acid sequence $\mathbf{s}$ for
the same protein. Notably, there exists a position-by-position
correspondence between $\mathbf{z}$ and $\mathbf{s}$, where $z_i$ and
$s_i$ refer to the two modalities of the $i$-th residue, respectively.
To reinforce this correspondence, we assign identical position encodings
to both $z_i$ and $s_i$, thereby ensuring that structural and sequence
information is aligned at the residue level.

To train [DPLM-2]{.smallcaps}, we leverage a high-quality dataset
comprising 20K clustered experimental structures from the Protein Data
Bank (PDB) [@berman2000protein] and 200K predicted structures from the
AFDB SwissProt split [@varadi2022alphafold], with length $< 512$. During
training, [DPLM-2]{.smallcaps} is tasked with denoising the input
sequence across a spectrum of noise levels, ranging from fully noisy to
completely clean. The multimodal training objective of
[DPLM-2]{.smallcaps} is derived from
Eq. ([\[eq:reparam_obj\]](#eq:reparam_obj){reference-type="ref"
reference="eq:reparam_obj"}) as, $$\begin{aligned}
\mathcal{J}_{t} & = \mathbb{E}_{q(\mathbf{x}^{(0)},\mathbf{s}^{(0)}),\mathbf{z}^{(0)}\leftarrow \textit{tokenize}(\mathbf{x}^{(0)})} \Big[\lambda^{(t)}  \textstyle{\sum_{1 \leq i \leq L}} b_i(t) \cdot \log p_{\theta}(z^{(0)}_i,s^{(0)}_{i}|\mathbf{z}^{(t)},\mathbf{s}^{(t)})\Big], \nonumber 
\end{aligned}$$

where
$\log p_{\theta}(z_i,s_i|\cdot) = \log p_{\theta}(z_i|\cdot) + \log p_{\theta} (s_i|\cdot)$
by assuming conditional independence. By learning
$p_{\theta}(\mathbf{z}^{(t-1)}, \mathbf{s}^{(t-1)} | \mathbf{z}^{(t)}, \mathbf{s}^{(t)})$,
the model enables the simultaneous generation of highly correlated
protein structures and sequences. This eliminates the need for a
cascaded generation paradigm, allowing us to derive both the protein's
structure and sequence in a single step.

To further enhance [DPLM-2]{.smallcaps}'s ability to differentiate
between structure and sequence, noising level for each modality is
subjected to distinct scheduler, denoted as $t_{\mathbf{z}}$ and
$t_{\mathbf{s}}$, respectively. This facilitates a more comprehensive
understanding of the relationships between protein sequences and their
corresponding structures. This design also allows us to explore
arbitrary combinations of $(t_{\mathbf{z}}, t_{\mathbf{s}})$, thus
providing flexible sampling options, including sampling from the
marginals of each modality and conditionals between them for various
applications (Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}C). Furthermore, we also identify the exposure bias
issue in discrete diffusion for sequence
learning [@ranzato2015sequence; @bengio2015scheduled], and mitigate this
by proposing a self-mixup strategy inspired by scheduled sampling, which
improves both generation quality and diversity (see
§[6.1](#sec:self-mixup){reference-type="ref"
reference="sec:self-mixup"}).

## Efficient Warm-up from Pre-trained Sequence-based DPLM {#sec:method-train}

Protein sequences encode critical evolutionary information, reflecting
co-evolutionary processes where residue pairs mutate together and often
interact in 3D space, offering insights for predicting protein
folding [@melnyk2022alphafold]. @lin2022esmfold further showed that
protein language models trained on large-scale evolutionary data
implicitly capture this information, which can facilitate structure
prediction. Motivated by the link between evolutionary knowledge and
structural interactions, we propose to built [DPLM-2]{.smallcaps} with
an efficient warmup from pre-trained sequence-based DPLM, to make the
most of established evolutionary information for protein structure
modeling, Since our structure dataset is significantly smaller than
UniRef50 sequence database (200K *vs*.. 45M), enabling efficient
fine-tuning of the pre-trained model. we want to keep the sequence
knowledge intact and reduce the risk of catastrophic forgetting, we
apply LoRA [@hu2021lora] to limit too much deviation to the original
parameters. This approach not only lowers training costs compared to
starting from scratch but also effectively transfers valuable
evolutionary information.

## Learning Structure Tokenization {#sec:method-tokenizer}

The core difficulty of achieving a mulimodal protein LM lies in enabling
the language model to learn structural information, which is challenging
and remains elusive, Tokenizing continuous data modalities into discrete
representations [@van2017vqvae] has gained attraction across domains
like image synthesis due to its ability to capture compact, meaningful
information, enabling effective compression and efficient generation,
especially with sequence-based models like Transformers. Recent efforts
have applied this approach to protein structure
coordinates [@van2024foldseek; @haiyan2023diffusion; @gao2024foldtoken; @lu2024tokenized].
This allows language models to better learn the composition of local
structural elements. However, how to learn an effective structure
tokenizer remains an active research question.

::: wrapfigure
r0.5 ![image](figures/tokenizer-ssp.pdf){width="50%"}
:::

Structure tokenization under a typical VQ-VAE [@van2017vqvae] framework
can be summarized as follows:
$$\mathbf{x} \xrightarrow{\text{encoder}} \mathbf{e} \xrightarrow{\text{quantizer}}\mathbf{z} \xrightarrow{\text{decoder}} \tilde{\mathbf{x}},$$
where (1) a structure encoder encodes backbone 3D coordinates
$\mathbf{x} \in \mathbb{R}^{L \times N_\text{backb} \times 3}$ into
invariant features
$\mathbf{e} \in \mathbb{R}^{L \times d_\text{quant}}$, (2) a quantizer
converts $\mathbf{e}$ into $\mathbf{z}$ of $L$ discrete tokens where
$z_i \in \{0, 1, \ldots, |\mathcal{Z}|\}$ given a finite-size codebook
$\mathcal{Z}$; and (3) a structure decoder reconstructs 3D coordinates
$\tilde{\mathbf{x}}$ from the discrete tokens. We utilize a
GVP-based [@jing2020gvp] structure encoder from pre-trained
GVP-Transformer [@hsu2022esmif] and a IPA-based [@jumper2021AF2]
structure decoder. In terms of quantizer, our preliminary experiment
showed that conventional VQ-VAE pretty much struggles in training. To
mitigate this, we instead adopts Lookup-Free Quantizer (LFQ) from the
currently best visual tokenizer [@yu2023language] to protein structure
tokenization. Specifically, the latent space of LFQ is decomposed as the
Cartesian product of single-dimensional binary variables, as
$\mathbb{C} = \times_{k=1}^{\log_2 |\mathcal{Z}|} \mathcal{C}_k$, where
$\mathcal{C}_k = \{-1, 1\}$. Given the encoded feature
$\mathbf{e} = \text{encoder}(\mathbf{x}) \in \mathbb{R}^{L \times \log_2 |\mathcal{Z}|}$,
each dimension (indexed by $k$) of the quantized representation
$\mathtt{quant}(e_i)$ is obtained from:
$$\mathtt{quant}(e_i)[k] = \mathcal{C}_{i,k} = \mathtt{sign}(e_i[k]) = -\mathbf{1} \{z_i[k] \leq 0\} + \mathbf{1} \{e_i[k] > 0\}. \nonumber$$
As such, with LFQ, the token indices for
$\mathbf{z} = \{z_1,z_2,..., z_i,..., z_L\}$ is given by:
$$z_i = \mathtt{index}(\mathtt{quant}(e_i)) = \textstyle\sum_{k=1}^{\log_2 |\mathcal{Z}|} 2^{k-1} \mathbf{1}\{e_i[k] > 0\},~ \forall z_i \in \mathbf{z}. \nonumber$$
The LFQ-based structure tokenizer is trained on the same structure
dataset as mentioned before, using a combination of reconstruction,
commitment, and entropy regularization losses, similar to standard
VQ-VAE. Here FAPE loss [@jumper2021AF2] is used as the primary
reconstruction loss.

**Evaluation.** As shown in
Fig. [\[fig:tokenizer\]](#fig:tokenizer){reference-type="ref"
reference="fig:tokenizer"}A, LFQ significantly outperforms VQ-VAE
regarding reconstruction accuracy while training of LFQ is much faster
than VQ-VAE (2 *vs*.. days on 8 A100s). Increasing codebook size leads
to improved reconstruction while a codebook size of 8192 achieves the
best compression-reconstruction trade-off. Meanwhile in
Fig. [\[fig:tokenizer\]](#fig:tokenizer){reference-type="ref"
reference="fig:tokenizer"}B, we observe a strong correlation between
structure tokens and secondary structures. For instance, a lot of
structure tokens concentrated at the alpha helix and beta sheet
vertices, while some tokens lie between regions. This suggests that
structure tokens the fine-grained structural elements in backbone local
environment.

# Experiments {#sec:experiment}

In this section, we evaluate [DPLM-2]{.smallcaps} on various generative
and understanding scenarios, including unconditional protein
generation (structure, sequence, and structure-sequence co-generation,
§[4.1](#sec:exp-uncond){reference-type="ref"
reference="sec:exp-uncond"}), and a variety of conditional tasks, such
as folding (§[4.2](#sec:exp-folding){reference-type="ref"
reference="sec:exp-folding"}), inverse
folding (§[4.3](#sec:exp-invfold){reference-type="ref"
reference="sec:exp-invfold"}) and motif-scaffolding
(§[4.4](#sec:exp-motif){reference-type="ref"
reference="sec:exp-motif"}), and a series of protein predictive tasks
(§[4.5](#sec:exp-repr){reference-type="ref" reference="sec:exp-repr"}).

::: figure*
![image](figures/uncond_main.pdf){width="\\linewidth"}
:::

## Unconditional Protein Generation {#sec:exp-uncond}

The goal of unconditional protein generation is to produce both the 3D
structure and amino acid sequence. Typically, this is done using a
cascaded approach: either generating the structure first and then use
another model to predict the sequence, or vice versa. Here, we focus on
generating structure and sequence simultaneously. We evaluate
[DPLM-2]{.smallcaps} on both cascaded and simultaneous generation across
three tasks: *unconditional structure generation*, *unconditional
sequence generation*, and *structure-sequence co-generation*.

Following Multiflow [@campbell2024generative], we evaluate the generated
proteins in terms of *quality*, *novelty* and *diversity*. **Quality**
is measured through *designability* (structure's ability to fold into a
valid sequence) and *foldability* (sequence's ability to fold into a
reasonable structure). Designability is assessed by folding the
generated sequence with ESMFold [@lin2022esmfold], then using
`sc-TMscore` and `sc-RMSD` with the co-generated structure to evaluate
similarity. Foldability is evaluated via ESMFold, with `pLDDT` $>$ 70
considered plausible. **Novelty** is assessed by comparing generated
structures to known ones in PDB using TMScore (`pdb-TM`), with lower
values indicating greater novelty. **Diversity** is measured by
calculating pairwise `TMscore` (`inner-TM`), where lower scores indicate
more dissimilarity. The number of clusters identified by
FoldSeek [@van2023foldseek] also quantifies diversity, normalized by the
total number of structures.

### [DPLM-2]{.smallcaps} Enables High-quality, Diverse and Novel Protein Sequence and Structure Generation {#subsec:main results}

Tab. [\[tab:uncond_main\]](#tab:uncond_main){reference-type="ref"
reference="tab:uncond_main"} and
Fig. [\[fig:uncond_all\]](#fig:uncond_all){reference-type="ref"
reference="fig:uncond_all"} present the results of [DPLM-2]{.smallcaps}
for unconditional protein generation. We highlight our key findings in
the following aspects:

**(1) [DPLM-2]{.smallcaps} can generate diverse and highly-plausible
protein with simultaneous structure-sequence co-generation.** We sampled
100 proteins for each length in 100, 200, 300, 400, and 500.
Fig. [\[fig:uncond_all\]](#fig:uncond_all){reference-type="ref"
reference="fig:uncond_all"}A/B demonstrates that [DPLM-2]{.smallcaps}
can sample sequence and structures with high designability across
various lengths, with most `sc-TM` values exceeding 0.9, with diverse
structure clusters.
Fig. [\[fig:uncond_all\]](#fig:uncond_all){reference-type="ref"
reference="fig:uncond_all"}D shows that the novelty of sampled proteins,
measured by `pdb-TM`, generally increases with longer protein lengths.
In addition, [DPLM-2]{.smallcaps} can generate with both modalities
simultaneously or a modality-by-modality. As shown in
Tab. [\[tab:uncond_main\]](#tab:uncond_main){reference-type="ref"
reference="tab:uncond_main"}, the co-generation performance exhibit
highest `scTM`, suggesting that co-modeling indeed benefits protein
generation.

**(2) [DPLM-2]{.smallcaps} can attains competitive performance with
strong baselines on co-generation, as well as backbone-only and
sequence-only generation, respectively.** As shown in
Tab. [\[tab:uncond_main\]](#tab:uncond_main){reference-type="ref"
reference="tab:uncond_main"}, [DPLM-2]{.smallcaps} achieves the strong
`sc-TM` compared to strong baselines, approaching the quality of native
structures from PDB. We notice that ESM3-Open [@hayes2024esm3], which
runs in a sequence-then-structure order, fails short of unconditional
generation. Compared to MultiFlow [@campbell2024generative],
[DPLM-2]{.smallcaps} achieves comparable co-generation quality. Notably,
as also reported in @campbell2024generative, Multiflow falls short of
sequence generation when directly trained from structures with native
sequences, resulting in greatly degraded co-generation performance
without data distillation from external inverse folding models
(ProteinMPNN). For reference, we also provide the result of Multiflow
retrained using our training data, where its co-generation performance
remains unsatisfying and lags behind [DPLM-2]{.smallcaps}, which
suggests that [DPLM-2]{.smallcaps} has advantages of directly and
effectively learning from complex structure-sequence joint distribution.
Moreover, [DPLM-2]{.smallcaps} can also only produce single modality if
needed, where it matches the best competitive models in these settings
respectively. These results demonstrate [DPLM-2]{.smallcaps}'s
effectiveness as a mulitmodal generative model.

**(3) [DPLM-2]{.smallcaps} generates longer proteins beyond training
data.** As [DPLM-2]{.smallcaps} is trained with a $512$ length cutoff,
we are curious about its length extrapolation, and evaluate sampled
proteins at lengths of $[600,700,800, 900,1000]$. As shown in
Fig. [\[fig:uncond_all\]](#fig:uncond_all){reference-type="ref"
reference="fig:uncond_all"}F, notably, for proteins exceeding the
maximum training length of 512, the `pLDDT` scores of sequences sampled
by [DPLM-2]{.smallcaps} are close to those of DPLM. This suggests that
[DPLM-2]{.smallcaps} largely retains its sequence generation capability
inherited from sequence pre-training in DPLM, leading to its capability
of length extrapolation.

**(4) Case study.**
Fig. [\[fig:uncond_all\]](#fig:uncond_all){reference-type="ref"
reference="fig:uncond_all"}H shows some generated samples of
[DPLM-2]{.smallcaps} up to 700 residues, while in
Fig. [\[fig:uncond_all\]](#fig:uncond_all){reference-type="ref"
reference="fig:uncond_all"}I we showcase that we can manipulate
[DPLM-2]{.smallcaps} to design symmetric oligomers by forcing to
duplicate the predicted tokens with repetitive structure and sequence
patterns.

::: table*
[]{#tab:uncond_main label="tab:uncond_main"}
:::

::: figure*
![image](figures/ssp_main.pdf){width="\\linewidth"}
:::

### [DPLM-2]{.smallcaps} Generates Proteins That Resembles Natural Proteins

To further analyze the properties of different model, we examine their
secondary structure distribution against natural proteins from PDB.

**Proteins sampled by [DPLM-2]{.smallcaps} have secondary structures
most similar to natural proteins.** As seen in
Fig. [\[fig:ssp_all\]](#fig:ssp_all){reference-type="ref"
reference="fig:ssp_all"}A, structure-based models like RFDiffusion and
MultiFlow generate proteins with more helices and fewer sheets and loops
than natural proteins in PDB. Protein language models like ESM3 and
[DPLM-2]{.smallcaps} show no strong bias towards alpha helices, but ESM3
tends to generate more loops. Among the methods, [DPLM-2]{.smallcaps}
produces the most natural-like secondary structure proportions, closely
matching PDB proteins. In
Fig. [\[fig:ssp_all\]](#fig:ssp_all){reference-type="ref"
reference="fig:ssp_all"}C, proteins generated by MultiFlow contain many
helices and become more globular as length increases, exhibiting
idealized secondary structures. In contrast, proteins generated from
[DPLM-2]{.smallcaps} resembles natural ones have more balanced
structures, with fewer helices and more beta sheets and loops. On the
other hands, simplex plots in
Fig. [\[fig:ssp_all\]](#fig:ssp_all){reference-type="ref"
reference="fig:ssp_all"}C shows that while MultiFlow's proteins are
clustered in helix-rich regions, [DPLM-2]{.smallcaps}'s proteins span a
wider area similar to natural proteins, while it rarely samples proteins
composed mostly of sheets and loops, which do occur in nature.
Additionally, Fig. [\[fig:ssp_all\]](#fig:ssp_all){reference-type="ref"
reference="fig:ssp_all"}B shows that the loop ratio has a significant
impact on designability, where a higher proportion of loops will
increase `scRMSD`, as loops are highly flexible. Thus, proteins with
long loops, which [DPLM-2]{.smallcaps} often generates, tend to have
relatively high `scRMSD`, aligning with the results in
Tab. [\[tab:uncond_main\]](#tab:uncond_main){reference-type="ref"
reference="tab:uncond_main"}.

### Ablation Study

In [DPLM-2]{.smallcaps} training, we start with a warmup from the
sequence-based pre-trained DPLM to exploit established evolutionary
information and augment the data with high-quality AlphaFold-predicted
structures from SwissProt (around 200K) and clustered PDB structures.
This section evaluates the effects of sequence pre-training and data
augmentation on unconditional protein generation.

[]{#tab:ablation label="tab:ablation"}

Tab. [\[tab:ablation\]](#tab:ablation){reference-type="ref"
reference="tab:ablation"} demonstrates that *sequence pre-training and
data augmentation can significantly improve the designability and
diversity*, especially in generating long proteins (length $> 300$). We
hypothesize that the limited number of long proteins in PDB leads to
insufficient training. In contrast, sequence pretraining, which includes
evolutionary data, is essential and can be transferred to improve
protein structure modeling and generation quality. Additionally, this
evolutionary information boosts sampling diversity. While increasing the
amount of training data improves designability, it is less effective in
enhancing diversity compared to sequence pretraining. By combining both
strategies, we achieve the best overall performance, which forms the
core of our training strategy.

## Forward Folding (Sequence-conditioned Structure Prediction) {#sec:exp-folding}

::: wraptable
r0.55
:::

The goal of folding is to predict the 3D structure for the given amino
acid sequence [@jumper2021AF2]. As a mulitmodal generative model,
[DPLM-2]{.smallcaps} spontaneously enables protein structure prediction
task (see Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}C-3) given sequence as conditioning. We assess
[DPLM-2]{.smallcaps} on CAMEO 2022 and a PDB data split used by
Multiflow [@campbell2024generative]. We utilize `RMSD` and `TMscore`
between predicted and ground truth structure for evaluation, while
[DPLM-2]{.smallcaps} adopts `argmax` decoding for 100 sampling
iterations.

**Tab. [\[tab:folding\]](#tab:folding){reference-type="ref"
reference="tab:folding"} indicates that [DPLM-2]{.smallcaps} can perform
sufficiently good folding in a zero-shot manner.** Performance can be
improved after further supervised fine-tuning (SFT) using folding
objective ($\max_{\theta} \log p_\theta (\mathbf{z} | \mathbf{s})$).
Overall, [DPLM-2]{.smallcaps} can outperform or on par with the strong
baselines, while achieving close performance with ESMFold. Furthermore,
We observe that [DPLM-2]{.smallcaps} with larger model scales can attain
better results than smaller ones. We suggest that [DPLM-2]{.smallcaps}
benefits from the evolutionary information inherited from DPLM
pre-trained on the vast number of protein sequences, which can be
transferred and leveraged into structure modeling.

## Inverse Folding (Structure-conditioned Sequence Generation) {#sec:exp-invfold}

The goal of inverse folding is to find an amino acid sequence that can
fold to a given backbone structure. For evaluation, we employ amino acid
recovery (`AAR`) for sequence evaluation, and we also assess the
structure by self-consistency TM-score (`scTM`) between the native
structure and the ESMFold-predicted structure of the generated sequence.

::: wraptable
r0.55
:::

**[DPLM-2]{.smallcaps} can generate reasonable sequences that fold into
the given structures.**
Tab. [\[tab:invfold\]](#tab:invfold){reference-type="ref"
reference="tab:invfold"} presents that [DPLM-2]{.smallcaps} can
outperform or be on par with other co-generation models (MultiFlow,
ESM3). As the model size increases, the performance in terms of sequence
recovery (`AAR`) and structural consistency (`scTM`) improves, revealing
the same scaling law observed in the folding task. We suggest that
multimodal training effectively aligns the structure and sequence into
the same space, such that [DPLM-2]{.smallcaps} can yield the
corresponding sequence without additional training.

## Scaffolding with Mixed-modal Motif Conditioning {#sec:exp-motif}

The objective of motif-scaffolding is to generate a suitable scaffold to
preserve the structure of the given motif and maintain its original
function. We follow the experimental setting of @yim2024improved, with
24 motif-scaffolding problems and we sample 100 scaffolds for each
motif, where we (1) first determine the length of scaffold, and then (2)
keep the motif segment unchanged and sample the scaffold part
conditioned on the motif. The scaffold length is sampled from a range
provided by @yim2024improved, and when there are multiple motifs, the
order of motif segments is consistent with @yim2024improved. We provide
the 3D structure and sequence of motif as input of [DPLM-2]{.smallcaps}.
As a multimodal model, we evaluate [DPLM-2]{.smallcaps} using
sequence-based, structure-based, and co-generation approaches. A
scaffold is considered successful if it satisfies both criteria (1)
overall designablity, which is successful when `pLDDT` $> 70$ (for
sequence-based models) or `scTM` $> 0.8$, and (2) motif-preseving, which
is deemed successful when the predicted motif structure matches the
native one with `motif-RMSD` $<$`<!-- -->`{=html}1Å.

::: wrapfigure
r0.35 ![image](figures/motif_main.pdf){width="\\linewidth"}
:::

**Fig. [\[fig:motif_main\]](#fig:motif_main){reference-type="ref"
reference="fig:motif_main"} reveals that [DPLM-2]{.smallcaps} is capable
of generate reasonable scaffolds for the given functional motifs.** In
sequence-based, structure-based and co-generation evaluation,
[DPLM-2]{.smallcaps} can outperform or be on par with the corresponding
approaches in most cases, solving more motif problem and achieving
higher average success rate. We compared to sequence-based method,
[DPLM-2]{.smallcaps} shows better performance since it allows structural
input of motif, which is important for preserving motif's structure
hence the functions. Remarkably, [DPLM-2]{.smallcaps} attains comparable
performance with RFDiffusion when only generating scaffold structure,
while achieve better performance when simultaneously designing scaffold
sequence and structure, outperforming ESM3. Despite not experimentally
verified, these results suggest that with [DPLM-2]{.smallcaps},
mulitmodal conditioning and generation could lead to more successful
conditional protein design.

::: table*
:::

## Evaluation of Protein Representation Learning {#sec:exp-repr}

Directly access to structure information is supposed to benefit
downstream protein predictive tasks. To inspect this, we evaluate
[DPLM-2]{.smallcaps} on a variety of protein predictive tasks utilizing
the dataset provided by SaProt [@su2023saprot], where we provide
tokenized protein structure tokens along with the protein sequences to
[DPLM-2]{.smallcaps}.

::: wraptable
r0.25
:::

**[DPLM-2]{.smallcaps} can perform multimodal representation learning by
leveraging both structure and sequence information.**
Tab. [\[tab:results_understanding\]](#tab:results_understanding){reference-type="ref"
reference="tab:results_understanding"} presents that
[DPLM-2]{.smallcaps} shows further improvement compared to sequence-only
methods (ESM2, DPLM) on some tasks, indicating that [DPLM-2]{.smallcaps}
can leverage protein structures to generate better representations
containing multimodal information for downstream tasks. However, we find
that [DPLM-2]{.smallcaps} falls behind the state-of-the-art
structure-aware protein LM, *i.e*.., SaProt, in most tasks and even lags
behind DPLM in certain tasks. We hypothesize this is because the
strutcure training data of DPLM-2, consisting of PDB and SwissProt, is
smaller and differs from UniRef50, which DPLM is pretrained on,
potentially causing catastrophic forgetting and suboptimal
representation. To test this, we conducted an experiment on the DeepLoc
subcellular task, where [DPLM-2]{.smallcaps} underperforms compared to
DPLM. As shown in
Tab. [\[tab:results_understanding_noptrn\]](#tab:results_understanding_noptrn){reference-type="ref"
reference="tab:results_understanding_noptrn"}, without large-scale
sequence pretraining, [DPLM-2]{.smallcaps} outperforms DPLM
significantly, suggesting that: (1) Incorporating structure information
enhances performance over sequence-only models. (2) Smaller datasets can
lead to catastrophic forgetting, diminishing the benefits of large-scale
pretraining. As result, to further improve the predictive performance,
one deserving direction is to exploit larger-scale predicted structures
in our future work.

# Discussions

In this paper, we introduce [DPLM-2]{.smallcaps}, a multimodal diffusion
protein language model that understands, generates and reasons over
protein structure and sequence, aiming to severe as a mulimodal
foundation for protein. Despite promising performance spanning protein
co-generation, folding, inverse folding and conditional
motif-scaffolding with mulimodal input and output, there remains several
limitations deserving to be addressed. (1) Structure data: Our findings
indicate that while structure awareness may help with predictive tasks,
the limited structure data constrains [DPLM-2]{.smallcaps}'s ability to
learn robust representations. It is also important to account for longer
protein chains and multimers in future studies. (2) Trade-off of
discrete latent representation: Tokenizing structure into discrete
symbols facilitates multimodal protein language models and co-generation
but may come at the cost of losing fine-grained structural details and
control, such as precise atomic positions and inter-atomic distances.
Future work should aim to also integrate the strengths of data-space
structure-based generative models into sequence-based mulitimodal
language models to maximize the best of both worlds.

# Acknowledgement {#acknowledgement .unnumbered}

We would like to thank Dr. Hang Li for insightful discussions on the
project and feedback on the manuscript that help shape this study. We
thank Yi Zhou, Jing Yuan, Yilai Li, Yuning Shen, Wesley Hsieh and
Daiheng Zhang for their valuable comments.

# [DPLM-2]{.smallcaps} Training

## Tackling Exposure Bias in Discrete Diffusion with Self-mixup Training Strategy {#sec:self-mixup}

We find that discrete diffusion training will face the *exposure bias*
problem [@ranzato2015sequence; @bengio2015scheduled], which means
mismatch between training and inference. The model is trained to denoise
given the ground-truth context during training. However, during
inference, the model needs to denoise based on the predicted tokens,
which may not be correct and inconsistent with the always-accurate
context during training. This may lead to error accumulation and
negatively impact the generation performance.

To address this issue, we propose a *self-mixup* training paradigm for
discrete diffusion model, enhancing the consistency between training and
inference. During training, we perform an additional forward pass,
allowing the model to first make predictions and then denoise based on
those predictions.

Tab. [\[tab:self-mixup\]](#tab:self-mixup){reference-type="ref"
reference="tab:self-mixup"} shows that the *self-mixup* training
strategy effectively enhances the diversity of samples. We attribute
this to the model producing more accurate logits during inference,
leading to more diverse reasonable sampling paths instead of converging
on the sampling paths with the highest probability, which results in
more diverse proteins.

[]{#tab:self-mixup label="tab:self-mixup"}

## Dataset

The training set of [DPLM-2]{.smallcaps} is composed by experimental
data, *i.e*.., PDB [@berman2000protein], and high quality synthetic
data, *i.e*.., SwissProt [@varadi2022alphafold]. We filter the SwissProt
data by pLDDT $>$ 85. After filtering, the overall training set contains
approximately 200,000 proteins. We limit the maximum length of the
training set to 512. For proteins longer than 512, we randomly crop it
to 512. We crop the low pLDDT (pLDDT $<$ 50) segments located at the
both ends of proteins in the SwissProt dataset. These segments are
typically non-structural and may negatively impact the training results.
Moreover, we find that the length distribution of the training set is
not balanced, where the number of proteins with length less than 100 is
relatively small, leading to a suboptimal diversity among the short
proteins. Therefore, during training, we randomly crop long proteins to
short proteins with a probability of 50% for each batch to improve the
diversity.

## Hyperparameter

We train all models using AdamW optimizer [@kingma2014adam] with
$\beta_1$ = 0.9 and $\beta_2$ = 0.95. We use a weight decay of 0.01 and
gradient clipping of 0.5. We employ 2K warmup steps until reaching the
maximum learning rate, and utilize a linear decay scheduler to decay LR
to 10% of the maximum learning rate by the end of training. The maximum
learning rate is 1e-4, and the overall training step is 100,000. We
utilize the pretrained DPLM as the parameter initialization, and the
diffusion timestep is set to 500. We train 150M [DPLM-2]{.smallcaps}
with 8 A100 GPUs for 3 days, while 650M with 16 A100 GPUs for 3 days and
3B with 16 A100 GPUs for a week.

# Structure Tokenizer

The core difficulty of achieving a mulimodal protein LM lies in enabling
the language model to learn structural information, which is challenging
and remains elusive, Tokenizing continuous data modalities into discrete
representations [@van2017vqvae] has gained attraction across domains
like image synthesis due to its ability to capture compact, meaningful
information, enabling effective compression and efficient generation,
especially with sequence-based models like Transformers. Recent efforts
have applied this approach to protein structure
coordinates [@van2024foldseek; @haiyan2023diffusion; @gao2024foldtoken; @lu2024tokenized].

## Dataset

Our structure tokenizers are trained using the same structure data as
our mulitmodal language model, containing both experimental and
high-quality structures, totaling 200K proteins.

## Model Architecture

As shown in Fig. [\[fig:main\]](#fig:main){reference-type="ref"
reference="fig:main"}A, the structure tokenizer in this paper consists
of a structure encoder, quantizer, and structure decoder. The encoder is
based on a pre-trained GVP-Transformer [@hsu2022esmif], with its
parameters frozen during training. It transforms backbone structures
into geometric features, which are projected onto a latent embedding
using an MLP layer. For the quantizer, we adopt a lookup-free quantizer
from a state-of-the-art video tokenizer [@yu2023language], where the
latent dimension is set to $\log_2 |\mathcal{Z}|$, with $|\mathcal{Z}|$
as the codebook size. The structure decoder follows the IPA-based
modules from AlphaFold2 [@jumper2021AF2], using 4 EvoFormer layers
without MSA row attention, following ESMFold [@lin2022esmfold], to
generate atomic positions from the structure tokens.

## Training

The structure tokenizer is trained using a standard VQ-VAE framework,
with the objective including reconstruction loss, codebook commitment
loss, and entropy regularization loss to ensure effective codebook
utilization. For the reconstruction loss, we adopt the FAPE loss,
violation loss, and distogram loss from AlphaFold2, measuring the
difference between predicted and native structures. To further enhance
the training, we introduce a sequence prediction head on top of the
structure decoder's final representation and minimize the cross-entropy
against the native sequence.

# Motif Scaffolding

## Evaluation Pipeline {#sec:motif_evaluation}

We evaluate [DPLM-2]{.smallcaps} in sequence-based, structure-based and
co-generation ways. The overall illustration is shown in
Fig. [1](#fig:motif_evaluation){reference-type="ref"
reference="fig:motif_evaluation"}.

We focus on the two aspects: overall quality and motif part consistency.
The assessment of overall quality varies across different approaches.
Specifically, (1) For sequence-based method, we only take the generated
sequence and utilize ESMFold to obtain the predicted structure, and the
`pLDDT` score provided by ESMFold is used to assess overall quality. (2)
For structure-based method, we only take the generated structure, and
then leverage ProteinMPNN to predict the sequence, followed by ESMFold
to predict the structure, where overall quality is assessed by `scTM`.
(3) For co-generation method, we take both the generated structure and
sequence, and predict structure given generated sequence with ESMFold,
where `scTM` is calculated between generated structure and ESMFold
predicted structure to evaluate overall quality. Considering that the
ground truth motif structure is given, we only utilize the ESMFold
predicted structure to calculate `motif-RMSD`.

![ Sequence-based, structure-based and co-generation evaluation pipeline
of motif-scaffolding.
](figures/motif_evaluation_pipeline.pdf){#fig:motif_evaluation
width="0.8\\linewidth"}

## Result of Each Problem

Tab. [\[tab:motif_each_problem\]](#tab:motif_each_problem){reference-type="ref"
reference="tab:motif_each_problem"} presents the result of each
motif-scaffolding problem. [DPLM-2]{.smallcaps} achieves the best
average success rate in each evaluation. Compared with ESM3,
[DPLM-2]{.smallcaps} shows better results in 12 problems in
co-generation evaluation and 10 problems in sequence-based evaluation.
Meanwhile, [DPLM-2]{.smallcaps} outperforms RFDiffusion in 14 problems
in structure-based evaluation. This demonstrates that
[DPLM-2]{.smallcaps} can achieve strong performance under various
evaluation methods.

We also find that taking the best result from 8 samples can bring
significant improvement compared to 1 sample, especially in terms of
success rate. In the co-generation evaluation, DPLM2 with sampling 8
times improves the success rate of most of the problems by a large
margin. We hypothesize that sampling eight times largely alleviates
errors caused by randomness in the sampling process, thereby producing a
more suitable scaffold for the given motif.

# Related Work

## Protein Language Models

There is growing interest in developing protein LMs at the scale of
evolution, such as the series of ESM [@rives2019esm; @lin2022esmfold],
TAPE [@rao2019evaluating], ProtTrans [@elnaggar2021prottrans],
PRoBERTa [@nambiar2020transforming], PMLM [@he2021pre],
ProteinLM [@xiao2021modeling], PLUS [@min2021pre], Adversarial Masked
LMs [@mcdermott2021adversarial], ProteinBERT [@brandes2022proteinbert],
CARP [@yang2022convolutions] in masked language modeling (MLM) paradigm,
ProtGPT2 [@ferruz2022protgpt2] in causal language modeling paradigm, and
several
others [@melnyk2022reprogramming; @madani2021deep; @unsal2022learning; @nourani2021tripletprot; @lu2020self; @sturmfels2020profile; @strodthoff2020udsmprot].
These protein language models exhibit remarkable generalization ability
on various downstream tasks and be able to capture evolutionary
information about secondary and tertiary structures from sequences
alone. Meanwhile, recent study shows these models' potency in revealing
protein structures [@lin2022esmfold], predicting the effect of sequence
variation on function [@meier2021language], antibody
infilling [@melnyk2022reprogramming] and many other general
purposes [@rives2019esm]. Simultaneously, @verkuil2022language
demonstrate that the large scale protein LMs can generate *de novo*
proteins by generalizing beyond natural proteins, both theoretically and
experimentally validating their hypothesis in exhaustive detail, in
which protein LMs demonstrate competency in designing protein structure
despite being exclusively trained on sequences.

## Protein Structure Generative Models

Diffusion models have become popular tools in structural biology for
protein generation, and their utility has been demonstrated across a
range of generative tasks in recent years. @trippe2022diffusion, along
with others, have introduced several diffusion model variants, each with
its unique approach. For instance, while some models focus on generating
the protein backbone by diffusing over protein coordinates, others, such
as those proposed by @wu2022high, target inter-residue angles.
@lin2023generating and @yim2023framediff have developed models that
handle both the position and orientation of residue frames.
RFDiffusion [@watson2023RFdiffusion] is a model that assists in
designing protein structures for specific functions, such as enzymes. It
is versatile in protein design and has been used to create therapeutic
proteins, with some designs being confirmed in the laboratory.
ProteinSGM [@lee2022proteinsgm] is a model that uses 2D matrices, which
represent the distances and angles between protein parts, to create 3D
protein structures for novel protein designs.
FoldingDiff [@wu2022protein] is a model that generates protein sequences
expected to fold into a specific structure. These sequences are verified
with prediction tools, although they have not been experimentally
confirmed yet. Chroma [@ingraham2023chroma] is a model designed for
creating large proteins and protein complexes, considering various
constraints like distances and symmetry. It transforms a collapsed
polymer into protein backbone and sequence more quickly than older
methods, thereby allowing for the efficient generation of large
structures. Multiflow [@campbell2024generative] develop mulitmodal flow
matching for protein structure-sequence
co-generation [@jin2021iterative; @shi2022protein].
ProtPardelle [@chu2024all] propose an all-atom generative approach for
co-design.

[^1]: This work was done during Xinyou's internship at ByteDance
    Research.

[^2]: Project Lead.

[^3]: Corresponding Author.
