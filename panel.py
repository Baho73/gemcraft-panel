# START_MODULE_CONTRACT
#   PURPOSE: Окно-пульт поверх игры и покадровый цикл (10 раз в секунду): снимок -> детекторы и мана -> события паузы,
#            запуск цепочки по заморозке; галочки, поля и точки A-E хранятся в digit_grab.json.
#   SCOPE: Только проводка и интерфейс. Решение о пробеле — M-PAUSE-POLICY (request/observe), шаги цепочки — M-CHAIN.
#          Ошибка кадра пишется в digit_errors.log и не останавливает опрос.
#   DEPENDS: M-CAPTURE, M-MANA-OCR, M-DETECTORS, M-PAUSE-POLICY, M-CHAIN
#   LINKS: M-CLI, V-M-PANEL
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   loop - окно управления и цикл опроса; журналы digit_events.tsv, digit_frames/, digit_library/, freeze/, waves/
#   HERE - папка проекта
#   CFG - digit_grab.json: окно, область маны, галочки, точки цепочки
#   UNK - незнакомые символы (digit_unknown/)
#   FRAMES - кадры смены числа и log.tsv (digit_frames/)
#   LIB - все изменившиеся кадры маны (digit_library/)
#   WAVE_MARGIN - запас строк: волна ближе к порогу — цепочка не снимает паузу
#   HOVER_WAIT - пауза после наведения, с
#   STEP_GAP - пауза между шагами цепочки, с
#   FROZEN - кадров неподвижной карты = пауза (кнопок не видно)
# END_MODULE_MAP
import builtins, ctypes, json, re, time, winsound
from pathlib import Path

import cv2, numpy as np, win32gui

from capture import find_window, grab
from chain import Chain
from detectors import FIELD, FREEZE_SAT, filled, freeze_level, game_state, icon_full, light_top, pluses, swirls, wave_blocks
from mana_ocr import load, load_grad, mana_text, number, read, recent, steady, surge
from pause_policy import SPACE_WAIT, PausePolicy
from tooltip_grab import TooltipGrab

HERE = Path(__file__).parent
CFG = HERE / "digit_grab.json"
UNK = HERE / "digit_unknown"
FRAMES = HERE / "digit_frames"
LIB = HERE / "digit_library"
HOVER_WAIT = 5.0    # после наведения мыши ждём столько, потом щелчок в той же точке
STEP_GAP = 1.0      # пауза после каждого шага цепочки, с (Иван: пауза, секунда, 1, секунда, щелчок, секунда)
WAVE_MARGIN = 5    # в конце цепочки паузу не снимаем, если светлая волна ближе стольких строк к порогу
FROZEN = 3         # столько кадров подряд карта не меняется — игра на паузе, пробел не шлём


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
    st = dict(last=None, prev=None, n=0, pend=None, unk_saved=0.0, mana="", map=None, diffs=[], start=time.time(), seen=recent(), above_raw=False, above_known=False, frames=[])
    push, jump, above_ev, wave_ev = steady(), surge(), filled(), filled()
    full_ev = {k: filled() for k in icons}
    plus_ev = {k: filled() for k in icons}

    import win32api, win32event, winerror
    st["lock"] = win32event.CreateMutex(None, False, "GemCraftPausePanel")  # держится, пока жив процесс
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        builtins.print("пульт уже запущен (блокировка Windows) — второй не запускаю", flush=True)
        mine = []
        win32gui.EnumWindows(lambda h, _: mine.append(h) if win32gui.GetWindowText(h).startswith("GemCraft: ") else None, None)
        if mine:
            win32gui.ShowWindow(mine[0], 9)  # SW_RESTORE
        return
    mine = []  # уже запущено? второе окно слало бы двойные пробелы — разворачиваем первое и выходим
    win32gui.EnumWindows(lambda h, _: mine.append(h) if win32gui.GetWindowText(h).startswith("GemCraft: ") else None, None)
    if mine:
        win32gui.ShowWindow(mine[0], 9)  # SW_RESTORE
        builtins.print("окно уже открыто — развернул его", flush=True)
        return
    was_active = win32gui.GetForegroundWindow()  # окно скрипта при запуске забирает фокус, а игра без фокуса встаёт на паузу
    root = tk.Tk()
    root.title("GemCraft: пауза")
    root.attributes("-topmost", True)
    logbox = []  # Иван: «где терминал?» — журнал (всё, что печатается) показываем внизу окна

    def print(*a, **k):  # ponytail: подменяем print внутри loop — все вложенные журналы идут и в окно
        builtins.print(*a, **k)
        if logbox:
            logbox[0].insert("end", " ".join(map(str, a)) + "\n")
            logbox[0].delete("1.0", "end-201l")  # держим последние ~200 строк
            logbox[0].see("end")

    def give_focus_back():
        try:
            win32gui.SetForegroundWindow(was_active)
        except Exception:  # Windows иногда запрещает отдавать фокус — не страшно
            pass
    root.after(300, give_focus_back)

    def hold_toggle():
        """Иван: «гонки с тобой» — одна кнопка: оборвать цепочку, сбросить отложенное, поставить игру на паузу
        и больше ничего самим не жать. Повторное нажатие — пульт снова работает (игру не трогает)."""
        if policy.hold:
            policy.hold_off()
            hold_btn.config(text="СТОП ПУЛЬТА (пауза)", bg="#c62828")
            print(time.strftime("%H%M%S"), "ПУЛЬТ: снова работает", flush=True)
        else:
            chain.stop("кнопка «Стоп пульта»")
            policy.hold_on()
            hold_btn.config(text="Пульт на стопе — нажать, чтобы продолжить", bg="#9e9e9e")
            print(time.strftime("%H%M%S"), "ПУЛЬТ: стоп — цепочка оборвана, отложенное сброшено, игра на паузу",
                  flush=True)
    hold_btn = tk.Button(root, text="СТОП ПУЛЬТА (пауза)", bg="#c62828", fg="white", font=("Segoe UI", 12, "bold"),
                         command=hold_toggle)
    hold_btn.pack(fill="x", padx=8, pady=(6, 4))
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
        # человек пишет как угодно: 1.2E36, 1,2e36, 1 000 000, 1,000,000; number() — только для формата игры d.ddd
        t = txt.replace(" ", "").lower()
        t = t.replace(",", ".") if "e" in t else t.replace(",", "")
        try:
            v = float(t) if t else None
        except ValueError:
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
    fdelay_var = tk.StringVar(value=str(mc.get("freeze_delay", 1.0)))
    tk.Entry(crow, textvariable=fdelay_var, width=4).pack(side="left")
    tk.Label(crow, text="с").pack(side="left")
    freeze_now = tk.StringVar(value="")
    tk.Label(crow, textvariable=freeze_now, fg="gray30").pack(side="left", padx=6)
    tk.Label(crow, text="порог (ниже — есть)").pack(side="left")
    sat_var = tk.StringVar(value=str(mc.get("freeze_sat", FREEZE_SAT)))  # Иван: порог заморозки настраивается
    tk.Entry(crow, textvariable=sat_var, width=4).pack(side="left")
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
    # Иван, 11.10: цепочка не должна отнимать мышь — наводим сообщением; галочка — если игра так не понимает
    hover_real_var = tk.BooleanVar(value=mc.get("hover_real", False))

    def save_hover():
        mc["hover_real"] = hover_real_var.get()
        CFG.write_text(json.dumps(cfg), encoding="utf-8")
    tk.Checkbutton(trow, text="наводить курсором", variable=hover_real_var, command=save_hover).pack(side="left")
    screen_var = tk.BooleanVar(value=cfg.get("screen_grab", True))  # снимать с экрана: быстро, игру не грузит

    def save_screen():
        cfg["screen_grab"] = screen_var.get()
        CFG.write_text(json.dumps(cfg), encoding="utf-8")
    tk.Checkbutton(trow, text="снимать с экрана", variable=screen_var, command=save_screen).pack(side="left")
    # Иван, 15.10: подсказки под мышью — в tooltips/, разбор отдельно (tooltip_table.py)
    tips_var, tips = tk.BooleanVar(value=cfg.get("tooltips", True)), TooltipGrab()

    def save_tips():
        cfg["tooltips"] = tips_var.get()
        CFG.write_text(json.dumps(cfg), encoding="utf-8")
    tk.Checkbutton(trow, text="записывать подсказки", variable=tips_var, command=save_tips).pack(side="left")
    tips_lamp = tk.Label(trow, text=" захват ", fg="white", bg="gray", font=("Segoe UI", 9, "bold"))
    tips_lamp.pack(side="left", padx=4)

    def lamp():
        """Зелёный — подсказка под мышью записана; розовый — жду подсказку; серый — запись выключена."""
        bg = "gray" if not tips_var.get() else "#2e9e44" if tips.last else "#e88aa8"
        if tips_lamp["bg"] != bg:
            tips_lamp.config(bg=bg)

    def keep_clear():
        """С экрана снимается то, что видно: пульт не должен лежать на игре — отодвигаем его левее (или правее)."""
        gl, gt, gr, gb = win32gui.GetWindowRect(hwnd)
        pl, pt, pr, pb = win32gui.GetWindowRect(int(root.wm_frame(), 16))  # рамка окна пульта
        if pr <= gl or pl >= gr or pb <= gt or pt >= gb:
            return
        x = gl - (pr - pl)
        if x < ctypes.windll.user32.GetSystemMetrics(76):  # SM_XVIRTUALSCREEN: слева места нет — ставим справа
            x = gr
        root.geometry(f"+{x}+{pt}")
        print(time.strftime("%H%M%S"), "ПУЛЬТ: лежал на игре — отодвинул", flush=True)
    gap_var = tk.StringVar(value=str(mc.get("step_gap", STEP_GAP)))
    wait_var = tk.StringVar(value=str(mc.get("hover_wait", HOVER_WAIT)))
    # Иван, 10.10: игра под нагрузкой откликается на пробел дольше 20 с — сколько ждать, настраивается
    reply_var = tk.StringVar(value=str(mc.get("space_wait", SPACE_WAIT)))
    for text, var in (("между шагами, с", gap_var), ("после наведения, с", wait_var), ("ждать отклика на пробел, с", reply_var)):
        tk.Label(trow, text=text).pack(side="left", padx=(8, 2))
        tk.Entry(trow, textvariable=var, width=4).pack(side="left")

    def secs(var, default):
        try:
            return max(0.1, float(var.get().replace(",", ".")))
        except ValueError:
            return default

    def save_macro(*_):
        mc.update(real_mouse=real_var.get(), step_gap=secs(gap_var, STEP_GAP), hover_wait=secs(wait_var, HOVER_WAIT),
                  freeze_delay=secs(fdelay_var, 1.0), freeze_sat=secs(sat_var, FREEZE_SAT),
                  space_wait=secs(reply_var, SPACE_WAIT))
        CFG.write_text(json.dumps(cfg), encoding="utf-8")
        policy.wait = secs(reply_var, SPACE_WAIT)
    for v in (real_var, gap_var, wait_var, fdelay_var, sat_var, reply_var):
        v.trace_add("write", save_macro)
    # START_BLOCK_CHAIN_WIRING
    # политика паузы — единственная, кто жмёт пробел; цепочка — свои шаги, пробелы через политику
    policy = PausePolicy.for_window(hwnd, wait=secs(reply_var, SPACE_WAIT),
                                    log=lambda m: print(time.strftime("%H%M%S"), "ПАУЗА", m, flush=True))

    def mlog(text):
        print(time.strftime("%H%M%S"), "ЦЕПОЧКА", text, f"[игра: {st.get('state')}]", flush=True)

    def after_click(point):
        if point == "A":  # после первого щелчка появляется заморозка — собираем образцы
            freeze_sample("до", st["frames"][-1][1] if st["frames"] else None)
            for delay in (1000, 3000):
                root.after(delay, lambda d=delay: freeze_sample(f"через {d // 1000} с",
                                                                st["frames"][-1][1] if st["frames"] else None))

    def blocker():
        """Иван: условие паузы держится сейчас — в конце цепочки паузу не снимать (иначе игру больше ничто не остановит).
        Только условия-состояния с включённой галочкой; плюс левого — повод цепочки, не блокер."""
        top, wy = st.get("wave_top"), wave_y()
        if checks["wave"].get() and top is not None and wy is not None and top <= wy + WAVE_MARGIN:
            # с запасом: волна ползёт ~1 строку за 2 с, на 63 при пороге 60 игра встала бы уже через 7 с (Иван, 7.10)
            return f"светлая волна у строки {wy} (с {top}, запас {WAVE_MARGIN})"
        if checks["mana_above"].get() and st["above_raw"]:
            return "мана выше порога"
        if checks["swirl"].get() and st.get("swirls"):
            return "водоворот в жёлтой рамке"
        for k in icons:
            if checks[f"{k}_full"].get() and st.get(f"full_{k}"):
                return f"{names.get(k, k)} индикатор заполнен"
        return None

    chain = Chain(hwnd, policy, mc["points"],
                  lambda: {"real": real_var.get(), "hover_real": hover_real_var.get(),"gap": secs(gap_var, STEP_GAP), "wait": secs(wait_var, HOVER_WAIT)},
                  lambda: st.get("state"), root.after, mlog, lambda text: status.set(text),
                  on_change=lambda on: mbtn.config(text="Стоп цепочки" if on else "Пуск цепочки"),
                  after_click=after_click, blocker=blocker)
    mbtn = tk.Button(prow, text="Пуск цепочки",
                     command=lambda: chain.stop("вручную") if chain.on else chain.start("вручную"))
    mbtn.pack(side="right")
    # END_BLOCK_CHAIN_WIRING

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
    params = tk.StringVar(value="")  # Иван: все параметры разом, как мана — что сейчас видит окно
    tk.Label(root, textvariable=params, justify="left", anchor="w", font=("Consolas", 12, "bold")).pack(fill="x", padx=8)
    pic = tk.Label(root)
    pic.pack(padx=8)
    status = tk.StringVar(value="захват идёт")
    tk.Label(root, textvariable=status, anchor="w", fg="gray30").pack(fill="x", padx=8, pady=(0, 6))
    logbox.append(tk.Text(root, height=10, width=90, font=("Consolas", 9)))
    logbox[0].pack(fill="both", expand=True, padx=8, pady=(0, 6))

    # START_BLOCK_EVENTS
    def game_now():
        """Состояние игры для политики: по кнопкам; кнопок нет — по неподвижной карте, иначе None (вслепую не жмём)."""
        if st.get("state"):
            return st["state"]
        return "pause" if paused() else None

    def log_event(stamp, t, text, cols, why):
        with open(HERE / "digit_events.tsv", "a", encoding="utf-8") as f:
            f.write("\t".join([stamp, *map(str, cols), f"{time.time() - t:.3f}", why or "space"]) + "\n")
        hm = f"{stamp[:2]}:{stamp[2:4]}:{stamp[4:6]}"
        print(stamp, text, f"— пробел НЕ отправлен: {why}" if why else "— пробел отправлен", flush=True)
        status.set(f"{hm} {text} — " + (f"без пробела ({why})" if why else "пауза"))

    def event(stamp, t, text, *cols):
        """Событие паузы -> решение принимает M-PAUSE-POLICY (сейчас / присоединить к паузе в пути / отложить)."""
        if t - st["start"] < 1.0:
            return log_event(stamp, t, text, cols, "старт: это уже было на экране")  # было до запуска — не событие
        r = policy.request(text, cols, game_now())
        if r == "sent":
            winsound.MessageBeep()  # асинхронно, не тормозит
        log_event(stamp, t, text, cols, {"sent": None, "joined": "пауза уже в пути",
                                         "deferred": "отложено до хода игры", "held": "пульт на стопе"}[r])

    def released(stamp, t, evs):
        """Отложенные события дали один пробел, когда игра снова пошла."""
        winsound.MessageBeep()
        for text, cols in evs:
            log_event(stamp, t, f"{text} (отложенное)", cols, None)
    # END_BLOCK_EVENTS

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
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            if isinstance(e, MemoryError):  # Windows исчерпала память (14.10: WSL и др.) — не баг пульта, кадр пропущен
                print(time.strftime("%H%M%S"), "Windows не хватило памяти, кадр пропущен", flush=True)
            else:
                print(time.strftime("%H%M%S"), "ОШИБКА в кадре, опрос продолжается:\n" + err, flush=True)
            with open(HERE / "digit_errors.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + err + "\n")
            status.set("ошибка в кадре (см. digit_errors.log), опрос продолжается")
            root.after(int(interval * 1000), step)

    def step_inner():
        t = time.time()
        stamp = time.strftime("%H%M%S", time.localtime(t)) + f"_{int(t * 1000) % 1000:03d}"
        try:
            if screen_var.get():
                keep_clear()
            full = grab(hwnd, None, color=True, screen=screen_var.get())  # окно целиком: из него и мана, и индикаторы
        except Exception as e:  # окно свернули или закрыли — ждём, не падаем
            status.set(f"нет снимка окна: {e}")
            root.after(1000, step)
            return
        if tips_var.get() and (name := tips.feed(hwnd, full)):
            print(stamp, "подсказка записана:", name)
        lamp()
        mp = cv2.resize(cv2.cvtColor(full[50:600, 40:960], cv2.COLOR_BGR2GRAY), (184, 110),
                        interpolation=cv2.INTER_AREA).astype(np.int16)  # карта уменьшенно: движется ли игра
        if st["map"] is not None:
            st["diffs"] = (st["diffs"] + [float(np.abs(mp - st["map"]).mean())])[-10:]
        st["map"] = mp
        st["state"] = game_state(full)
        evs = policy.observe(game_now())  # снять ожидание отклика, выпустить отложенные события
        if evs:
            released(stamp, t, evs)
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
            st[f"full_{k}"] = icon_full(icon)
            if full_ev[k](st[f"full_{k}"]) and checks[f"{k}_full"].get():
                event(stamp, t, f"{names.get(k, k)} индикатор заполнен", "indicator", k, "full")
            np_ = pluses(icon)
            if st.get(f"plus_{k}") != np_:  # разбор: каждое изменение числа плюсиков с картинкой значка
                st[f"plus_{k}"] = np_
                cv2.imwrite(str(HERE / "icons" / f"plus_{k}_{stamp}_{np_}.png"), icon)
            plus_new = plus_ev[k](np_ >= 1)
            if plus_new and checks[f"{k}_plus"].get():
                fl = freeze_level(full, mc["points"].get("A")) if k == "left" else None
                if fl is not None and fl < secs(sat_var, FREEZE_SAT):  # Иван: заморозка лежит — плюс левого не повод для паузы
                    mlog(f"левый: первый плюс, но заморозка есть ({fl:.0f}) — без паузы")
                elif (k == "left" and checks["macro"].get() and st.get("state") == "run" and not policy.hold
                      and t - st["start"] >= 1.0):  # первую секунду — плюс уже был на экране, не событие
                    # плюс левого без заморозки — сразу цепочка: её первый шаг и есть пауза (Иван, 8.10: «не встала
                    # на паузу» — ждать 5 с хода игры здесь нельзя). Не стартовала — обычная пауза по плюсу
                    if not chain.on and not chain.start("первый плюс левого, заморозки нет"):
                        event(stamp, t, f"{names.get(k, k)} индикатор: первый плюс", "indicator", k, "plus")
                else:
                    event(stamp, t, f"{names.get(k, k)} индикатор: первый плюс", "indicator", k, "plus")
        frz = freeze_level(full, mc["points"].get("A"))  # насыщенность в круге вокруг A: ~94 заморозка, ~155 нет
        if frz is not None:
            frozen = frz < secs(sat_var, FREEZE_SAT)
            freeze_now.set(f"заморозка: {'есть' if frozen else 'нет'}, тон {frz:.0f}")
            # не по моменту «кончилась», а по состоянию «нет уже N с»: иначе после запуска окна без заморозки
            # цепочка ждала бы конца заморозки, которой без цепочки не будет
            if frozen or st.get("state") != "run":
                st["nofreeze_since"] = None  # на паузе время не идёт
            elif st.get("nofreeze_since") is None:
                st["nofreeze_since"] = t
            lp = st.get("plus_left", 0)
            since, delay = st.get("nofreeze_since"), secs(fdelay_var, 1.0)
            # почему цепочка не стартует — первой же причиной, в окно и (при смене) в журнал
            why = ("идёт" if chain.on else "галочка выключена" if not checks["macro"].get()
                   else "пульт на стопе" if policy.hold else f"заморозка есть (тон {frz:.0f})" if frozen
                   else f"игра не идёт ({st.get('state')})" if st.get("state") != "run"
                   else f"ход без заморозки {t - since:.0f} из {delay:g} с" if t - since < delay
                   else "нет плюса у левого" if not lp else "жду отклика на пробел" if policy.busy() else "")
            if why == "":
                why = ("идёт" if chain.start(f"заморозки нет {t - since:.0f} с, плюсов у левого: {lp}")
                       else "не запустилась — см. строку статуса")
            key = re.sub(r"\d+", "", why)  # секунды и тон меняются каждый кадр — в журнал только смену причины
            if key != st.get("chain_why_key"):
                st["chain_why_key"] = key
                if why != "идёт":
                    mlog(f"ждёт: {why}")
            st["chain_why"] = why
        found = {(x // 8, y // 8) for x, y in swirls(full)}  # значки стоят на месте; //8 — на дрожь в пиксель
        swirl_now.set(f"сейчас: {len(found)}")
        st["swirls"] = len(found)
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
        wy = wave_y()
        if (top, wy) != (st.get("wave_top"), st.get("wave_wy")):  # разбор: каждое изменение уровня или порога — в журнал
            st["wave_top"], st["wave_wy"] = top, wy
            with open(HERE / "waves" / "top.tsv", "a", encoding="utf-8") as f:
                f.write(f"{stamp}\t{top}\t{wy}\n")
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
        game = {"run": "идёт", "pause": "пауза"}.get(st.get("state"), "не вижу кнопок")
        st["dt"] = 0.9 * st.get("dt", interval) + 0.1 * (t - st.get("t_prev", t - interval))  # сглаженный шаг кадра
        st["t_prev"] = t
        lines = [f"Игра: {game} | кадр раз в {st['dt']:.1f} с" + ("  — ИГРА ТОРМОЗИТ" if st["dt"] > 1 else "")]
        lines += [f"{names.get(k, k).capitalize()} индикатор: плюсов {st.get(f'plus_{k}', 0)}, "
                  f"рамка {'есть' if st.get(f'full_{k}') else 'нет'}" for k in icons]
        lines.append(f"Светлое с: {'—' if top is None else top} (порог строки {'—' if wy is None else wy})")
        lim_sat = secs(sat_var, FREEZE_SAT)
        lines.append(f"Заморозка: {'есть' if frz < lim_sat else 'нет'}, тон {frz:.0f} (порог {lim_sat:g})"
                     if frz is not None else "Заморозка: точка A не задана")
        lines.append(f"Водоворотов в рамке: {len(found)}")
        lines.append(f"Цепочка: {'идёт' if chain.on else 'ждёт — ' + st.get('chain_why', '?')}, "
                     f"отложено событий: {len(policy.pending)}"
                     + (f", жду отклика на пробел ({policy.awaiting})" if policy.awaiting else ""))
        params.set("\n".join(lines))
        png = cv2.imencode(".png", cv2.resize(bgr, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))[1]
        pic.image = tk.PhotoImage(data=base64.b64encode(png.tobytes()))
        pic.configure(image=pic.image)
        root.after(max(1, int((interval - (time.time() - t)) * 1000)), step)

    root.bind("<Escape>", lambda e: root.destroy())
    root.after(0, step)
    root.mainloop()
