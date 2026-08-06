"""Discrete-annotation embedder for conditional DPLM-2.

Borrows the design of CFP-Gen's ``FuncTagEmbedder``
(``src/byprot/models/lm/esm_cfpgen.py:210-223`` in the cfpgen repo) but
outputs a fixed-width continuous vector that feeds into ProCALM's
``ProjectionMLP``. This bridges discrete multi-hot annotation labels
(Pfam / GO / IPR / EC IDs) to the parallel-adapter interface.

Each annotation type has its own ``nn.Embedding`` table. For a sample with
multiple labels of a given type (multi-hot), the embeddings are summed.
Vectors from different types are kept separate so that the parallel
adapters can treat them as independent conditions. For classifier-free
guidance, an extra "unconditional" learnable row is appended to each table
and selected stochastically with probability ``p_dropout_uncond``.
"""

from typing import Dict, List, Optional

import torch
import torch.nn as nn


class AnnotationEmbedder(nn.Module):
    """Embed multi-hot annotation labels per type into fixed-width vectors.

    Args:
        vocab_sizes: ``{type_name: num_labels}`` e.g.
            ``{"pfam": 20000, "go": 375, "ipr": 1154, "ec": 661}``.
        embed_dim: width of each per-type embedding vector. The same dim
            is used for every type so they can feed into a shared
            ``ProjectionMLP``.
        p_dropout_uncond: probability of replacing the condition with the
            learned "unconditional" token (classifier-free guidance dropout).
    """

    def __init__(
        self,
        vocab_sizes: Dict[str, int],
        embed_dim: int = 128,
        p_dropout_uncond: float = 0.1,
    ) -> None:
        super().__init__()
        if not vocab_sizes:
            raise ValueError("vocab_sizes must be non-empty.")
        self.vocab_sizes = dict(vocab_sizes)
        self.embed_dim = embed_dim
        self.p_dropout_uncond = p_dropout_uncond

        # +1 extra row per type for the learned "unconditional" embedding (CFG).
        self.tables = nn.ModuleDict(
            {
                name: nn.Embedding(num_labels + 1, embed_dim)
                for name, num_labels in self.vocab_sizes.items()
            }
        )
        self.uncond_idx = {name: num_labels for name, num_labels in self.vocab_sizes.items()}

    @property
    def type_names(self) -> List[str]:
        return list(self.vocab_sizes.keys())

    def forward(
        self,
        labels: Dict[str, torch.Tensor],
        force_unconditional: bool = False,
    ) -> Dict[str, torch.Tensor]:
        """Embed each annotation type into a ``[B, embed_dim]`` vector.

        Args:
            labels: ``{type_name: LongTensor[B, max_labels]}`` with padding
                value ``-1``. Each row holds the multi-hot label indices for
                one sample.
            force_unconditional: if True, return the unconditional token
                for every sample (used for the unconditional side of CFG).

        Returns:
            ``{type_name: FloatTensor[B, embed_dim]}``.
        """
        out: Dict[str, torch.Tensor] = {}
        device = next(self.parameters()).device

        for name, table in self.tables.items():
            if name not in labels:
                raise KeyError(f"Missing labels for annotation type '{name}'.")
            x = labels[name].to(device=device, dtype=torch.long)  # [B, max_labels]
            if x.dim() != 2:
                raise ValueError(
                    f"Expected labels['{name}'] to be 2D [B, max_labels], got shape {tuple(x.shape)}"
                )

            bsz = x.shape[0]
            if force_unconditional:
                idx = torch.full(
                    (bsz,), self.uncond_idx[name], device=device, dtype=torch.long
                )
                out[name] = table(idx)
                continue

            # Per-sample CFG dropout: replace the whole label set with the
            # unconditional token for a fraction of the batch.
            if self.training and self.p_dropout_uncond > 0:
                drop = torch.rand(bsz, device=device) < self.p_dropout_uncond
            else:
                drop = torch.zeros(bsz, device=device, dtype=torch.bool)

            # Map -1 padding to the unconditional index so it sums in zero
            # contribution only when we explicitly want it; but a cleaner
            # approach is to mask the embedding sum and add the uncond token
            # only for dropped samples.
            valid = x >= 0  # [B, max_labels]
            # Use a safe index for lookup; padding positions will be masked.
            safe_x = x.clamp(min=0)
            emb = table(safe_x)  # [B, max_labels, embed_dim]
            mask = valid.unsqueeze(-1).to(emb.dtype)
            summed = (emb * mask).sum(dim=1)  # [B, embed_dim]

            uncond = table(
                torch.full((bsz,), self.uncond_idx[name], device=device, dtype=torch.long)
            )
            out[name] = torch.where(drop.unsqueeze(-1), uncond, summed)

        return out
