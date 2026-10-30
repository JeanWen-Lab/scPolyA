"""DeepCPS released models: architecture, weight loading and inference (needs torch, numpy, pandas only).

Scoring  (models/score_seed{1,2,3}): one-hot sequence (4) + max-normalised total-read coverage (1) = 5 channels,
         201-nt window in transcript orientation, called cleavage base at index 100.
         raw score = mean of the three seeds' sigmoid outputs; calibrated score = frozen isotonic step function
         (models/calibration_isotonic_deepcps.tsv, linear interpolation, clipped at both ends, as sklearn
         IsotonicRegression(out_of_bounds="clip").predict).
Localisation (models/locate_seed{1,2,3}): one-hot sequence only, same window; 201 logits -> softmax over positions;
         ensemble = mean of the three seeds' probabilities; offset = argmax - 100 (transcript orientation,
         positive = downstream); confidence = maximum of the ensemble distribution.

The layer definitions are copied from the training code (site_likelihood/v2/03_train.py Block,
scripts/train_onehot_ablation.py OneHotModel, scripts/train_onehot_cov.py OneHotLocate); parameter names are
unchanged, so the released state_dicts load with strict=True.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

WIN = 201
CENTRE = 100
MODEL_DIR = Path(__file__).resolve().parent / "models"
_BASES = np.zeros(256, np.int64) - 1
for _i, _b in enumerate("ACGT"):
    _BASES[ord(_b)] = _i


def onehot(seqs):
    """List of equal-length uppercase strings -> float32 tensor (B, L, 4); N and any other letter = all zero."""
    a = _BASES[np.frombuffer("".join(seqs).encode(), np.uint8)].reshape(len(seqs), -1)
    x = np.zeros(a.shape + (4,), np.float32)
    m = a >= 0
    x[m, a[m]] = 1
    return torch.from_numpy(x)


def max_normalise(cov):
    """Per-window max normalisation used in training (01_labels.py): c / max(max(c), 1e-6)."""
    cov = np.asarray(cov, np.float32)
    return cov / np.maximum(cov.max(1, keepdims=True), 1e-6)


class Block(nn.Module):
    def __init__(self, ch, dil, p):
        super().__init__()
        self.c1 = nn.Conv1d(ch, ch, 5, padding=2 * dil, dilation=dil)
        self.c2 = nn.Conv1d(ch, ch, 5, padding=2 * dil, dilation=dil)
        self.n1, self.n2, self.d = nn.BatchNorm1d(ch), nn.BatchNorm1d(ch), nn.Dropout(p)

    def forward(self, x):
        return torch.relu(self.n2(self.c2(self.d(torch.relu(self.n1(self.c1(x)))))) + x)


class ScoreNet(nn.Module):
    """Scoring model: conv k=11 -> 6 residual dilated blocks -> max + mean pooling -> MLP -> 1 logit."""

    def __init__(self, ch=64, p=0.2):
        super().__init__()
        self.stem = nn.Sequential(nn.Conv1d(5, ch, 11, padding=5), nn.BatchNorm1d(ch), nn.ReLU())
        self.blocks = nn.Sequential(*[Block(ch, d, p) for d in (1, 2, 4, 8, 16, 1)])
        self.head = nn.Sequential(nn.Linear(2 * ch, 128), nn.ReLU(), nn.Dropout(p), nn.Linear(128, 1))

    def forward(self, oh, cov):
        h = self.blocks(self.stem(torch.cat([oh, cov.unsqueeze(-1)], -1).transpose(1, 2)))
        return self.head(torch.cat([h.amax(-1), h.mean(-1)], 1)).squeeze(1)


class LocateNet(nn.Module):
    """Localisation model: conv k=11 -> 6 residual dilated blocks -> 1x1 conv -> 201 position logits."""

    def __init__(self, ch=64, p=0.2):
        super().__init__()
        self.stem = nn.Sequential(nn.Conv1d(4, ch, 11, padding=5), nn.BatchNorm1d(ch), nn.ReLU())
        self.blocks = nn.Sequential(*[Block(ch, d, p) for d in (1, 2, 4, 8, 16, 1)])
        self.head = nn.Conv1d(ch, 1, 1)

    def forward(self, oh):
        return self.head(self.blocks(self.stem(oh.transpose(1, 2)))).squeeze(1)


def _load(cls, path, dev):
    ck = torch.load(path, map_location=dev, weights_only=True)
    m = cls().to(dev)
    m.load_state_dict(ck["state_dict"], strict=True)
    return m.eval()


class DeepCPS:
    """Loads the six released networks once; score() and locate() take windows already in transcript orientation."""

    def __init__(self, model_dir=MODEL_DIR, device="cpu", threads=4):
        torch.set_num_threads(threads)
        self.dev = torch.device(device)
        model_dir = Path(model_dir)
        self.score_models = [_load(ScoreNet, model_dir / f"score_seed{k}/model.pt", self.dev) for k in (1, 2, 3)]
        self.locate_models = [_load(LocateNet, model_dir / f"locate_seed{k}/model.pt", self.dev) for k in (1, 2, 3)]
        iso = pd.read_csv(model_dir / "calibration_isotonic_deepcps.tsv", sep="\t")
        self.iso_x, self.iso_y = iso.x_threshold.values, iso.y_threshold.values

    def calibrate(self, raw):
        return np.interp(raw, self.iso_x, self.iso_y)  # clips to the end values outside the fitted range

    @torch.no_grad()
    def score(self, seqs, cov, batch=512):
        """seqs: n strings of length 201; cov: (n, 201) total-read coverage (raw or scaled; max-normalised here).
        Returns (n, 3) per-seed probabilities."""
        cov = max_normalise(cov)
        out = np.zeros((len(seqs), 3), np.float32)
        for i in range(0, len(seqs), batch):
            oh = onehot(list(seqs[i:i + batch])).to(self.dev)
            c = torch.from_numpy(cov[i:i + batch]).to(self.dev)
            for j, m in enumerate(self.score_models):
                out[i:i + batch, j] = torch.sigmoid(m(oh, c)).cpu().numpy()
        return out

    @torch.no_grad()
    def locate(self, seqs, batch=512):
        """Returns the (n, 201) ensemble probability over window positions (index 100 = called site)."""
        out = np.zeros((len(seqs), WIN), np.float32)
        for i in range(0, len(seqs), batch):
            oh = onehot(list(seqs[i:i + batch])).to(self.dev)
            out[i:i + batch] = np.mean([torch.softmax(m(oh), -1).cpu().numpy() for m in self.locate_models], 0)
        return out
