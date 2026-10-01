import type {
  Backtest,
  BacktestTrade,
  BacktestWindow,
} from '@trader/api-client';
import {
  type BacktestForm,
  buildRequest,
  defaultForm,
  equityChart,
  metricRows,
  metricWarnings,
  parseGrid,
  resolved,
  testInfo,
  tradeRows,
  windowRows,
} from './backtest-model';

const form = (overrides: Partial<BacktestForm> = {}): BacktestForm => ({
  ...defaultForm(),
  instrumentId: 1,
  periodFrom: '2026-01-01',
  periodTo: '2026-06-30',
  ...overrides,
});

describe('parseGrid', () => {
  it('читает параметры по строкам', () => {
    expect(parseGrid('stop_atr: 1, 1.5\n\ntarget_atr: 3')).toEqual({
      stop_atr: [1, 1.5],
      target_atr: [3],
    });
    expect(parseGrid('')).toEqual({});
  });

  it('ошибки — строкой', () => {
    expect(parseGrid('bogus: 1')).toContain('Неизвестный параметр');
    expect(parseGrid('stop_atr: a, 2')).toContain('числа через запятую');
    expect(parseGrid('stop_atr:')).toContain('числа через запятую');
  });
});

describe('buildRequest', () => {
  it('одиночный запрос со стратегией, издержками и периодом', () => {
    const request = buildRequest(
      form({
        groups: 'double_top, double_bottom',
        maxBars: 12,
        qualityMin: 60,
        slippage: 1,
        quantity: 2,
      }),
    );

    expect(request).toMatchObject({
      instrument_id: 1,
      timeframe: '1h',
      kind: 'single',
      quantity: 2,
      period_from: '2026-01-01',
      costs: { slippage_ticks: 1, half_spread_ticks: 0 },
      strategy: {
        source: 'pattern',
        groups: ['double_top', 'double_bottom'],
        side: 'follow',
        quality_min: 60,
        max_bars: 12,
        stop: { kind: 'atr', value: 1.5 },
        target: { kind: 'atr', value: 3 },
      },
    });
    expect(request).not.toHaveProperty('walk_forward');
  });

  it('walk-forward с сеткой и test', () => {
    const request = buildRequest(
      form({
        kind: 'walk_forward',
        testFrom: '2026-07-01',
        testTo: '2026-09-01',
      }),
    );

    expect(request).toMatchObject({
      kind: 'walk_forward',
      walk_forward: {
        train_days: 60,
        valid_days: 20,
        step_days: 20,
        objective: 'profit_factor',
        grid: { stop_atr: [1, 1.5, 2], target_atr: [2, 3] },
      },
      test_from: '2026-07-01',
      test_to: '2026-09-01',
    });
  });

  it.each([
    [{ instrumentId: null }, 'инструмент'],
    [{ periodFrom: '' }, 'период'],
    [{ periodFrom: '2026-07-01' }, 'позже'],
    [{ kind: 'walk_forward' as const, grid: 'nope: 1' }, 'сетки'],
    [{ kind: 'walk_forward' as const, testFrom: '2026-07-01' }, 'двумя датами'],
    [
      {
        kind: 'walk_forward' as const,
        testFrom: '2026-06-01',
        testTo: '2026-07-01',
      },
      'после конца',
    ],
  ])('ошибка формы %o', (overrides, text) => {
    const result = buildRequest(form(overrides));

    expect(typeof result).toBe('string');
    expect(result as string).toContain(text);
  });

  it('test игнорируется для одиночного периода', () => {
    expect(
      buildRequest(form({ testFrom: '2026-07-01', testTo: '2026-08-01' })),
    ).not.toHaveProperty('test_from');
  });
});

describe('метрики', () => {
  const metrics = {
    trades: 12,
    net: 34.5,
    gross: 40,
    average_trade: 2.875,
    win_rate: 0.5833,
    profit_factor: null,
    max_drawdown: 8,
    sharpe: 1.234,
    sortino: null,
    average_mfe: 5,
    average_mae: -2,
    ambiguous_share: 0.0833,
    warnings: ['small_sample', 'x'],
  };

  it('строки и прочерки', () => {
    const rows = Object.fromEntries(
      metricRows(metrics).map((r) => [r.title, r.value]),
    );

    expect(rows).toMatchObject({
      Сделок: '12',
      Net: '34.50',
      'Win rate': '58%',
      'Profit factor': '—',
      Sortino: '—',
      'Неоднозначных баров': '8%',
    });
    expect(metricRows(undefined)).toEqual([]);
  });

  it('предупреждения переводятся', () => {
    expect(metricWarnings(metrics)).toEqual([
      'мало сделок — метрики ненадёжны',
      'x',
    ]);
    expect(metricWarnings(undefined)).toEqual([]);
  });

  it('resolved достаёт метрики и equity', () => {
    const backtest = {
      result: { metrics: { ticks: metrics }, equity: { ticks: [] } },
    } as unknown as Backtest;

    expect(resolved(backtest).metrics['ticks']).toBe(metrics);
    expect(resolved(null)).toEqual({ metrics: {}, equity: {} });
  });
});

describe('сделки и окна', () => {
  const trade = (overrides: Partial<BacktestTrade> = {}): BacktestTrade =>
    ({
      id: 1,
      segment: 'single',
      side: 'long',
      ref: 'double_triple:1',
      entry_time: '2026-09-28T10:15:00Z',
      exit_time: '2026-09-28T12:00:00Z',
      reason: 'target',
      gross_ticks: 5,
      cost_ticks: 2,
      net_points: 0.03,
      net_money: 60,
      ambiguous_bar: false,
      ...overrides,
    }) as BacktestTrade;

  it('единицы и флаги', () => {
    const t = trade({ ambiguous_bar: true });

    expect(tradeRows([t], 'ticks')[0]).toMatchObject({
      side: 'лонг',
      reason: 'цель',
      entry: '2026-09-28 10:15',
      net: '3.00',
      flags: 'неоднозначный бар',
    });
    expect(tradeRows([t], 'points')[0]?.net).toBe('0.03');
    expect(tradeRows([t], 'money')[0]).toMatchObject({
      net: '60.00',
      flags: 'неоднозначный бар',
    });
    expect(
      tradeRows(
        [trade({ net_money: null, side: 'short', ref: null })],
        'money',
      )[0],
    ).toMatchObject({ net: '—', side: 'шорт', ref: '—' });
  });

  it('окна', () => {
    const windows = [
      {
        id: 4,
        train_from: '2026-01-01',
        train_to: '2026-02-01',
        valid_from: '2026-02-02',
        valid_to: '2026-03-01',
        params: { stop_atr: 1.5 },
        train_metrics: { trades: 14 },
        valid_metrics: { trades: 5, net: 3.2 },
      },
      {
        id: 5,
        train_from: 'a',
        train_to: 'b',
        valid_from: 'c',
        valid_to: 'd',
        params: {},
        train_metrics: null,
        valid_metrics: null,
      },
    ] as unknown as BacktestWindow[];

    const [first, second] = windowRows(windows);

    expect(first).toMatchObject({
      params: 'stop_atr=1.5',
      trainTrades: '14',
      validTrades: '5',
      validNet: '3.20',
    });
    expect(second).toMatchObject({
      params: '—',
      trainTrades: '—',
      validNet: '—',
    });
  });
});

describe('test и equity', () => {
  it('статусы test', () => {
    const make = (test: unknown) =>
      ({ result: { test } }) as unknown as Backtest;

    expect(testInfo(make(null), 'ticks')).toBeNull();
    expect(testInfo(make({ status: 'rejected' }), 'ticks')?.title).toContain(
      'уже открывали',
    );
    const evaluated = testInfo(
      make({
        status: 'evaluated',
        params: { stop_atr: 2 },
        metrics: { ticks: { trades: 3 } },
      }),
      'ticks',
    );
    expect(evaluated).toMatchObject({
      status: 'evaluated',
      params: 'stop_atr=2',
      metrics: { trades: 3 },
    });
  });

  it('кривая equity в границах SVG', () => {
    const chart = equityChart([
      { day: '2026-01-01', equity: 5 },
      { day: '2026-01-02', equity: -2 },
      { day: '2026-01-03', equity: 8 },
    ]);

    expect(chart?.min).toBe(-2);
    expect(chart?.max).toBe(8);
    const numbers = chart?.points.split(' ').map(Number) ?? [];
    expect(numbers.every((n) => n >= 0 && n <= 420)).toBe(true);
    expect(chart?.points.split(' ')).toHaveLength(8); // старт + 3 точки
    expect(equityChart([])).toBeNull();
    expect(equityChart(undefined)).toBeNull();
  });
});
