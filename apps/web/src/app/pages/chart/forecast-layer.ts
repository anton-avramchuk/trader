import type {
  Forecast,
  ForecastCalibration,
  HorizonForecast,
  MethodForecast,
} from '@trader/api-client';
import { amount, percent, type StatsUnit } from './stats-layer';

/** Блок Forecast (ADR-0026): запросы к /forecast, строки таблиц, диаграмма квантилей. */

export type ForecastMode = 'pattern' | 'window';

export interface ForecastView {
  mode: ForecastMode;
  loading: boolean;
  error: string | null;
  data: Forecast | null;
}

export interface CalibrationView {
  loading: boolean;
  error: string | null;
  data: ForecastCalibration | null;
}

export const FORECAST_TITLES: Record<ForecastMode, string> = {
  pattern: 'Прогноз выбранного паттерна',
  window: 'Прогноз текущего окна',
};

export const METHOD_TITLES: Record<string, string> = {
  empirical: 'Empirical — вхождения того же типа и направления',
  knn: 'KNN — ближайшие по DTW аналоги',
};

export const WARNING_TITLES: Record<string, string> = {
  no_data: 'нет данных',
  small_sample: 'мало событий — оценка ненадёжна',
  missing_atr: 'у части событий нет ATR',
  no_history: 'в истории нет подходящих вхождений',
  no_matches: 'аналогов не найдено',
  unknown_regime: 'режим запроса неизвестен — фильтр по режиму не применён',
  empirical_needs_occurrence:
    'Empirical строится только для выбранного паттерна',
  occurrence_not_found: 'вхождение не найдено среди прогонов',
  not_enough_history: 'истории мало для проверки калибровки',
};

export const forecastQuery = (
  queryRunId: number,
  runIds: number[],
  unit: StatsUnit,
  key: string | undefined,
  asOf?: string,
) => ({
  query_run_id: queryRunId,
  run_id: runIds,
  key,
  unit,
  normalization: unit === 'atr' ? ('atr' as const) : ('percent' as const),
  as_of: asOf,
});

export const calibrationQuery = (
  runIds: number[],
  group: string,
  direction: 'bullish' | 'bearish',
  unit: StatsUnit,
  horizon: number,
  asOf?: string,
) => ({
  run_id: runIds,
  group,
  direction,
  unit,
  horizon,
  as_of: asOf,
});

const warn = (list: string[]): string[] =>
  [...new Set(list)].map((w) => WARNING_TITLES[w] ?? w);

export interface ForecastRow {
  horizon: number;
  sample: string;
  up: string[];
  down: string[];
  median: string;
  range: string;
  mfe: string;
  mae: string;
  unreliable: boolean;
}

const prob = (p: number | null | undefined): string => percent(p);

/** Строки таблицы метода по горизонтам. */
export function forecastRows(
  method: MethodForecast | null,
  unit: StatsUnit,
): ForecastRow[] {
  return (method?.horizons ?? []).map((h: HorizonForecast) => {
    const q = (n: number) => h.quantiles.find((x) => x.q === n)?.value;
    const low = q(10);
    const high = q(90);
    return {
      horizon: h.horizon,
      sample: `${h.n_effective} из ${h.n_raw}`,
      up: h.probabilities.map((p) => prob(p.up)),
      down: h.probabilities.map((p) => prob(p.down)),
      median: amount(h.median_ret, unit, true),
      range:
        low === undefined || high === undefined
          ? '—'
          : `${amount(low, unit, true)} … ${amount(high, unit, true)}`,
      mfe: amount(h.median_mfe, unit),
      mae: amount(h.median_mae, unit),
      unreliable:
        h.warnings.includes('small_sample') || h.warnings.includes('no_data'),
    };
  });
}

export function methodWarnings(method: MethodForecast | null): string[] {
  if (!method) {
    return [];
  }
  return warn([
    ...method.warnings,
    ...method.horizons.flatMap((h) => h.warnings),
  ]);
}

export function forecastWarnings(data: Forecast | null): string[] {
  return warn(data?.warnings ?? []);
}

export interface BoxRow {
  horizon: number;
  /** Координаты по оси X (0…WIDTH) для q10, q25, медианы, q75, q90. */
  low: number;
  q1: number;
  median: number;
  q3: number;
  high: number;
  y: number;
}

export interface BoxChart {
  width: number;
  height: number;
  zero: number;
  rows: BoxRow[];
}

const WIDTH = 320;
const ROW = 22;
const PAD = 10;

/** Диаграмма «ящик с усами» по квантилям 10/25/50/75/90 для каждого горизонта. */
export function boxChart(method: MethodForecast | null): BoxChart | null {
  const usable = (method?.horizons ?? []).filter(
    (h) => h.quantiles.length === 4 && h.median_ret !== null,
  );
  if (!usable.length) {
    return null;
  }
  const values = usable.flatMap((h) => [
    ...h.quantiles.map((q) => q.value),
    h.median_ret as number,
    0,
  ]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (v: number): number => PAD + ((WIDTH - 2 * PAD) * (v - min)) / span;
  return {
    width: WIDTH,
    height: usable.length * ROW + PAD,
    zero: x(0),
    rows: usable.map((h, i) => {
      const q = (n: number) => h.quantiles.find((v) => v.q === n)?.value ?? 0;
      return {
        horizon: h.horizon,
        low: x(q(10)),
        q1: x(q(25)),
        median: x(h.median_ret as number),
        q3: x(q(75)),
        high: x(q(90)),
        y: PAD / 2 + i * ROW + ROW / 2,
      };
    }),
  };
}

export interface CalibrationRow {
  key: string;
  threshold: string;
  side: string;
  n: number;
  brier: string;
  climatology: string;
  skill: string;
  bins: string[];
}

const SIDE: Record<string, string> = { up: '↑ рост', down: '↓ падение' };

/** Строки отчёта калибровки: Brier, навык и корзины «прогноз → факт (n)». */
export function calibrationRows(
  data: ForecastCalibration | null,
  unit: StatsUnit,
): CalibrationRow[] {
  return (data?.rows ?? []).map((r) => ({
    key: `${r.threshold}:${r.side}`,
    threshold: amount(r.threshold, unit),
    side: SIDE[r.side] ?? r.side,
    n: r.n,
    brier: r.brier.toFixed(3),
    climatology: r.brier_climatology.toFixed(3),
    skill:
      r.skill === null || r.skill === undefined
        ? '—'
        : `${r.skill > 0 ? '+' : ''}${(r.skill * 100).toFixed(1)}%`,
    bins: r.bins.map((b) =>
      b.n && b.mean_predicted !== null && b.observed !== null
        ? `${percent(b.mean_predicted)}→${percent(b.observed)} (${b.n})`
        : '—',
    ),
  }));
}

export function calibrationWarnings(
  data: ForecastCalibration | null,
): string[] {
  return warn(data?.warnings ?? []);
}
