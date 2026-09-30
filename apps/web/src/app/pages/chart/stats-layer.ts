import type { HorizonStats, OutcomeStats } from '@trader/api-client';
import { PATTERN_TITLES, type PatternInfo } from './pattern-layer';
import type { LevelInfo } from './structure';

/** Блок «Исторически» (ADR-0023): запросы к /stats/outcomes и подготовка строк таблицы. */

export type StatsUnit = 'atr' | 'pct';
type Direction = 'bullish' | 'bearish';

/** Одна выборка для показа: какой движок, какие группы, какое направление. */
export interface StatsRequest {
  title: string;
  engine: string;
  groups: string[];
  direction: Direction;
  /** У паттернов показываем доли цели и отмены, у событий уровней их нет. */
  pattern: boolean;
}

/** Готовое к показу состояние одной выборки. */
export interface StatsView {
  title: string;
  pattern: boolean;
  loading: boolean;
  error: string | null;
  data: OutcomeStats | null;
}

export const patternStatsRequests = (info: PatternInfo): StatsRequest[] => [
  {
    title: `${PATTERN_TITLES[info.pattern] ?? info.pattern}, ${
      info.direction === 'bullish' ? 'бычьи' : 'медвежьи'
    } — история на этом ряду`,
    engine: info.engine,
    groups: [info.pattern],
    direction: info.direction,
    pattern: true,
  },
];

/** Отбой и пробой уровней той же роли; у пробитого уровня роль уже перевёрнута. */
export function levelStatsRequests(level: LevelInfo): StatsRequest[] {
  const flipped = level.role === 'support' ? 'resistance' : 'support';
  const origin = level.state === 'broken' ? flipped : level.role;
  const support = origin === 'support';
  const bounce: Direction = support ? 'bullish' : 'bearish';
  const breakout: Direction = support ? 'bearish' : 'bullish';
  const name = support ? 'поддержки' : 'сопротивления';
  return [
    {
      title: `Отбой от ${name}: все уровни ряда`,
      engine: 'levels',
      groups: ['level_touch'],
      direction: bounce,
      pattern: false,
    },
    {
      title: `Пробой ${name}: все уровни ряда`,
      engine: 'levels',
      groups: ['level_break'],
      direction: breakout,
      pattern: false,
    },
  ];
}

export const statsQuery = (
  runId: number,
  request: StatsRequest,
  unit: StatsUnit,
  asOf?: string,
) => ({
  run_id: [runId],
  group: request.groups,
  direction: request.direction,
  unit,
  as_of: asOf,
});

export const WARNING_TITLES: Record<string, string> = {
  no_data: 'нет данных',
  small_sample: 'мало событий — оценка ненадёжна',
  missing_atr: 'у части событий нет ATR (не хватило истории)',
  no_baseline: 'нет сравнения со случайными точками',
  small_baseline: 'слишком мало случайных точек для сравнения',
  entries_without_bar: 'часть событий не найдена в барах ряда — пересчитайте',
};

const SUFFIX: Record<StatsUnit, string> = { atr: ' ATR', pct: ' %' };

const signed = (value: number): string =>
  `${value > 0 ? '+' : ''}${value.toFixed(2)}`;

export const percent = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`;

export function amount(
  value: number | null | undefined,
  unit: StatsUnit,
  withSign = false,
): string {
  if (value === null || value === undefined) {
    return '—';
  }
  return `${withSign ? signed(value) : value.toFixed(2)}${SUFFIX[unit]}`;
}

export interface StatsRow {
  horizon: number;
  sample: string;
  ret: string;
  mfe: string;
  mae: string;
  win: string;
  target: string;
  invalidated: string;
  edge: string;
  censored: number;
  /** Мало событий: строку нужно показывать приглушённой. */
  unreliable: boolean;
}

function edgeText(stats: HorizonStats, unit: StatsUnit): string {
  if (stats.edge === null || stats.edge === undefined) {
    return '—';
  }
  const ci = stats.edge_ci;
  const interval = ci ? ` [${signed(ci.low)}; ${signed(ci.high)}]` : '';
  return `${amount(stats.edge, unit, true)}${interval}`;
}

/** Строки таблицы по горизонтам первой (и единственной) корзины ответа. */
export function statsRows(
  data: OutcomeStats | null,
  unit: StatsUnit,
): StatsRow[] {
  const bucket = data?.buckets[0];
  if (!bucket) {
    return [];
  }
  return bucket.horizons.map((h) => ({
    horizon: h.horizon,
    sample: `${h.n_effective} из ${h.n_raw}`,
    ret: amount(h.ret?.median, unit, true),
    mfe: amount(h.mfe?.median, unit),
    mae: amount(h.mae?.median, unit),
    win: percent(h.win_rate),
    target: percent(h.target_rate),
    invalidated: percent(h.invalidated_rate),
    edge: edgeText(h, unit),
    censored: h.censored,
    unreliable:
      h.warnings.includes('small_sample') || h.warnings.includes('no_data'),
  }));
}

/** Предупреждения, общие для всех горизонтов (без повторов). */
export function statsWarnings(data: OutcomeStats | null): string[] {
  if (!data) {
    return [];
  }
  const found = new Set<string>(data.warnings);
  for (const bucket of data.buckets) {
    for (const horizon of bucket.horizons) {
      horizon.warnings.forEach((w) => found.add(w));
    }
  }
  return [...found].map((w) => WARNING_TITLES[w] ?? w);
}
