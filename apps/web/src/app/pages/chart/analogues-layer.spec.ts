import type { AnalogueMatch, Analogues } from '@trader/api-client';
import {
  analogueRows,
  analoguesQuery,
  analoguesWarnings,
  fanChart,
  listHorizon,
} from './analogues-layer';

const formation = {
  series_key: 'r1:15m',
  start: '2026-09-28T04:00:00Z',
  end: '2026-09-28T09:00:00Z',
  shape: { indices: [0, 5], times: [0, 1], values: [0, 1] },
};

const match = (
  key: string,
  trajectory: number[] | null,
  overrides: Partial<AnalogueMatch> = {},
): AnalogueMatch =>
  ({
    occurrence: {
      key,
      engine: 'double_top',
      kind: 'pattern',
      group: 'double_top',
      direction: 'bearish',
      entry: 'confirmed',
      available_at: '2026-09-20T10:15:00Z',
    },
    formation,
    distance: 1,
    normalized_distance: 0.25,
    similarity: 0.8,
    path: [[0, 0]],
    outcomes: [
      {
        horizon: 5,
        censored: true,
        ret_atr: null,
        ret_pct: null,
        mfe_atr: null,
        mfe_pct: null,
        mae_atr: null,
        mae_pct: null,
      },
      {
        horizon: 10,
        censored: false,
        ret_atr: -1.5,
        ret_pct: -0.8,
        mfe_atr: 0.4,
        mfe_pct: 0.2,
        mae_atr: 2.1,
        mae_pct: 1.1,
      },
    ],
    trajectory,
    ...overrides,
  }) as unknown as AnalogueMatch;

const analogues = (
  matches: AnalogueMatch[],
  extra: Partial<Analogues> = {},
): Analogues =>
  ({
    query: formation,
    occurrence_key: null,
    as_of: '2026-09-28T09:00:00Z',
    unit: 'atr',
    considered: 10,
    matches,
    stats: {},
    percentiles: [
      { q: 25, values: [0, 1, 2] },
      { q: 50, values: [1, 2, 3] },
      { q: 75, values: [2, 3, 4] },
    ],
    trajectory_count: matches.length,
    warnings: [],
    ...extra,
  }) as unknown as Analogues;

describe('analoguesQuery', () => {
  it('нормализация следует единице статистики', () => {
    expect(analoguesQuery(1, [1, 2], 'atr', 'x:1', 'T')).toEqual({
      query_run_id: 1,
      run_id: [1, 2],
      key: 'x:1',
      normalization: 'atr',
      as_of: 'T',
    });
    expect(analoguesQuery(1, [1], 'pct', undefined).normalization).toBe(
      'percent',
    );
  });
});

describe('analogueRows', () => {
  it('берёт первый несцензурированный горизонт и форматирует значения', () => {
    const data = analogues([match('a:1', [1, 2, 3])]);

    const [row] = analogueRows(data, 'atr');

    expect(row).toMatchObject({
      key: 'a:1',
      title: 'Двойная вершина',
      date: '2026-09-20 10:15',
      similarity: '80%',
      ret: '-1.50 ATR',
      mfe: '0.40 ATR',
      mae: '2.10 ATR',
    });
    expect(listHorizon(data)).toBe(10);
    expect(analogueRows(data, 'pct')[0]?.ret).toBe('-0.80 %');
  });

  it('событие уровня подписывается как касание или пробой', () => {
    const touch = match('l:1', null, {
      occurrence: {
        key: 'l:1',
        group: 'level_touch',
        kind: 'level',
        available_at: '2026-09-20T10:15:00Z',
      } as AnalogueMatch['occurrence'],
    });

    expect(analogueRows(analogues([touch]), 'atr')[0]?.title).toBe(
      'Касание уровня',
    );
  });

  it('без данных строк нет, прочерки при цензуре', () => {
    expect(analogueRows(null, 'atr')).toEqual([]);
    const censored = match('c:1', null, {
      outcomes: [{ horizon: 5, censored: true }] as AnalogueMatch['outcomes'],
    });
    expect(analogueRows(analogues([censored]), 'atr')[0]).toMatchObject({
      ret: '—',
      mfe: '—',
      mae: '—',
    });
    expect(listHorizon(analogues([censored]))).toBeNull();
  });
});

describe('analoguesWarnings', () => {
  it('переводит известные предупреждения', () => {
    expect(
      analoguesWarnings(analogues([], { warnings: ['few_matches', 'x'] })),
    ).toEqual(['мало аналогов — вывод ненадёжен', 'x']);
    expect(analoguesWarnings(null)).toEqual([]);
  });
});

describe('fanChart', () => {
  it('строит траектории и перцентили в границах SVG', () => {
    const fan = fanChart(
      analogues([match('a:1', [0.5, -1, 2]), match('b:1', [1, 0, 1])]),
    );

    expect(fan).not.toBeNull();
    expect(fan?.paths).toHaveLength(2);
    expect(fan?.bands.map((b) => b.quartile)).toEqual([25, 50, 75]);
    expect(fan?.steps).toBe(3);
    const coords = (fan?.paths ?? [])
      .flatMap((p) => p.points.split(' '))
      .map(Number);
    expect(coords.every((n) => n >= 0 && n <= 320)).toBe(true);
    // стартовая точка траектории — на нулевой линии
    expect(fan?.paths[0]?.points.split(' ')[1]).toBe(fan?.zero.toFixed(1));
    expect(fan?.min).toBe(-1);
    expect(fan?.max).toBe(4);
  });

  it('без траекторий или перцентилей веера нет', () => {
    expect(fanChart(null)).toBeNull();
    expect(fanChart(analogues([match('a:1', null)]))).toBeNull();
    expect(
      fanChart(analogues([match('a:1', [1])], { percentiles: [] })),
    ).toBeNull();
  });
});
