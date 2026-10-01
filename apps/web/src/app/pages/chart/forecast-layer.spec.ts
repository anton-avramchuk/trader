import type {
  Forecast,
  ForecastCalibration,
  HorizonForecast,
  MethodForecast,
} from '@trader/api-client';
import {
  boxChart,
  calibrationQuery,
  calibrationRows,
  calibrationWarnings,
  forecastQuery,
  forecastRows,
  forecastWarnings,
  methodWarnings,
} from './forecast-layer';

const horizon = (overrides: Partial<HorizonForecast> = {}): HorizonForecast =>
  ({
    horizon: 5,
    unit: 'atr',
    n_raw: 40,
    n_effective: 32,
    censored: 0,
    missing_atr: 0,
    mean_ret: 0.4,
    median_ret: 0.35,
    ret_ci: { low: 0.1, high: 0.7 },
    quantiles: [
      { q: 10, value: -1.2 },
      { q: 25, value: -0.4 },
      { q: 75, value: 1.1 },
      { q: 90, value: 2.0 },
    ],
    median_mfe: 1.2,
    median_mae: 0.7,
    probabilities: [
      { threshold: 0.5, up: 0.4, down: 0.2, up_ci: null, down_ci: null },
      { threshold: 1, up: 0.25, down: 0.1, up_ci: null, down_ci: null },
    ],
    warnings: [],
    ...overrides,
  }) as HorizonForecast;

const method = (
  horizons: HorizonForecast[],
  overrides: Partial<MethodForecast> = {},
): MethodForecast => ({
  method: 'empirical',
  sample: 40,
  horizons,
  warnings: [],
  ...overrides,
});

describe('запросы', () => {
  it('forecastQuery: нормализация следует единице', () => {
    expect(forecastQuery(1, [1, 2], 'atr', 'x:1', 'T')).toEqual({
      query_run_id: 1,
      run_id: [1, 2],
      key: 'x:1',
      unit: 'atr',
      normalization: 'atr',
      as_of: 'T',
    });
    expect(forecastQuery(1, [1], 'pct', undefined).normalization).toBe(
      'percent',
    );
  });

  it('calibrationQuery: тип, направление, горизонт', () => {
    expect(
      calibrationQuery([3, 4], 'double_top', 'bearish', 'pct', 10, 'T'),
    ).toEqual({
      run_id: [3, 4],
      group: 'double_top',
      direction: 'bearish',
      unit: 'pct',
      horizon: 10,
      as_of: 'T',
    });
  });
});

describe('forecastRows', () => {
  it('форматирует вероятности, медиану и разброс', () => {
    const [row] = forecastRows(method([horizon()]), 'atr');

    expect(row).toMatchObject({
      horizon: 5,
      sample: '32 из 40',
      up: ['40%', '25%'],
      down: ['20%', '10%'],
      median: '+0.35 ATR',
      range: '-1.20 ATR … +2.00 ATR',
      mfe: '1.20 ATR',
      mae: '0.70 ATR',
      unreliable: false,
    });
  });

  it('малая выборка помечается, пустые значения — прочерки', () => {
    const [row] = forecastRows(
      method([
        horizon({
          warnings: ['small_sample'],
          median_ret: null,
          quantiles: [],
          median_mfe: null,
          median_mae: null,
          probabilities: [],
        }),
      ]),
      'pct',
    );

    expect(row).toMatchObject({
      unreliable: true,
      median: '—',
      range: '—',
      mfe: '—',
      up: [],
    });
    expect(forecastRows(null, 'atr')).toEqual([]);
  });
});

describe('предупреждения', () => {
  it('переводятся и не повторяются', () => {
    const m = method([horizon({ warnings: ['small_sample'] })], {
      warnings: ['small_sample', 'no_history'],
    });

    expect(methodWarnings(m)).toEqual([
      'мало событий — оценка ненадёжна',
      'в истории нет подходящих вхождений',
    ]);
    expect(methodWarnings(null)).toEqual([]);
    expect(
      forecastWarnings({
        warnings: ['empirical_needs_occurrence', 'x'],
      } as Forecast),
    ).toEqual(['Empirical строится только для выбранного паттерна', 'x']);
    expect(forecastWarnings(null)).toEqual([]);
  });
});

describe('boxChart', () => {
  it('ящик по квантилям: порядок координат и нулевая линия', () => {
    const chart = boxChart(
      method([horizon(), horizon({ horizon: 10, median_ret: 0.9 })]),
    );

    expect(chart?.rows).toHaveLength(2);
    for (const row of chart?.rows ?? []) {
      expect(row.low).toBeLessThan(row.q1);
      expect(row.q1).toBeLessThan(row.q3);
      expect(row.q3).toBeLessThan(row.high);
      expect(row.median).toBeGreaterThan(row.low);
      expect(row.median).toBeLessThan(row.high);
      expect(row.high).toBeLessThanOrEqual(320);
      expect(row.low).toBeGreaterThanOrEqual(0);
    }
    expect(chart?.zero).toBeGreaterThan(chart?.rows[0]?.low ?? 0);
    expect(chart?.zero).toBeLessThan(chart?.rows[0]?.high ?? 0);
  });

  it('без квантилей диаграммы нет', () => {
    expect(boxChart(null)).toBeNull();
    expect(boxChart(method([horizon({ quantiles: [] })]))).toBeNull();
  });
});

describe('калибровка', () => {
  const report = (): ForecastCalibration =>
    ({
      group: 'double_top',
      direction: 'bearish',
      horizon: 10,
      unit: 'atr',
      thresholds: [1],
      occurrences: 80,
      tested: 60,
      skipped: 20,
      warnings: ['small_sample'],
      rows: [
        {
          threshold: 1,
          side: 'up',
          n: 60,
          brier: 0.2134,
          brier_climatology: 0.2201,
          skill: 0.0304,
          bins: [
            {
              low: 0,
              high: 0.2,
              n: 0,
              mean_predicted: null,
              observed: null,
            },
            {
              low: 0.2,
              high: 0.4,
              n: 60,
              mean_predicted: 0.3,
              observed: 0.28,
            },
          ],
        },
        {
          threshold: 1,
          side: 'down',
          n: 60,
          brier: 0.18,
          brier_climatology: 0.18,
          skill: null,
          bins: [],
        },
      ],
    }) as unknown as ForecastCalibration;

  it('строки: Brier, навык и корзины', () => {
    const [up, down] = calibrationRows(report(), 'atr');

    expect(up).toMatchObject({
      threshold: '1.00 ATR',
      side: '↑ рост',
      n: 60,
      brier: '0.213',
      climatology: '0.220',
      skill: '+3.0%',
      bins: ['—', '30%→28% (60)'],
    });
    expect(down).toMatchObject({ side: '↓ падение', skill: '—' });
    expect(calibrationRows(null, 'atr')).toEqual([]);
  });

  it('предупреждения переводятся', () => {
    expect(calibrationWarnings(report())).toEqual([
      'мало событий — оценка ненадёжна',
    ]);
    expect(calibrationWarnings(null)).toEqual([]);
  });
});
