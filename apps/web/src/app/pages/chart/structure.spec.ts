import type { EngineEvent, FibGrid, LevelZone } from '@trader/api-client';
import {
  chartLevels,
  DEFAULT_LEVEL_FILTER,
  levelReference,
  currentTrend,
  fibonacciSegments,
  levelInfos,
  levelSegments,
  manualFibSegments,
  pivotSegments,
  snapToBar,
  structureMarkers,
  swingMarkers,
  swingPoints,
  zigzagSegments,
  zoneRects,
} from './structure';

const T0 = '2026-09-28T04:00:00Z';
const sec = (iso: string) => Date.parse(iso) / 1000;

function event(
  seq: number,
  kind: string,
  status: EngineEvent['status'],
  payload: Record<string, unknown>,
  availableAt = '2026-09-28T05:00:00Z',
): EngineEvent {
  return {
    seq,
    kind,
    status,
    payload,
    detected_at: availableAt,
    confirmed_at: null,
    available_at: availableAt,
    revises: null,
  };
}

const swing = (
  seq: number,
  status: EngineEvent['status'],
  type: string,
  price: number,
  timestamp: string,
) => event(seq, 'swing', status, { type, price, timestamp });

describe('swing и zigzag', () => {
  const events = [
    swing(3, 'confirmed', 'low', 90, '2026-09-28T06:00:00Z'),
    swing(1, 'confirmed', 'high', 110, T0),
    swing(5, 'revised', 'high', 105, '2026-09-28T08:00:00Z'),
  ];

  it('точки упорядочены по времени, неподтверждённая помечена', () => {
    expect(
      swingPoints(events).map((p) => [p.type, p.price, p.confirmed]),
    ).toEqual([
      ['high', 110, true],
      ['low', 90, true],
      ['high', 105, false],
    ]);
  });

  it('маркеры: максимумы сверху, минимумы снизу, кандидат со знаком вопроса', () => {
    const markers = swingMarkers(events);

    expect(markers.map((m) => m.position)).toEqual([
      'aboveBar',
      'belowBar',
      'aboveBar',
    ]);
    expect(markers.map((m) => m.text)).toEqual(['', '', '?']);
    expect(markers[0]?.time).toBe(sec(T0));
  });

  it('ZigZag соединяет соседние точки, последнее колено — пунктиром', () => {
    const segments = zigzagSegments(events);

    expect(segments).toHaveLength(2);
    expect(segments.map((s) => s.dashed)).toEqual([false, true]);
    expect([segments[0]?.price1, segments[0]?.price2]).toEqual([110, 90]);
  });

  it('без точек ничего не рисуется', () => {
    expect(zigzagSegments([])).toEqual([]);
    expect(swingMarkers([])).toEqual([]);
  });
});

describe('структура', () => {
  const events = [
    event(0, 'structure_point', 'confirmed', {
      label: 'HH',
      type: 'high',
      price: 130,
      timestamp: '2026-09-28T07:00:00Z',
    }),
    event(1, 'structure_point', 'confirmed', {
      label: 'LL',
      type: 'low',
      price: 80,
      timestamp: '2026-09-28T06:00:00Z',
    }),
    event(2, 'trend_state', 'confirmed', { state: 'uptrend' }),
    event(3, 'trend_state', 'confirmed', { state: 'downtrend' }),
  ];

  it('метки HH/LL с цветом и стрелкой по типу точки', () => {
    const markers = structureMarkers(events);

    expect(markers.map((m) => m.text)).toEqual(['LL', 'HH']);
    expect(markers.map((m) => m.shape)).toEqual(['arrowUp', 'arrowDown']);
    expect(markers[0]?.color).toBeDefined();
  });

  it('текущее состояние тренда — последнее', () => {
    expect(currentTrend(events)).toBe('downtrend');
    expect(currentTrend([])).toBeNull();
  });
});

describe('уровни', () => {
  const level = (id: number, score: number, state: string, role: string) =>
    event(id, 'level', 'detected', {
      id,
      source: 'swing_high',
      family: 'swing',
      price: 100 + id,
      role,
      state,
      touches: 2,
      created_at: T0,
      strength: { version: 1, score, components: { touches: 2, age: 5 } },
    });

  it('уровни сортируются по силе; линии — от появления до правого края', () => {
    const infos = levelInfos([
      level(1, 20, 'active', 'support'),
      level(2, 60, 'active', 'resistance'),
      level(3, 40, 'broken', 'support'),
    ]);

    expect(infos.map((l) => l.id)).toEqual([2, 3, 1]);
    const segments = levelSegments(infos, 1);
    expect(segments.every((s) => s.time2 === null && s.time1 === sec(T0))).toBe(
      true,
    );
    expect(segments.map((s) => s.width)).toEqual([3, 2, 4]); // 60→3, 40→2, выбранный→4
    expect(segments[1]?.dashed).toBe(true); // пробитый
    expect(segments[0]?.label).toBe('swing_high 60');
    expect(infos[0]?.components).toEqual({ touches: 2, age: 5 });
  });

  describe('фильтр на график', () => {
    const at = (
      id: number,
      price: number,
      score: number,
      family = 'swing',
    ) => ({
      ...levelInfos([level(id, score, 'active', 'support')])[0]!,
      id,
      price,
      family,
      touches: 2,
    });
    const reference = { price: 100, atr: 1 };
    const filter = { ...DEFAULT_LEVEL_FILTER, reference };

    it('только активные, сильнейшие первыми, не больше perSide с каждой стороны', () => {
      const many = Array.from({ length: 10 }, (_, i) =>
        at(i + 1, 90 + i * 2, i + 1),
      );
      const infos = levelInfos([level(99, 99, 'broken', 'resistance')]);

      const shown = chartLevels(
        [...many, ...infos].sort((a, b) => b.score - a.score),
        {
          ...filter,
          maxDistanceAtr: null,
          perSide: 2,
        },
      );

      expect(shown.some((l) => l.state === 'broken')).toBe(false);
      expect(shown.filter((l) => l.price <= 100)).toHaveLength(2);
      expect(shown.filter((l) => l.price > 100)).toHaveLength(2);
      expect(shown[0]?.score).toBeGreaterThan(shown[3]?.score ?? 0);
    });

    it('далёкие от цены уровни не занимают места', () => {
      const shown = chartLevels(
        [at(1, 60, 90), at(2, 99, 10), at(3, 105, 20)],
        { ...filter, maxDistanceAtr: 10 },
      );

      expect(shown.map((l) => l.id)).toEqual([2, 3]);
    });

    it('близкие дубли сливаются: остаётся сильнейший', () => {
      const shown = chartLevels(
        [at(2, 99.3, 70, 'prev'), at(1, 99, 50), at(3, 96, 30)],
        filter,
      );

      expect(shown.map((l) => l.id)).toEqual([2, 3]);
    });

    it('fib и pivot скрыты по умолчанию; swing с малым числом касаний — по порогу', () => {
      const infos = [
        at(1, 98, 50, 'fib'),
        at(2, 97, 50, 'pivot'),
        at(3, 96, 50),
      ];

      expect(chartLevels(infos, filter).map((l) => l.id)).toEqual([3]);
      expect(
        chartLevels(infos, { ...filter, derived: true }).map((l) => l.id),
      ).toEqual([1, 2, 3]);
      expect(chartLevels(infos, { ...filter, minTouches: 3 })).toEqual([]);
      expect(chartLevels(infos, { ...filter, minScore: 60 })).toEqual([]);
    });

    it('без опорной цены сторона берётся по роли, расстояние не применяется', () => {
      const shown = chartLevels([at(1, 500, 40)], DEFAULT_LEVEL_FILTER);

      expect(shown).toHaveLength(1);
    });
  });

  it('опорная цена и ATR считаются по последним свечам', () => {
    const bars = Array.from({ length: 20 }, (_, i) => ({
      high: 101 + i,
      low: 99 + i,
      close: 100 + i,
    }));

    const ref = levelReference(bars);

    expect(ref?.price).toBe(119);
    expect(ref?.atr).toBe(2); // TR = max(high−low, |high−prevClose|, |low−prevClose|) = 2
    expect(levelReference([])).toBeNull();
  });
});

describe('pivot', () => {
  const pivot = (seq: number, period: string, at: string) =>
    event(
      seq,
      'pivot',
      'confirmed',
      { period, pp: 100, r1: 110, r2: 120, r3: 130, s1: 90, s2: 80, s3: 70 },
      at,
    );

  it('каждый уровень действует до следующего события периода; хранится лишь хвост', () => {
    const segments = pivotSegments([
      pivot(0, 'day', '2026-09-25T04:00:00Z'),
      pivot(1, 'day', '2026-09-26T04:00:00Z'),
      pivot(2, 'day', '2026-09-27T04:00:00Z'),
      pivot(3, 'week', '2026-09-28T04:00:00Z'),
    ]);

    const day = segments.filter((s) => s.label?.startsWith('D '));
    const week = segments.filter((s) => s.label?.startsWith('W '));
    expect(day).toHaveLength(14); // 2 последних дня × 7 уровней
    expect(
      day.slice(0, 7).every((s) => s.time2 === sec('2026-09-27T04:00:00Z')),
    ).toBe(true);
    expect(day.slice(7).every((s) => s.time2 === null)).toBe(true);
    expect(week).toHaveLength(7);
    expect(week.every((s) => s.dashed)).toBe(true);
  });
});

describe('Fibonacci', () => {
  it('автоматическая сетка берётся из последнего события fib_grid', () => {
    const grid = (seq: number, end: number) =>
      event(seq, 'fib_grid', 'revised', {
        start: { price: 120, timestamp: T0 },
        end: { price: end, timestamp: '2026-09-28T06:00:00Z' },
        retracement: { '38.2': 107.64 },
        extension: { '100': end, '161.8': 87.64 },
      });

    const segments = fibonacciSegments([grid(1, 105), grid(2, 100)]);

    expect(segments).toHaveLength(3); // нога + 38.2 + 161.8 (100 % пропущен)
    expect(segments[0]).toMatchObject({
      price1: 120,
      price2: 100,
      dashed: true,
    });
    expect(segments.slice(1).map((s) => s.label)).toEqual([
      'F 38.2',
      'F 161.8',
    ]);
    expect(fibonacciSegments([])).toEqual([]);
  });

  it('ручные сетки рисуются отдельным цветом', () => {
    const manual: FibGrid = {
      id: 1,
      instrument_id: 5,
      timeframe: '15m',
      start: { time: T0, price: 100 },
      end: { time: '2026-09-28T06:00:00Z', price: 120 },
      label: null,
      direction: 'up',
      retracement: { '50': 110 },
      extension: { '127.2': 125.44 },
      manual: true,
      created_at: T0,
    };

    const segments = manualFibSegments([manual]);

    expect(segments.map((s) => s.label)).toEqual([
      undefined,
      'M 50',
      'M 127.2',
    ]);
    expect(new Set(segments.map((s) => s.color)).size).toBe(1);
  });
});

describe('зоны и привязка к барам', () => {
  it('зона начинается с самого раннего члена, сила меняет заливку', () => {
    const zone = (strength: number): LevelZone => ({
      low: 100,
      high: 101,
      center: 100.5,
      width_atr: 0.2,
      role: 'resistance',
      strength,
      families: ['pivot', 'swing'],
      members: [
        {
          id: 1,
          price: 100,
          source: 'a',
          family: 'swing',
          role: 'resistance',
          strength: 10,
          source_timeframe: '15m',
          created_at: '2026-09-28T06:00:00Z',
          distance_atr: 0,
        },
        {
          id: 2,
          price: 101,
          source: 'b',
          family: 'pivot',
          role: 'resistance',
          strength: 20,
          source_timeframe: '15m',
          created_at: T0,
          distance_atr: 0,
        },
      ],
    });

    const [weak] = zoneRects([zone(10)]);
    const [strong] = zoneRects([zone(90)]);

    expect(weak?.time1).toBe(sec(T0));
    expect(weak?.label).toContain('2 ур.');
    expect(weak?.color).not.toBe(strong?.color);
  });

  it('snapToBar: ближайший бар не позже времени', () => {
    const times = [10, 20, 30];

    expect(snapToBar(times, 25)).toBe(20);
    expect(snapToBar(times, 30)).toBe(30);
    expect(snapToBar(times, 99)).toBe(30);
    expect(snapToBar(times, 5)).toBeNull();
    expect(snapToBar([], 5)).toBeNull();
  });
});
