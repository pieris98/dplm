"""LightningDataModule for annotated DPLM-2 training.

Minimal wrapper around :class:`AnnotatedProteinDataset` and
:class:`AnnotatedProteinCollater`. Uses a plain token-based batch sampler
(no length-cropping / cluster-training bells and whistles) — those can be
added later if needed.
"""
from typing import Optional

import torch
from pytorch_lightning import LightningDataModule
from torch.utils.data import DataLoader

from byprot.datamodules import register_datamodule
from byprot.datamodules.dataset.annotated_protein import (
    AnnotatedProteinCollater,
    AnnotatedProteinDataset,
)
from byprot.datamodules.dataset.tokenized_protein import DPLM2Tokenizer


def _length_based_sampler(dataset, max_tokens, max_len, shuffle):
    """Greedy batch sampler: keep adding samples until either max_tokens
    tokens or max_len * (current_batch_size+1) tokens would be exceeded."""
    lens = dataset.get_metadata_lens()
    indices = list(torch.randperm(len(lens)).tolist()) if shuffle else list(range(len(lens)))
    batches = []
    cur = []
    cur_max = 0
    for i in indices:
        L = lens[i]
        new_max = max(cur_max, L)
        # tokens if we add i:  (len(cur)+1) * new_max
        if cur and (len(cur) + 1) * new_max > max_tokens:
            batches.append(cur)
            cur = [i]
            cur_max = L
        else:
            cur.append(i)
            cur_max = new_max
    if cur:
        batches.append(cur)
    if shuffle:
        import random; random.shuffle(batches)
    return batches


@register_datamodule("annotated_protein")
class AnnotatedProteinDataModule(LightningDataModule):
    def __init__(
        self,
        parquet_path: str = "",
        vocab_file: str = "airkingbd/dplm2_650m",
        max_tokens: int = 4000,
        max_len: int = 512,
        num_workers: int = 4,
        val_ratio: float = 0.05,
        split_seed: int = 0,
    ):
        super().__init__()
        self.save_hyperparameters(logger=False)
        self.tokenizer = DPLM2Tokenizer.from_pretrained(vocab_file)
        self.collate = AnnotatedProteinCollater(self.tokenizer)
        self._train_dataset = None
        self._val_dataset = None

    def setup(self, stage: Optional[str] = None):
        if stage == "fit":
            self._train_dataset = AnnotatedProteinDataset(
                parquet_path=self.hparams.parquet_path,
                vocab_file=self.hparams.vocab_file,
                max_len=self.hparams.max_len,
                split_seed=self.hparams.split_seed,
                val_ratio=self.hparams.val_ratio,
                split="train",
            )
            self._val_dataset = AnnotatedProteinDataset(
                parquet_path=self.hparams.parquet_path,
                vocab_file=self.hparams.vocab_file,
                max_len=self.hparams.max_len,
                split_seed=self.hparams.split_seed,
                val_ratio=self.hparams.val_ratio,
                split="valid",
            )

    def _make_loader(self, dataset, shuffle):
        from torch.utils.data import BatchSampler
        batches = _length_based_sampler(
            dataset, self.hparams.max_tokens, self.hparams.max_len, shuffle
        )
        return DataLoader(
            dataset,
            batch_sampler=batches,
            num_workers=self.hparams.num_workers,
            collate_fn=self.collate,
        )

    def train_dataloader(self):
        return self._make_loader(self._train_dataset, shuffle=True)

    def val_dataloader(self):
        return self._make_loader(self._val_dataset, shuffle=False)
