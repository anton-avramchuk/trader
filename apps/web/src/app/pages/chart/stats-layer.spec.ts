import type { HorizonStats, OutcomeStats } from '@trader/api-client';
import type { PatternInfo } from './pattern-layer';
import type { LevelInfo } from './structure';
import {
  amount,
  levelStatsRequests,
  patternStatsRequests,
  percent,
  statsQuery,
  statsRows,
  type StatsRequest,
  statsWarnings,
} from './stats-layer';

const horizon = (overrides: Partial<HorizonStats> = {}): HorizonStats => ({
  horizon: 5,
  unit: 'atr',
  n_raw: 40,
  n_effective: 32,
  censored: 2,
  missing_atr: 0,
  win_rate: 0.625,
  ret: { mean: 0.4, median: 0.35 },
  mfe: { mean: 1.3, median: 1.2 },
  mae: { mean: 0.8, median: 0.7 },
  ret_ci: { low: 0.1, high: 0.7 },
  target_rate: 0.4,
  invalidated_rate: 0.25,
  baseline_n: 100,
  baseline_ret: { mean: 0.05, median: 0.0 },
  edge: 0.35,
  edge_ci: { low: 0.05, high: 0.66 },
  flags: {},
  warnings: [],
  ...overrides,
});

const stats = (
  horizons: HorizonStats[],
  warnings: string[] = [],
): OutcomeStats => ({
  matched: 40,
  unit: 'atr',
  skipped: 0,
  warnings,
  buckets: [{ key: 'all', n_occurrences: 40, horizons }],
});

const level = (overrides: Partial<LevelInfo> = {}): LevelInfo => ({
  id: 1,
  source: 'swing_high',
  family: 'swing',
  price: 100,
  role: 'support',
  state: 'active',
  touches: 0,
  createdAt: '2026-09-28T04:00:00Z',
  score: 50,
  components: {},
  version: 1,
  ...overrides,
});

describe('запросы', () => {
  it('паттерн: его тип и направление на его прогоне', () => {
    const info = {
      engine: 'double_triple',
      pattern: 'double_top',
      direction: 'bearish',
    } as PatternInfo;

    const [request] = patternStatsRequests(info);

    expect(request).toMatchObject({
      engine: 'double_triple',
      groups: ['double_top'],
      direction: 'bearish',
      pattern: true,
    });
    expect(request?.title).toContain('Двойная вершина');
    expect(request?.title).toContain('медвежьи');
  });

  it('поддержка: отбой вверх, пробой вниз; сопротивление — наоборот', () => {
    const support = levelStatsRequests(level({ role: 'support' }));
    const resistance = levelStatsRequests(level({ role: 'resistance' }));

    expect(support.map((r) => [r.groups[0], r.direction])).toEqual([
      ['level_touch', 'bullish'],
      ['level_break', 'bearish'],
    ]);
    expect(resistance.map((r) => [r.groups[0], r.direction])).toEqual([
      ['level_touch', 'bearish'],
      ['level_break', 'bullish'],
    ]);
    expect(support.every((r) => r.engine === 'levels' && !r.pattern)).toBe(
      true,
    );
  });

  it('у пробитого уровня роль уже перевёрнута — берётся исходная', () => {
    const [touch] = levelStatsRequests(
      level({ role: 'support', state: 'broken' }),
    );

    expect(touch?.direction).toBe('bearish'); // был сопротивлением
  });

  it('параметры запроса', () => {
    const request = patternStatsRequests({
      engine: 'e',
      pattern: 'double_top',
      direction: 'bearish',
    } as PatternInfo)[0] as StatsRequest;

    expect(statsQuery(7, request, 'pct', '2026-09-28T06:00:00Z')).toEqual({
      run_id: [7],
      group: ['double_top'],
      direction: 'bearish',
      unit: 'pct',
      as_of: '2026-09-28T06:00:00Z',
    });
  });
});

describe('форматирование', () => {
  it('значения с единицами и знаком, пустое — прочерк', () => {
    expect(amount(1.234, 'atr')).toBe('1.23 ATR');
    expect(amount(1.234, 'pct')).toBe('1.23 %');
    expect(amount(0.35, 'atr', true)).toBe('+0.35 ATR');
    expect(amount(-0.35, 'atr', true)).toBe('-0.35 ATR');
    expect(amount(null, 'atr')).toBe('—');
    expect(amount(undefined, 'pct')).toBe('—');
  });

  it('доли в процентах', () => {
    expect(percent(0.625)).toBe('63%');
    expect(percent(0)).toBe('0%');
    expect(percent(null)).toBe('—');
  });
});

describe('строки таблицы', () => {
  it('выборка, медианы, доли и преимущество с интервалом', () => {
    const [row] = statsRows(stats([horizon()]), 'atr');

    expect(row).toMatchObject({
      horizon: 5,
      sample: '32 из 40',
      ret: '+0.35 ATR',
      mfe: '1.20 ATR',
      mae: '0.70 ATR',
      win: '63%',
      target: '40%',
      invalidated: '25%',
      edge: '+0.35 ATR [+0.05; +0.66]',
      censored: 2,
      unreliable: false,
    });
  });

  it('без baseline и интервала — прочерк; мало событий — приглушённая строка', () => {
    const [row] = statsRows(
      stats([
        horizon({
          edge: null,
          edge_ci: null,
          ret: null,
          warnings: ['small_sample'],
        }),
      ]),
      'pct',
    );

    expect(row?.edge).toBe('—');
    expect(row?.ret).toBe('—');
    expect(row?.unreliable).toBe(true);
  });

  it('пустой ответ и отсутствие корзин дают пустую таблицу', () => {
    expect(statsRows(null, 'atr')).toEqual([]);
    expect(
      statsRows({ ...stats([]), buckets: [] } as OutcomeStats, 'atr'),
    ).toEqual([]);
  });
});

describe('предупреждения', () => {
  it('общие и по горизонтам, без повторов, по-русски', () => {
    const data = stats(
      [
        horizon({ warnings: ['small_sample', 'no_baseline'] }),
        horizon({ horizon: 10, warnings: ['small_sample'] }),
      ],
      ['entries_without_bar'],
    );

    const warnings = statsWarnings(data);

    expect(warnings).toHaveLength(3);
    expect(warnings.join(' ')).toContain('мало событий');
    expect(warnings.join(' ')).toContain('случайными точками');
    expect(warnings.join(' ')).toContain('пересчитайте');
    expect(statsWarnings(null)).toEqual([]);
  });

  it('неизвестный код показывается как есть', () => {
    expect(statsWarnings(stats([horizon({ warnings: ['новое'] })]))).toEqual([
      'новое',
    ]);
  });
});
