# Copyright (c) 2024 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: Apache-2.0

import math
from dataclasses import dataclass, field

import torch
import torch.nn as nn
from omegaconf import OmegaConf

from byprot.models import register_model
from byprot.models.dplm.dplm import DPLMConfig, DiffusionProteinLanguageModel
from byprot.models.utils import (
    sample_from_categorical,
    stochastic_sample_from_categorical,
)


@dataclass
class PfamPrefixDPLMConfig(DPLMConfig):
    num_families: int = field(default=0)
    prefix_len: int = field(default=8)
    cond_dim: int = field(default=256)
    prefix_dropout: float = field(default=0.0)
    freeze_dplm: bool = field(default=True)


@register_model("pfam_prefix_dplm")
@register_model("conditional_dplm")
class PfamPrefixDiffusionProteinLanguageModel(DiffusionProteinLanguageModel):
    """DPLM with learned Pfam-family soft prefixes.

    The base DPLM still sees only ordinary sequence tokens at the public API
    boundary. Internally, family IDs are projected to K soft prefix embeddings;
    logits are sliced back to protein positions so the existing criterion and
    generation loop do not need to know about prefix positions.
    """

    _default_cfg = PfamPrefixDPLMConfig()

    def __init__(self, cfg, net=None):
        super().__init__(cfg, net=net)

        if self.cfg.num_families <= 0:
            raise ValueError("pfam_prefix_dplm requires cfg.num_families > 0")
        if self.cfg.prefix_len <= 0:
            raise ValueError("pfam_prefix_dplm requires cfg.prefix_len > 0")

        hidden_size = self.net.config.hidden_size
        self.family_embedding = nn.Embedding(self.cfg.num_families, self.cfg.cond_dim)
        self.prefix_projector = nn.Sequential(
            nn.Linear(self.cfg.cond_dim, hidden_size),
            nn.GELU(),
            nn.Dropout(self.cfg.prefix_dropout),
            nn.Linear(hidden_size, self.cfg.prefix_len * hidden_size),
        )

        if self.cfg.freeze_dplm:
            for param in self.net.parameters():
                param.requires_grad = False

    def _update_cfg(self, cfg):
        self.cfg = OmegaConf.merge(self._default_cfg, cfg)

    def _get_family_ids(self, batch_or_ids):
        if torch.is_tensor(batch_or_ids):
            return batch_or_ids.long()

        for key in ("family_ids", "family_id", "pfam_family_ids"):
            if key in batch_or_ids:
                family_ids = batch_or_ids[key]
                if not torch.is_tensor(family_ids):
                    family_ids = torch.as_tensor(family_ids)
                return family_ids.to(batch_or_ids["targets"].device).long()

        raise KeyError(
            "pfam_prefix_dplm batches must include integer family IDs under "
            "'family_ids', 'family_id', or 'pfam_family_ids'"
        )

    def _build_prefix(self, family_ids):
        prefix = self.prefix_projector(self.family_embedding(family_ids))
        return prefix.view(family_ids.size(0), self.cfg.prefix_len, -1)

    def _forward_with_prefix(self, input_ids, family_ids, return_last_hidden_state=False):
        if not torch.is_tensor(family_ids):
            family_ids = torch.as_tensor(family_ids, device=input_ids.device)
        prefix_embeds = self._build_prefix(family_ids.to(input_ids.device))
        token_embeds = self.net.esm.embeddings.word_embeddings(input_ids)
        inputs_embeds = torch.cat([prefix_embeds, token_embeds], dim=1)

        prefix_attention_mask = torch.ones(
            input_ids.size(0), self.cfg.prefix_len, device=input_ids.device, dtype=torch.bool
        )
        attention_mask = torch.cat([prefix_attention_mask, input_ids.ne(self.pad_id)], dim=1)
        prefix_input_ids = torch.full(
            (input_ids.size(0), self.cfg.prefix_len),
            self.bos_id,
            device=input_ids.device,
            dtype=input_ids.dtype,
        )
        prefixed_input_ids = torch.cat([prefix_input_ids, input_ids], dim=1)

        outputs = self.net.esm(
            input_ids=prefixed_input_ids,
            inputs_embeds=inputs_embeds,
            attention_mask=attention_mask,
        )
        sequence_output = outputs[0]
        logits = self.net.lm_head(sequence_output)

        protein_logits = logits[:, self.cfg.prefix_len :]
        if return_last_hidden_state:
            protein_hidden = sequence_output[:, self.cfg.prefix_len :]
            return protein_logits, protein_hidden
        return protein_logits

    def forward(
        self,
        input_ids,
        family_ids=None,
        return_last_hidden_state=False,
        **kwargs,
    ):
        if family_ids is None:
            family_ids = kwargs.get("family_id", kwargs.get("pfam_family_ids"))
        if family_ids is None:
            raise ValueError("pfam_prefix_dplm.forward requires family_ids")
        return self._forward_with_prefix(
            input_ids,
            family_ids,
            return_last_hidden_state=return_last_hidden_state,
        )

    def compute_loss(self, batch, weighting="constant"):
        target = batch["targets"]
        family_ids = self._get_family_ids(batch)

        t1, t2 = torch.randint(
            1,
            self.cfg.num_diffusion_timesteps + 1,
            (2 * target.size(0),),
            device=target.device,
        ).chunk(2)

        if self.cfg.rdm_couple:
            x_t, t, loss_mask = list(
                self.q_sample_coupled(
                    target,
                    t1,
                    t2,
                    maskable_mask=self.get_non_special_symbol_mask(target),
                ).values()
            )
            target = target.repeat(2, 1)
            family_ids = family_ids.repeat(2)
        else:
            x_t, t, loss_mask = list(
                self.q_sample(
                    target,
                    t1,
                    maskable_mask=self.get_non_special_symbol_mask(target),
                ).values()
            )

        logits = self.forward(x_t, family_ids=family_ids)

        num_timesteps = self.cfg.num_diffusion_timesteps
        weight = {
            "linear": num_timesteps - (t - 1),
            "constant": num_timesteps * torch.ones_like(t),
        }[weighting][:, None].float() / num_timesteps

        return logits, target, loss_mask, weight

    def forward_encoder(self, input_tokens, family_ids=None, **kwargs):
        return {"family_ids": family_ids}

    def forward_decoder(
        self,
        prev_decoder_out,
        encoder_out=None,
        need_attn_weights=False,
        partial_masks=None,
        sampling_strategy="gumbel_argmax",
        disable_resample=True,
        resample_ratio=0.25,
    ):
        output_tokens = prev_decoder_out["output_tokens"].clone()
        output_scores = prev_decoder_out["output_scores"].clone()
        step, max_step = prev_decoder_out["step"], prev_decoder_out["max_step"]
        temperature = prev_decoder_out["temperature"]
        history = prev_decoder_out["history"]
        family_ids = None if encoder_out is None else encoder_out.get("family_ids")

        output_masks = self.get_non_special_symbol_mask(
            output_tokens, partial_masks=partial_masks
        )

        logits, hidden_states = self.forward(
            output_tokens,
            family_ids=family_ids,
            return_last_hidden_state=True,
        )
        attentions = None

        if logits.dtype != output_scores.dtype:
            logits = logits.type_as(output_scores)

        logits[..., self.mask_id] = -math.inf
        logits[..., self.x_id] = -math.inf
        logits[..., self.pad_id] = -math.inf
        logits[..., self.bos_id] = -math.inf
        logits[..., self.eos_id] = -math.inf

        if sampling_strategy == "vanilla":
            _tokens, _scores = sample_from_categorical(logits, temperature=temperature)
        elif sampling_strategy == "argmax":
            _scores, _tokens = logits.max(-1)
        elif sampling_strategy == "gumbel_argmax":
            _tokens, _scores = stochastic_sample_from_categorical(
                logits, temperature=0.0, noise_scale=1.0
            )
        else:
            raise NotImplementedError

        output_tokens.masked_scatter_(output_masks, _tokens[output_masks])
        output_scores.masked_scatter_(output_masks, _scores[output_masks])

        history.append(output_tokens.clone())

        return dict(
            output_tokens=output_tokens,
            output_scores=output_scores,
            attentions=attentions,
            step=step + 1,
            max_step=max_step,
            history=history,
            hidden_states=hidden_states,
        )

    def generate(
        self,
        input_tokens,
        family_ids,
        tokenizer=None,
        max_iter=None,
        temperature=None,
        partial_masks=None,
        sampling_strategy="gumbel_argmax",
        disable_resample=False,
        resample_ratio=0.25,
    ):
        tokenizer = tokenizer
        max_iter = max_iter
        temperature = temperature

        encoder_out = self.forward_encoder(input_tokens, family_ids=family_ids)
        initial_output_tokens, initial_output_scores = self.initialize_output_tokens(
            input_tokens, encoder_out=encoder_out, partial_masks=partial_masks
        )
        prev_decoder_out = dict(
            output_tokens=initial_output_tokens,
            output_scores=initial_output_scores,
            output_masks=None,
            attentions=None,
            step=0,
            max_step=max_iter,
            history=[initial_output_tokens.clone()],
            temperature=temperature,
        )

        prev_decoder_out["output_masks"] = self.get_non_special_symbol_mask(
            prev_decoder_out["output_tokens"], partial_masks=partial_masks
        )

        from tqdm import tqdm

        for step in tqdm(range(max_iter), desc="Decoding"):
            with torch.no_grad():
                decoder_out = self.forward_decoder(
                    prev_decoder_out=prev_decoder_out,
                    encoder_out=encoder_out,
                    partial_masks=partial_masks,
                    sampling_strategy=sampling_strategy,
                    disable_resample=disable_resample,
                    resample_ratio=resample_ratio,
                )

            non_special_sym_mask = self.get_non_special_symbol_mask(
                prev_decoder_out["output_tokens"], partial_masks=partial_masks
            )
            output_masks, result_tokens, result_scores = self._reparam_decoding(
                output_tokens=prev_decoder_out["output_tokens"].clone(),
                output_scores=prev_decoder_out["output_scores"].clone(),
                cur_tokens=decoder_out["output_tokens"].clone(),
                cur_scores=decoder_out["output_scores"].clone(),
                decoding_strategy="reparam-uncond-deterministic-linear",
                xt_neq_x0=prev_decoder_out["output_masks"],
                non_special_sym_mask=non_special_sym_mask,
                t=step + 1,
                max_step=max_iter,
                noise=self.mask_id,
            )

            prev_decoder_out.update(
                output_masks=output_masks,
                output_tokens=result_tokens,
                output_scores=result_scores,
                step=step + 1,
                history=decoder_out["history"],
            )

        return prev_decoder_out["output_tokens"]
