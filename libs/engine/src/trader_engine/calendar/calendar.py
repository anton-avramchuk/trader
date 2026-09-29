"""Торговый календарь: сессии, торговые дни и недели, ожидаемые разрывы, сетка баров."""

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from trader_engine.calendar.model import BarSlot, Session, SessionRule, SessionWindow

# Насколько далеко назад искать сессии, относящиеся к торговому дню (вечерняя
# сессия пятницы принадлежит понедельнику; праздники удлиняют цепочку).
_LOOKBACK_DAYS = 14
_NEXT_TRADING_DAY_LIMIT = 14


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise ValueError("Момент времени должен быть timezone-aware")


class TradingCalendar:
    """Расписание торгов биржи.

    Правила действуют в интервалах дат и не пересекаются. Все моменты времени
    на входе и выходе — aware, на выходе — в UTC. Границы окон исключающие.
    """

    def __init__(
        self,
        timezone: str,
        rules: Iterable[SessionRule],
        holidays: Iterable[date] = (),
        special_days: Iterable[date] = (),
    ) -> None:
        self._timezone_name = timezone
        self._tz = ZoneInfo(timezone)
        self._rules = tuple(sorted(rules, key=lambda rule: rule.effective_from))
        for previous, current in zip(self._rules, self._rules[1:], strict=False):
            if (
                previous.effective_to is None
                or previous.effective_to >= current.effective_from
            ):
                raise ValueError("Правила календаря пересекаются по датам")
        self._starts = [rule.effective_from for rule in self._rules]
        self._holidays = frozenset(holidays)
        # Выходные, которые торгуются как обычный день (рабочие субботы-переносы).
        self._special_days = frozenset(special_days)

    @property
    def timezone(self) -> str:
        return self._timezone_name

    def excluding_weekend_sessions(self) -> "TradingCalendar":
        """Календарь без выходных сессий (``include_weekend_sessions = false``)."""
        rules = [replace(rule, weekend_windows=()) for rule in self._rules]
        return TradingCalendar(
            self._timezone_name, rules, self._holidays, self._special_days
        )

    @property
    def rules(self) -> tuple[SessionRule, ...]:
        """Правила по возрастанию даты начала."""
        return self._rules

    def rule_for(self, day: date) -> SessionRule | None:
        index = bisect_right(self._starts, day) - 1
        if index < 0:
            return None
        rule = self._rules[index]
        return rule if rule.contains(day) else None

    def is_trading_weekday(self, day: date) -> bool:
        """Торговый день с обычными окнами: будний или особый выходной."""
        return (
            (day.weekday() < 5 or day in self._special_days)
            and day not in self._holidays
            and self.rule_for(day) is not None
        )

    def _next_trading_weekday(self, day: date) -> date:
        candidate = day
        for _ in range(_NEXT_TRADING_DAY_LIMIT):
            candidate += timedelta(days=1)
            if self.is_trading_weekday(candidate):
                return candidate
        raise ValueError(
            f"Нет торгового дня в {_NEXT_TRADING_DAY_LIMIT} днях после {day}"
        )

    def _to_utc(self, day: date, at: time) -> datetime:
        return datetime.combine(day, at, tzinfo=self._tz).astimezone(UTC)

    def sessions_on(self, day: date) -> tuple[Session, ...]:
        """Окна торгов на локальную дату ``day``, по порядку."""
        rule = self.rule_for(day)
        if rule is None or day in self._holidays:
            return ()
        is_weekend = day.weekday() >= 5 and day not in self._special_days
        windows: tuple[SessionWindow, ...] = (
            rule.weekend_windows if is_weekend else rule.weekday_windows
        )
        return tuple(
            Session(
                name=window.name,
                start=self._to_utc(day, window.start),
                end=self._to_utc(day, window.end),
                trading_day=(
                    self._next_trading_weekday(day) if window.next_day else day
                ),
                is_weekend=is_weekend,
            )
            for window in windows
        )

    def sessions_between(self, start: datetime, end: datetime) -> list[Session]:
        """Сессии, пересекающиеся с полуинтервалом ``[start, end)``."""
        _require_aware(start)
        _require_aware(end)
        if end <= start:
            return []
        first = start.astimezone(self._tz).date()
        last = (end - timedelta(microseconds=1)).astimezone(self._tz).date()
        result: list[Session] = []
        day = first
        while day <= last:
            result.extend(
                session
                for session in self.sessions_on(day)
                if session.start < end and session.end > start
            )
            day += timedelta(days=1)
        return result

    def session_at(self, moment: datetime) -> Session | None:
        _require_aware(moment)
        local_day = moment.astimezone(self._tz).date()
        for session in self.sessions_on(local_day):
            if session.contains(moment):
                return session
        return None

    def session_end(self, moment: datetime) -> datetime | None:
        """Конец окна, в которое попал ``moment`` (для обрезки баров)."""
        session = self.session_at(moment)
        return None if session is None else session.end

    def trading_day_of(self, moment: datetime) -> date | None:
        session = self.session_at(moment)
        return None if session is None else session.trading_day

    def trading_day_bounds(self, day: date) -> tuple[datetime, datetime] | None:
        """Начало первого и конец последнего окна торгового дня ``day``."""
        sessions = [
            session
            for offset in range(_LOOKBACK_DAYS + 1)
            for session in self.sessions_on(day - timedelta(days=offset))
            if session.trading_day == day
        ]
        if not sessions:
            return None
        return min(s.start for s in sessions), max(s.end for s in sessions)

    @staticmethod
    def trading_week_start(day: date) -> date:
        """Понедельник недели, к которой относится торговый день."""
        return day - timedelta(days=day.weekday())

    def trading_week_bounds(self, week_start: date) -> tuple[datetime, datetime] | None:
        """Границы торговой недели, начинающейся в понедельник ``week_start``."""
        bounds = [
            found
            for offset in range(7)
            if (found := self.trading_day_bounds(week_start + timedelta(days=offset)))
        ]
        if not bounds:
            return None
        return min(b[0] for b in bounds), max(b[1] for b in bounds)

    def is_expected_gap(self, start: datetime, end: datetime) -> bool:
        """Нет торгов в ``[start, end)`` — разрыв ожидаем, это не пропуск данных.

        ``start`` — конец предыдущей свечи, ``end`` — начало следующей.
        """
        return not self.sessions_between(start, end)

    def bar_slot(self, moment: datetime, duration: timedelta) -> BarSlot | None:
        """Ячейка сетки баров длительностью ``duration``, содержащая ``moment``.

        Сетка строится от ``bar_anchor`` каждого календарного дня и обрезается
        началом следующего дня сетки. Ячейка не может объединять сессии разных
        торговых дней — иначе ``ValueError`` (длительность несовместима
        с расписанием).
        """
        _require_aware(moment)
        if duration <= timedelta(0):
            raise ValueError("Длительность бара должна быть положительной")
        session = self.session_at(moment)
        if session is None:
            return None
        local = moment.astimezone(self._tz)
        rule = self.rule_for(local.date())
        assert rule is not None  # сессия есть, значит правило есть
        anchor_day = local.date()
        anchor = datetime.combine(anchor_day, rule.bar_anchor, tzinfo=self._tz)
        if local < anchor:
            anchor_day -= timedelta(days=1)
            anchor = datetime.combine(anchor_day, rule.bar_anchor, tzinfo=self._tz)
        next_anchor = datetime.combine(
            anchor_day + timedelta(days=1), rule.bar_anchor, tzinfo=self._tz
        )
        index = (local - anchor) // duration
        slot_start = anchor + index * duration
        slot_end = min(slot_start + duration, next_anchor)

        pieces = self.sessions_between(slot_start, slot_end)
        if len({piece.trading_day for piece in pieces}) > 1:
            raise ValueError(
                f"Ячейка {slot_start} длительностью {duration} объединяет "
                "сессии разных торговых дней"
            )
        covered = sum(
            (
                min(piece.end, slot_end) - max(piece.start, slot_start)
                for piece in pieces
            ),
            timedelta(0),
        )
        return BarSlot(
            start=slot_start.astimezone(UTC),
            close_time=max(min(piece.end, slot_end) for piece in pieces).astimezone(
                UTC
            ),
            trading_day=session.trading_day,
            is_partial=covered != duration,
        )
