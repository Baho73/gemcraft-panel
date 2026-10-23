# Чтение чисел подсказок сетью numnet (обучение — numnet/train.py, веса — numnet/numnet.npz).
# Прямой проход на numpy: onnxruntime и torch в одном процессе с cv2 не грузятся (конфликт DLL, 23.10).
# Windows OCR находит строки и слова; каждое слово-число дочитывает сеть: «F28», «202 €29», «gent» больше не нужны.
import re
from pathlib import Path

import cv2, numpy as np

MODEL = Path(__file__).parent / "numnet" / "numnet.npz"
ABC = "0123456789.,ε"  # класс 0 — пусто (CTC blank)
H, W = 16, 96
MIN_CONF = 0.7  # ниже — число не прочитано: пульт не гадает. Отложенные: берёт 99.2%, с проверкой вида ошибок 0 из 518
# вид числа игры: 1,234,567 · 9,343.9 · 1.839 · ε65 · 123; «3094,325» — не число, не принимать (23.10)
VALID = re.compile(r"\d{1,3}(,\d{3})*(\.\d+)?|\d+(\.\d+)?|ε\d{1,3}")
POOLS = ((2, 1), (2, 2), (4, 1))
_w = None


def prep(g):
    """Полоса числа (серое, 0-255) -> H x W: высота по H, ширина с сохранением пропорций, справа ноль."""
    w = max(1, min(W, round(g.shape[1] * H / g.shape[0])))
    out = np.zeros((H, W), np.float32)
    out[:, :w] = cv2.resize(g, (w, H), interpolation=cv2.INTER_AREA) / 255.0
    return out


def band(img, x, y, w, h):
    """Полоса слова: строка текста по высоте (непустые ряды вокруг центра слова в пределах рамки слова),
    по ширине — рамка слова ±2-3 px. Так же вырезались числа для обучения (numnet/harvest.py)."""
    br = img.max(2) if img.ndim == 3 else img
    rows = (br > 60).sum(1)
    yc = int(y + h / 2)
    if not (0 <= yc < len(rows)) or not rows[yc]:
        return None
    # строки касаются друг друга — без предела полоса забирала всю подсказку (однозначные числа, 23.10)
    top, bot = max(int(y) - 3, 0), min(int(y + h) + 4, len(rows))
    y0 = yc
    while y0 > top and rows[y0 - 1]:
        y0 -= 1
    y1 = yc
    while y1 < bot and rows[y1]:
        y1 += 1
    g = br[y0:y1, max(int(x) - 2, 0):min(int(x + w) + 3, br.shape[1])].astype(np.float32)
    return (g / g.max() * 255).astype(np.uint8) if g.size and g.max() >= 60 else None


NUM_WORD = r"[\d.,]*\d[\d.,]*|\D\d{1,3}"  # число или степень («ε28», прочитанная как «F28», «€30»)


def numbers(words, gap=4):
    """Слова строки Windows OCR [(текст, x, y, w, h)] -> [(i0, i1, текст, x, y, w, h)]: подряд идущие слова-числа
    с просветом меньше gap px склеены — Windows режет «36,126,694» на куски, а кусок с запасом ±2 px захватывал
    край соседней цифры («336126694», 23.10). Между числом и степенью — пробел ~5 px, их не склеивает."""
    out = []
    for i, (t, x, y, w, h) in enumerate(words):
        if not re.fullmatch(NUM_WORD, t):
            continue
        if out and out[-1][1] == i and x - (out[-1][3] + out[-1][5]) < gap and not re.fullmatch(r"\D\d{1,3}", t):
            i0, _, t0, x0, y0, w0, h0 = out[-1]
            out[-1] = (i0, i + 1, t0 + t, x0, min(y0, y), x + w - x0, max(y0 + h0, y + h) - min(y0, y))
        else:
            out.append((i, i + 1, t, x, y, w, h))
    return out


def _conv(x, k, b):
    """3x3, отступ 1: x C x H x W, k O x C x 3 x 3 -> O x H x W (im2col)."""
    c, h, w = x.shape
    p = np.pad(x, ((0, 0), (1, 1), (1, 1)))
    cols = np.stack([p[:, i:i + h, j:j + w] for i in range(3) for j in range(3)], 1).reshape(c * 9, h * w)
    return (k.reshape(len(k), -1) @ cols + b[:, None]).reshape(len(k), h, w)


def _gru(xs, wi, wh, bi, bh):
    hid = wh.shape[1]
    hs, h = [], np.zeros(hid, np.float32)
    gi = xs @ wi.T + bi
    for g in gi:
        gh = wh @ h + bh
        r = 1 / (1 + np.exp(-(g[:hid] + gh[:hid])))
        z = 1 / (1 + np.exp(-(g[hid:2 * hid] + gh[hid:2 * hid])))
        n = np.tanh(g[2 * hid:] + r * gh[2 * hid:])
        h = (1 - z) * n + z * h
        hs.append(h)
    return np.stack(hs)


def logits(x):
    """x: H x W -> T x классов."""
    global _w
    if _w is None:
        _w = dict(np.load(MODEL))
    f = x[None]
    for i, (ph, pw) in enumerate(POOLS):
        f = np.maximum(_conv(f, _w[f"k{i}"], _w[f"b{i}"]), 0)  # BatchNorm уже сложен в свёртку
        c, h, w = f.shape
        f = f.reshape(c, h // ph, ph, w // pw, pw).max((2, 4))
    seq = f[:, 0, :].T  # T x C
    fw = _gru(seq, _w["wi"], _w["wh"], _w["bi"], _w["bh"])
    bw = _gru(seq[::-1], _w["wi_r"], _w["wh_r"], _w["bi_r"], _w["bh_r"])[::-1]
    return np.concatenate([fw, bw], 1) @ _w["wo"].T + _w["bo"]


def decode(lg):
    """Жадная расшифровка CTC -> (текст, уверенность: минимум по выбранным символам)."""
    p = np.exp(lg - lg.max(-1, keepdims=True))
    p /= p.sum(-1, keepdims=True)
    s, conf, prev = [], [], 0
    for k, c in zip(p.argmax(-1), p.max(-1)):
        if k and k != prev:
            s.append(ABC[k - 1])
            conf.append(float(c))
        elif k and k == prev:  # символ растянут на несколько кадров — берём лучший кадр, не первый
            conf[-1] = max(conf[-1], float(c))
        prev = k
    return "".join(s), min(conf, default=1.0)


def read(g):
    """Полоса числа (серое) -> (текст, уверенность)."""
    return decode(logits(prep(g)))
