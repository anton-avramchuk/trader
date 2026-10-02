import type { EngineEvent, FibGrid, LevelZone } from '@trader/api-client';
import { toChartTime } from './chart-data';

/** Слои структуры на графике (spec §45). */
export const LAYERS = [
  'swings',
  'zigzag',
  'structure',
  'levels',
  'zones',
  'pivot',
  'fibonacci',
  'patterns',
] as const;
export type LayerKey = (typeof LAYERS)[number];

export const LAYER_TITLES: Record<LayerKey, string> = {
  swings: 'Swing points',
  zigzag: 'ZigZag',
  structure: 'Структура (HH/HL/LH/LL)',
  levels: 'Уровни S/R',
  zones: 'Confluence-зоны',
  pivot: 'Pivot',
  fibonacci: 'Fibonacci',
  patterns: 'Паттерны',
};

/** Движки паттернов (ADR-0022): у каждого своя нумерация вхождений. */
export const PATTERN_ENGINES = [
  'double_triple',
  'head_shoulders',
  'trendlines',
  'range_breakout',
];

/** Движки, которые нужны слою (имя из каталога `GET /engines`). */
export const LAYER_ENGINES: Record<LayerKey, string[]> = {
  swings: ['zigzag'],
  zigzag: ['zigzag'],
  structure: ['market_structure'],
  levels: ['levels'],
  zones: ['levels'],
  pivot: ['pivot'],
  fibonacci: ['fibonacci'],
  patterns: PATTERN_ENGINES,
};

export type LayerState = Record<LayerKey, boolean>;

export function emptyLayers(): LayerState {
  return Object.fromEntries(LAYERS.map((key) => [key, false])) as LayerState;
}

/** Ключ слоя в `layers` профиля графика. */
export const profileKey = (layer: LayerKey): string => `structure.${layer}`;

export const UP = '#26a69a';
export const DOWN = '#ef5350';
export const NEUTRAL = '#9e9e9e';
export const GOLD = '#f9a825';
const BLUE = '#42a5f5';
const PURPLE = '#ab47bc';

export interface OverlayMarker {
  time: number;
  position: 'aboveBar' | 'belowBar';
  shape: 'circle' | 'arrowUp' | 'arrowDown';
  color: string;
  text: string;
}

/** Отрезок: от `time1` до `time2` (`null` — до правого края графика). */
export interface OverlaySegment {
  time1: number;
  price1: number;
  time2: number | null;
  price2: number;
  color: string;
  dashed: boolean;
  width: number;
  label?: string;
  levelId?: number;
}

/** Прямоугольник зоны от `time1` до правого края. */
export interface OverlayZone {
  time1: number;
  low: number;
  high: number;
  color: string;
  label: string;
}

export interface Overlay {
  markers: OverlayMarker[];
  segments: OverlaySegment[];
  zones: OverlayZone[];
}

export const EMPTY_OVERLAY: Overlay = { markers: [], segments: [], zones: [] };

const num = (value: unknown): number => Number(value);
const str = (value: unknown): string => String(value);
const payload = (event: EngineEvent): Record<string, unknown> =>
  event.payload as Record<string, unknown>;

/** Экстремум ZigZag: подтверждённый или «кандидат» текущего колена. */
export interface SwingPoint {
  time: number;
  price: number;
  type: 'high' | 'low';
  confirmed: boolean;
}

/** Точки swing из событий `zigzag`/`swing_fixed` (актуальные версии), по времени. */
export function swingPoints(events: EngineEvent[]): SwingPoint[] {
  return events
    .filter((e) => e.kind === 'swing')
    .map((e) => {
      const p = payload(e);
      return {
        time: toChartTime(str(p['timestamp'])),
        price: num(p['price']),
        type: p['type'] === 'high' ? ('high' as const) : ('low' as const),
        confirmed: e.status === 'confirmed',
      };
    })
    .sort((a, b) => a.time - b.time);
}

export function swingMarkers(events: EngineEvent[]): OverlayMarker[] {
  return swingPoints(events).map((point) => ({
    time: point.time,
    position: point.type === 'high' ? 'aboveBar' : 'belowBar',
    shape: 'circle',
    color: point.confirmed ? (point.type === 'high' ? DOWN : UP) : NEUTRAL,
    text: point.confirmed ? '' : '?',
  }));
}

/** Ломаная ZigZag; последнее колено (до неподтверждённой точки) — пунктиром. */
export function zigzagSegments(events: EngineEvent[]): OverlaySegment[] {
  const points = swingPoints(events);
  const segments: OverlaySegment[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (!a || !b) {
      continue;
    }
    segments.push({
      time1: a.time,
      price1: a.price,
      time2: b.time,
      price2: b.price,
      color: BLUE,
      dashed: !b.confirmed,
      width: 2,
    });
  }
  return segments;
}

const LABEL_COLORS: Record<string, string> = {
  HH: UP,
  HL: UP,
  LH: DOWN,
  LL: DOWN,
};

/** Метки HH/HL/LH/LL на подтверждённых swing. */
export function structureMarkers(events: EngineEvent[]): OverlayMarker[] {
  return events
    .filter((e) => e.kind === 'structure_point')
    .map((e) => {
      const p = payload(e);
      const label = str(p['label']);
      return {
        time: toChartTime(str(p['timestamp'])),
        position:
          p['type'] === 'high' ? ('aboveBar' as const) : ('belowBar' as const),
        shape:
          p['type'] === 'high' ? ('arrowDown' as const) : ('arrowUp' as const),
        color: LABEL_COLORS[label] ?? NEUTRAL,
        text: label,
      };
    })
    .sort((a, b) => a.time - b.time);
}

export type Trend = 'uptrend' | 'downtrend' | 'range';

/** Текущее состояние тренда (последнее событие `trend_state`). */
export function currentTrend(events: EngineEvent[]): Trend | null {
  const last = events.filter((e) => e.kind === 'trend_state').at(-1);
  return last ? (str(payload(last)['state']) as Trend) : null;
}

/** Уровень для панели деталей: строка события `level` в удобном виде. */
export interface LevelInfo {
  id: number;
  source: string;
  family: string;
  price: number;
  role: 'resistance' | 'support';
  state: 'active' | 'broken';
  touches: number;
  createdAt: string;
  score: number;
  components: Record<string, number>;
  version: number;
}

export function levelInfos(events: EngineEvent[]): LevelInfo[] {
  return events
    .filter((e) => e.kind === 'level')
    .map((e) => {
      const p = payload(e);
      const strength = p['strength'] as {
        score: number;
        version: number;
        components: Record<string, number>;
      };
      return {
        id: num(p['id']),
        source: str(p['source']),
        family: str(p['family']),
        price: num(p['price']),
        role:
          p['role'] === 'support'
            ? ('support' as const)
            : ('resistance' as const),
        state:
          p['state'] === 'broken' ? ('broken' as const) : ('active' as const),
        touches: num(p['touches']),
        createdAt: str(p['created_at']),
        score: strength.score,
        components: strength.components,
        version: strength.version,
      };
    })
    .sort((a, b) => b.score - a.score);
}

/** Опорная цена и ATR графика: от них считаются расстояние и слияние уровней. */
export interface LevelReference {
  price: number;
  atr: number;
}

/** Что показывать из уровней: остальное тонет в шуме (ADR-0029). */
export interface LevelFilter {
  reference: LevelReference | null;
  /** Не дальше N·ATR от цены; `null` — без ограничения. */
  maxDistanceAtr: number | null;
  minScore: number;
  /** Для swing-уровней: не меньше стольких касаний. */
  minTouches: number;
  /** Сколько уровней выше и ниже цены. */
  perSide: number;
  /** Показывать ли fib- и pivot-уровни (у них есть свои слои). */
  derived: boolean;
}

/** Уровни ближе этого расстояния (в ATR) считаются дублем: остаётся сильнейший. */
export const LEVEL_MERGE_ATR = 0.5;
const DERIVED_FAMILIES = ['fib', 'pivot'];

export const DEFAULT_LEVEL_FILTER: LevelFilter = {
  reference: null,
  maxDistanceAtr: 20,
  minScore: 0,
  minTouches: 0,
  perSide: 4,
  derived: false,
};

/** ATR(`period`) по последним свечам (простое среднее TR) и последняя цена. */
export function levelReference(
  candles: {
    high: number | string;
    low: number | string;
    close: number | string;
  }[],
  period = 14,
): LevelReference | null {
  const last = candles.at(-1);
  if (candles.length < 2 || !last) {
    return null;
  }
  const tail = candles.slice(-(period + 1));
  let sum = 0;
  tail.forEach((c, i) => {
    const before = tail[i - 1];
    if (!before) {
      return;
    }
    const previous = Number(before.close);
    sum += Math.max(
      Number(c.high) - Number(c.low),
      Math.abs(Number(c.high) - previous),
      Math.abs(Number(c.low) - previous),
    );
  });
  const atr = sum / (tail.length - 1);
  return atr > 0 ? { price: Number(last.close), atr } : null;
}

/**
 * Уровни для графика и списка: активные, не дальше `maxDistanceAtr`, без близких
 * дублей, сильнейшие `perSide` выше и ниже цены; порядок — по силе.
 * Без опорной цены расстояние и слияние не применяются, сторона — по роли.
 */
export function chartLevels(
  levels: LevelInfo[],
  filter: LevelFilter = DEFAULT_LEVEL_FILTER,
): LevelInfo[] {
  const { reference } = filter;
  const candidates = levels.filter(
    (l) =>
      l.state === 'active' &&
      (filter.derived || !DERIVED_FAMILIES.includes(l.family)) &&
      l.score >= filter.minScore &&
      (l.family !== 'swing' || l.touches >= filter.minTouches) &&
      (!reference ||
        filter.maxDistanceAtr === null ||
        Math.abs(l.price - reference.price) <=
          filter.maxDistanceAtr * reference.atr),
  );
  const chosen: LevelInfo[] = [];
  const count = { above: 0, below: 0 };
  for (const level of candidates) {
    const above = reference
      ? level.price > reference.price
      : level.role === 'resistance';
    const side = above ? 'above' : 'below';
    if (count[side] >= filter.perSide) {
      continue;
    }
    if (
      reference &&
      chosen.some(
        (c) =>
          Math.abs(c.price - level.price) <= LEVEL_MERGE_ATR * reference.atr,
      )
    ) {
      continue;
    }
    chosen.push(level);
    count[side]++;
  }
  return chosen;
}

/** Линии уровней: сила задаёт толщину, пробитые — бледным пунктиром. */
export function levelSegments(
  levels: LevelInfo[],
  selectedId: number | null = null,
): OverlaySegment[] {
  return levels.map((level) => ({
    time1: toChartTime(level.createdAt),
    price1: level.price,
    time2: null,
    price2: level.price,
    color:
      level.state === 'broken'
        ? NEUTRAL
        : level.role === 'resistance'
          ? DOWN
          : UP,
    dashed: level.state === 'broken',
    width:
      level.id === selectedId
        ? 4
        : level.score >= 50
          ? 3
          : level.score >= 25
            ? 2
            : 1,
    label: `${level.source} ${Math.round(level.score)}`,
    levelId: level.id,
  }));
}

const PIVOT_COLORS: Record<string, string> = {
  pp: GOLD,
  r1: DOWN,
  r2: DOWN,
  r3: DOWN,
  s1: UP,
  s2: UP,
  s3: UP,
};
const PIVOT_NAMES = ['pp', 'r1', 'r2', 'r3', 's1', 's2', 's3'] as const;
/** Сколько последних периодов pivot показывать: иначе линии заслоняют график. */
export const PIVOT_KEEP: Record<string, number> = { day: 2, week: 1 };

/** Уровни pivot: каждый действует от события до следующего того же периода. */
export function pivotSegments(events: EngineEvent[]): OverlaySegment[] {
  const segments: OverlaySegment[] = [];
  for (const period of ['day', 'week']) {
    const list = events
      .filter((e) => e.kind === 'pivot' && payload(e)['period'] === period)
      .sort((a, b) => a.seq - b.seq);
    const shown = list.slice(-(PIVOT_KEEP[period] ?? 1));
    shown.forEach((event, index) => {
      const next = shown[index + 1];
      const p = payload(event);
      for (const name of PIVOT_NAMES) {
        const price = num(p[name]);
        segments.push({
          time1: toChartTime(event.available_at),
          price1: price,
          time2: next ? toChartTime(next.available_at) : null,
          price2: price,
          color: PIVOT_COLORS[name] ?? NEUTRAL,
          dashed: period === 'week',
          width: name === 'pp' ? 2 : 1,
          label: `${period === 'week' ? 'W' : 'D'} ${name.toUpperCase()}`,
        });
      }
    });
  }
  return segments;
}

interface GridLike {
  start: { time: number; price: number };
  end: { time: number; price: number };
  retracement: Record<string, number>;
  extension: Record<string, number>;
}

function gridSegments(
  grid: GridLike,
  color: string,
  prefix: string,
): OverlaySegment[] {
  const segments: OverlaySegment[] = [
    {
      time1: grid.start.time,
      price1: grid.start.price,
      time2: grid.end.time,
      price2: grid.end.price,
      color,
      dashed: true,
      width: 2,
    },
  ];
  const levels: [string, number][] = [
    ...Object.entries(grid.retracement),
    ...Object.entries(grid.extension).filter(([key]) => key !== '100'),
  ];
  for (const [key, price] of levels) {
    segments.push({
      time1: grid.end.time,
      price1: price,
      time2: null,
      price2: price,
      color,
      dashed: false,
      width: 1,
      label: `${prefix}${key}`,
    });
  }
  return segments;
}

/** Последняя автоматическая сетка Fibonacci (события `fib_grid`). */
export function fibonacciSegments(events: EngineEvent[]): OverlaySegment[] {
  const last = events.filter((e) => e.kind === 'fib_grid').at(-1);
  if (!last) {
    return [];
  }
  const p = payload(last);
  const start = p['start'] as { price: number; timestamp: string };
  const end = p['end'] as { price: number; timestamp: string };
  return gridSegments(
    {
      start: { time: toChartTime(start.timestamp), price: start.price },
      end: { time: toChartTime(end.timestamp), price: end.price },
      retracement: p['retracement'] as Record<string, number>,
      extension: p['extension'] as Record<string, number>,
    },
    PURPLE,
    'F ',
  );
}

/** Ручные сетки (по двум точкам) — отдельным цветом, не смешиваются с автоматической. */
export function manualFibSegments(grids: FibGrid[]): OverlaySegment[] {
  return grids.flatMap((grid) =>
    gridSegments(
      {
        start: { time: toChartTime(grid.start.time), price: grid.start.price },
        end: { time: toChartTime(grid.end.time), price: grid.end.price },
        retracement: grid.retracement,
        extension: grid.extension,
      },
      GOLD,
      'M ',
    ),
  );
}

/** Confluence-зоны: прямоугольники от самого раннего члена; сильнее — плотнее заливка. */
export function zoneRects(zones: LevelZone[]): OverlayZone[] {
  return zones.map((zone) => {
    const alpha = (0.08 + (0.22 * zone.strength) / 100).toFixed(2);
    const rgb = zone.role === 'resistance' ? '239, 83, 80' : '38, 166, 154';
    const start = Math.min(
      ...zone.members.map((m) => toChartTime(m.created_at)),
    );
    return {
      time1: start,
      low: zone.low,
      high: zone.high,
      color: `rgba(${rgb}, ${alpha})`,
      label: `${Math.round(zone.strength)} · ${zone.members.length} ур. · ${zone.families.join('+')}`,
    };
  });
}

/** Ближайший бар не позже `time`: события привязываются к сетке баров графика. */
export function snapToBar(
  times: readonly number[],
  time: number,
): number | null {
  let lo = 0;
  let hi = times.length - 1;
  let found: number | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const value = times[mid] as number;
    if (value <= time) {
      found = value;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}
