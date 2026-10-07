"""A small temporal CNN over validated pose sequences.

Input per example: keypoints (x, y, confidence), angles and movement features, EACH with its missing-data mask, over a
fixed window of frames. Missing values are zero and their mask is one of the inputs, so the network can tell "the
joint was at the centre" from "the joint was not seen". Frames with no usable pose do not contribute to the embedding.

Outputs: a movement embedding (always), a reconstruction of keypoints (used only for training: predict joints that
were hidden on purpose, scored only where a real observation exists), and optionally a drill classifier that exists only
when enough coach-reviewed labels do. It outputs no score, rating or judgement of a player.
"""
import torch
from torch import nn

from app.ml1.config import ANGLES, KEYPOINTS, MODEL_ARCHITECTURE, MOTION


def input_channels(n_kp=len(KEYPOINTS), n_ang=len(ANGLES), n_mot=len(MOTION)) -> int:
    return n_kp * 4 + n_ang * 2 + n_mot * 2


class _Block(nn.Module):
    def __init__(self, channels: int, dilation: int):
        super().__init__()
        self.conv1 = nn.Conv1d(channels, channels, 3, padding=dilation, dilation=dilation)
        self.norm = nn.GroupNorm(4, channels)
        self.conv2 = nn.Conv1d(channels, channels, 1)
        self.act = nn.GELU()

    def forward(self, x):
        return x + self.conv2(self.act(self.norm(self.conv1(x))))


class PoseTemporalCNN(nn.Module):
    architecture = MODEL_ARCHITECTURE

    def __init__(self, channels: int = 48, embedding_dim: int = 32, n_classes: int = 0, n_kp: int = len(KEYPOINTS),
                 n_ang: int = len(ANGLES), n_mot: int = len(MOTION)):
        super().__init__()
        self.config = {"architecture": MODEL_ARCHITECTURE, "channels": channels, "embedding_dim": embedding_dim,
                       "n_classes": n_classes, "n_kp": n_kp, "n_ang": n_ang, "n_mot": n_mot}
        self.n_kp = n_kp
        self.stem = nn.Conv1d(input_channels(n_kp, n_ang, n_mot), channels, 1)
        self.blocks = nn.Sequential(_Block(channels, 1), _Block(channels, 2), _Block(channels, 4))
        self.embed = nn.Linear(channels, embedding_dim)
        self.reconstruct = nn.Conv1d(channels, n_kp * 2, 1)
        self.classifier = nn.Linear(embedding_dim, n_classes) if n_classes > 0 else None

    def forward(self, x: torch.Tensor, frame_valid: torch.Tensor) -> dict:
        """x: (B, C, T); frame_valid: (B, T) bool (frames with a usable pose)."""
        h = self.blocks(self.stem(x))
        weights = frame_valid.unsqueeze(1).to(h.dtype)
        pooled = (h * weights).sum(dim=2) / weights.sum(dim=2).clamp(min=1.0)
        embedding = self.embed(pooled)
        out = {"embedding": embedding, "reconstruction": self.reconstruct(h)}
        out["logits"] = self.classifier(embedding) if self.classifier is not None else None
        return out


def to_tensors(examples: list[tuple[dict, dict]], hide: torch.Tensor | None = None):
    """Batch (meta, arrays) pairs into (x, frame_valid, target_xy, observed_mask). `hide` (B, T, K) bool hides
    OBSERVED keypoints from the input on purpose (value and mask zeroed); the unhidden originals are returned as the target."""
    import numpy as np
    kp = torch.from_numpy(np.stack([a["keypoints"] for _, a in examples]))           # B,T,K,3
    kp_mask = torch.from_numpy(np.stack([a["keypoint_mask"] for _, a in examples]))  # B,T,K
    ang = torch.from_numpy(np.stack([a["angles"] for _, a in examples]))
    ang_mask = torch.from_numpy(np.stack([a["angle_mask"] for _, a in examples]))
    mot = torch.from_numpy(np.stack([a["motion"] for _, a in examples]))
    mot_mask = torch.from_numpy(np.stack([a["motion_mask"] for _, a in examples]))
    target_xy = kp[..., :2].clone()
    frame_valid = kp_mask.any(dim=2)
    visible = kp_mask.clone()
    if hide is not None:
        visible = visible & ~hide
    kp_in = kp * visible.unsqueeze(-1)
    B, T, K, _ = kp.shape
    parts = [kp_in.reshape(B, T, K * 3), visible.reshape(B, T, K).float(), ang, ang_mask.float(), mot, mot_mask.float()]
    x = torch.cat(parts, dim=2).permute(0, 2, 1).contiguous().float()               # B,C,T
    return x, frame_valid, target_xy, kp_mask
