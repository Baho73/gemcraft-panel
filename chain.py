# START_MODULE_CONTRACT
#   PURPOSE: Цепочка постановки заморозки, один раз: пауза -> 1-A, 2-B, 2-C, 3-D (D дважды) -> курсор в E -> «.» -> снять паузу.
#   SCOPE: Пошаговый автомат на таймере окна. Пробелы — только через M-PAUSE-POLICY (chain_space), клавиши и курсор —
#          через M-CAPTURE. Прерванная цепочка ничего не жмёт и не щёлкает (решение Ивана), только сообщает.
#   DEPENDS: M-PAUSE-POLICY, M-CAPTURE
#   LINKS: M-PANEL, V-M-CHAIN
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   MACRO - шаги цепочки между паузой и снятием паузы
#   VK - коды клавиш, которые не совпадают с ord(символа)
#   Chain - автомат: start / stop / tick; on — идёт ли
#   self_check - прерывание посреди цепочки не шлёт ни одного действия (вызывается из test)
# END_MODULE_MAP
from capture import press_key, hover, click, real_hover, real_click

VK = {".": 0xBE}  # у точки свой код клавиши (VK_OEM_PERIOD); ord(".") = 0x2E — это Delete
MACRO = []  # цепочка Ивана: клавиша, пауза, навести курсор на точку, пауза после наведения, щелчок — для 1-A, 2-B, 2-C, 3-D
for _k, _p in (("1", "A"), ("2", "B"), ("2", "C"), ("3", "D")):
    MACRO += [("key", _k), ("hover", _p), ("click", _p)]
MACRO += [("key", "3"), ("click", "D")]  # Иван: последний щелчок (D) — дважды, второй тоже после клавиши 3
MACRO.append(("away", "E"))  # увести курсор на пустое место E: подсказка камушка закрывала заморозку
MACRO.append(("key", "."))  # и точка — прячет информацию о камушке


class Chain:
    """Один прогон цепочки. Всё внешнее передаётся снаружи: окно, политика паузы, точки, настройки, таймер, журнал."""

    def __init__(self, hwnd, policy, points, settings, game, schedule, log, status, on_change=lambda on: None,
                 after_click=lambda point: None, blocker=lambda: None):
        # blocker(): причина не снимать паузу в конце (текст) или None
        # points: живой словарь точек {"A": [x, y], …}; settings(): {"real": bool, "gap": с, "wait": с}
        # game(): 'pause'|'run'|None; schedule(мс, функция) — таймер окна; log/status — журнал и строка окна
        self.hwnd, self.policy, self.points, self.settings = hwnd, policy, points, settings
        self.game, self.schedule, self.log, self.status = game, schedule, log, status
        self.on_change, self.after_click, self.blocker = on_change, after_click, blocker
        self.on, self.i, self.tries = False, 0, 0

    @staticmethod
    def steps():
        return [("pause", None)] + MACRO + [("resume", None), ("done", None)]

    # START_CONTRACT: start
    #   PURPOSE: Запустить один прогон, если точки A-D заданы и политика не ждёт отклика на свой пробел.
    #   SIDE_EFFECTS: policy.chain_begin(); первый шаг — пробел на паузу через policy.chain_space
    # END_CONTRACT: start
    def start(self, why):
        if self.on:
            return False
        if not all(n in self.points for n in "ABCD"):
            self.status("цепочка: задайте точки A, B, C, D")
            return False
        if self.policy.hold:
            self.status("цепочка: пульт на стопе")
            return False
        if self.policy.busy():
            self.status("цепочка: жду отклика игры на пробел")  # в строку окна, не в журнал: проверка идёт каждый кадр
            return False
        self.on, self.i, self.tries = True, 0, 0
        self.policy.chain_begin()
        self.on_change(True)
        self.status(f"цепочка идёт ({why})")
        self.log(f"пуск: {why}")
        self.tick()
        return True

    def stop(self, why, done=False, paused=False):
        """done — цепочка выполнена (её повод сбрасывается); paused — оставлена на паузе из-за блокера
        (тогда сбрасываются все отложенные события: игра и так стоит)."""
        if not self.on:
            return
        mid = 0 < self.i < len(self.steps()) - 1 and not paused  # прервали внутри — игра стоит на паузе цепочки
        self.on = False
        dropped = self.policy.chain_end(drop=done, paused=paused)  # Иван: после выполненной цепочки игра идёт
        self.on_change(False)
        self.status(f"цепочка остановлена: {why}" + (" (игра на паузе)" if mid else ""))
        self.log(f"стоп: {why}" + (f"; без паузы после цепочки: {', '.join(t for t, _ in dropped)}" if dropped else ""))

    # START_CONTRACT: tick
    #   PURPOSE: Один шаг. pause/resume — один пробел через политику и ожидание отклика (до policy.wait);
    #            перед действием — проверка, что игра на паузе; если сняли — прервать и ничего не жать.
    #   SIDE_EFFECTS: клавиши, курсор, щелчки в окне игры; планирует следующий tick
    # END_CONTRACT: tick
    def tick(self):
        if not self.on:
            return
        kind, arg = self.steps()[self.i]
        state, cfg = self.game(), self.settings()
        nxt = cfg["gap"]
        if kind in ("pause", "resume"):
            want = "pause" if kind == "pause" else "run"
            if state == want:
                self.log(f"{'пауза' if want == 'pause' else 'игра идёт'} — проверено")
                self.i, self.tries = self.i + 1, 0
            elif state is None:  # кнопок скорости не видно — вслепую не жмём: пробел переключает паузу
                self.stop("не вижу кнопок скорости в углу игры")
                return
            elif self.policy.busy():  # пробел в пути, игра в фоне откликается секунды — ждём
                nxt = 0.2
            elif self.tries >= 1:  # один пробел и ждём; повтор мог бы снять только что поставленную паузу
                self.stop(f"игра не {'встала на паузу' if want == 'pause' else 'пошла'} после пробела")
                return
            elif want == "run" and self.blocker():
                # Иван: есть блокер (светлая волна выше строки и т. п.) — не снимать паузу: после снятия
                # условие уже не «новое», и игру больше ничто не остановит
                self.stop(f"выполнена, игра оставлена на паузе: {self.blocker()}", done=True, paused=True)
                return
            else:
                self.policy.chain_space(want)
                self.tries += 1
                self.log(f"пробел ({'пауза' if want == 'pause' else 'снять паузу'}), попытка {self.tries}")
        elif kind == "done":
            self.stop("выполнена", done=True)
            return
        elif state == "run":
            # игру сняли с паузы посреди цепочки (Иван или прошлый шаг) — прервать и ничего не жать
            prev = self.steps()[self.i - 1]
            self.stop(f"игру сняли с паузы (после шага {prev[0]} {prev[1]}) — цепочка прервана")
            return
        elif kind == "key":
            press_key(self.hwnd, VK.get(arg, ord(arg)))
            self.log(f"клавиша {arg}")
            self.i += 1
        elif kind == "away":
            p = self.points.get(arg)
            if p:
                (real_hover if cfg["real"] and cfg.get("hover_real") else hover)(self.hwnd, *p)
                self.log(f"курсор в сторону, на {arg} {p}")
            else:
                self.log(f"точка {arg} не задана — курсор не увожу")
            self.i += 1
        else:
            p = self.points.get(arg)
            if not p:
                self.stop(f"не задана точка {arg}")
                return
            if kind == "hover":
                (real_hover if cfg["real"] and cfg.get("hover_real") else hover)(self.hwnd, *p)
                self.log(f"навёл {'курсор' if cfg['real'] and cfg.get('hover_real') else 'мышь (сообщением)'} на {arg} {p}")
                nxt = cfg["wait"]
            else:
                (real_click if cfg["real"] else click)(self.hwnd, *p)
                self.log(f"щелчок {arg} {p}")
                self.after_click(arg)
            self.i += 1
        self.schedule(int(nxt * 1000), self.tick)


# START_BLOCK_SELF_CHECK
def self_check():
    """Прерывание посреди цепочки не отправляет ни одного действия; пробелы идут через политику."""
    import chain as me
    sent = []
    for name in ("press_key", "hover", "click", "real_hover", "real_click"):
        setattr(me, name, lambda *a, n=name: sent.append(n))

    class Policy:
        def __init__(self):
            self.spaces, self.chain, self.hold = [], False, False

        def busy(self):
            return False

        def chain_begin(self):
            self.chain = True

        def chain_end(self, drop=False, paused=False):
            self.chain, self.ended = False, (drop, paused)
            return []

        def chain_space(self, want):
            self.spaces.append(want)

    game = ["run"]
    queue = []
    pol = Policy()
    ch = Chain(0, pol, {n: [1, 1] for n in "ABCDE"}, lambda: {"real": True, "gap": 1, "wait": 1}, lambda: game[0],
               lambda ms, fn: queue.append(fn), lambda *a, **k: None, lambda *a: None)
    assert ch.start("тест") and pol.spaces == ["pause"] and pol.chain
    game[0] = "pause"
    queue.pop(0)()   # пауза проверена
    queue.pop(0)()   # клавиша 1
    assert sent == ["press_key"]
    game[0] = "run"  # Иван снял паузу
    sent.clear()
    queue.pop(0)()   # шаг навести на A — прерывание
    assert not ch.on and not pol.chain and sent == [] and not queue and pol.spaces == ["pause"]
    # блокер в конце (светлая волна выше строки) — паузу не снимаем, цепочка выполнена (Иван, 7.10)
    block = ["светлая волна"]
    pol = Policy()
    ch = Chain(0, pol, {n: [1, 1] for n in "ABCDE"}, lambda: {"real": True, "gap": 1, "wait": 1}, lambda: game[0],
               lambda ms, fn: queue.append(fn), lambda *a, **k: None, lambda *a: None, blocker=lambda: block[0])
    game[0] = "run"
    assert ch.start("тест")
    game[0] = "pause"
    while queue:
        queue.pop(0)()
    assert not ch.on and pol.spaces == ["pause"] and pol.ended == (True, True)
    block[0] = None  # блокера нет — в конце пробел «снять паузу»
    assert ch.start("тест")
    while queue and pol.spaces[-1] != "run":
        queue.pop(0)()
    assert pol.spaces == ["pause", "run"]  # игра уже стояла — пробел паузы не нужен
# END_BLOCK_SELF_CHECK
