"""Conditional DPLM-2 wrapper.

Subclasses ``MultimodalDiffusionProteinLanguageModel`` (the base DPLM-2) and
adds ProCALM-style parallel adapters at every selected encoder layer plus a
CFP-Gen-style discrete annotation embedder that feeds the adapters via a
``ProjectionMLP``.

The base DPLM-2 (``self.net``) stays frozen. Only the annotation embedders,
projection MLPs, and parallel adapters are trainable. At init the adapters
are near-zero (``weight_init=1e-5`` on ``linear_up``), so a forward pass on
a freshly-initialised ``ConditionalDPLM2`` produces logits identical to the
pretrained ``airkingbd/dplm2_650m`` within numerical tolerance.

Conditions API
--------------
``conditions`` is an optional dict passed to ``forward`` / ``generate``:

* ``conditions["annotations"]`` — ``{type_name: LongTensor[B, max_labels]}``
  with padding value ``-1``. Each row holds the multi-hot annotation IDs
  for one sample. Embedded via ``AnnotationEmbedder`` and ``ProjectionMLP``
  into one vector per annotation type per layer.
* ``conditions["external"]`` — optional ``FloatTensor[B, D_ext]`` carrying
  a pre-encoded condition (retrieved-fragment mean-pool, text-encoder CLS,
  ...). Fed through its own ``ProjectionMLP`` and an adapter.

For multi-condition composition, each entry in ``conditions`` maps to one
parallel adapter slot. Unused slots contribute zero (the adapter is
near-zero-init anyway).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import torch
import torch.nn as nn

from byprot.models import register_model
from byprot.models.dplm2.dplm2 import DPLM2Config, MultimodalDiffusionProteinLanguageModel
from byprot.models.dplm2.conditioning.annotation_embedder import AnnotationEmbedder
from byprot.models.dplm2.conditioning.parallel_adapter import (
    ParallelAdapterLayer,
    ProjectionMLP,
)


@dataclass
class AnnotationsConfig:
    # {type_name: vocab_size}. e.g. {"pfam": 20000, "go": 375, "ipr": 1154, "ec": 661}.
    vocab_sizes: Dict[str, int] = field(default_factory=dict)
    embed_dim: int = 128
    p_dropout_uncond: float = 0.1


@dataclass
class AdapterConfig:
    # Adapter placement and shape.
    layer_indices: Optional[List[int]] = None  # default: every layer
    c_s: int = 128  # adapter input dim (must match projection output)
    c_hidden: int = 16  # low-rank bottleneck dim
    low_rank_cond: bool = True
    low_rank_mlp: bool = True
    adapter_summation: bool = False
    dropout_rate: float = 0.1
    weight_init: float = 1e-5
    adapter_nlayers: int = 2
    # MLP projecting arbitrary-dim condition vectors to adapter input dim.
    proj_hidden_dim: int = 128


@dataclass
class ExternalMemoryConfig:
    # Optional external continuous condition (retrieved fragments / text encoder).
    enable: bool = False
    input_dim: int = 0  # must be set when enable=True


@dataclass
class ConditioningConfig:
    annotations: AnnotationsConfig = field(default_factory=AnnotationsConfig)
    adapter: AdapterConfig = field(default_factory=AdapterConfig)
    external: ExternalMemoryConfig = field(default_factory=ExternalMemoryConfig)
    freeze_base: bool = True


@dataclass
class ConditionalDPLM2Config(DPLM2Config):
    # All base DPLM-2 fields (tokenizer, net, lora, training_stage, ...) are
    # inherited. The conditioning config is the only addition.
    conditioning: ConditioningConfig = field(default_factory=ConditioningConfig)


@register_model("conditional_dplm2")
class ConditionalDPLM2(MultimodalDiffusionProteinLanguageModel):
    """DPLM-2 with parallel-adapter annotation conditioning.

    Construction flow:
      1. Load the base DPLM-2 (``airkingbd/dplm2_650m``) exactly as the
         parent class does — every pretrained weight is preserved.
      2. Build ``AnnotationEmbedder`` + one ``ProjectionMLP`` per annotation
         type (and one for the external-memory condition if enabled).
      3. Build one ``ParallelAdapterLayer`` per selected encoder layer. Each
         has ``n_parallel = num_annotation_types + int(external.enable)``
         adapters.
      4. If ``freeze_base``: set ``requires_grad=False`` on every base param.
    """

    _default_cfg = ConditionalDPLM2Config()

    def __init__(self, cfg, net=None):
        # Build the base DPLM-2 as usual. The parent constructor loads
        # pretrained weights and prepares the tokenizer. We pass the whole
        # cfg; the parent's _update_cfg merges keys that exist on
        # DPLM2Config (inherited by ConditionalDPLM2Config) and ignores the
        # extra ``conditioning`` key.
        super().__init__(cfg, net=net)

        # Now read the conditioning sub-config.
        cond_cfg = self.cfg.get("conditioning", ConditioningConfig()) if hasattr(self.cfg, "get") else getattr(self.cfg, "conditioning", ConditioningConfig())
        if isinstance(cond_cfg, dict):
            cond_cfg = ConditioningConfig(
                annotations=AnnotationsConfig(**cond_cfg.get("annotations", {})),
                adapter=AdapterConfig(**cond_cfg.get("adapter", {})),
                external=ExternalMemoryConfig(**cond_cfg.get("external", {})),
                freeze_base=cond_cfg.get("freeze_base", True),
            )
        self.cond_cfg = cond_cfg

        # 1. Annotation embedder + projection MLPs.
        self.annotation_types = list(cond_cfg.annotations.vocab_sizes.keys())
        if self.annotation_types:
            self.annotation_embedder = AnnotationEmbedder(
                vocab_sizes=cond_cfg.annotations.vocab_sizes,
                embed_dim=cond_cfg.annotations.embed_dim,
                p_dropout_uncond=cond_cfg.annotations.p_dropout_uncond,
            )
            self.annotation_projectors = nn.ModuleDict(
                {
                    name: ProjectionMLP(
                        input_dim=cond_cfg.annotations.embed_dim,
                        hidden_dim=cond_cfg.adapter.proj_hidden_dim,
                        output_dim=cond_cfg.adapter.c_s,
                    )
                    for name in self.annotation_types
                }
            )
        else:
            self.annotation_embedder = None
            self.annotation_projectors = None

        # 2. External memory projection (optional).
        if cond_cfg.external.enable:
            if cond_cfg.external.input_dim <= 0:
                raise ValueError(
                    "conditioning.external.input_dim must be > 0 when external.enable=True."
                )
            self.external_projector = ProjectionMLP(
                input_dim=cond_cfg.external.input_dim,
                hidden_dim=cond_cfg.adapter.proj_hidden_dim,
                output_dim=cond_cfg.adapter.c_s,
            )
        else:
            self.external_projector = None

        # 3. Per-layer parallel adapters.
        num_layers = self.net.config.num_hidden_layers
        if cond_cfg.adapter.layer_indices is None:
            layer_indices = list(range(num_layers))
        else:
            layer_indices = list(cond_cfg.adapter.layer_indices)
        self.layer_indices = layer_indices

        n_parallel = len(self.annotation_types) + int(cond_cfg.external.enable)
        if n_parallel == 0:
            raise ValueError(
                "ConditionalDPLM2 needs at least one condition source "
                "(annotation type or external memory)."
            )

        adapter_kwargs = dict(
            c_s=cond_cfg.adapter.c_s,
            c_h=self.net.config.hidden_size,
            c_hidden=cond_cfg.adapter.c_hidden,
            low_rank_cond=cond_cfg.adapter.low_rank_cond,
            low_rank_mlp=cond_cfg.adapter.low_rank_mlp,
            adapter_summation=cond_cfg.adapter.adapter_summation,
            dropout_rate=cond_cfg.adapter.dropout_rate,
            weight_init=cond_cfg.adapter.weight_init,
            adapter_nlayers=cond_cfg.adapter.adapter_nlayers,
        )
        # Register adapters as a flat list indexed by absolute layer index,
        # with None for layers that do not have an adapter. This list is
        # what ``ModifiedEsmEncoder.forward`` consumes via ``layer_adapters``.
        self.layer_adapters: List[Optional[ParallelAdapterLayer]] = [None] * num_layers
        for idx in layer_indices:
            self.layer_adapters[idx] = ParallelAdapterLayer(
                n_parallel=n_parallel, **adapter_kwargs
            )
        # Register as submodule so state_dict / .to() / .eval() propagate.
        self.adapters_module = nn.ModuleList(
            [a if a is not None else nn.Identity() for a in self.layer_adapters]
        )

        # 4. Freeze base if requested.
        if cond_cfg.freeze_base:
            for p in self.net.parameters():
                p.requires_grad_(False)
            # Tokenizer / struct_tokenizer are not trainable either; leave as is.

    # ------------------------------------------------------------------
    # Condition encoding
    # ------------------------------------------------------------------

    def _encode_conditions(
        self, conditions: Optional[Dict[str, torch.Tensor]], force_unconditional: bool = False
    ) -> Optional[List[List[torch.Tensor]]]:
        """Build the per-layer list of parallel condition vectors.

        Returns ``layer_adapter_inputs`` of length ``num_hidden_layers``,
        where each entry is either ``None`` (no adapter at that layer) or a
        list of ``n_parallel`` tensors each of shape ``[B, c_s]``. The same
        condition vectors are reused at every adapter-equipped layer, which
        matches ProCALM (one condition embedding per sample, broadcast to
        all layers).
        """
        if conditions is None and self.external_projector is None:
            return None
        conditions = conditions or {}

        # Annotation branch.
        if (
            self.annotation_embedder is not None
            and "annotations" in conditions
            and conditions["annotations"] is not None
        ):
            ann_emb = self.annotation_embedder(
                conditions["annotations"], force_unconditional=force_unconditional
            )
            per_type_vectors = [
                self.annotation_projectors[name](ann_emb[name])
                for name in self.annotation_types
            ]
        else:
            per_type_vectors = []

        # External-memory branch.
        if self.external_projector is not None and "external" in conditions:
            ext_vec = self.external_projector(conditions["external"])
            per_type_vectors = per_type_vectors + [ext_vec]

        if not per_type_vectors:
            return None

        # Broadcast: same condition vectors at every adapter-equipped layer.
        num_layers = self.net.config.num_hidden_layers
        layer_inputs: List[Optional[List[torch.Tensor]]] = [None] * num_layers
        for idx in self.layer_indices:
            layer_inputs[idx] = list(per_type_vectors)
        return layer_inputs

    # ------------------------------------------------------------------
    # Forward overrides
    # ------------------------------------------------------------------
    #
    # The base ``forward_decoder`` (dplm2.py:477) calls
    # ``self.forward(input_ids=output_tokens)`` with no conditions kwarg.
    # Rather than reimplement that 70-line body (modality logit masking,
    # top-p filtering, annealing, history), we use a per-instance stash
    # ``_active_layer_adapter_inputs`` that ``forward`` consults whenever
    # no explicit ``conditions`` kwarg is passed. ``generate`` populates
    # this stash once at entry (conditions do not depend on the noisy
    # tokens) and the parent ``forward_decoder`` / ``generate`` loop is
    # reused unchanged.

    _active_layer_adapter_inputs: Optional[List] = None

    def forward(
        self,
        input_ids,
        conditions: Optional[Dict[str, torch.Tensor]] = None,
        **kwargs,
    ):
        # Resolve the per-layer adapter inputs.
        # Three sources, in priority order:
        #   1. Explicit ``layer_adapter_inputs`` kwarg (test paths).
        #   2. ``conditions=`` kwarg → freshly encoded.
        #   3. The active stash (set by ``generate`` for the duration of a
        #      sampling run).
        if "layer_adapter_inputs" in kwargs:
            layer_adapter_inputs = kwargs.pop("layer_adapter_inputs")
            kwargs.pop("layer_adapters", None)
        else:
            if conditions is not None:
                layer_adapter_inputs = self._encode_conditions(conditions)
            else:
                layer_adapter_inputs = self._active_layer_adapter_inputs

        # We can NOT just call ``super().forward(...)``: the parent
        # ``DPLM2.forward`` (dplm2.py:263-268) hardcodes the kwargs it
        # forwards to ``self.net(...)`` and would drop ``layer_adapters`` /
        # ``layer_adapter_inputs``. Replicate the small parent body here and
        # pass the adapter kwargs through explicitly.
        single_modality = kwargs.pop("single_modality", None)
        input_mask = input_ids.ne(self.pad_id)
        type_ids = self.get_modality_type(input_ids)
        L = input_ids.shape[1]
        num_heads = self.net.config.num_attention_heads
        attention_bias = self.net.esm.get_extended_attention_mask(
            input_mask, input_ids.shape
        ).repeat(1, num_heads, L, 1)
        if single_modality is not None:
            struct_bias, aa_bias = attention_bias.chunk(2, dim=-2)
            struct_bias[single_modality, :, :, L // 2:] = float("-inf")
            aa_bias[single_modality, :, :, : L // 2] = float("-inf")
            attention_bias = torch.concat([struct_bias, aa_bias], dim=-2)
        input_embeds = self.net.esm.embeddings(input_ids, attention_mask=input_mask)
        outputs = self.net(
            input_ids=input_ids,
            inputs_embeds=input_embeds,
            attention_mask=attention_bias,
            type_ids=type_ids,
            layer_adapters=self.layer_adapters,
            layer_adapter_inputs=layer_adapter_inputs,
        )
        return outputs

    def compute_loss(
        self,
        batch,
        weighting: str = "linear",
        conditions: Optional[Dict[str, torch.Tensor]] = None,
    ):
        """Training-time loss with annotation conditions active.

        The parent ``compute_loss`` (dplm2.py:394) calls
        ``self.forward(input_ids=x_t, single_modality=...)`` with no
        conditions kwarg. To make conditions flow through during training we
        stash the encoded condition vectors on ``_active_layer_adapter_inputs``
        for the duration of the call — the same mechanism ``generate`` uses.
        The parent's inner ``self.forward(...)`` then hits the ``else`` branch
        of our ``forward`` override and picks the stash up.
        """
        prev = self._active_layer_adapter_inputs
        self._active_layer_adapter_inputs = self._encode_conditions(conditions)
        try:
            return super().compute_loss(batch, weighting=weighting)
        finally:
            self._active_layer_adapter_inputs = prev

    def generate(
        self,
        input_tokens,
        conditions: Optional[Dict[str, torch.Tensor]] = None,
        max_iter=None,
        temperature=1.0,
        partial_masks=None,
        unmasking_strategy="stochastic1.0",
        sampling_strategy="annealing@2.0:0.1",
        keep_history=False,
    ):
        """Generation with fixed ``conditions`` applied at every step.

        Condition vectors are encoded once (they do not depend on the noisy
        tokens) and stashed on ``self._active_layer_adapter_inputs`` for the
        duration of the run. The parent ``generate`` body is reused
        verbatim via ``super().generate(...)``.
        """
        # Encode conditions once, set the stash, delegate to parent.
        prev_active = self._active_layer_adapter_inputs
        self._active_layer_adapter_inputs = self._encode_conditions(conditions)
        try:
            return super().generate(
                input_tokens=input_tokens,
                max_iter=max_iter,
                temperature=temperature,
                partial_masks=partial_masks,
                unmasking_strategy=unmasking_strategy,
                sampling_strategy=sampling_strategy,
                keep_history=keep_history,
            )
        finally:
            self._active_layer_adapter_inputs = prev_active
