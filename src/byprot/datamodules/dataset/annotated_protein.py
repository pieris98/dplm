"""Annotated protein dataset for ConditionalDPLM2 training.

Extends DPLM-2's :class:`TokenizedProteinDataset` and :class:`DPLM2Collater`
to also load and collate CFP-Gen-style function annotations
(``ipr_mapped``, ``go_f_mapped``) alongside the standard struct + aa tokens.

The on-disk source is a single parquet file (schema-compatible with DPLM-2's
``pdb_swissprot`` parquet) with two extra columns:
  - ``ipr_mapped``: list[int] of InterPro label IDs (vocab 1154)
  - ``go_f_mapped``: list[int] of GO molecular-function label IDs (vocab 375)

Collator output mirrors CFP-Gen's format: multi-hot lists padded with -1.
"""
import os
from typing import List

import numpy as np
import pyarrow.parquet as pq
import torch
from datasets import Dataset
from torch.utils.data import Dataset as TorchDataset

from byprot.datamodules.dataset.tokenized_protein import DPLM2Tokenizer


class AnnotatedProteinDataset(TorchDataset):
    """Reads a single parquet of annotated DPLM-2 examples.

    Each row must have: ``struct_seq``, ``aa_seq``, ``length``,
    ``ipr_mapped`` (list[int]), ``go_f_mapped`` (list[int]).
    Optional: ``pdb_name``, ``uniprot_id``.
    """

    def __init__(
        self,
        parquet_path: str,
        vocab_file: str = "airkingbd/dplm2_650m",
        max_len: int = 512,
        split_seed: int = 0,
        val_ratio: float = 0.05,
        split: str = "train",
    ):
        super().__init__()
        self.parquet_path = parquet_path
        self.max_len = max_len
        self.split = split
        self.tokenizer = DPLM2Tokenizer.from_pretrained(vocab_file)

        table = pq.read_table(parquet_path)
        # Filter out rows with empty annotation lists — they can't contribute
        # to function-conditioned training.
        n_before = table.num_rows
        ipr = table.column("ipr_mapped").to_pylist()
        go = table.column("go_f_mapped").to_pylist()
        keep_mask = [bool(a) or bool(b) for a, b in zip(ipr, go)]
        keep_idx = [i for i, k in enumerate(keep_mask) if k]
        table = table.take(keep_idx)
        n_after = table.num_rows
        print(
            f"  [AnnotatedProteinDataset] loaded {parquet_path}: "
            f"{n_before} -> {n_after} rows after dropping unannotated"
        )
        self.data = table.to_pylist()

        # Simple deterministic train/val split. There's only one parquet for
        # now (the 36K missing-targets set); we carve a val slice out of it.
        rng = np.random.default_rng(split_seed)
        perm = rng.permutation(len(self.data))
        n_val = max(1, int(len(self.data) * val_ratio))
        val_idx = set(perm[:n_val].tolist())
        if split == "train":
            self._idx_map = [i for i in range(len(self.data)) if i not in val_idx]
        elif split in ("valid", "val", "test"):
            self._idx_map = sorted(val_idx)
        else:
            raise ValueError(f"Unknown split: {split}")
        print(f"    split={split!r}: {len(self._idx_map)} samples")

    def __len__(self):
        return len(self._idx_map)

    def get_metadata_lens(self):
        return [self.data[i]["length"] for i in self._idx_map]

    def __getitem__(self, idx):
        row = self.data[self._idx_map[idx]]
        max_len = min(self.max_len, row["length"])

        # Struct tokens: comma-separated string -> char string with cls/eos.
        struct_tokens = row["struct_seq"].split(",")
        if len(struct_tokens) - max_len > 0:
            start = np.random.choice(len(struct_tokens) - max_len)
            stop = start + max_len
        else:
            start, stop = 0, len(struct_tokens)
        struct_tokens = "".join(struct_tokens[start:stop])
        struct_tokens = (
            self.tokenizer.struct_cls_token + struct_tokens + self.tokenizer.struct_eos_token
        )

        aatype_tokens = row["aa_seq"]
        if len(aatype_tokens) - max_len > 0:
            aatype_tokens = aatype_tokens[start:stop]
        aatype_tokens = (
            self.tokenizer.aa_cls_token + aatype_tokens + self.tokenizer.aa_eos_token
        )

        return_dict = {
            "struct_tokens": struct_tokens,
            "aatype_tokens": aatype_tokens,
            "length": max_len + 2,
            "ipr_mapped": list(row.get("ipr_mapped") or []),
            "go_f_mapped": list(row.get("go_f_mapped") or []),
        }
        if "pdb_name" in row:
            return_dict["pdb_name"] = row["pdb_name"]
        if "uniprot_id" in row:
            return_dict["uniprot_id"] = row["uniprot_id"]
        return return_dict


class AnnotatedProteinCollater:
    """Collator that produces a DPLM-2 batch + an ``annotations`` dict.

    The ``annotations`` dict has the format expected by ``ConditionalDPLM2``:
    ``{type_name: LongTensor[B, max_labels]}`` with padding value ``-1``.
    """

    def __init__(self, tokenizer: DPLM2Tokenizer):
        self.tokenizer = tokenizer

    def _pad_labels(self, lists: List[List[int]], pad: int = -1):
        """Pad a list of variable-length label lists to a LongTensor."""
        max_len = max((len(lst) for lst in lists), default=0)
        max_len = max(max_len, 1)  # avoid 0-length dim
        out = torch.full((len(lists), max_len), pad, dtype=torch.long)
        for i, lst in enumerate(lists):
            if len(lst) > 0:
                out[i, : len(lst)] = torch.tensor(lst, dtype=torch.long)
        return out

    def __call__(self, raw_batch):
        struct_tokens_list = [s["struct_tokens"] for s in raw_batch]
        # ``padding=True`` is more robust than ``padding="longest"`` for
        # the EsmTokenizer subclass used by DPLM-2 (the latter sometimes
        # fails to pad when sequences have different lengths, causing the
        # ``return_tensors="pt"`` conversion to choke).
        batch_struct = self.tokenizer.batch_encode_plus(
            struct_tokens_list, add_special_tokens=False,
            padding=True, return_tensors="pt",
        )
        batch_struct = {
            "targets": batch_struct["input_ids"],
            "attention_mask": batch_struct["attention_mask"].bool(),
        }

        aatype_list = [s["aatype_tokens"] for s in raw_batch]
        batch_aatype = self.tokenizer.batch_encode_plus(
            aatype_list, add_special_tokens=False,
            padding=True, return_tensors="pt",
        )
        batch_aatype = {
            "targets": batch_aatype["input_ids"],
            "attention_mask": batch_aatype["attention_mask"].bool(),
        }

        batch = {"struct_tokens": batch_struct, "aatype_tokens": batch_aatype}

        # Annotations — only include types that exist in the data.
        ipr_lists = [s.get("ipr_mapped", []) for s in raw_batch]
        go_lists = [s.get("go_f_mapped", []) for s in raw_batch]
        annotations = {}
        if any(ipr_lists):
            annotations["ipr"] = self._pad_labels(ipr_lists)
        if any(go_lists):
            annotations["go"] = self._pad_labels(go_lists)
        if annotations:
            batch["annotations"] = annotations

        if "pdb_name" in raw_batch[0]:
            batch["pdb_name"] = [s["pdb_name"] for s in raw_batch]
        if "uniprot_id" in raw_batch[0]:
            batch["uniprot_id"] = [s["uniprot_id"] for s in raw_batch]
        return batch
