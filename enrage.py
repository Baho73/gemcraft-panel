"""Злость волн бомбами. Пока шаг 1: найти светлые (неразозлённые) блоки волн и прочитать их подсказки.
Запуск (bash): cd "D:/Python/gems" && PYTHONUTF8=1 python enrage.py"""
import json
import math
import re
import time
import threading
from contextlib import contextmanager
from pathlib import Path

import ctypes

import cv2

import win32gui

import capture
from detectors import wave_blocks
from tooltip_ocr import ocr, tooltip_box
from tooltip_table import WAVE_NO, parse

HERE = Path(__file__).parent
EMPTY = (560, 480)  # точка без подсказки — увести курсор
PAUSE = 1.0  # с между щелчками-бросками
BOMBS = 1500  # цель: бомб на волну (6 бросков по 250)
YELLOW = 0.4  # доля жёлтого на кнопке бомбы: с Shift 0.80, без 0.00 (19.10)
PER = 250  # бомб за щелчок — стоит на кнопке Throw Gem Bomb
SLOT = (1052, 467)
# digit_grab.json → "enrage": mode "1500" — 6 бросков камнем 1; "limit" — камнем grade из ячейки «камень 61»,
# пока здоровье или броня волны не превысят limit (Иван, 22.10). grade — и для искр (21.10: 69, не 61)
SETTINGS = {"mode": "1500", "limit": 1e61, "grade": 69}  # D кладёт копию камня в правую нижнюю ячейку инвентаря (Иван, 20.10)


STOP = threading.Event()  # «Стоп пульта»: злость идёт в своём потоке и проверяет флаг перед каждым действием


class Stopped(RuntimeError):
    pass


def check():
    if STOP.is_set():
        raise Stopped("стоп пульта — злость прервана")


def snap(h, stop=True):
    """Кадр игры с экрана: PrintWindow — до секунд и грузит игру (ревью 1, №9). Пульт держит себя вне игры.
    stop=False — для уборки после стопа (выключить режим бомбы, вернуть камень)."""
    if stop:
        check()
    return capture.grab(h, color=True, screen=True)


def mouse(h, x, y, flags):
    """Событие мыши с координатами внутри (ABSOLUTE): живая мышь Ивана не уводит щелчок (ревью 1, №4)."""
    u = ctypes.windll.user32
    sx, sy = win32gui.ClientToScreen(h, (int(x), int(y)))
    vx, vy, vw, vh = (u.GetSystemMetrics(i) for i in (76, 77, 78, 79))  # виртуальный экран: все мониторы
    u.mouse_event(0x0001 | 0x8000 | 0x4000 | flags, (sx - vx) * 65535 // (vw - 1), (sy - vy) * 65535 // (vh - 1), 0, 0)


def _tip(h, x, y):
    check()
    # настоящий курсор: наведение сообщением перебивает живая мышь над игрой — пустые чтения (21.10)
    capture.real_hover(h, *EMPTY)
    time.sleep(0.4)
    capture.real_hover(h, x, y)
    # подсказка появляется с задержкой: 0.6 с не хватало — все чтения пустые (Иван, 16:27 21.10)
    end = time.time() + 2.5
    while True:
        time.sleep(0.3)
        full = snap(h)
        b = tooltip_box(full)
        if b:  # дать подсказке дорисоваться (проявляется плавно) и снять заново
            time.sleep(0.4)
            full = snap(h)
            return full, tooltip_box(full)
        if time.time() > end:
            return full, b


def dot():
    """Клавиша «.» — в игре включает/выключает подсказки (Иван, 19.10). Игра должна быть активной."""
    ctypes.windll.user32.keybd_event(0xBE, 0x34, 0, 0)
    ctypes.windll.user32.keybd_event(0xBE, 0x34, 2, 0)
    time.sleep(0.3)


READS = HERE / "waves" / "reads"  # датасет для дообучения numnet: каждое чтение подсказки волны (Иван, 23.10)
CTX = {}  # где читали: обход волн или щелчок N по волне K — пишется рядом с чтением


def wave_info(h, x, y):
    """Навести на блок волны и разобрать подсказку: (номер волны, {Здоровье, Бомб, ...}) или (None, None).
    Каждое чтение — в датасет: картинка подсказки в waves/reads/, строка в waves/reads.jsonl."""
    rec = {}
    k, row = _wave_info(h, x, y, rec)
    if "crop" in rec:
        try:
            READS.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}.png"
            cv2.imwrite(str(READS / name), rec["crop"])
            with open(HERE / "waves" / "reads.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "img": name, "xy": [x, y], **CTX,
                                    "k": k, "row": row, "lines": rec["lines"], "dim": rec.get("dim")},
                                   ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass  # датасет — не повод срывать злость
    return k, row


def _wave_info(h, x, y, rec):
    """Подсказки нет — нажать «.» (подсказки могли выключить) и проверить снова; не помогло — «.» назад."""
    full, b = _tip(h, x, y)
    if not b:
        dot()
        full, b = _tip(h, x, y)
        if not b:
            dot()
            return None, None
    bx, by, bw, bh = b
    lines = ocr(full[by:by + bh, bx:bx + bw])
    rec["crop"], rec["lines"] = full[by:by + bh, bx:bx + bw], lines
    t, k, row = parse(lines)
    if any("Spark" in s for s in lines[:3]):  # маленький блок — искра: «Drop gem bomb to heat spark» (20.10)
        k, row = lines[0], {"spark": True}
    elif t != "wave":
        m = re.search(WAVE_NO, lines[0] if lines else "")  # «Wave» бывает прочитано криво: «W CVE 77 of 262»
        for sc in (4, 2):  # номер бывает потерян целиком: «Wave of 262» на масштабе 3, на 4 и 2 читается (волна 130, 20.10)
            if m:
                break
            alt = ocr(full[by:by + bh, bx:bx + bw], scale=sc)
            m = re.search(WAVE_NO, alt[0] if alt else "")
        if not m:
            return None, None
        k, row = int(m.group(1)), {}
    dim = rec["dim"] = ocr(cv2.convertScaleAbs(full[by:by + bh, bx:bx + bw], alpha=1.5))  # число бомб тусклое — без осветления теряется
    m = re.search(r"([\d,]+)(?: \S+)? bombs? dropped", " ".join(dim), re.I)  # OCR: «6 gent bombs droppEd» (22.10)
    # злая волна — с «Rage modifiers» и «N gem bombs dropped»; высота не признак: чистая со свойствами — 324 (21.10)
    if not m and any("modif" in s.lower() or "dropp" in s.lower() for s in lines + dim):
        return None, None  # волна уже злая, а число не прочитано: 0 по ошибке — лишние броски в 143 (20.10)
    row["Бомб"] = int(m.group(1).replace(",", "")) if m else 0
    return k, row


def focus(h):
    """Сделать игру активной: подсказки по наведению сообщением игра показывает только активной (19.10).
    Windows не всегда отдаёт фокус фоновому процессу — нажатие Alt это разрешает."""
    try:
        win32gui.SetForegroundWindow(h)
    except Exception:
        ctypes.windll.user32.keybd_event(0x12, 0x38, 0, 0)  # Alt
        ctypes.windll.user32.keybd_event(0x12, 0x38, 2, 0)
        win32gui.SetForegroundWindow(h)
    time.sleep(0.2)


def bomb_on(full):
    """Режим бомбы включён: кнопка Throw Gem Bomb подсвечена (ярких пикселей 0.66 против 0.08 без режима, 19.10)."""
    return (cv2.cvtColor(full[97:130, 976:1010], cv2.COLOR_BGR2HSV)[:, :, 2] > 200).mean() > 0.3


def bomb_yellow(full):
    """Доля жёлтых пикселей кнопки бомбы: с зажатым Shift она жёлтая (бросок копиями), без Shift — белая."""
    hsv = cv2.cvtColor(full[97:130, 976:1010], cv2.COLOR_BGR2HSV)
    return float(((hsv[:, :, 0] >= 18) & (hsv[:, :, 0] <= 40) & (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 150)).mean())


def set_bomb(h, want):
    """B настоящей клавишей, пока режим бомбы не станет want (игра активна). B переключает режим туда-обратно."""
    for _ in range(3):
        if bomb_on(snap(h, False)) == want:
            return True
        ctypes.windll.user32.keybd_event(0x42, 0x30, 0, 0)
        ctypes.windll.user32.keybd_event(0x42, 0x30, 2, 0)
        time.sleep(0.3)
    return bomb_on(snap(h, False)) == want


@contextmanager
def armed(h):
    """B (режим бомбы) и зажатый Shift — щелчок по волне бросает копии камня, сколько стоит на кнопке
    Throw Gem Bomb. Подсказки волн в этом режиме видны (Иван, 23.10) — читать можно, не выходя из него."""
    kb = ctypes.windll.user32.keybd_event
    capture.real_hover(h, *EMPTY)
    focus(h)
    if not set_bomb(h, True):
        raise RuntimeError("режим бомбы не включился")
    kb(0x10, 0x2A, 0, 0)  # Shift вниз
    try:
        for _ in range(10):  # Иван: бросать, только когда кнопка жёлтая
            time.sleep(0.1)
            yel = bomb_yellow(snap(h))
            if yel > YELLOW:
                break
        else:
            raise RuntimeError(f"кнопка бомбы не пожелтела (жёлтого {yel:.2f})")
        yield
    finally:
        kb(0x10, 0x2A, 2, 0)  # Shift вверх — даже при ошибке, иначе залипнет
        time.sleep(0.2)
        set_bomb(h, False)  # режим остаётся после бросков — выключить, иначе следующее B его выключит


def hit(h, x, y):
    """Настоящий щелчок по волне (щелчок сообщением игра не принимает, 19.10). Нажатие несёт свои координаты."""
    check()
    mouse(h, x, y, 0)
    time.sleep(0.1)
    mouse(h, x, y, 0x0002)  # LEFTDOWN
    mouse(h, x, y, 0x0004)  # LEFTUP


def throw(h, points):
    """С зажатым Shift в режиме бомбы щёлкнуть по каждой волне — Shift не отпускать: так можно подряд (Иван)."""
    was_pos, was_fg = win32gui.GetCursorPos(), win32gui.GetForegroundWindow()
    try:
        with armed(h):
            for x, y in points:
                hit(h, x, y)
                time.sleep(PAUSE)
    finally:
        ctypes.windll.user32.SetCursorPos(*was_pos)
        if was_fg and was_fg != h:
            try:
                win32gui.SetForegroundWindow(was_fg)
            except Exception:
                pass


INV = (980, 142, 1070, 484)  # сетка инвентаря: x0, y0, x1, y1


def click(h, p):
    """Настоящий щелчок по точке окна игры."""
    # без check(): так камень возвращается на место и после «Стоп пульта»
    mouse(h, *p, 0)
    time.sleep(0.15)
    mouse(h, *p, 0x0002)
    mouse(h, *p, 0x0004)
    time.sleep(0.3)


def inv(h):
    x0, y0, x1, y1 = INV
    return snap(h, False)[y0:y1, x0:x1]


def grade(h, p):
    """Уровень камня под точкой p по подсказке «Grade N Gem»; не прочитан — None."""
    for _ in range(3):
        full, b = _tip(h, *p)
        if b:
            bx, by, bw, bh = b
            m = re.search(r"Grade (\d+) Gem", " ".join(ocr(full[by:by + bh, bx:bx + bw])), re.I)  # OCR: «GradE 61 GEM»
            if m:
                return int(m.group(1))
    return None


def must_grade(h, p, want):
    """Бросать только тем камнем, что задан: в точке камня 1 оказался камень 68 — им закидало волны (20.10)."""
    g = grade(h, p)
    if g != want:
        raise RuntimeError(f"в точке {list(p)} камень уровня {g}, нужен {want} — не бросаю; задай место кнопкой «камень {want}»")


def take(h, src, want=1):
    """Камень с поля в инвентарь: навести и Tab (Иван, 20.10). Возвращает точку, куда он лёг, — по разнице снимков."""
    must_grade(h, src, want)
    capture.real_hover(h, *src)
    time.sleep(0.3)
    before = inv(h)
    ctypes.windll.user32.keybd_event(0x09, 0x0F, 0, 0)  # Tab
    ctypes.windll.user32.keybd_event(0x09, 0x0F, 2, 0)
    time.sleep(0.4)
    diff = cv2.absdiff(before, inv(h)).max(axis=2) > 40
    ys, xs = diff.nonzero()
    if len(xs) < 50:
        raise RuntimeError("камень с поля в инвентарь не лёг (Tab)")
    return (INV[0] + int(xs.mean()), INV[1] + int(ys.mean())), before


def put_back(h, slot, src, empty):
    """Камень из инвентаря назад: взять щелчком и щёлкнуть по пустому месту, где он лежал (Иван, 20.10).
    Проверка: ячейка снова как до Tab (empty — снимок инвентаря тогда); нет — ещё раз, потом ошибка."""
    x, y = slot[0] - INV[0], slot[1] - INV[1]
    for _ in range(2):
        click(h, slot)
        click(h, src)
        capture.real_hover(h, *EMPTY)
        time.sleep(0.3)
        if cv2.absdiff(empty[y - 8:y + 8, x - 8:x + 8], inv(h)[y - 8:y + 8, x - 8:x + 8]).mean() < 10:
            return
    raise RuntimeError(f"камень не вернулся на поле — лежит в инвентаре {slot}")


def drag(h, a, b):
    """Настоящее перетаскивание мышью из точки a в точку b окна игры."""
    check()
    (ax, ay), (bx, by) = a, b
    mouse(h, ax, ay, 0)
    time.sleep(0.15)
    mouse(h, ax, ay, 0x0002)
    time.sleep(0.15)
    for i in range(1, 9):
        mouse(h, ax + (bx - ax) * i // 8, ay + (by - ay) * i // 8, 0)
        time.sleep(0.04)
    time.sleep(0.15)
    mouse(h, bx, by, 0x0004)
    time.sleep(0.3)


def steady(h, x, y, want, tries=6):
    """Подсказка волны want, пока два чтения подряд не совпадут по бомбам, здоровью и броне: бомбы падают не сразу.
    Под точкой оказалась другая волна (полосу прокрутили) — None (ревью 1, №8)."""
    last = None
    for _ in range(tries):
        k, row = wave_info(h, x, y)
        if row and k != want:
            print(f"под точкой волна {k}, а не {want}", flush=True)
            return None
        key = row and (row["Бомб"], row.get("Здоровье"), row.get("Броня"))
        if key and key == last:
            return row
        last = key
        time.sleep(0.5)
    return None


def to_limit(h, k, x, y, limit, row):
    """Щелчок за щелчком в волну k, пока здоровье или броня не превысят limit (Иван, 22.10); row — подсказка
    из обхода волн до камня. Предохранитель: прирост от щелчка 1 и 2 должен совпасть (±10%) — по нему число
    щелчков до порога +10%; дальше каждый щелчок сверяется с этим приростом. Не так — стоп.
    Возвращает (щелчков, строка подсказки)."""
    keys = ("Здоровье", "Броня")
    if not row or any(not row.get(c) for c in keys):
        print(f"волна {k}: здоровье/броня не прочитаны — не кидаю", flush=True)
        return 0, row
    with armed(h):  # режим бомбы и Shift — на всю волну: щелчок, подсказка, решение (Иван, 23.10)
        return _to_limit(h, k, x, y, limit, row, keys)


def _to_limit(h, k, x, y, limit, row, keys):
    first, way, cap, n = None, None, None, 0
    while max(row[c] for c in keys) <= limit:
        if cap is not None and n >= cap:
            print(f"волна {k}: {n} щелчков — предел по расчёту, порог не достигнут — стоп", flush=True)
            break
        hit(h, x, y)
        n += 1
        CTX.update(stage="щелчок", wave=k, click=n)
        time.sleep(1.0)
        new = steady(h, x, y, k)
        if not new or any(not new.get(c) for c in keys) or new["Бомб"] <= row["Бомб"]:
            print(f"волна {k}: после щелчка {n} подсказка не та ({new}) — стоп", flush=True)
            break
        step = {c: (new[c] / row[c], new[c] - row[c]) for c in keys}
        row = new
        if first is None:
            first = step
            continue
        grow = [c for c in keys if first[c][1] > 0]
        if not grow:
            print(f"волна {k}: бросок не растит здоровье и броню — стоп", flush=True)
            break
        # множитель: сравниваю показатели степени — при множителе 1.001 отношение самих множителей ничего не ловит
        mult = all(first[c][0] > 1 and abs(math.log(step[c][0]) / math.log(first[c][0]) - 1) <= 0.1 for c in grow)
        add = all(abs(step[c][1] / first[c][1] - 1) <= 0.1 for c in grow)
        if way is None:
            way = "mult" if mult else "add" if add else None
            if way is None:
                print(f"волна {k}: щелчки 1 и 2 дали разный прирост {first} / {step} — стоп", flush=True)
                break
            need = min(math.log(limit / row[c]) / math.log(first[c][0]) if way == "mult"
                       else (limit - row[c]) / first[c][1] for c in grow)
            cap = n + math.ceil(max(need, 0) * 1.1) + 1
            print(f"волна {k}: прирост {way}, до {limit:g} ещё ~{need:.1f} щелчков, предел {cap}", flush=True)
        elif not (mult if way == "mult" else add):
            print(f"волна {k}: щелчок {n} дал другой прирост {step} — стоп", flush=True)
            break
    return n, row


def throw_copy(h, src, points, want):
    """Маленьким волнам — по одному камню (Иван, 20.10): навести на камень src, D — копия ложится в SLOT,
    B, перетащить копию на волну. Оригинал остаётся на месте."""
    was_pos, was_fg = win32gui.GetCursorPos(), win32gui.GetForegroundWindow()
    focus(h)
    try:
        for x, y in points:
            set_bomb(h, False)
            must_grade(h, src, want)
            capture.real_hover(h, *src)
            time.sleep(0.3)
            sx, sy = SLOT
            before = snap(h)[sy - 10:sy + 10, sx - 10:sx + 10]
            ctypes.windll.user32.keybd_event(0x44, 0x20, 0, 0)  # D
            ctypes.windll.user32.keybd_event(0x44, 0x20, 2, 0)
            time.sleep(0.4)
            after = snap(h)[sy - 10:sy + 10, sx - 10:sx + 10]
            if cv2.absdiff(before, after).mean() < 10:
                raise RuntimeError("копия камня не появилась в правой нижней ячейке")
            if not set_bomb(h, True):
                raise RuntimeError("режим бомбы не включился")
            drag(h, SLOT, (x, y))
    finally:
        set_bomb(h, False)
        ctypes.windll.user32.SetCursorPos(*was_pos)
        if was_fg and was_fg != h:
            try:
                win32gui.SetForegroundWindow(was_fg)
            except Exception:
                pass


def enrage_all():
    """Длинным волнам на экране — добросить до BOMBS бомб; брошенное читается из подсказки («N gem bombs dropped»).
    Все щелчки — одним зажатием Shift (Иван: шифтом бросаем во все волны)."""
    cfg = json.load(open(HERE / "digit_grab.json", encoding="utf-8"))
    h = capture.find_window(cfg["title"])
    was_fg = win32gui.GetForegroundWindow()
    try:
        focus(h)
        _enrage(h, cfg.get("gems", {}), {**SETTINGS, **cfg.get("enrage", {})})
    finally:
        if was_fg and was_fg != h:
            try:
                win32gui.SetForegroundWindow(was_fg)
            except Exception:
                pass


THROWN_LOG = HERE / "waves" / "thrown.tsv"  # журнал для статистики: время, волна, бросков, бомб за бросок


def sure(h, x, y, tries=5):
    """Прочитать подсказку блока, пока два чтения не совпадут по номеру волны и числу бомб.
    Одно чтение врёт: 1476 бывало прочитано меньшим числом — лишний бросок в 128; пустое — пропуск 130 (20.10).
    Не совпали за tries чтений — (None, None): волну не трогать."""
    reads = []
    for _ in range(tries):
        k, row = wave_info(h, x, y)
        if row:
            key = (k, row["Бомб"])
            if key in reads:
                return k, row
            reads.append(key)
    print(y, "чтения не совпали:", reads, flush=True)
    return None, None


def _enrage(h, gems, opt):
    # что-то не прочиталось — сразу стоп, ничего больше не кидать; пульт оставит игру на паузе (Иван, 22.10)
    plan, small, seen = [], [], set()
    focus(h)  # подсказки игра показывает только активной: без этого все чтения пустые (19:58, 20.10)
    set_bomb(h, False)  # в режиме бомбы подсказок над волнами нет — все чтения пустые (13:36, 21.10)
    for top, bot, (x, y), kind, light in wave_blocks(snap(h)):
        CTX.clear()
        CTX["stage"] = "обход"
        k, row = sure(h, x, y)  # вид решает подсказка: у края полосы искра тоже «обрез» (Иван, 20.10)
        if not row:
            raise RuntimeError(f"строка {y}: подсказка не прочиталась — стоп")
        if k in seen:
            continue
        seen.add(k)
        if row.get("spark"):
            if row["Бомб"] == 0:
                small.append((k, x, y, 1))
            continue
        b = row["Бомб"]
        if b > 0:  # бомбы в волне уже есть — не трогать, добрасывать нельзя (Иван, 21.10)
            continue
        n = round(BOMBS / PER)
        if n > 0:
            plan.append((k, x, y, n, row))
    capture.hover(h, *EMPTY)
    lim = opt["mode"] == "limit"
    src, want = ("61", opt["grade"]) if lim else ("1", 1)
    if plan and src not in gems:
        print("волны", [p[0] for p in plan], f"— не задано место камня {src}, пропуск")  # чем бросать — не проверить
        plan = []
    if plan:
        focus(h)
        slot, empty = take(h, gems[src], want)  # внутри — проверка «Grade N Gem» до Tab
        try:
            # волны прочитаны один раз до камня — повторной сверки нет: лишнее дёргание (Иван, 23.10)
            if lim:
                done = []
                for k, x, y, n, row in plan:
                    n, row = to_limit(h, k, x, y, opt["limit"], row)
                    done.append((k, x, y, n))
                    with open(THROWN_LOG, "a", encoding="utf-8") as f:
                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{k}\t{n}\tкамень {want} до {opt['limit']:g}"
                                f"\t{row and (row['Бомб'], row.get('Здоровье'), row.get('Броня'))}\n")
                    if not (row and max(row.get("Здоровье") or 0, row.get("Броня") or 0) > opt["limit"]):
                        raise RuntimeError(f"волна {k}: порог не достигнут за {n} щелчков — стоп")
                plan = done
            else:
                plan = [p[:4] for p in plan]
                with open(THROWN_LOG, "a", encoding="utf-8") as f:
                    for k, x, y, n in plan:
                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{k}\t{n}\t{PER}\n")
                if plan:
                    throw(h, [(x, y) for k, x, y, n in plan for _ in range(n)])
        finally:
            focus(h)
            put_back(h, slot, gems[src], empty)
    if small and "61" not in gems:
        print("маленькие волны", [k for k, *_ in small], "— не задано место камня 61, пропуск")
        small = []
    if small:
        throw_copy(h, gems["61"], [(x, y) for k, x, y, n in small], opt["grade"])
    time.sleep(0.5)
    for k, x, y, n in plan + small:
        _, row = wave_info(h, x, y)
        print(f"волна {k} +{n} бросков, бомб:", row and row["Бомб"], flush=True)
    capture.hover(h, *EMPTY)


def main():
    h = capture.find_window(json.load(open(HERE / "digit_grab.json", encoding="utf-8"))["title"])
    for top, bot, (x, y), kind, light in wave_blocks(snap(h)):
        if light:
            k, row = wave_info(h, x, y)
            print(y, kind, k, row and {c: row.get(c) for c in ("Количество", "Здоровье", "Броня", "Мана за убийство")})
    capture.hover(h, *EMPTY)


if __name__ == "__main__":
    main()
