"""Сеть-чтец чисел из подсказок игры: полоса числа -> строка из «0-9 . , ε» (CRNN + CTC).
Данные: numnet/data/crops.pkl — настоящие вырезки чисел (подписи Windows OCR, clean = число кусков совпало с длиной);
numnet/data/samples.pkl — настоящие образцы символов для синтетики (склейка в случайные числа, подписи точные).
Запуск (bash): cd "D:/Python/gems" && .venv-train/Scripts/python numnet/train.py [число_проб_optuna]"""
import pickle, random, sys
from pathlib import Path

import cv2, numpy as np, optuna, torch
from torch import nn

HERE = Path(__file__).parent
ABC = "0123456789.,ε"  # класс 0 — пусто (CTC blank)
H, W = 16, 96
DEV = "cuda" if torch.cuda.is_available() else "cpu"


def prep(g):
    """Полоса числа (серое, 0-255) -> H x W, высота по H, ширина с сохранением пропорций, справа ноль."""
    w = max(1, min(W, round(g.shape[1] * H / g.shape[0])))
    out = np.zeros((H, W), np.float32)
    out[:, :w] = cv2.resize(g, (w, H), interpolation=cv2.INTER_AREA) / 255.0
    return out


class Net(nn.Module):
    def __init__(self, c1=32, c2=64, c3=96, hid=96):
        super().__init__()
        self.cnn = nn.Sequential(
            nn.Conv2d(1, c1, 3, padding=1), nn.BatchNorm2d(c1), nn.ReLU(), nn.MaxPool2d((2, 1)),   # 8 x 96
            nn.Conv2d(c1, c2, 3, padding=1), nn.BatchNorm2d(c2), nn.ReLU(), nn.MaxPool2d((2, 2)),  # 4 x 48
            nn.Conv2d(c2, c3, 3, padding=1), nn.BatchNorm2d(c3), nn.ReLU(), nn.MaxPool2d((4, 1)),  # 1 x 48
        )
        self.rnn = nn.GRU(c3, hid, batch_first=True, bidirectional=True)
        self.out = nn.Linear(2 * hid, len(ABC) + 1)

    def forward(self, x):  # x: B x 1 x H x W -> B x T x C (логиты)
        f = self.cnn(x).squeeze(2).permute(0, 2, 1)
        return self.out(self.rnn(f)[0])


def decode(logits):
    """Жадная расшифровка CTC + уверенность (минимум по выбранным символам)."""
    p = logits.softmax(-1)
    conf, idx = p.max(-1)
    res = []
    for row, cf in zip(idx.tolist(), conf.tolist()):
        s, prev, cmin = [], 0, 1.0
        for k, c in zip(row, cf):
            if k and k != prev:
                s.append(ABC[k - 1])
                cmin = min(cmin, c)
            prev = k
        res.append(("".join(s), cmin))
    return res


def synth(glyphs, rng):
    """Случайное число из настоящих образцов символов: впритык, с зазором или наложением на 1 px."""
    kind = rng.random()
    if kind < 0.3:
        s = f"{rng.randint(1, 9)}.{rng.randint(0, 999):03d}"[:rng.choice((3, 4, 5))]
    elif kind < 0.45:
        s = "ε" + str(rng.randint(1, 120))
    elif kind < 0.75:
        s = f"{rng.randint(1, 999_999):,}"
    else:
        s = str(rng.randint(0, 99_999)) + (f".{rng.randint(0, 9)}" if rng.random() < 0.3 else "")
    hgt = rng.choice(list(glyphs["2"]))  # один размер шрифта на число
    parts = [rng.choice(glyphs[ch].get(hgt) or sum(glyphs[ch].values(), [])) for ch in s]
    h = max(p.shape[0] for p in parts)
    img = np.zeros((h, sum(p.shape[1] for p in parts) + 2 * len(parts) + 4), np.uint8)
    x = rng.randint(0, 2)
    for p in parts:
        img[h - p.shape[0]:, x:x + p.shape[1]] = np.maximum(img[h - p.shape[0]:, x:x + p.shape[1]], p)
        x += p.shape[1] + rng.choice((-1, 0, 0, 1))
    img = img[:, :x + rng.randint(1, 3)]
    if rng.random() < 0.5:  # тусклые числа (бомбы серым), шум сглаживания
        img = (img * rng.uniform(0.5, 1.0)).astype(np.uint8)
    return img, s


def load():
    crops = pickle.load(open(HERE / "data" / "crops.pkl", "rb"))
    samples = pickle.load(open(HERE / "data" / "samples.pkl", "rb"))
    glyphs = {}
    for ch, lst in samples.items():
        for hgt, g in lst:
            if g.max() > 80 and g.shape[1] >= 2:
                glyphs.setdefault(ch, {}).setdefault(hgt, []).append(g)
    real = [(g, lab) for f, lab, clean, g in crops if clean]
    random.Random(0).shuffle(real)
    cut = len(real) // 5
    return glyphs, real[cut:], real[:cut], [(g, lab) for f, lab, clean, g in crops if not clean]


def batch(items):
    x = torch.tensor(np.stack([prep(g) for g, _ in items]))[:, None].to(DEV)
    tg = [torch.tensor([ABC.index(c) + 1 for c in s]) for _, s in items]
    return x, torch.cat(tg).to(DEV), torch.tensor([len(t) for t in tg]).to(DEV)


def fit(p, data, steps, log=False):
    glyphs, train, val, _ = data
    rng = random.Random(1)
    net = Net(p["c1"], p["c2"], p["c3"], p["hid"]).to(DEV)
    opt = torch.optim.AdamW(net.parameters(), lr=p["lr"], weight_decay=p["wd"])
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, p["lr"], total_steps=steps)
    ctc = nn.CTCLoss(zero_infinity=True)
    for step in range(steps):
        n_real = int(p["bs"] * p["real"])
        items = rng.sample(train, n_real) + [synth(glyphs, rng) for _ in range(p["bs"] - n_real)]
        x, tg, tl = batch(items)
        lp = net(x).log_softmax(-1).permute(1, 0, 2)
        loss = ctc(lp, tg, torch.full((len(items),), lp.shape[0], dtype=torch.long), tl)
        opt.zero_grad()
        loss.backward()
        opt.step()
        sched.step()
        if log and step % 500 == 0:
            print(step, f"{loss.item():.4f}", f"val {accuracy(net, val):.4f}", flush=True)
    return net


@torch.no_grad()
def accuracy(net, items):
    net.eval()
    ok = 0
    for i in range(0, len(items), 512):
        part = items[i:i + 512]
        ok += sum(s == lab for (s, _), (_, lab) in zip(decode(net(batch(part)[0])), part))
    net.train()
    return ok / len(items)


def main(trials=30):
    data = load()
    print("train", len(data[1]), "val", len(data[2]), "noisy", len(data[3]), "glyph sizes", sorted(data[0]["2"]))

    def objective(t):
        p = dict(c1=t.suggest_categorical("c1", [16, 32, 48]), c2=t.suggest_categorical("c2", [32, 64, 96]),
                 c3=t.suggest_categorical("c3", [64, 96, 128]), hid=t.suggest_categorical("hid", [48, 96, 128]),
                 lr=t.suggest_float("lr", 3e-4, 5e-3, log=True), wd=t.suggest_float("wd", 1e-5, 1e-2, log=True),
                 bs=t.suggest_categorical("bs", [64, 128]), real=t.suggest_float("real", 0.0, 0.8))
        return accuracy(fit(p, data, 2500), data[2])

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=0))
    study.optimize(objective, n_trials=trials)
    best = study.best_params
    print("best", study.best_value, best, flush=True)
    net = fit(best, data, 8000, log=True)
    net.eval()
    print("val", accuracy(net, data[2]), "noisy agree", accuracy(net, data[3]))
    torch.save({"params": best, "state": net.state_dict()}, HERE / "numnet.pt")
    torch.onnx.export(net.cpu(), torch.zeros(1, 1, H, W), HERE / "numnet.onnx", input_names=["x"],
                      output_names=["logits"], dynamic_axes={"x": {0: "b"}, "logits": {0: "b"}}, dynamo=False)
    print("saved", HERE / "numnet.onnx")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
