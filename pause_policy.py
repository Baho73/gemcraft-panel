# START_MODULE_CONTRACT
#   PURPOSE: Единственное место, где решается, слать ли пробел (пробел переключает паузу игры).
#   SCOPE: Решение и учёт пробелов: события пауз от панели, пробелы цепочки, ожидание отклика игры, отложенные события.
#          Сам пробел — через переданную функцию press (M-CAPTURE.press_space); состояние игры — от M-DETECTORS.game_state.
#   DEPENDS: none (функции нажатия и часов передаются снаружи — для тестов без окна)
#   LINKS: M-PANEL, M-CHAIN, M-CAPTURE, M-DETECTORS, V-M-PAUSE-POLICY
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PausePolicy - решение «пробел сейчас / присоединить / отложить» и учёт своих пробелов
#   PausePolicy.for_window - политика с пробелом в окно игры (M-CAPTURE)
#   PausePolicy.request -событие паузы (рост маны, волна, индикатор…) -> 'sent' | 'joined' | 'deferred'
#   PausePolicy.observe - каждый кадр: состояние игры; снимает ожидание отклика, выпускает отложенные события
#   PausePolicy.chain_begin / chain_end / chain_space - цепочка: пока идёт — события копятся (выполнена — сбрасывается только её повод); её пробелы учитываются
#   CHAIN_TRIGGER - событие-повод цепочки: после выполненной цепочки сбрасывается только оно
#   PausePolicy.hold_on / hold_off - кнопка «Стоп пульта»: довести игру до паузы и больше ничего не жать
#   SPACE_WAIT - сколько ждать отклика игры на пробел
#   self_check - сценарии политики на поддельных часах (вызывается из test)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   C-MODULAR-PAUSE-POLICY: раньше решение о пробеле было в пяти местах, события во время цепочки и в окне
#   отклика (игра в фоне откликается на пробел 2-7 с) терялись молча. Теперь откладываются и дают один пробел,
#   когда игра снова идёт; пробелы цепочки учитываются.
# END_CHANGE_SUMMARY
import time

CHAIN_TRIGGER = ("indicator", "left", "plus")  # событие, которое и запускает цепочку: после неё оно не повод для паузы
SPACE_WAIT = 20.0  # столько ждём, пока кнопка «II» переключится после пробела (в фоне — секунды); дольше — отклика нет


class PausePolicy:
    def __init__(self, press, clock=time.time, wait=SPACE_WAIT, log=print):
        self.press, self.clock, self.wait, self.log = press, clock, wait, log
        self.awaiting = None      # 'pause' | 'run' — ждём, что игра перейдёт в это состояние после нашего пробела
        self.sent_at = 0.0
        self.chain = False        # идёт цепочка: её пробелы и шаги не перебивать
        self.pending = []         # отложенные события: (text, cols)
        self.hold = False         # кнопка «Стоп пульта»: сами ничего не жмём, кроме одной постановки на паузу
        self.ensure = 0           # сколько ещё пробелов можно, чтобы довести игру до паузы после «Стоп пульта»

    @classmethod
    def for_window(cls, hwnd, speed=lambda: 1, **kw):
        """Политика, которая щёлкает кнопки «II» / «▷» в окне игры — единственное место, где пауза привязана к окну.
        Иван 23.10: щелчок, а не пробел — кнопка не переключатель: прочитали состояние криво — щелчок паузы
        игру не запустит (пробел на мнимое «run» запускал стоящую игру). speed — какую стрелку жать для «run»."""
        from capture import real_click
        from detectors import SPEED_X, SPEED_Y
        return cls(press=lambda want: real_click(hwnd, SPEED_X[0 if want == "pause" else speed() or 1], SPEED_Y), **kw)

    # START_CONTRACT: request
    #   PURPOSE: Событие хочет поставить игру на паузу.
    #   INPUTS: { text: str - описание для журнала; cols: tuple - поля для digit_events.tsv; game: 'pause'|'run'|None }
    #   OUTPUTS: { str - 'sent' (пробел ушёл), 'joined' (наш пробел на паузу уже в пути), 'deferred' (отложено), 'held' (пульт на стопе) }
    #   SIDE_EFFECTS: может нажать пробел (self.press)
    # END_CONTRACT: request
    def request(self, text, cols, game):
        now = self.clock()
        if self.hold:
            return "held"  # пульт на стопе — Иван управляет сам
        if self.awaiting == "pause" and not self.chain:
            return "joined"  # пауза уже в пути — второй пробел снял бы её
        if self.chain or self.awaiting or game != "run":
            self.pending.append((text, cols))
            return "deferred"
        self._space("pause", now)
        return "sent"

    # START_CONTRACT: observe
    #   PURPOSE: Каждый кадр — текущее состояние игры: снять ожидание отклика, выпустить отложенные события.
    #   INPUTS: { game: 'pause'|'run'|None }
    #   OUTPUTS: { list - отложенные события (text, cols), под которые сейчас ушёл пробел; иначе [] }
    #   SIDE_EFFECTS: может нажать пробел
    # END_CONTRACT: observe
    def observe(self, game):
        now = self.clock()
        if self.awaiting and game == self.awaiting:
            self.awaiting = None
        elif self.awaiting and now - self.sent_at > self.wait:
            self.log(f"пробел без отклика {self.wait:.0f} с (ждали «{self.awaiting}») — ожидание снято")
            self.awaiting = None
        if self.hold:  # довести игру до паузы (не больше self.ensure пробелов), остальное не трогать
            if game == "pause":
                self.ensure = 0
            elif self.ensure and not self.awaiting and game == "run":
                self.ensure -= 1
                self._space("pause", now)
            return []
        if self.pending and not self.chain and not self.awaiting and game == "run":
            out, self.pending = self.pending, []
            self._space("pause", now)  # все отложенные — одним пробелом
            return out
        return []

    def hold_on(self):
        """Кнопка «Стоп пульта» (Иван, 7.10: «гонки с тобой»): отложенное сбросить, игру довести до паузы
        (пробел — в observe, когда наш прошлый пробел отработает), дальше сами ничего не жмём."""
        self.hold, self.ensure, self.pending = True, 2, []

    def hold_off(self):
        self.hold, self.ensure = False, 0

    def chain_begin(self):
        self.chain = True

    def chain_end(self, drop=False, paused=False):
        """drop=True — цепочка выполнена: сбросить её собственный повод (первый плюс левого), игра идёт дальше
        (Иван, 6.10). Остальное (светлая волна, мана, водоворот) остаётся и даст паузу, когда игра пойдёт.
        paused=True — цепочка оставила игру на паузе (блокер): сбросить всё, пауза уже есть.
        Возвращает сброшенные события."""
        self.chain = False
        if paused:
            out, self.pending = self.pending, []
            return out
        if not drop:
            return []
        out = [e for e in self.pending if tuple(e[1]) == CHAIN_TRIGGER]
        self.pending = [e for e in self.pending if tuple(e[1]) != CHAIN_TRIGGER]
        return out

    def chain_space(self, want):
        """Пробел цепочки (want: 'pause' или 'run') — учитывается как свой: дальше ждём отклика."""
        self._space(want, self.clock())

    def busy(self):
        """Ждём отклика на пробел — для цепочки: пока True, свой пробел не слать."""
        return self.awaiting is not None

    def _space(self, want, now):
        self.press(want)
        self.awaiting, self.sent_at = want, now


# START_BLOCK_SELF_CHECK
def self_check():
    """Сценарии политики на поддельных часах и поддельном пробеле — без окна."""
    clock = [0.0]
    spaces = []
    p = PausePolicy(lambda *_: spaces.append(clock[0]), clock=lambda: clock[0], log=lambda *_: None)

    def tick(game, dt=0.1):
        clock[0] += dt
        return p.observe(game)

    # 1. игра идёт: событие -> пробел; второе событие, пока пауза в пути, -> без второго пробела
    assert p.request("a", (), "run") == "sent" and len(spaces) == 1
    assert p.request("b", (), "run") == "joined" and len(spaces) == 1
    tick("pause")
    assert not p.busy()
    # 2. пауза игрока: событие откладывается, пробела нет; игра пошла -> один пробел
    assert p.request("c", (), "pause") == "deferred"
    for _ in range(50):
        assert tick("pause") == []
    assert len(spaces) == 1
    assert [e[0] for e in tick("run")] == ["c"] and len(spaces) == 2
    tick("pause")
    # 3. цепочка: события во время неё копятся; после снятия паузы и отклика — один пробел на все
    tick("run")
    p.chain_begin()
    p.chain_space("pause")
    assert len(spaces) == 3
    tick("run")  # игра ещё откликается
    tick("pause")
    assert p.request("d", (), "pause") == "deferred"
    assert p.request("e", (), "pause") == "deferred"
    p.chain_space("run")
    p.chain_end()
    assert len(spaces) == 4
    for _ in range(30):  # отклик на «снять паузу» идёт 3 с — всё это время пробела нет
        assert tick("pause") == []
    assert len(spaces) == 4
    assert [e[0] for e in tick("run")] == ["d", "e"] and len(spaces) == 5
    # 4. событие в окне отклика на пробел цепочки «снять паузу» (без цепочки) — откладывается, не теряется
    tick("pause")
    p.chain_space("run")
    assert p.request("f", (), "pause") == "deferred" and len(spaces) == 6
    assert [e[0] for e in tick("run")] == ["f"] and len(spaces) == 7
    # 5. отклика нет 20 с — ожидание снимается, а не висит вечно
    tick("pause")
    assert p.request("g", (), "run") == "sent" and p.busy()
    tick("run", dt=21)
    assert not p.busy()
    # 6. кнопок не видно (меню) — вслепую не жмём, откладываем
    n = len(spaces)
    assert p.request("h", (), None) == "deferred" and len(spaces) == n
    # 7. цепочка выполнена — её повод (плюс левого) сбрасывается, игра идёт без паузы (Иван, 6.10)
    p.pending.clear()
    tick("run")
    p.chain_begin()
    p.chain_space("pause")
    tick("pause")
    assert p.request("i", CHAIN_TRIGGER, "pause") == "deferred"
    p.chain_space("run")
    assert [e[0] for e in p.chain_end(drop=True)] == ["i"] and not p.pending
    n = len(spaces)
    assert tick("run") == [] and len(spaces) == n
    # 8. светлая волна во время выполненной цепочки не сбрасывается: пауза, когда игра пошла (Иван, 6.10, волна дошла до 52)
    p.chain_begin()
    p.chain_space("pause")
    tick("pause")
    assert p.request("i2", list(CHAIN_TRIGGER), "pause") == "deferred"
    assert p.request("w", ("wave", 58), "pause") == "deferred"
    p.chain_space("run")
    assert [e[0] for e in p.chain_end(drop=True)] == ["i2"]
    assert [e[0] for e in tick("run")] == ["w"] and len(spaces) == n + 3
    # 9. цепочка оставила игру на паузе (блокер) — сбрасывается всё: пауза уже есть, лишнего пробела потом нет
    tick("pause")
    p.chain_begin()
    assert p.request("w2", ("wave", 50), "pause") == "deferred"
    assert [e[0] for e in p.chain_end(drop=True, paused=True)] == ["w2"] and not p.pending
    # 10. «Стоп пульта» при идущей игре: один пробел на паузу, потом ничего; события не жмут; после отпускания — как обычно
    n = len(spaces)
    p.hold_on()
    assert tick("run") == [] and len(spaces) == n + 1
    assert p.request("x", (), "run") == "held" and len(spaces) == n + 1
    tick("pause")  # пауза встала
    tick("run")    # Иван сам снял паузу — пульт не вмешивается
    assert len(spaces) == n + 1 and not p.pending
    p.hold_off()
    assert p.request("y", (), "run") == "sent" and len(spaces) == n + 2
# END_BLOCK_SELF_CHECK
