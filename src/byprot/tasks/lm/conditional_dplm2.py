"""Lightning task for ConditionalDPLM2.

Thin subclass of :class:`DPLM2TrainingTask` that forwards the
``batch["annotations"]`` dict produced by :class:`AnnotatedProteinCollater`
into ``model.compute_loss(..., conditions=...)``. Everything else
(metrics, optimizer, scheduler, criterion) is inherited unchanged.
"""
import torch

from byprot.tasks import register_task
from byprot.tasks.lm.dplm2 import DPLM2TrainingTask, cal_index_acc


@register_task("lm/conditional_dplm2")
class ConditionalDPLM2TrainingTask(DPLM2TrainingTask):
    def step(self, batch):
        # Pull annotations out of the batch and pass them as the conditions
        # kwarg to ConditionalDPLM2.compute_loss. The collator keys them
        # by annotation type ('ipr', 'go') matching AnnotationEmbedder.
        conditions = batch.pop("annotations", None)
        # Move conditions to the model's device.
        if conditions is not None:
            device = next(self.model.parameters()).device
            conditions = {
                "annotations": {
                    k: v.to(device) for k, v in conditions.items()
                }
            }

        weighting = self.hparams.learning.weight
        logits, targets, loss_masks, weights = self.model.compute_loss(
            batch, weighting=weighting, conditions=conditions
        )

        loss, logging_output = self.criterion(
            logits,
            targets,
            loss_masks,
            weights,
            watch_t1_t2_loss=self.hparams.learning.watch_t1_t2_loss,
            cal_constant_loss=self.hparams.learning.cal_constant_loss,
        )

        # Reuse the parent's accuracy computation (struct + aa index acc).
        logging_output["aatype/index_accuracy"] = cal_index_acc(
            logits["aatype"], targets["aatype"], loss_masks["aatype"]
        )
        if len(loss_masks["struct"].shape) == (len(targets["struct"].shape) - 1):
            (
                logging_output["struct/index_accuracy"],
                logging_output["struct/bit_accuracy"],
            ) = cal_index_acc(
                logits["struct"], targets["struct"], loss_masks["struct"],
                bit_level=True,
            )
        else:
            logging_output["struct/index_accuracy"] = cal_index_acc(
                logits["struct"], targets["struct"], loss_masks["struct"]
            )

        if torch.isnan(loss):
            print("Loss NAN on step ", self.global_step)
            loss = loss * 0
            logging_output["nll_loss"] = logging_output["nll_loss"] * 0
            logging_output["fullseq_loss"] = logging_output["fullseq_loss"] * 0
            logging_output["fullseq_nll_loss"] = (
                logging_output["fullseq_nll_loss"] * 0
            )
            logging_output["ppl"] = logging_output["ppl"] * 0

        return loss, logging_output
