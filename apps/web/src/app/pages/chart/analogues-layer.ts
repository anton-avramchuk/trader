import type { AnalogueMatch, Analogues } from '@trader/api-client';
import { PATTERN_TITLES } from './pattern-layer';
import { amount, percent, type StatsUnit } from './stats-layer';

/** Блок «Аналоги» (ADR-0025): запрос к /analogues, строки списка, веер траекторий. */

export type AnaloguesMode = 'pattern' | 'window';

/** Готовое к показу состояние поиска аналогов. */
export interface AnaloguesView {
  mode: AnaloguesMode;
  loading: boolean;
  error: string | null;
  data: Analogues | null;
}

export const WARNING_TITLES: Record<string, string> = {
  no_candidates: 'в истории нет подходящих вхождений',
  few_matches: 'мало аналогов — вывод ненадёжен',
};

export const MODE_TITLES: Record<AnaloguesMode, string> = {
  pattern: 'Аналоги выбранного паттерна',
  window: 'Аналоги текущего окна',
};

export const analoguesQuery = (
  queryRunId: number,
  runIds: number[],
  unit: StatsUnit,
  key: string | undefined,
  asOf?: string,
) => ({
  query_run_id: queryRunId,
  run_id: runIds,
  key,
  normalization: unit === 'atr' ? ('atr' as const) : ('percent' as const),
  as_of: asOf,
});

export interface AnalogueRow {
  key: string;
  title: string;
  date: string;
  similarity: string;
  ret: string;
  mfe: string;
  mae: string;
}

const dateOf = (iso: string): string => iso.slice(0, 16).replace('T', ' ');

function titleOf(match: AnalogueMatch): string {
  const o = match.occurrence;
  const base = PATTERN_TITLES[o.group] ?? o.group;
  return o.kind === 'level'
    ? `${o.group === 'level_touch' ? 'Касание' : 'Пробой'} уровня`
    : base;
}

/** Строки списка аналогов: доход и экскурсии на первом горизонте. */
export function analogueRows(
  data: Analogues | null,
  unit: StatsUnit,
): AnalogueRow[] {
  if (!data) {
    return [];
  }
  return data.matches.map((m) => {
    const outcome = m.outcomes.find((o) => !o.censored) ?? null;
    const pick = (
      atr: number | null | undefined,
      pct: number | null | undefined,
    ) => (unit === 'atr' ? atr : pct);
    return {
      key: m.occurrence.key,
      title: titleOf(m),
      date: dateOf(m.occurrence.available_at),
      similarity: percent(m.similarity),
      ret: outcome
        ? amount(pick(outcome.ret_atr, outcome.ret_pct), unit, true)
        : '—',
      mfe: outcome ? amount(pick(outcome.mfe_atr, outcome.mfe_pct), unit) : '—',
      mae: outcome ? amount(pick(outcome.mae_atr, outcome.mae_pct), unit) : '—',
    };
  });
}

/** Горизонт, по которому показаны исходы в списке (первый несцензурированный). */
export function listHorizon(data: Analogues | null): number | null {
  const first = data?.matches
    .flatMap((m) => m.outcomes)
    .find((o) => !o.censored);
  return first?.horizon ?? null;
}

export function analoguesWarnings(data: Analogues | null): string[] {
  return (data?.warnings ?? []).map((w) => WARNING_TITLES[w] ?? w);
}

export interface FanPath {
  /** Точки `x y` для SVG polyline (x — бар после входа, y — сдвиг). */
  points: string;
  quartile?: number;
}

export interface Fan {
  width: number;
  height: number;
  steps: number;
  min: number;
  max: number;
  /** Нулевая линия (вход) в координатах SVG. */
  zero: number;
  paths: FanPath[];
  bands: FanPath[];
}

const WIDTH = 320;
const HEIGHT = 140;
const PAD = 8;

/** Веер траекторий аналогов и перцентили 25/50/75 в координатах SVG. */
export function fanChart(data: Analogues | null): Fan | null {
  if (!data) {
    return null;
  }
  const trajectories = data.matches
    .map((m) => m.trajectory)
    .filter((t): t is number[] => !!t && t.length > 0);
  const bands = data.percentiles;
  if (!trajectories.length || !bands.length) {
    return null;
  }
  const steps = trajectories[0].length;
  const values = [...trajectories.flat(), ...bands.flatMap((b) => b.values), 0];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (step: number): number =>
    PAD + ((WIDTH - 2 * PAD) * step) / Math.max(steps, 1);
  const y = (value: number): number =>
    PAD + ((HEIGHT - 2 * PAD) * (max - value)) / span;
  const line = (series: number[]): string =>
    [0, ...series]
      .map((v, i) => `${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
      .join(' ');
  return {
    width: WIDTH,
    height: HEIGHT,
    steps,
    min,
    max,
    zero: y(0),
    paths: trajectories.map((t) => ({ points: line(t) })),
    bands: bands.map((b) => ({ points: line(b.values), quartile: b.q })),
  };
}
