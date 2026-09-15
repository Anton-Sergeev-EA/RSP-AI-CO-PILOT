"""
RSP COPILOT — инференс LSTM раннего предупреждения.
"""
import os
import numpy as np
import torch

from train_predictive_model import EarlyWarningLSTM, rolling_mean, trend_features, OBS_WINDOW

HERE = os.path.dirname(__file__)
MODEL_PATH = os.path.join(HERE, "..", "data", "models", "early_warning_lstm.pt")

_cache = {}


def _load():
    if "model" in _cache:
        return _cache["model"], _cache["aux_mean"], _cache["aux_std"]
    ckpt = torch.load(MODEL_PATH, map_location="cpu")
    model = EarlyWarningLSTM(hidden=48)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    aux_mean = np.array(ckpt["aux_mean"], dtype=np.float32)
    aux_std = np.array(ckpt["aux_std"], dtype=np.float32)
    _cache.update(model=model, aux_mean=aux_mean, aux_std=aux_std)
    return model, aux_mean, aux_std


def predict_early_warning(vibration, bearing_temp):
    model, aux_mean, aux_std = _load()
    vib = np.asarray(vibration[:OBS_WINDOW], dtype=np.float64)
    temp = np.asarray(bearing_temp[:OBS_WINDOW], dtype=np.float64)
    if len(vib) < OBS_WINDOW:
        return None

    obs_v = rolling_mean(vib)
    obs_t = rolling_mean(temp)
    v_mean, v_std = obs_v[:14].mean(), obs_v[:14].std() + 1e-3
    t_mean, t_std = obs_t[:14].mean(), obs_t[:14].std() + 1e-3
    day_idx = np.linspace(0, 1, OBS_WINDOW)
    seq = np.stack([(obs_v - v_mean) / v_std, (obs_t - t_mean) / t_std, day_idx], axis=1).astype(np.float32)

    sv, dv, sdv = trend_features(obs_v)
    st, dt, sdt = trend_features(obs_t)
    aux = np.array([sv, dv, sdv, st, dt, sdt], dtype=np.float32)
    aux = (aux - aux_mean) / aux_std

    with torch.no_grad():
        x = torch.from_numpy(seq).unsqueeze(0)
        a = torch.from_numpy(aux).unsqueeze(0)
        prob = torch.sigmoid(model(x, a)).item()
    return round(float(prob), 4)
