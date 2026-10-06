# START_MODULE_CONTRACT
#   PURPOSE: Увидеть на кадре игры факты: на паузе ли игра, индикаторы (рамка, плюсики), светлая волна и блоки волн,
#            заморозка, водовороты в жёлтой рамке; плюс детектор перехода «стало / перестало» (filled).
#   SCOPE: Только чтение кадра и возврат фактов. Пробел не шлют и решений «ставить паузу» не принимают — это M-PAUSE-POLICY.
#   DEPENDS: none
#   LINKS: M-PANEL, M-PAUSE-POLICY, M-CHAIN, V-M-DETECTORS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   game_state - 'pause' / 'run' / None по жёлтой кнопке «II» или стрелке в углу
#   icon_full / pluses - индикатор заполнен (жёлтая рамка) / сколько плюсиков
#   light_top - строка, с которой сверху начинается светлый блок волны
#   wave_blocks - блоки полосы волн: границы, центр, тип (длинный/маленький/обрез), светлый ли
#   freeze_level - насыщенность в круге вокруг точки A (FREEZE_SAT: ниже — заморозка есть)
#   framed_icons / swirls - значки в жёлтой рамке / из них водовороты
#   filled - переход «нет -> есть» с подтверждением и задержкой сброса (защита от мигания)
# END_MODULE_MAP
from pathlib import Path

import cv2, numpy as np

HERE = Path(__file__).parent


def game_state(full):
    """По кнопкам скорости в левом верхнем углу «II ▷ ▷▷ ▷▷▷»: нажатая подсвечена жёлтым.
    'pause' — жёлтая «II» (столбцы ~18-29), 'run' — жёлтая стрелка (37-101), None — жёлтого нет (меню и т.п.)."""
    hsv = cv2.cvtColor(full[12:34, 12:106], cv2.COLOR_BGR2HSV)
    yel = (hsv[:, :, 0] >= 18) & (hsv[:, :, 0] <= 40) & (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 120)
    cols = yel.sum(axis=0)
    p, r = cols[:22].sum(), cols[25:].sum()  # 12+22=34: граница между «II» и первой стрелкой
    if max(p, r) < 10:
        return None
    return "pause" if p > r else "run"


def light_top(full, y0=44, x0=8, x1=30, run=20, need=15):
    """Строка, с которой сверху начинается светлый блок волны, или None.
    Светлая строка = серая без цвета (насыщенность < 12) и не чёрная (яркость > 30); медиана — цифры не мешают.
    Блок = из run строк подряд светлых хотя бы need: тёмные значки внутри светлого квадрата (звёздочка) не рвут блок,
    а серые значки на тёмных блоках (~10 строк) блоком не считаются. С y0=44: маленький квадрат бывает выше 60."""
    hsv = cv2.cvtColor(full[y0:, x0:x1], cv2.COLOR_BGR2HSV)
    ok = (np.median(hsv[:, :, 1], axis=1) < 12) & (np.median(hsv[:, :, 2], axis=1) > 30)
    win = np.convolve(ok, np.ones(run), "valid") >= need
    hits = np.flatnonzero(win)
    if not len(hits):
        return None
    i = int(hits[0])
    return y0 + i + int(np.argmax(ok[i:i + run]))  # первая светлая строка в найденном окне


FIELD = (40, 52, 955, 595)  # x0, y0, x1, y1: игровое поле внутри рамки окна


def ymask(img):
    """Ярко-жёлтое (рамки готовности): песчаная дорога тусклее, снаряды оранжевее."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return (((hsv[:, :, 0] >= 20) & (hsv[:, :, 0] <= 36) & (hsv[:, :, 1] > 150) & (hsv[:, :, 2] > 170)) * 255).astype(np.uint8)


FRAME_TPL = None


def framed_icons(full, score=0.8):
    """Значки на поле в жёлтой пунктирной рамке (4 уголка, 28x29): по форме рамки в ярко-жёлтом.
    Образец — assets/frame_sample.png. На кадре с 6 рамками: все 0.97-1.0, остальное поле < 0.5."""
    global FRAME_TPL
    if FRAME_TPL is None:
        FRAME_TPL = ymask(cv2.imread(str(HERE / "assets" / "frame_sample.png")))[5:36, 9:40].astype(np.float32)
    x0, y0, x1, y1 = FIELD
    r = cv2.matchTemplate(ymask(full[y0:y1, x0:x1]).astype(np.float32), FRAME_TPL, cv2.TM_CCORR_NORMED)
    out = []
    while True:
        y, x = np.unravel_index(int(r.argmax()), r.shape)
        if r[y, x] < score:
            return out
        out.append((x0 + int(x), y0 + int(y)))
        r[max(0, y - 15):y + 15, max(0, x - 15):x + 15] = 0


SWIRL_TPL = None


FREEZE_SAT = 125  # насыщенность в круге r=180 вокруг точки A: с заморозкой ~94, без ~150-158


def freeze_level(full, a, r=180):
    """Средняя насыщенность цвета в круге вокруг точки A (там ложится заморозка — белая, бесцветная)."""
    if not a:
        return None
    hsv = cv2.cvtColor(full[::2, ::2], cv2.COLOR_BGR2HSV)
    m = np.zeros(hsv.shape[:2], np.uint8)
    cv2.circle(m, (a[0] // 2, a[1] // 2), r // 2, 1, -1)
    return float(hsv[:, :, 1][m.astype(bool)].mean())


def swirls(full):
    """Значки-водовороты в жёлтой рамке (нужен Ивану только этот тип): рамка по форме, внутри — сравнение
    с образцом (без рамки и плюсиков). Водоворот 1.0, остальные значки в рамке 0.38-0.48."""
    global SWIRL_TPL
    if SWIRL_TPL is None:
        ref = cv2.cvtColor(cv2.imread(str(HERE / "assets" / "frame_sample.png")), cv2.COLOR_BGR2GRAY)
        SWIRL_TPL = ref[9:26, 13:36].astype(np.float32)
    g = cv2.cvtColor(full, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return [(x, y) for x, y in framed_icons(full)
            if cv2.matchTemplate(g[y + 1:y + 24, x + 1:x + 30], SWIRL_TPL, cv2.TM_CCOEFF_NORMED).max() > 0.75]


STRIP = (6, 32, 44, 612)  # x0, x1, y0, y1: левая полоса волн


def wave_blocks(full):
    """Блоки в полосе волн: [(y_верх, y_низ, (x_центр, y_центр), тип, светлый)].
    Границы — светлые линии-разделители во всю ширину (>80% пикселей ярче 150). Длинный блок — ~105 строк,
    маленький квадрат — ~22; тип 'обрез' — блок обрезан краем полосы. Верхний кусок до первого разделителя —
    блок, только если это не пустой тёмный фон (яркость ~40, у блоков 56-126)."""
    x0, x1, y0, y1 = STRIP
    g = cv2.cvtColor(full[:, x0:x1], cv2.COLOR_BGR2GRAY).astype(int)
    sep = (g > 150).mean(axis=1) > 0.8
    lines, y = [], y0
    while y < y1:  # группы соседних светлых строк -> одна граница
        if sep[y]:
            a = y
            while y < y1 and sep[y]:
                y += 1
            lines.append((a, y))
        y += 1
    edges = [(y0, y0)] + lines + [(y1, y1)]
    hsv = cv2.cvtColor(full[:, x0 + 2:x1 - 2], cv2.COLOR_BGR2HSV)
    out = []
    for (a0, a1), (b0, b1) in zip(edges, edges[1:]):
        top, bot = a1, b0
        if bot - top < 8:  # пустой или тонкий кусок (разделитель у самого края) — не блок; раньше тут падал np.convolve
            continue
        cut = a0 == y0 or b1 == y1
        if cut:  # у края полосы может быть пустой тёмный фон: срезаем строки темнее 50 (фон ~40, блоки 56-126)
            rowv = np.convolve(np.median(hsv[top:bot, :, 2], axis=1), np.ones(5) / 5, "same")
            body = np.flatnonzero(rowv > 50)
            if not len(body):
                continue
            top, bot = top + int(body[0]), top + int(body[-1]) + 1
        if bot - top < 8:
            continue
        h = bot - top
        if h > 150:  # тёмные блоки идут без светлых разделителей — слитный кусок режем по шагу 105 (блок 103 + линия 2)
            n = max(1, round((h + 2) / 105))
            step = (h + 2) / n
            for i in range(n):
                a, b = top + round(i * step), top + round((i + 1) * step) - 2
                light = float(np.median(hsv[a:b, :, 1])) < 12
                out.append((a, b, ((x0 + x1) // 2, (a + b) // 2), "длинный", light))
            continue
        # маленький — только между двумя разделителями; у края полосы короткий кусок — обрезанный длинный
        kind = "длинный" if h >= 95 else ("обрез" if cut else ("маленький" if h < 45 else "обрез"))
        light = float(np.median(hsv[top:bot, :, 1])) < 12
        out.append((top, bot, ((x0 + x1) // 2, (top + bot) // 2), kind, light))
    return out


def icon_full(icon):
    """Индикатор заполнен = жёлтая рамка с уголками. До заполнения жёлтого на рамке нет вовсе (0),
    у заполненного 0.2-0.3 рамки жёлтые — порог 0.1 с запасом в обе стороны."""
    hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
    yel = (hsv[:, :, 0] >= 18) & (hsv[:, :, 0] <= 40) & (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 120)
    ring = np.ones(yel.shape, bool)
    ring[3:-3, 3:-3] = False
    return yel[ring].mean() > 0.1


def _cross(x0):
    m = np.zeros((32, 32), bool)
    m[22:28, x0 + 2:x0 + 4] = True  # вертикальная палочка
    m[24:26, x0:x0 + 6] = True      # горизонтальная
    return m


PLUS = [_cross(3), _cross(13)]  # два места под плюсики в левом нижнем углу значка 32x32


PLUS_RING = [cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~m for m in PLUS]


def pluses(icon):
    """Сколько плюсиков на значке: яркий крест (есть плюс: 190-255, нет: до 94) светлее своей обводки.
    Абсолютный порог обводки не годится: у бледного значка она до 133 — плюс «пропадал» и давал ложный пробел."""
    g = cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY)
    return sum(1 for m, r in zip(PLUS, PLUS_RING) if g[m].mean() > 170 and g[m].mean() - g[r].mean() > 50)


def filled(confirm=2, release=5):
    """push(full) -> True один раз, когда индикатор стал заполненным (рамка держится confirm кадров).
    Снова «пусто» — только после release кадров без рамки, чтобы мигание не дало второй пробел."""
    state, run = False, 0

    def push(full):
        nonlocal state, run
        run = run + 1 if full != state else 0
        if not state and run >= confirm:
            state, run = True, 0
            return True
        if state and run >= release:
            state, run = False, 0
        return False
    return push
