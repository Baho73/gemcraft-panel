"""Захват участка окна и распознавание цифр по шаблонам.

  python digit_grab.py select [заголовок] выбрать окно (по умолчанию GemCraft Chasing Shadows) и обвести область
  python digit_grab.py [интервал_сек]    опрос; незнакомые символы -> digit_unknown/, кадры -> digit_frames/
                                         разметка: перенести digit_unknown/X.png в digit_templates/<коды>_X.png
  python digit_grab.py test              самопроверка без окна
  python digit_grab.py check             сверка с кадрами, прочитанными глазами (digit_truth.json)
"""
import ctypes, json, sys, time, winsound
from pathlib import Path

import cv2, numpy as np, win32gui, win32ui

from capture import find_window, grab, press_space, press_key, hover, real_hover, real_click, click

ctypes.windll.user32.SetProcessDPIAware()
HERE = Path(__file__).parent
CFG = HERE / "digit_grab.json"
from mana_ocr import TPL, GRAD, EXP_DIGITS, GLOW, WHITE_DEEP, WHITE_SURE, GRAD_SCORE, MIN_SCORE, flat, grad, load_grad, load, match, white_ok, read, mana_text, number, steady, recent, surge  # noqa: F401


UNK = HERE / "digit_unknown"
FRAMES = HERE / "digit_frames"
LIB = HERE / "digit_library"
SPACE_WAIT = 20.0   # после пробела ждём смены кнопки «II» до 20 с (игра в фоне откликается секундами); повторно не жмём
HOVER_WAIT = 5.0    # после наведения мыши ждём столько, потом щелчок в той же точке
STEP_GAP = 1.0      # пауза после каждого шага цепочки, с (Иван: пауза, секунда, 1, секунда, щелчок, секунда)
SPACE_GAP = 20.0    # после своего пробела столько секунд новый не шлём: второй снял бы паузу (игра в фоне откликается секунды)
FROZEN = 3          # столько кадров подряд карта не меняется — игра на паузе, пробел не шлём


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


VK = {".": 0xBE}  # у точки свой код клавиши (VK_OEM_PERIOD); ord(".") = 0x2E — это Delete
MACRO = []  # цепочка Ивана: клавиша, 1 с, навести мышь на точку, 5 с, щелчок в ней, 1 с — для 1-A, 2-B, 2-C, 3-D
for _k, _p in (("1", "A"), ("2", "B"), ("2", "C"), ("3", "D")):
    MACRO += [("key", _k), ("hover", _p), ("click", _p)]
MACRO.append(("click", "D"))  # Иван: последний щелчок (D) — дважды
MACRO.append(("away", "E"))  # увести курсор на пустое место E: подсказка камушка закрывала заморозку
MACRO.append(("key", "."))  # и точка — прячет информацию о камушке


def select(part):
    hwnd = find_window(part)
    img = grab(hwnd)
    x, y, w, h = cv2.selectROI("1/2 rough area, Enter", img, showCrosshair=False)
    cv2.destroyAllWindows()
    k = 4  # шаг 2: грубый кусок крупно, чтобы точно обвести мелкий шрифт
    big = cv2.resize(img[y:y + h, x:x + w], None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST)
    x2, y2, w2, h2 = cv2.selectROI("2/2 exact area x4, Enter", big, showCrosshair=False)
    cv2.destroyAllWindows()
    roi = [x + x2 // k, y + y2 // k, -(-w2 // k), -(-h2 // k)]
    cfg = json.loads(CFG.read_text(encoding="utf-8")) if CFG.exists() else {}
    cfg.update(title=part, roi=roi)  # остальное (индикаторы) не трогаем
    CFG.write_text(json.dumps(cfg), encoding="utf-8")
    print("Сохранено:", CFG)


def loop(interval):
    """Окно управления: 5 галочек (когда ставить игру на паузу), мана с кнопкой «Копировать», картинка области.
    Каждый изменившийся кадр -> LIB, смена числа -> FRAMES + log.tsv, незнакомые символы -> UNK,
    события и пробелы -> digit_events.tsv. Рост маны — тоже галочкой."""
    import base64
    import tkinter as tk
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    hwnd, grads = find_window(cfg["title"]), load_grad()
    for d in (UNK, FRAMES, LIB):
        d.mkdir(exist_ok=True)
    icons = cfg.get("icons", {})
    st = dict(last=None, prev=None, n=0, pend=None, unk_saved=0.0, mana="", map=None, diffs=[], space_at=0.0, start=time.time(), seen=recent(), above_raw=False, above_known=False, frames=[])
    push, jump, above_ev, wave_ev = steady(), surge(), filled(), filled()
    full_ev = {k: filled() for k in icons}
    plus_ev = {k: filled() for k in icons}

    mine = []  # уже запущено? второе окно слало бы двойные пробелы — разворачиваем первое и выходим
    win32gui.EnumWindows(lambda h, _: mine.append(h) if win32gui.GetWindowText(h).startswith("GemCraft: ") else None, None)
    if mine:
        win32gui.ShowWindow(mine[0], 9)  # SW_RESTORE
        print("окно уже открыто — развернул его", flush=True)
        return
    was_active = win32gui.GetForegroundWindow()  # окно скрипта при запуске забирает фокус, а игра без фокуса встаёт на паузу
    root = tk.Tk()
    root.title("GemCraft: пауза")
    root.attributes("-topmost", True)

    def give_focus_back():
        try:
            win32gui.SetForegroundWindow(was_active)
        except Exception:  # Windows иногда запрещает отдавать фокус — не страшно
            pass
    root.after(300, give_focus_back)
    pause = cfg.setdefault("pause", {})
    names = {"left": "левый", "right": "правый"}
    checks = {}
    checks["mana_rise"] = tk.BooleanVar(value=pause.get("mana_rise", True))
    tk.Checkbutton(root, text="Пауза: мана выросла после затишья", variable=checks["mana_rise"],
                   anchor="w", command=lambda: save_pause()).pack(fill="x", padx=8)
    arow = tk.Frame(root)
    arow.pack(fill="x", padx=8)
    checks["mana_above"] = tk.BooleanVar(value=pause.get("mana_above", False))
    tk.Checkbutton(arow, text="Пауза: мана стала больше чем", variable=checks["mana_above"],
                   command=lambda: save_pause()).pack(side="left")
    limit_var = tk.StringVar(value=cfg.get("mana_limit", ""))
    limit_entry = tk.Entry(arow, textvariable=limit_var, width=12)
    limit_entry.pack(side="left", padx=4)
    limit_hint = tk.Label(arow, fg="gray30")
    limit_hint.pack(side="left")

    def rearm_limit():
        """Заново взвести проверку порога: если мана уже выше — пауза на ближайших кадрах."""
        nonlocal above_ev
        above_ev = filled()
        st.update(above_raw=False, above_known=False)
        status.set(f"порог маны обновлён: {limit_hint.cget('text')}")
    tk.Button(arow, text="Обновить", command=rearm_limit).pack(side="left", padx=4)

    def limit():
        """Порог из поля: «1.5e39», «1,000,000»; не число — None (подсказка рядом с полем)."""
        txt = limit_var.get().strip()
        v = number(txt) if txt else None
        limit_hint.config(text=f"= {mana_text(v)}" if v else ("не число" if txt else ""))
        return v

    def save_limit(*_):
        cfg["mana_limit"] = limit_var.get().strip()
        CFG.write_text(json.dumps(cfg), encoding="utf-8")
        limit()
    limit_var.trace_add("write", save_limit)
    wrow = tk.Frame(root)
    wrow.pack(fill="x", padx=8)
    checks["wave"] = tk.BooleanVar(value=pause.get("wave", False))
    tk.Checkbutton(wrow, text="Пауза: светлая волна выше строки", variable=checks["wave"],
                   command=lambda: save_pause()).pack(side="left")
    wave_var = tk.StringVar(value=str(cfg.get("wave_y", 70)))
    tk.Entry(wrow, textvariable=wave_var, width=5).pack(side="left", padx=4)
    wave_now = tk.StringVar(value="светлое с: —")
    tk.Label(wrow, textvariable=wave_now, font=("Consolas", 14, "bold")).pack(side="left", padx=6)

    def wave_y():
        try:
            return int(float(wave_var.get()))
        except ValueError:
            return None

    def save_wave(*_):
        if wave_y() is not None:
            cfg["wave_y"] = wave_y()
            CFG.write_text(json.dumps(cfg), encoding="utf-8")
    wave_var.trace_add("write", save_wave)
    blocks_now = tk.StringVar(value="")
    tk.Label(root, textvariable=blocks_now, anchor="w", fg="gray30").pack(fill="x", padx=8)
    srow = tk.Frame(root)
    srow.pack(fill="x", padx=8)
    checks["swirl"] = tk.BooleanVar(value=pause.get("swirl", False))
    tk.Checkbutton(srow, text="Пауза: на поле водоворот в жёлтой рамке", variable=checks["swirl"],
                   command=lambda: save_pause()).pack(side="left")
    swirl_now = tk.StringVar(value="")
    tk.Label(srow, textvariable=swirl_now, fg="gray30").pack(side="left", padx=6)
    swirl_ev = {}  # место на поле -> детектор появления (у каждого водоворота своё)
    for k in icons:
        for kind, text in (("full", "заполнен (жёлтая рамка)"), ("plus", "получил первый плюс")):
            key = f"{k}_{kind}"
            checks[key] = tk.BooleanVar(value=pause.get(key, kind == "full"))
            tk.Checkbutton(root, text=f"Пауза: {names.get(k, k)} индикатор {text}", variable=checks[key],
                           anchor="w", command=lambda: save_pause()).pack(fill="x", padx=8)

    def save_pause():
        cfg["pause"] = {k: v.get() for k, v in checks.items()}
        CFG.write_text(json.dumps(cfg), encoding="utf-8")

    # --- цепочка: когда заморозка на поле кончилась (+ задержка) — один раз «пауза -> 1-клик 2-клик 2-клик 3-клик -> снять паузу»
    # (щелчок A ставит заморозку; раньше запускалась по первому плюсу левого, Иван поправил)
    mc = cfg.setdefault("macro", {"points": {}})
    crow = tk.Frame(root)
    crow.pack(fill="x", padx=8)
    checks["macro"] = tk.BooleanVar(value=pause.get("macro", False))
    tk.Checkbutton(crow, text="Цепочка, когда кончилась заморозка, через", variable=checks["macro"],
                   command=lambda: save_pause()).pack(side="left")
    fdelay_var = tk.StringVar(value=str(mc.get("freeze_delay", 3.0)))
    tk.Entry(crow, textvariable=fdelay_var, width=4).pack(side="left")
    tk.Label(crow, text="с").pack(side="left")
    freeze_now = tk.StringVar(value="")
    tk.Label(crow, textvariable=freeze_now, fg="gray30").pack(side="left", padx=6)
    prow = tk.Frame(root)
    prow.pack(fill="x", padx=8)
    pbtn = {}

    def capture(name, left=3):
        if left:
            pbtn[name].config(text=f"{name}: {left}…")
            root.after(1000, capture, name, left - 1)
            return
        x, y = win32gui.ScreenToClient(hwnd, win32gui.GetCursorPos())
        mc["points"][name] = [x, y]
        CFG.write_text(json.dumps(cfg), encoding="utf-8")
        pbtn[name].config(text=f"{name}: {x},{y}")

    for name in "ABCDE":  # E — пустое место, куда увести курсор в конце (без щелчка)
        p = mc["points"].get(name)
        pbtn[name] = tk.Button(prow, text=f"{name}: {p[0]},{p[1]}" if p else f"{name}: задать",
                               command=lambda n=name: capture(n))
        pbtn[name].pack(side="left", padx=1)
    trow = tk.Frame(root)  # настройки цепочки: курсор и паузы
    trow.pack(fill="x", padx=8)
    real_var = tk.BooleanVar(value=mc.get("real_mouse", True))
    tk.Checkbutton(trow, text="настоящий курсор", variable=real_var).pack(side="left")
    gap_var = tk.StringVar(value=str(mc.get("step_gap", STEP_GAP)))
    wait_var = tk.StringVar(value=str(mc.get("hover_wait", HOVER_WAIT)))
    for text, var in (("между шагами, с", gap_var), ("после наведения, с", wait_var)):
        tk.Label(trow, text=text).pack(side="left", padx=(8, 2))
        tk.Entry(trow, textvariable=var, width=4).pack(side="left")

    def secs(var, default):
        try:
            return max(0.1, float(var.get().replace(",", ".")))
        except ValueError:
            return default

    def save_macro(*_):
        mc.update(real_mouse=real_var.get(), step_gap=secs(gap_var, STEP_GAP), hover_wait=secs(wait_var, HOVER_WAIT),
                  freeze_delay=secs(fdelay_var, 3.0))
        CFG.write_text(json.dumps(cfg), encoding="utf-8")
    for v in (real_var, gap_var, wait_var, fdelay_var):
        v.trace_add("write", save_macro)
    macro = dict(on=False, i=0)

    def macro_steps():
        return [("pause", None)] + MACRO + [("resume", None), ("done", None)]

    def mlog(text):
        print(time.strftime("%H%M%S"), "ЦЕПОЧКА", text, f"[игра: {st.get('state')}]", flush=True)

    def macro_tick():
        """Один шаг цепочки раз в STEP_GAP. Состояние игры — по жёлтой кнопке «II»/стрелки (st['state']).
        pause/resume — пробел и проверка через секунду (до 3 попыток). Перед каждым действием проверка,
        что игра на паузе: если прошлый шаг паузу снял, ставим обратно и пишем, какой шаг виноват."""
        if not macro["on"]:
            return
        steps = macro_steps()
        kind, arg = steps[macro["i"]]
        state = st.get("state")
        nxt = secs(gap_var, STEP_GAP)
        if kind in ("pause", "resume"):
            want = "pause" if kind == "pause" else "run"
            if state == want:
                mlog(f"{'пауза' if want == 'pause' else 'игра идёт'} — проверено")
                macro.update(i=macro["i"] + 1, tries=0, sent_at=0)
            elif state is None:  # кнопок скорости не видно (меню и т.п.) — вслепую не жмём, пробел переключает паузу
                macro_stop("не вижу кнопок скорости в углу игры")
                return
            elif time.time() - macro.get("sent_at", 0) < SPACE_WAIT:  # игра в фоне откликается ~2.5 с — ждём
                nxt = 0.2
            elif macro.get("tries", 0) >= 1:  # один пробел и ждём; повтор мог бы снять только что поставленную паузу
                macro_stop(f"игра не {'встала на паузу' if want == 'pause' else 'пошла'} за {SPACE_WAIT:.0f} с после пробела")
                return
            else:
                press_space(hwnd)
                macro["sent_at"] = time.time()
                macro["tries"] = macro.get("tries", 0) + 1
                mlog(f"пробел ({'пауза' if want == 'pause' else 'снять паузу'}), попытка {macro['tries']}")
        elif kind == "done":  # цепочка выполняется один раз: пауза, шаги, снять паузу — и всё
            macro_stop("выполнена")
            return
        else:
            if state == "run":
                # игру сняли с паузы посреди цепочки (Иван или прошлый шаг) — прерываем и ничего не жмём:
                # раньше тут ставили паузу обратно пробелом каждую секунду, а игра откликается за 2-7 с —
                # пробелы копились и переключали паузу туда-обратно, споря с Иваном
                prev = steps[macro["i"] - 1]
                macro_stop(f"игру сняли с паузы (после шага {prev[0]} {prev[1]}) — цепочка прервана")
                return
            elif kind == "key":
                press_key(hwnd, VK.get(arg, ord(arg)))
                mlog(f"клавиша {arg}")
                macro["i"] += 1
            elif kind == "away":  # увести курсор с камушка, иначе подсказка с характеристиками закрывает заморозку
                p = mc["points"].get(arg)
                if p:
                    (real_hover if real_var.get() else hover)(hwnd, *p)
                    mlog(f"курсор в сторону, на {arg} {p}")
                else:
                    mlog(f"точка {arg} не задана — курсор не увожу")
                macro["i"] += 1
            else:
                p = mc["points"].get(arg)
                if not p:
                    macro_stop(f"не задана точка {arg}")
                    return
                real = real_var.get()
                if kind == "hover":
                    (real_hover if real else hover)(hwnd, *p)
                    mlog(f"навёл {'курсор' if real else 'мышь (сообщением)'} на {arg} {p}")
                    nxt = secs(wait_var, HOVER_WAIT)
                else:
                    (real_click if real else click)(hwnd, *p)
                    mlog(f"щелчок {arg} {p}")
                    if arg == "A":  # после первого щелчка появляется заморозка — собираем образцы
                        freeze_sample("до", st["frames"][-1][1] if st["frames"] else None)
                        for delay in (1000, 3000):
                            root.after(delay, lambda d=delay: freeze_sample(f"через {d // 1000} с",
                                                                            st["frames"][-1][1] if st["frames"] else None))
                macro["i"] += 1
        root.after(int(nxt * 1000), macro_tick)

    def macro_start(why):
        if macro["on"]:
            return
        if not all(n in mc["points"] for n in "ABCD"):
            status.set("цепочка: задайте точки A, B, C, D")
            return
        macro.update(on=True, i=0, tries=0, sent_at=0)
        mbtn.config(text="Стоп цепочки")
        status.set(f"цепочка идёт ({why})")
        print(time.strftime("%H%M%S"), f"ЦЕПОЧКА пуск: {why}", flush=True)
        macro_tick()

    def macro_stop(why):
        if not macro["on"]:
            return
        mid = 0 < macro["i"] < len(macro_steps()) - 1  # прервали внутри круга — игра стоит на паузе цепочки
        macro.update(on=False)
        mbtn.config(text="Пуск цепочки")
        status.set(f"цепочка остановлена: {why}" + (" (игра на паузе)" if mid else ""))
        print(time.strftime("%H%M%S"), f"ЦЕПОЧКА стоп: {why}", flush=True)

    mbtn = tk.Button(prow, text="Пуск цепочки",
                     command=lambda: macro_stop("вручную") if macro["on"] else macro_start("вручную"))
    mbtn.pack(side="right")

    row = tk.Frame(root)
    row.pack(fill="x", padx=8, pady=6)
    mana_var = tk.StringVar(value="—")
    tk.Label(row, text="Мана:").pack(side="left")
    tk.Label(row, textvariable=mana_var, font=("Consolas", 16, "bold")).pack(side="left", padx=6)

    def copy():
        root.clipboard_clear()
        root.clipboard_append(st["mana"])
        status.set(f"Скопировано: {st['mana']}")
    tk.Button(row, text="Копировать", command=copy).pack(side="right")

    def saw_flyer():
        """Иван видит летающего монстра — сохраняем последние 2 с кадров окна игры, чтобы научиться его ловить."""
        (HERE / "flyers").mkdir(exist_ok=True)
        stamp = time.strftime("%H%M%S")
        for i, (fs, img) in enumerate(st["frames"]):
            cv2.imwrite(str(HERE / "flyers" / f"seen_{stamp}_{i:02d}_{fs}.png"), img)
        status.set(f"летун: сохранено {len(st['frames'])} кадров в flyers/seen_{stamp}_*")
        print(stamp, f"ЛЕТУН отмечен, кадров {len(st['frames'])}", flush=True)
    tk.Button(row, text="Вижу летуна", command=saw_flyer).pack(side="right", padx=4)

    def freeze_sample(label, img):
        """Образец для обучения заморозке: кадр + средние поля с пометкой (до щелчка A / через 1 и 3 с после)."""
        if img is None:
            return
        try:  # сбор образцов не должен ронять цепочку
            (HERE / "freeze").mkdir(exist_ok=True)
            stamp = time.strftime("%H%M%S")
            tag = "before" if label == "до" else "after" + label.split()[1]
            cv2.imwrite(str(HERE / "freeze" / f"auto_{stamp}_{tag}.png"), img)
            fx0, fy0, fx1, fy1 = FIELD
            hsv = cv2.cvtColor(img[fy0:fy1:4, fx0:fx1:4], cv2.COLOR_BGR2HSV)
            with open(HERE / "freeze" / "stats.tsv", "a", encoding="utf-8") as f:
                f.write(f"{stamp}\tAUTO_{tag}\t{hsv[:, :, 2].mean():.1f}\t{hsv[:, :, 1].mean():.1f}\n")
        except Exception as e:
            print("образец заморозки не сохранён:", e, flush=True)

    def saw_freeze():
        """Иван видит белую заморозку — сохраняем последние 2 с кадров и отмечаем момент в freeze/stats.tsv."""
        (HERE / "freeze").mkdir(exist_ok=True)
        stamp = time.strftime("%H%M%S")
        for i, (fs, img) in enumerate(st["frames"]):
            cv2.imwrite(str(HERE / "freeze" / f"seen_{stamp}_{i:02d}_{fs}.png"), img)
        with open(HERE / "freeze" / "stats.tsv", "a", encoding="utf-8") as f:
            f.write(f"{stamp}\tMARK\n")
        status.set(f"заморозка: сохранено {len(st['frames'])} кадров в freeze/seen_{stamp}_*")
        print(stamp, "ЗАМОРОЗКА отмечена", flush=True)
    tk.Button(row, text="Есть заморозка", command=saw_freeze).pack(side="right", padx=4)
    pic = tk.Label(root)
    pic.pack(padx=8)
    status = tk.StringVar(value="захват идёт")
    tk.Label(root, textvariable=status, anchor="w", fg="gray30").pack(fill="x", padx=8, pady=(0, 6))

    def event(stamp, t, text, *cols):
        # пробел переключает паузу: в уже стоящую игру (её поставили вы) или второй подряд — снимет паузу
        if t - st["start"] < 1.0:
            why = "старт: это уже было на экране"  # плюс/рамка, которые были до запуска, — не событие
        elif macro["on"]:
            why = "идёт цепочка"  # свой пробел сбил бы её чередование «пауза — снять паузу»
        elif paused():
            why = "игра уже на паузе"
        elif t - st["space_at"] < SPACE_GAP:
            why = f"пробел был {t - st['space_at']:.1f} с назад"
        else:
            why = None
            press_space(hwnd)  # первым делом — пробел в игру
            st["space_at"] = t
            winsound.MessageBeep()  # асинхронно, не тормозит
        with open(HERE / "digit_events.tsv", "a", encoding="utf-8") as f:
            f.write("\t".join([stamp, *map(str, cols), f"{time.time() - t:.3f}", why or "space"]) + "\n")
        hm = f"{stamp[:2]}:{stamp[2:4]}:{stamp[4:6]}"
        print(stamp, text, f"— пробел НЕ отправлен: {why}" if why else "— пробел отправлен", flush=True)
        status.set(f"{hm} {text} — " + (f"без пробела ({why})" if why else "пауза"))

    def paused():
        """Пауза — по жёлтой кнопке «II» в углу игры (подсказал Иван); кнопки не видно — по неподвижной карте
        (FROZEN кадров подряд; на ходу соседние кадры отличаются на 0.2-13)."""
        if st.get("state"):
            return st["state"] == "pause"
        return len(st["diffs"]) >= FROZEN and max(st["diffs"][-FROZEN:]) < 0.05

    def step():
        """Один кадр. Любая ошибка внутри не должна останавливать опрос (так 6.10 в 01:29 встало всё: паузы,
        цепочка) — пишем её и продолжаем со следующего кадра."""
        try:
            step_inner()
        except Exception:
            import traceback
            err = traceback.format_exc()
            print(time.strftime("%H%M%S"), "ОШИБКА в кадре, опрос продолжается:\n" + err, flush=True)
            with open(HERE / "digit_errors.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + err + "\n")
            status.set("ошибка в кадре (см. digit_errors.log), опрос продолжается")
            root.after(int(interval * 1000), step)

    def step_inner():
        t = time.time()
        stamp = time.strftime("%H%M%S", time.localtime(t)) + f"_{int(t * 1000) % 1000:03d}"
        try:
            full = grab(hwnd, None, color=True)  # окно целиком один раз: из него и мана, и индикаторы
        except Exception as e:  # окно свернули или закрыли — ждём, не падаем
            status.set(f"нет снимка окна: {e}")
            root.after(1000, step)
            return
        mp = cv2.resize(cv2.cvtColor(full[50:600, 40:960], cv2.COLOR_BGR2GRAY), (184, 110),
                        interpolation=cv2.INTER_AREA).astype(np.int16)  # карта уменьшенно: движется ли игра
        if st["map"] is not None:
            st["diffs"] = (st["diffs"] + [float(np.abs(mp - st["map"]).mean())])[-10:]
        st["map"] = mp
        st["state"] = game_state(full)
        st["frames"] = (st["frames"] + [(stamp, full)])[-20:]  # последние 2 с — для кнопок «Вижу летуна», «Есть заморозка»
        if t - st.get("freeze_at", 0) >= 1:  # раз в секунду: средние яркость и насыщенность поля — учимся видеть заморозку
            st["freeze_at"] = t
            fx0, fy0, fx1, fy1 = FIELD
            hsv = cv2.cvtColor(full[fy0:fy1:4, fx0:fx1:4], cv2.COLOR_BGR2HSV)
            (HERE / "freeze").mkdir(exist_ok=True)
            with open(HERE / "freeze" / "stats.tsv", "a", encoding="utf-8") as f:
                f.write(f"{stamp}\t{hsv[:, :, 2].mean():.1f}\t{hsv[:, :, 1].mean():.1f}\t{st.get('state')}\n")
        root.title("GemCraft: на паузе" if paused() else "GemCraft: игра идёт")
        for k, (ix, iy, iw, ih) in icons.items():
            icon = full[iy:iy + ih, ix:ix + iw]
            if full_ev[k](icon_full(icon)) and checks[f"{k}_full"].get():
                event(stamp, t, f"{names.get(k, k)} индикатор заполнен", "indicator", k, "full")
            np_ = pluses(icon)
            if st.get(f"plus_{k}") != np_:  # разбор: каждое изменение числа плюсиков с картинкой значка
                st[f"plus_{k}"] = np_
                cv2.imwrite(str(HERE / "icons" / f"plus_{k}_{stamp}_{np_}.png"), icon)
            plus_new = plus_ev[k](np_ >= 1)
            if plus_new and checks[f"{k}_plus"].get():
                fl = freeze_level(full, mc["points"].get("A")) if k == "left" else None
                if fl is not None and fl < FREEZE_SAT:  # Иван: заморозка лежит — плюс левого не повод для паузы
                    mlog(f"левый: первый плюс, но заморозка есть ({fl:.0f}) — без паузы")
                else:
                    event(stamp, t, f"{names.get(k, k)} индикатор: первый плюс", "indicator", k, "plus")
        frz = freeze_level(full, mc["points"].get("A"))  # насыщенность в круге вокруг A: ~94 заморозка, ~155 нет
        if frz is not None:
            frozen = frz < FREEZE_SAT
            freeze_now.set(f"заморозка: {'есть' if frozen else 'нет'} ({frz:.0f})")
            # не по моменту «кончилась», а по состоянию «нет уже N с»: иначе после запуска окна без заморозки
            # цепочка ждала бы конца заморозки, которой без цепочки не будет
            if frozen or st.get("state") != "run":
                st["nofreeze_since"] = None  # на паузе время не идёт
            elif st.get("nofreeze_since") is None:
                st["nofreeze_since"] = t
            lp = st.get("plus_left", 0)
            ready = (checks["macro"].get() and not macro["on"] and st.get("nofreeze_since") is not None
                     and t - st["nofreeze_since"] >= secs(fdelay_var, 3.0) and t - st.get("chain_done", 0) > 15)
            if ready and lp:
                st["chain_done"] = t  # не чаще раза в 15 с: заморозка ложится не сразу
                macro_start(f"заморозки нет {t - st['nofreeze_since']:.0f} с, плюсов у левого: {lp}")
            elif ready and not st.get("noplus_said"):
                st["noplus_said"] = True
                mlog("заморозки нет, но у левого индикатора нет плюсов — жду плюс")
            if lp:
                st["noplus_said"] = False
        found = {(x // 8, y // 8) for x, y in swirls(full)}  # значки стоят на месте; //8 — на дрожь в пиксель
        swirl_now.set(f"сейчас: {len(found)}")
        for key in found - swirl_ev.keys():
            swirl_ev[key] = filled(confirm=2, release=30)  # снаряды заслоняют на миг — «снова нет» только через 3 с
        for key, det in swirl_ev.items():
            if det(key in found) and checks["swirl"].get():
                event(stamp, t, f"водоворот в жёлтой рамке ({key[0] * 8},{key[1] * 8})", "swirl", key[0] * 8, key[1] * 8)
        if st.get("state") == "pause":  # Иван: блоки искать только на паузе — полоса стоит, разделители чёткие
            st["blocks"] = wave_blocks(full)  # блоки полосы волн с центрами — потом по ним щёлкать
            st["blocks_at"] = stamp
        short = {"длинный": "Д", "маленький": "м", "обрез": "о"}
        desc = " ".join(f"{short[k]}{'св' if lt else ''}{c[1]}" for _, _, c, k, lt in st.get("blocks", []))
        when = st.get("blocks_at")
        blocks_now.set(f"блоки (на паузе {when[:2]}:{when[2:4]}:{when[4:6]}): {desc}" if when else
                       "блоки: ищутся на паузе")
        if when == stamp and desc != st.get("blocks_desc"):
            st["blocks_desc"] = desc
            with open(HERE / "waves" / "blocks.tsv", "a", encoding="utf-8") as f:
                f.write(f"{stamp}\t{desc}\n")
        top = light_top(full)  # где сверху начинается светлый блок волны
        wave_now.set(f"светлое с: {top if top is not None else '—'}")
        if top != st.get("wave_top"):  # разбор: каждое изменение — в журнал
            st["wave_top"] = top
            with open(HERE / "waves" / "top.tsv", "a", encoding="utf-8") as f:
                f.write(f"{stamp}\t{top}\n")
        wy = wave_y()
        if wave_ev(top is not None and wy is not None and top <= wy) and checks["wave"].get():
            cv2.imwrite(str(HERE / "waves" / f"{stamp}_pause_{top}.png"), full[0:612, 0:60])
            event(stamp, t, f"светлая волна выше строки {wy} (с {top})", "wave", top)
        x, y, w, h = cfg["roi"]
        bgr = full[y:y + h, x:x + w]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if st["prev"] is None or np.abs(gray.astype(int) - st["prev"]).mean() > 1:
            cv2.imwrite(str(LIB / f"{stamp}.png"), bgr)
            st["prev"], st["n"] = gray, st["n"] + 1
        unk = []
        s = read(gray, load(), unk, grads)  # ponytail: маски перечитываем каждый круг — разметка подхватывается на лету
        if unk and t - st["unk_saved"] >= 1:  # не чаще раза в секунду, иначе при 10 кадрах/с папка забьётся
            st["unk_saved"] = t
            for ux, g in unk:
                cv2.imwrite(str(UNK / f"{stamp}_{ux}.png"), g)
        ev = jump(t, number(s))  # по текущему кадру, без задержки фильтра: на всё про всё 0.5 с
        nm = number(s)
        known = st["seen"](t, nm) if nm else False
        if ev and checks["mana_rise"].get():
            event(stamp, t, f"рост маны {ev[0]:.4g} -> {ev[1]:.4g}", f"{ev[0]:.4g}", f"{ev[1]:.4g}")
        lim = limit()
        if nm and lim:  # мана перешла порог снизу вверх и держится 2 кадра
            above = nm > lim
            if above and not st["above_raw"]:
                st["above_known"] = known  # вернулась к числу, что уже было, — чтение исправилось, порог не пересекали
            st["above_raw"] = above
            if above_ev(above) and checks["mana_above"].get() and not st["above_known"]:
                event(stamp, t, f"мана {nm:.4g} больше порога {lim:.4g}", "above", f"{lim:.4g}", f"{nm:.4g}")
        val = push(number(s))  # вердикт по предыдущему кадру: число или None (не число / выброс)
        if st["pend"]:  # предыдущий кадр: теперь, зная соседа справа, можно решить, выброс ли он
            pstamp, ps, pgray = st["pend"]
            if val is not None:
                # показываем и копируем число, а не сырой текст: в нём точку может съесть край полосы («1959e38»)
                sci = "e" in ps.lower()
                st["mana"] = mana_text(val, sci)
                mana_var.set(st["mana"] if sci else f"{int(val):,}")
            key = val if val is not None else ps  # мигание запятой не меняет число — не печатаем
            if key != st["last"]:
                cv2.imwrite(str(FRAMES / f"{pstamp}.png"), pgray)
                with open(FRAMES / "log.tsv", "a", encoding="utf-8") as f:
                    f.write(f"{pstamp}\t{ps}\t{number(ps)}\t{val}\n")  # 4-й столбец — принятое число
                st["last"] = key
        st["pend"] = (stamp, s, gray)
        png = cv2.imencode(".png", cv2.resize(bgr, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))[1]
        pic.image = tk.PhotoImage(data=base64.b64encode(png.tobytes()))
        pic.configure(image=pic.image)
        root.after(max(1, int((interval - (time.time() - t)) * 1000)), step)

    root.bind("<Escape>", lambda e: root.destroy())
    root.after(0, step)
    root.mainloop()


def test():
    def render(s):
        img = np.zeros((40, 300), np.uint8)
        cv2.putText(img, s, (5, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, 255, 2)
        return img
    tpls = []
    for ch in "0123456789.E+-,":
        f = flat(render(ch))
        xs = np.flatnonzero(f[5:38].max(axis=0) > 60)
        tpls.append((ch, f[5:38, xs[0]:xs[-1] + 1]))
    for s in ["907", "2.5E-7", "1.5E+12", "4E9", "3,14"]:
        assert read(render(s), tpls) == s, (s, read(render(s), tpls))
    assert read(255 - render("3,14"), tpls) == "3,14"  # тёмный текст на светлом
    glued = render("29")
    glued[18:21, 23:28] = 255  # перемычка между 2 и 9 — маскам не мешает
    assert read(glued, tpls).replace("?", "") == "29", read(glued, tpls)  # сама перемычка может дать «?»
    assert read(np.zeros((40, 300), np.uint8) + 30, tpls) == ""
    edge = np.zeros((612, 1088, 3), np.uint8)
    edge[44:46, 6:32] = 255
    edge[608:612, 6:32] = 255  # разделители у самых краёв полосы — 6.10 это роняло весь опрос
    assert wave_blocks(edge) == []
    assert number("6.202e231") is None and number("6.202e23") == 6.202e23
    assert mana_text(number("1959e38")) == "1.959e38" and mana_text(1798646114.0, False) == "1798646114"
    push = steady()
    seq = [6.21e23, 6.20e23, 6.20e25, 6.19e23, 51065, 49931, 46937, 46900]
    got = [push(v) for v in seq + [46900]][1:]  # вердикт приходит на кадр позже
    assert got == [6.21e23, 6.20e23, None, 6.19e23, 51065, 49931, 46937, 46900], got
    f = filled()  # рамка мигнула на кадр — не считается; держится 2 кадра — одно событие; мигание внутри — без повтора
    assert [f(v) for v in [0, 1, 0, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0, 0, 1, 1]] == [0] * 4 + [1] + [0] * 10 + [1]
    j = surge()
    calm = [4e30] * 25  # 2.5 с покоя
    evs = [j(t / 10, n) for t, n in enumerate([5e30, 4.5e30] + calm + [2e33, 2e33, 2e33, 1e30])]
    assert [e for e in evs if e] == [(4e30, 2e33)], evs  # пауза, потом рост x500 — одно событие
    j = surge()  # 7.144 стояла, чтение сбилось на 1.144 и вернулось — паузы нет; настоящий рост — есть
    seq = [7.144e35] * 30 + [1.144e35] * 30 + [7.144e35] * 3 + [7.144e35] * 30 + [9.5e35] * 3
    evs = [e for e in (j(t / 10, n) for t, n in enumerate(seq)) if e]
    assert evs == [(7.144e35, 9.5e35)], evs
    j = surge()  # 17:24 — чтение скачет 7.744/3.744/2.744 каждые полсекунды, потом настоящий рост до 9.968: пауза есть
    seq = [7.744e38] * 30 + [3.744e38, 3.744e38, 7.744e38, 7.744e38, 2.744e38, 2.744e38] * 10 + [9.968e38] * 3
    evs = [e for e in (j(t / 10, n) for t, n in enumerate(seq)) if e]
    assert len(evs) == 1 and evs[0][1] == 9.968e38, evs
    j = surge()
    spend =[6e23 - i * 1e21 for i in range(20)]  # трата без паузы, потом ошибка e23 -> e25
    assert not any(j(t / 10, n) for t, n in enumerate(spend + [5.8e25, 5.8e25, 5.8e25]))
    assert number("6?833e12") == 6.833e12 and number("8864e12") == 8.864e12 and number("2?.092e19") == 2.092e19
    assert number("8,871e12") == 8.871e12 and number("8.86?4e12") is None and number("8.8?1e12") is None
    assert number("15.732e26") == 5.732e26 and number("9.5501e12") == 9.55e12 and number("9.5?6e12") is None and number("9.5?61e12") is None
    assert number("92,447") == 92447 and number("21.365") == 21365 and number("9.550e12") == 9.55e12 and number("1.2?") is None
    print("ok")


def check():
    """Прогон по кадрам, прочитанным глазами (digit_truth.json): запускать после каждой правки масок и правил."""
    truth = json.loads((HERE / "digit_truth.json").read_text(encoding="utf-8"))
    tpls, grads = [t for t in load() if t[0] != "?"], load_grad()
    wrong = [(k, v, read(cv2.imread(str(HERE / k), cv2.IMREAD_GRAYSCALE), tpls, None, grads)) for k, v in truth.items()]
    wrong = [w for w in wrong if number(w[2]) != number(w[1])]
    for w in wrong:
        print("ошибка:", *w)
    print(f"{len(truth) - len(wrong)} из {len(truth)} верно")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "select":
        select(" ".join(a[1:]) or "GemCraft Chasing Shadows")
    elif a and a[0] == "test":
        test()
    elif a and a[0] == "check":
        check()
    elif a and a[0] == "space":  # проверка: через 3 с пробел в окно игры (успейте переключиться или нет)
        time.sleep(3)
        press_space(find_window(json.loads(CFG.read_text(encoding="utf-8"))["title"]))
    else:
        loop(float(a[0]) if a else 0.1)  # 10 раз в секунду: круг ~40 мс (снимок 31, распознавание 5)
