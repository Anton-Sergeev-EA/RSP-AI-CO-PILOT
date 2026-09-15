"""
RSP COPILOT — LSTM-модель раннего предупреждения о деградации подшипников
энергоблока. Видит только первые OBS_WINDOW суток эксплуатации (вибрация +
температура подшипника) и оценивает вероятность будущей деградации —
раньше, чем сработает классический пороговый детектор (см. cpp/).
"""
import os
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from simulate_units import simulate_series, N_DAYS, OBS_WINDOW

HERE = os.path.dirname(__file__)
MODEL_DIR = os.path.join(HERE, "..", "data", "models")
os.makedirs(MODEL_DIR, exist_ok=True)


def rolling_mean(arr, window=5):
    kernel = np.ones(window) / window
    padded = np.pad(arr, (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def trend_features(obs):
    days = np.arange(len(obs))
    slope = np.polyfit(days, obs, 1)[0]
    diff = obs[-10:].mean() - obs[:10].mean()
    std = obs.std()
    return slope, diff, std


def build_dataset(n=2400, seed=99):
    rng = np.random.default_rng(seed)
    X, AUX, y = [], [], []
    for _ in range(n):
        risk = rng.uniform(0.05, 0.85)
        s = simulate_series(rng, degrade_risk=risk)
        vib = np.array(s["vibration_mm_s"][:OBS_WINDOW])
        temp = np.array(s["bearing_temp_c"][:OBS_WINDOW])
        obs_v = rolling_mean(vib)
        obs_t = rolling_mean(temp)

        v_mean, v_std = obs_v[:14].mean(), obs_v[:14].std() + 1e-3
        t_mean, t_std = obs_t[:14].mean(), obs_t[:14].std() + 1e-3
        day_idx = np.linspace(0, 1, OBS_WINDOW)
        feat = np.stack([(obs_v - v_mean) / v_std, (obs_t - t_mean) / t_std, day_idx], axis=1)
        X.append(feat.astype(np.float32))

        sv, dv, sdv = trend_features(obs_v)
        st, dt, sdt = trend_features(obs_t)
        AUX.append(np.array([sv, dv, sdv, st, dt, sdt], dtype=np.float32))
        y.append(1.0 if s["will_degrade"] else 0.0)

    X = np.array(X, dtype=np.float32)
    AUX = np.array(AUX, dtype=np.float32)
    aux_mean, aux_std = AUX.mean(axis=0), AUX.std(axis=0) + 1e-6
    AUX = (AUX - aux_mean) / aux_std
    y = np.array(y, dtype=np.float32)
    return X, AUX, y, aux_mean, aux_std


class SeriesDataset(Dataset):
    def __init__(self, X, AUX, y):
        self.X, self.AUX, self.y = X, AUX, y

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return torch.from_numpy(self.X[idx]), torch.from_numpy(self.AUX[idx]), torch.tensor(self.y[idx])


class EarlyWarningLSTM(nn.Module):
    def __init__(self, n_features=3, hidden=48, n_aux=6):
        super().__init__()
        self.lstm = nn.LSTM(input_size=n_features, hidden_size=hidden, num_layers=1, batch_first=True)
        self.head = nn.Sequential(
            nn.Linear(hidden * 2 + n_aux, 32), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(32, 1),
        )

    def forward(self, x, aux):
        out, (h_n, _) = self.lstm(x)
        mean_pool = out.mean(dim=1)
        last = h_n[-1]
        combined = torch.cat([mean_pool, last, aux], dim=1)
        return self.head(combined).squeeze(-1)


def main():
    torch.manual_seed(0)
    X, AUX, y, aux_mean, aux_std = build_dataset(n=2400)
    n_val = 400
    X_train, AUX_train, y_train = X[n_val:], AUX[n_val:], y[n_val:]
    X_val, AUX_val, y_val = X[:n_val], AUX[:n_val], y[:n_val]

    train_loader = DataLoader(SeriesDataset(X_train, AUX_train, y_train), batch_size=64, shuffle=True)
    val_loader = DataLoader(SeriesDataset(X_val, AUX_val, y_val), batch_size=128, shuffle=False)

    model = EarlyWarningLSTM(hidden=48)
    opt = torch.optim.Adam(model.parameters(), lr=3e-3, weight_decay=1e-4)
    loss_fn = nn.BCEWithLogitsLoss()

    best_val = 0.0
    for epoch in range(30):
        model.train()
        total_loss = 0.0
        for xb, auxb, yb in train_loader:
            opt.zero_grad()
            logits = model(xb, auxb)
            loss = loss_fn(logits, yb)
            loss.backward()
            opt.step()
            total_loss += loss.item() * xb.size(0)
        model.eval()
        correct, total = 0, 0
        with torch.no_grad():
            for xb, auxb, yb in val_loader:
                pred = (torch.sigmoid(model(xb, auxb)) > 0.5).float()
                correct += (pred == yb).sum().item()
                total += yb.size(0)
        val_acc = correct / total
        best_val = max(best_val, val_acc)
        print(f"epoch {epoch+1}/30 loss={total_loss/len(X_train):.4f} val_acc={val_acc:.4f}")

    torch.save({
        "state_dict": model.state_dict(),
        "obs_window": OBS_WINDOW,
        "aux_mean": aux_mean.tolist(),
        "aux_std": aux_std.tolist(),
        "best_val_acc": best_val,
    }, os.path.join(MODEL_DIR, "early_warning_lstm.pt"))
    print(f"Модель сохранена. Лучшая val_acc: {best_val:.4f}")


if __name__ == "__main__":
    main()
