"""Parallel adapter conditioning for DPLM-2.

Ported from ProCALM's adapter design (``progen_conditional/model/adapter.py``)
but reimplemented cleanly to fit DPLM-2's ``ModifiedEsmLayer`` residual stream.

The adapter is a low-rank bottleneck module attached in parallel at each
transformer block. It takes the post-block hidden state ``h`` and a projected
conditioning vector ``s`` and produces a residual update that is summed into
the encoder's residual stream before the next layer. With ``weight_init``
small on the up-projection, the adapter is near-identity at initialisation,
so a frozen pretrained DPLM-2 produces bit-identical outputs at step 0.
"""

from typing import List, Optional

import torch
import torch.nn as nn


class ProjectionMLP(nn.Module):
    """Project an arbitrary-dim condition vector to the adapter input dim.

    Mirrors ProCALM ``ProjectionMLP``. This is the "conditioning encoder":
    any condition source (one-hot EC, learned Pfam embeddings, retrieved-
    fragment embeddings, text-encoder outputs) is reduced to a fixed-width
    vector that the parallel adapter consumes.
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 128,
        output_dim: int = 128,
        num_layers: int = 2,
        bias: bool = True,
    ) -> None:
        super().__init__()
        if num_layers < 2:
            raise ValueError("num_layers must be >= 2")
        self.W_in = nn.Linear(input_dim, hidden_dim, bias=bias)
        self.W_inter = nn.ModuleList(
            [nn.Linear(hidden_dim, hidden_dim, bias=bias) for _ in range(num_layers - 2)]
        )
        self.W_out = nn.Linear(hidden_dim, output_dim, bias=bias)
        self.act = nn.ReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.act(self.W_in(x))
        for layer in self.W_inter:
            x = self.act(layer(x))
        return self.W_out(x)


class AdapterLayer(nn.Module):
    """Single low-rank bottleneck adapter.

    Flow (ProCALM ``AdapterLayer``):
        h -> LayerNorm -> Dropout -> [down-project] -> concat/sum with s
            -> MLP -> ReLU -> up-projection (near-zero init)
    The up-projection is initialised with small Gaussian weights and zero
    bias so the adapter contribution at init is ~0.

    Args:
        c_s: dim of the projected condition vector (adapter input dim).
        c_h: hidden size of the host language model (1280 for DPLM-2 650M).
        c_hidden: low-rank dim of the adapter bottleneck.
        low_rank_cond: if True, the condition is concatenated to the
            low-rank hidden state inside the bottleneck (ProCALM default).
            If False, the condition is concatenated to ``h`` before the
            down-projection (larger projection, rarely used).
        adapter_summation: if True, sum the condition into the low-rank
            hidden state instead of concatenating; overrides ``c_hidden``
            to ``c_s`` along the inner dim.
        dropout_rate: dropout on both ``h`` and ``s``.
        weight_init: std of the Gaussian used to init ``linear_up``.
        adapter_nlayers: depth of the inner MLP.
    """

    def __init__(
        self,
        c_s: int = 128,
        c_h: int = 1280,
        c_hidden: int = 16,
        low_rank_cond: bool = True,
        low_rank_mlp: bool = True,
        adapter_summation: bool = False,
        dropout_rate: float = 0.1,
        weight_init: float = 1e-5,
        adapter_nlayers: int = 2,
    ) -> None:
        super().__init__()
        self.low_rank_cond = low_rank_cond
        self.low_rank_mlp = low_rank_mlp
        self.adapter_summation = adapter_summation
        if adapter_summation:
            c_hidden = c_s

        self.h_dropout = nn.Dropout(dropout_rate)
        self.h_ln = nn.LayerNorm(c_h)
        self.s_dropout = nn.Dropout(dropout_rate)
        self.s_ln = nn.LayerNorm(c_s)

        if low_rank_cond:
            c_down_in = c_h
            c_down_out = c_hidden
            if not adapter_summation:
                # reserve room in the bottleneck for the condition vector
                c_hidden = c_hidden + c_s
        else:
            c_down_in = c_h + c_s
            c_down_out = c_hidden

        self.linear_down = nn.Linear(c_down_in, c_down_out, bias=True)
        self.linear_up = nn.Linear(c_hidden, c_h, bias=True)

        # Near-zero init on the up-projection -> adapter is identity-like at start.
        self.linear_up.weight.data.normal_(mean=0.0, std=weight_init)
        self.linear_up.bias.data.zero_()

        self.act = nn.ReLU()

        if low_rank_mlp:
            self.mlp = _MLP(
                c_hidden,
                c_hidden * 2,
                c_hidden,
                num_layers=adapter_nlayers,
            )
        else:
            self.mlp = None

    def forward(self, h: torch.Tensor, s: torch.Tensor) -> torch.Tensor:
        # Cast to fp32 before norm for parity with ProCALM, then back to input dtype.
        h = self.h_dropout(self.h_ln(h.float())).to(h.dtype)
        s = self.s_dropout(self.s_ln(s))

        # ``s`` is per-sample [B, c_s]; broadcast across the sequence dim so
        # it can be concatenated/summed with the per-position hidden state
        # [B, L, *]. If the caller already passed a per-position condition,
        # leave it as-is.
        if s.dim() == h.dim() - 1:
            s = s.unsqueeze(1).expand(*h.shape[:-1], s.shape[-1])
        elif s.dim() != h.dim():
            raise ValueError(
                f"Condition s (dim={s.dim()}) and hidden h (dim={h.dim()}) "
                "must either match, or s must be one rank lower than h."
            )

        if not self.low_rank_cond:
            x = torch.cat([h, s], dim=-1)
        else:
            x = h
        x = self.linear_down(x)
        if self.low_rank_cond:
            if self.adapter_summation:
                x = x + s
            else:
                x = torch.cat([s, x], dim=-1)
        if self.mlp is not None:
            x = self.mlp(x)
        x = self.act(x)
        x = self.linear_up(x)
        return x


class _MLP(nn.Module):
    """Inner MLP used inside ``AdapterLayer`` (ProCALM ``MLP``)."""

    def __init__(
        self,
        c_in: int,
        c_hidden: int,
        c_out: int,
        num_layers: int,
        bias: bool = True,
    ) -> None:
        super().__init__()
        if num_layers < 2:
            raise ValueError("num_layers must be >= 2")
        self.W_in = nn.Linear(c_in, c_hidden, bias=bias)
        self.W_inter = nn.ModuleList(
            [nn.Linear(c_hidden, c_hidden, bias=bias) for _ in range(num_layers - 2)]
        )
        self.W_out = nn.Linear(c_hidden, c_out, bias=bias)
        self.act = nn.ReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.act(self.W_in(x))
        for layer in self.W_inter:
            x = self.act(layer(x))
        return self.W_out(x)


class ParallelAdapterLayer(nn.Module):
    """Stack of independent ``AdapterLayer``s that share ``h`` but get distinct
    condition vectors. Outputs are summed to produce a single residual update.

    This is ProCALM's multi-condition primitive: each condition (Pfam tag,
    GO term, retrieved fragment, ...) flows through its own adapter and the
    updates are summed. Conditions stay separable, unlike CFP-Gen's
    pre-summed single adaLN embedding.
    """

    def __init__(
        self,
        n_parallel: int = 1,
        **adapter_kwargs,
    ) -> None:
        super().__init__()
        self.adapters = nn.ModuleList(
            [AdapterLayer(**adapter_kwargs) for _ in range(n_parallel)]
        )

    def forward(
        self, h: torch.Tensor, s_parallel: List[torch.Tensor]
    ) -> torch.Tensor:
        if len(s_parallel) != len(self.adapters):
            raise ValueError(
                f"Got {len(s_parallel)} condition vectors for "
                f"{len(self.adapters)} parallel adapters."
            )
        updates = [adapter(h, s) for adapter, s in zip(self.adapters, s_parallel)]
        return torch.stack(updates, dim=0).sum(dim=0)
