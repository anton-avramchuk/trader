"""Шаблон стратегии «вход по событию» (ADR-0027): сигналы из вхождений MVP-5.

Стратегия — данные, не код: источник сигнала (подтверждённый паттерн, касание или
пробой уровня), сторона (по событию или обратная), фильтры (режим, качество,
период), стоп, цель и ограничение по времени. Сигнал рождается на закрытии бара
``available_at`` события; цены стопа и цели — в шкале ряда (continuous), отсчёт —
от close сигнального бара. Ничего из будущего не используется: ATR и режим берутся
на сигнальном баре, экстремум и цель паттерна известны к его подтверждению.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from trader_engine.backtest.simulator import Side, Signal
from trader_engine.stats.occurrences import GROUP_BREAK, GROUP_TOUCH
from trader_engine.stats.pipeline import Filters, SeriesOccurrence, matches

Source = Literal["pattern", "level_touch", "level_break"]
SideMode = Literal["follow", "fade"]
StopKind = Literal["none", "atr", "structure"]
TargetKind = Literal["none", "atr", "pattern"]

SOURCES: tuple[Source, ...] = ("pattern", "level_touch", "level_break")
MAX_BARS_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class ExitRule:
    """Стоп или цель: ``atr`` — ``value`` ATR от close сигнала; ``structure``
    (стоп) — экстремум паттерна; ``pattern`` (цель) — цель паттерна."""

    kind: str = "none"
    value: float = 0.0


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """Параметры стратегии; вместе с версией движков определяют результат."""

    source: Source = "pattern"
    groups: tuple[str, ...] = ()
    side: SideMode = "follow"
    quality_min: float | None = None
    quality_max: float | None = None
    trends: tuple[str, ...] = ()
    volatilities: tuple[str, ...] = ()
    since: datetime | None = None
    until: datetime | None = None
    stop: ExitRule = field(default_factory=lambda: ExitRule("atr", 1.5))
    target: ExitRule = field(default_factory=lambda: ExitRule("atr", 3.0))
    max_bars: int | None = None

    def validate(self) -> None:
        """Ошибка ``ValueError`` для невозможных сочетаний параметров."""
        if self.source not in SOURCES:
            raise ValueError(f"Неизвестный источник сигнала: {self.source}")
        if self.stop.kind not in ("none", "atr", "structure"):
            raise ValueError(f"Неизвестный тип стопа: {self.stop.kind}")
        if self.target.kind not in ("none", "atr", "pattern"):
            raise ValueError(f"Неизвестный тип цели: {self.target.kind}")
        for rule in (self.stop, self.target):
            if rule.kind == "atr" and rule.value <= 0:
                raise ValueError("Размер стопа и цели в ATR должен быть больше 0")
        if self.source != "pattern" and (
            self.stop.kind == "structure" or self.target.kind == "pattern"
        ):
            raise ValueError("Стоп «structure» и цель «pattern» — только для паттернов")
        if self.side == "fade" and (
            self.stop.kind == "structure" or self.target.kind == "pattern"
        ):
            raise ValueError("Для обратной стороны нужны стоп и цель в ATR")
        if self.max_bars is not None and not 1 <= self.max_bars <= MAX_BARS_LIMIT:
            raise ValueError(f"max_bars: от 1 до {MAX_BARS_LIMIT}")
        if (
            self.stop.kind == "none"
            and self.target.kind == "none"
            and not self.max_bars
        ):
            raise ValueError("Нужен хотя бы один выход: стоп, цель или max_bars")

    def to_dict(self) -> dict[str, Any]:
        """Описание для хранения и хеша параметров (детерминированный порядок)."""
        return {
            "source": self.source,
            "groups": sorted(self.groups),
            "side": self.side,
            "quality_min": self.quality_min,
            "quality_max": self.quality_max,
            "trends": sorted(self.trends),
            "volatilities": sorted(self.volatilities),
            "since": None if self.since is None else self.since.isoformat(),
            "until": None if self.until is None else self.until.isoformat(),
            "stop": {"kind": self.stop.kind, "value": self.stop.value},
            "target": {"kind": self.target.kind, "value": self.target.value},
            "max_bars": self.max_bars,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StrategySpec":
        defaults = cls()
        stop: Mapping[str, Any] | None = data.get("stop")
        target: Mapping[str, Any] | None = data.get("target")
        return cls(
            source=data.get("source", "pattern"),
            groups=tuple(data.get("groups") or ()),
            side=data.get("side", "follow"),
            quality_min=data.get("quality_min"),
            quality_max=data.get("quality_max"),
            trends=tuple(data.get("trends") or ()),
            volatilities=tuple(data.get("volatilities") or ()),
            since=_dt(data.get("since")),
            until=_dt(data.get("until")),
            stop=defaults.stop
            if stop is None
            else ExitRule(stop.get("kind", "none"), float(stop.get("value", 0.0))),
            target=defaults.target
            if target is None
            else ExitRule(target.get("kind", "none"), float(target.get("value", 0.0))),
            max_bars=data.get("max_bars"),
        )


def _dt(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


@dataclass(frozen=True, slots=True)
class BuiltSignals:
    """Сигналы и причины, по которым вхождения ими не стали."""

    signals: list[Signal]
    filtered: int = 0
    no_atr: int = 0
    bad_levels: int = 0


def _wanted(item: SeriesOccurrence, spec: StrategySpec) -> bool:
    occurrence = item.occurrence
    if spec.source == "pattern":
        if occurrence.kind != "pattern" or occurrence.entry != "confirmed":
            return False
    elif occurrence.group != (
        GROUP_TOUCH if spec.source == "level_touch" else GROUP_BREAK
    ):
        return False
    if spec.groups and occurrence.group not in spec.groups:
        return False
    return matches(
        item,
        Filters(
            quality_min=spec.quality_min,
            quality_max=spec.quality_max,
            trends=spec.trends,
            volatilities=spec.volatilities,
            since=spec.since,
            until=spec.until,
        ),
    )


def build_signals(
    spec: StrategySpec, items: Iterable[SeriesOccurrence]
) -> BuiltSignals:
    """Сигналы по вхождениям одного ряда (индексы — его бары)."""
    spec.validate()
    signals: list[Signal] = []
    filtered = no_atr = bad = 0
    for item in sorted(
        items, key=lambda i: (i.occurrence.available_at, i.occurrence.key)
    ):
        index = item.entry_index
        if index is None or not _wanted(item, spec):
            filtered += 1
            continue
        occurrence, series = item.occurrence, item.series
        ref = series.bars[index].close
        atr = series.atrs[index]
        follow = occurrence.direction == "bullish"
        side: Side = "long" if follow == (spec.side == "follow") else "short"
        sign = 1.0 if side == "long" else -1.0
        stop = target = None
        uses_atr = spec.stop.kind == "atr" or spec.target.kind == "atr"
        if uses_atr and (atr is None or atr <= 0):
            no_atr += 1
            continue
        if spec.stop.kind == "atr":
            assert atr is not None
            stop = ref - sign * spec.stop.value * atr
        elif spec.stop.kind == "structure":
            extreme = occurrence.meta.get("extreme")
            stop = None if extreme is None else float(extreme)
            if stop is None or sign * (ref - stop) <= 0:
                bad += 1
                continue
        if spec.target.kind == "atr":
            assert atr is not None
            target = ref + sign * spec.target.value * atr
        elif spec.target.kind == "pattern":
            target = occurrence.target
            if target is None or sign * (target - ref) <= 0:
                bad += 1
                continue
        signals.append(
            Signal(
                index,
                side,
                stop,
                target,
                spec.max_bars,
                ref=occurrence.key,
                ref_price=ref,
            )
        )
    return BuiltSignals(signals, filtered, no_atr, bad)
