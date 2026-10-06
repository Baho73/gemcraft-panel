# START_MODULE_CONTRACT
#   PURPOSE: Распознать ману в участке кадра и превратить текст в число; отфильтровать ошибки чтения; поймать рост маны.
#   SCOPE: Чистые функции над картинкой и числами, без окна и без ввода. Пороги подобраны на digit_truth.json.
#   DEPENDS: none
#   LINKS: M-PANEL, M-PAUSE-POLICY, V-M-MANA-OCR
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   read - текст маны по маскам цифр (белые штрихи; в светлом пятне — по обводке, grad)
#   number - текст -> число; научный формат d.ddd с опорой на точку; сомнение -> None, а не угадывание
#   mana_text - число для окна и буфера: 1.959e38 / 92447
#   steady - отсев одиночных выбросов по соседним кадрам (вердикт на кадр позже)
#   recent - «это число уже было на экране за 5 мин» (ошибка первой цифры в пятне)
#   surge - рост маны после затишья (любой, > 0.5%), возврат к виденному числу — не рост
#   load / load_grad / flat / grad / match / white_ok - маски и их совпадения
# END_MODULE_MAP
from pathlib import Path

import cv2, numpy as np

HERE = Path(__file__).parent


TPL = HERE / "digit_templates"


GRAD = HERE / "digit_templates_outline"  # маски штриха для пятна, см. grad()


EXP_DIGITS = 2     # ponytail: экспонента в игре пока до 2 цифр; появится e100 — поставить 3


GLOW = 120         # яркость нижней строки области, выше которой там светлый конец полосы


WHITE_DEEP, WHITE_SURE = 3, 0.8  # ponytail: подобрано на 20 размеченных кадрах, запас узкий (0.75 и 0.85 уже ошибаются)


GRAD_SCORE = 0.4  # ponytail: подобран на 44 размеченных кадрах (0.5 и 0.6 хуже)


MIN_SCORE = 0.6   # ponytail: порог совпадения маски на глаз; путает символы — поднять, теряет — опустить


def flat(gray):
    """Светлые тонкие штрихи текста без фона полосы (top-hat)."""
    if np.median(gray) > 127:
        gray = 255 - gray  # текст должен быть светлым на тёмном
    # квадратный top-hat убирает заливку полосы, вертикальный во всю высоту — её светлый край сквозь цифры
    tall = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(gray.shape[0] - 2, 1)))
    sq = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    return np.minimum(cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, tall), cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, sq))


def grad(gray, r=2, dark=60):
    """Штрих внутри светлого пятна: там белая цифра сливается с фоном, но у штриха тёмная обводка
    с двух противоположных сторон (слева и справа или сверху и снизу), а у фона — только с одной."""
    g = gray.astype(np.int16)
    H, W = g.shape
    p = np.pad(g, r, mode="edge")
    drop = lambda dy, dx: g - p[r + dy:r + dy + H, r + dx:r + dx + W]
    side = lambda pairs: np.max([drop(dy, dx) for dy, dx in pairs], axis=0) > dark
    ks = range(1, r + 1)
    lr = side([(0, -k) for k in ks]) & side([(0, k) for k in ks])
    ud = side([(-k, 0) for k in ks]) & side([(k, 0) for k in ks])
    return (((g >= 150) & (lr | ud)) * 255).astype(np.uint8)


def load_grad():
    # маски цифр по перепадам яркости, имя: <код>_<h>x<w>.png
    return [(chr(int(p.stem.split("_")[0])), cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)) for p in GRAD.glob("*.png")]


def load():
    # имя файла: коды символов через '-', напр. 44_3.png = «,». Незнакомые из UNK масками НЕ служат:
    # обрывок «e» из пятна перебивал настоящую «e», и «3.700e37» читалось как «3.700??37»
    tpls = [("".join(chr(int(c)) for c in p.stem.split("_")[0].split("-")),
             cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)) for p in TPL.glob("*.png")]
    return [(lab, t) for lab, t in tpls if t is not None]  # файл могли перенести во время чтения (разметка на лету)


def match(f, tpls, score):
    """Все места, где маска совпала с картинкой: (оценка, x, w, метка, y, h)."""
    out = []
    for lab, t in tpls:
        if t.shape[0] > f.shape[0] or t.shape[1] > f.shape[1]:
            continue
        r = cv2.matchTemplate(f, t, cv2.TM_CCOEFF_NORMED)
        # форма совпала, но в окне почти пусто — это шум фона (маска запятой почти вся чёрная)
        ink = cv2.matchTemplate(f.astype(np.float32), np.ones_like(t, np.float32), cv2.TM_CCORR)
        ys, xs = np.nonzero((r >= score) & (ink >= 0.5 * float(t.sum())))
        out += [(r[y, x], x, t.shape[1], lab, y, t.shape[0]) for y, x in zip(ys, xs)]
    return out


def white_ok(c, glow):
    """Маска по белому у пятна: заходит на 1 столбец — верим; глубже — только уверенной и полной ширины
    (узкая «1» у пятна совпадает с ножкой 4)."""
    if c[2] < 7 and c[3].isdigit():  # узкая цифра («1»): держим ещё 2 столбца от пятна, иначе ловит ножку соседней
        return not glow[max(c[1] - 2, 0):c[1] + c[2] + 2].any()
    over = int(glow[c[1]:c[1] + c[2]].sum())
    return over <= 1 or (over <= WHITE_DEEP and c[0] >= WHITE_SURE)


def read(gray, tpls, unknown=None, grads=()):
    """Поиск по маске: каждый шаблон двигается по области, лучшие непересекающиеся совпадения — символы.
    Светлый конец полосы (пятно) белую цифру прячет, видна только обводка: там ищем по перепадам яркости."""
    f = flat(gray)
    # пятно = столбцы, где под цифрами (низ области) светло
    low = gray[-2:].max(axis=0)  # две нижние строки: у края пятна ярче бывает предпоследняя
    low = 255 - gray[-2:].min(axis=0) if np.median(gray) > 127 else low  # тёмный текст на светлом — развернуть
    glow = np.convolve(low > GLOW, np.ones(5), "same") > 0  # ±2 столбца: край пятна размыт
    cands = [c for c in match(f, tpls, MIN_SCORE) if white_ok(c, glow)]
    if glow.any() and grads:
        cands += [c for c in match(grad(gray), grads, GRAD_SCORE) if glow[c[1]:c[1] + c[2]].sum() >= 2]
    W = f.shape[1]  # у краёв области цифр не бывает, там светлая кромка полосы (давала лишнюю «e» в начале)
    cands = [c for c in cands if 2 <= c[1] and c[1] + c[2] <= W - 2]
    taken = np.zeros(f.shape[1], bool)
    hits, rows = [], []
    for sc, x, w, lab, y, h in sorted(cands, reverse=True):  # жадно: сначала самые уверенные
        if taken[x:x + w].sum() > max(1, w * 0.3):  # соседние маски могут заходить друг на друга на 1 столбец
            continue
        taken[x:x + w] = True
        hits.append((x, lab))
        if lab != "?":
            rows.append((y, y + h))
    # «чернила» вне найденных масок и вне пятна = незнакомый символ
    _, bw = cv2.threshold(f, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # ищем только в строке найденных цифр: выше неё — блик полосы (дуга над цифрами при пустой мане)
    y0, y1 = (min(r[0] for r in rows), max(r[1] for r in rows)) if rows else (1, f.shape[0] - 1)
    # верхнюю четверть строки пропускаем: там крапинки от скруглённого угла полосы, а . , - всегда ниже
    ink = (bw[y0 + (y1 - y0) // 4:y1] > 0).sum(axis=0) * ~taken * ~glow
    x = 0
    while x < len(ink):
        if ink[x]:
            x1 = x
            while x1 < len(ink) and ink[x1]:
                x1 += 1
            if ink[x:x1].sum() >= 6:  # ponytail: меньше 6 пикселей — крапинка, не символ
                hits.append((x, "?"))
                if unknown is not None:
                    unknown.append((x, f[:, max(x - 1, 0):x1 + 1]))
            x = x1
        x += 1
    out = "".join(lab for x, lab in sorted(hits)).replace(" ", "")
    if not any(ch.isdigit() for ch in out):
        # ни одной цифры — на экране не число (другой экран игры, меню): мусор не сохраняем
        if unknown:
            unknown.clear()
        return ""
    return out


def mana_text(v, sci=True):
    """Число маны для окна и буфера: как в игре, но всегда с точкой — 1.959e38; без e на экране — целым, 92447."""
    if sci:
        m, e = f"{v:.3e}".split("e")
        return f"{m}e{int(e)}"
    return str(int(v))


def number(s):
    """'92,447' -> 92447.0, '9.550e12' -> 9.55e12; не разобралось -> None."""
    if "e" in s.lower():
        # игра пишет мантиссу всегда как d.ddd: точку ставим сами по 4 цифрам — тогда не важно, съело ли её
        # светлое пятно, принят ли её верх за запятую и не налип ли рядом «?» от края полосы
        m, _, exp = s.lower().partition("e")
        if not exp.lstrip("+-").isdigit() or len(exp.lstrip("+-")) > EXP_DIGITS:
            return None
        m = m.replace(",", ".")
        if m.count(".") == 1:
            # точка видна — она опора: одна цифра слева, три справа, лишнее по краям — мусор от края полосы
            left, right = m.split(".")
            left = [c for c in left if c.isdigit()]
            if not left or not right[:3].isdigit() or len(right) < 3:  # «?» среди трёх после точки — не угадываем
                return None
            digits = [left[-1]] + list(right[:3])
        else:
            digits = [c for c in m if c.isdigit()]
            if len(digits) != 4:
                return None
        s = f"{digits[0]}.{''.join(digits[1:])}e{exp}"
    else:
        # ponytail: игра пишет дробную точку только в научном формате, а маска точки ловит верх запятой —
        # без e любая точка = разделитель тысяч. Появятся дроби без e — различать точку и запятую по хвосту
        s = s.replace(".", "")
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def steady(tol=0.03):
    """Выброс = число, далёкое от обоих соседей, хотя соседи близки друг к другу (e23 -> e25 -> e23).
    Быстрая трата маны (51065 -> 49931 -> 46937) проходит: там средний кадр между соседями.
    push(n) отдаёт вердикт по ПРЕДЫДУЩЕМУ кадру: число или None (выброс). Задержка — один кадр."""
    far = lambda x, y: abs(x - y) > tol * max(abs(x), abs(y))
    before, cur = None, None

    def push(n):
        nonlocal before, cur
        out = cur
        if None not in (before, cur, n) and far(cur, before) and far(cur, n) and not far(before, n):
            out = None
        if out is not None:
            before = out
        cur = n
        return out
    return push


def recent(memory=300.0):
    """seen(t, n) -> было ли число n на экране за последние memory секунд (до этого кадра), и запоминает его.
    Время — последнего появления: мана может стоять минутами, а ошибочное чтение длиться дольше минуты."""
    last = {}  # число -> когда видели в последний раз; ponytail: числа из 4 цифр — сравнение точное, ключ — repr

    def seen(t, n):
        for k in [k for k, tt in last.items() if t - tt > memory]:
            del last[k]
        hit = n in last and last[n] < t
        last[n] = t
        return hit
    return seen


def surge(factor=1.005, calm=2.0, confirm=2):
    """Ловит момент игры: трата маны -> пауза (число стоит) -> любой рост (больше чем на 0.5%: меньше — последняя цифра).
    Было x10, но Иван попросил любой рост: в игре бывают и ступеньки x1.03-x1.5 каждые 20-60 с.
    Событие, если перед ростом число не менялось (в пределах 1%) calm секунд и рост держится confirm кадров.
    Ошибка чтения e23 -> e25 случается посреди траты, без паузы, поэтому не проходит.
    Возврат к числу, которое уже было за memory секунд, — не рост: это чтение исправилось
    (7.144 -> ошибочно 1.144 -> снова 7.144). Настоящий рост в точности в прежнее число почти не попадает.
    push(t, n) -> (было, стало) или None. Задержка — confirm кадров (0.2 с при 10 кадрах в секунду)."""
    base, since, run, first, was_known = None, None, 0, None, False
    seen = recent()

    def push(t, n):
        nonlocal base, since, run, first, was_known
        if not n:  # None — не число; 0 — сбой отображения в игре («0.000 e29» посреди траты)
            return None
        known = seen(t, n)
        if base is not None and n > base * factor:
            run += 1
            if run == 1:
                first, was_known = t, known  # «уже было» — по первому кадру роста, дальше число уже запомнено
            if run >= confirm:
                ev = (base, n) if first - since >= calm and not was_known else None
                base, run = n, 0
                if not was_known:
                    since = t
                return ev
            return None
        run = 0
        if base is None or abs(n - base) > 0.01 * abs(base):
            base = n
            if not known or since is None:
                since = t  # скачок чтения между уже виденными числами (7.744 <-> 3.744) затишье не прерывает
        return None
    return push
