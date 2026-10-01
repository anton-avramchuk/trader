import type {
  Backtest,
  BacktestCreate,
  BacktestTrade,
  BacktestWindow,
} from '@trader/api-client';
import type { LevelInfo } from '../chart/structure';

/** Форма бэктеста (ADR-0027): сборка запроса и представление результата. */

export type BacktestKind = 'single' | 'walk_forward';
export type Source = 'pattern' | 'level_touch' | 'level_break';
export type MetricUnit = 'ticks' | 'points' | 'rub';

export const SOURCE_TITLES: Record<Source, string> = {
  pattern: 'Подтверждённый паттерн',
  level_touch: 'Касание уровня',
  level_break: 'Пробой уровня',
};

export const STOP_KINDS = [
  { value: 'atr', title: 'в ATR' },
  { value: 'structure', title: 'экстремум паттерна' },
  { value: 'none', title: 'нет' },
];
export const TARGET_KINDS = [
  { value: 'atr', title: 'в ATR' },
  { value: 'pattern', title: 'цель паттерна' },
  { value: 'none', title: 'нет' },
];

export const GRID_KEYS = [
  'stop_atr',
  'target_atr',
  'max_bars',
  'quality_min',
] as const;

export interface BacktestForm {
  rootId: number | null;
  timeframe: string;
  kind: BacktestKind;
  periodFrom: string;
  periodTo: string;
  source: Source;
  groups: string;
  side: 'follow' | 'fade';
  stopKind: string;
  stopValue: number;
  targetKind: string;
  targetValue: number;
  maxBars: number | null;
  qualityMin: number | null;
  halfSpread: number;
  slippage: number;
  commission: number;
  contracts: number;
  trainDays: number;
  validDays: number;
  stepDays: number;
  objective: 'profit_factor' | 'net';
  minTrades: number;
  /** По строке `параметр: значение, значение`. */
  grid: string;
  testFrom: string;
  testTo: string;
}

export const defaultForm = (): BacktestForm => ({
  rootId: null,
  timeframe: '1h',
  kind: 'single',
  periodFrom: '',
  periodTo: '',
  source: 'pattern',
  groups: '',
  side: 'follow',
  stopKind: 'atr',
  stopValue: 1.5,
  targetKind: 'atr',
  targetValue: 3,
  maxBars: null,
  qualityMin: null,
  halfSpread: 0,
  slippage: 0,
  commission: 0,
  contracts: 1,
  trainDays: 60,
  validDays: 20,
  stepDays: 20,
  objective: 'profit_factor',
  minTrades: 10,
  grid: 'stop_atr: 1, 1.5, 2\ntarget_atr: 2, 3',
  testFrom: '',
  testTo: '',
});

/** Сетка из текста: по строке на параметр; ошибка — строкой. */
export function parseGrid(text: string): Record<string, number[]> | string {
  const grid: Record<string, number[]> = {};
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line) {
      continue;
    }
    const [key, rest] = line.split(':');
    const name = (key ?? '').trim();
    if (!GRID_KEYS.includes(name as (typeof GRID_KEYS)[number])) {
      return `Неизвестный параметр сетки «${name}»; доступны: ${GRID_KEYS.join(', ')}`;
    }
    const values = (rest ?? '')
      .split(',')
      .map((v) => v.trim())
      .filter((v) => v !== '')
      .map(Number);
    if (!values.length || values.some((v) => !Number.isFinite(v))) {
      return `Параметр «${name}»: укажите числа через запятую`;
    }
    grid[name] = values;
  }
  return grid;
}

/** Запрос к `POST /backtests` или текст ошибки формы. */
export function buildRequest(form: BacktestForm): BacktestCreate | string {
  if (form.rootId === null) {
    return 'Выберите инструмент';
  }
  if (!form.periodFrom || !form.periodTo) {
    return 'Укажите период';
  }
  if (form.periodFrom > form.periodTo) {
    return 'Начало периода позже конца';
  }
  const groups = form.groups
    .split(',')
    .map((g) => g.trim())
    .filter(Boolean);
  const request: BacktestCreate = {
    root_id: form.rootId,
    timeframe: form.timeframe,
    kind: form.kind,
    strategy: {
      source: form.source,
      groups,
      side: form.side,
      quality_min: form.qualityMin,
      stop: { kind: form.stopKind, value: form.stopValue },
      target: { kind: form.targetKind, value: form.targetValue },
      max_bars: form.maxBars,
    },
    costs: {
      half_spread_ticks: form.halfSpread,
      slippage_ticks: form.slippage,
      commission_per_contract: form.commission,
    },
    contracts: form.contracts,
    period_from: form.periodFrom,
    period_to: form.periodTo,
  };
  if (form.kind === 'walk_forward') {
    const grid = parseGrid(form.grid);
    if (typeof grid === 'string') {
      return grid;
    }
    request.walk_forward = {
      train_days: form.trainDays,
      valid_days: form.validDays,
      step_days: form.stepDays,
      grid,
      objective: form.objective,
      min_trades: form.minTrades,
    };
    if (form.testFrom || form.testTo) {
      if (!form.testFrom || !form.testTo) {
        return 'Test-период задаётся двумя датами';
      }
      if (form.testFrom <= form.periodTo) {
        return 'Test-период должен идти после конца периода walk-forward';
      }
      request.test_from = form.testFrom;
      request.test_to = form.testTo;
    }
  }
  return request;
}

export const WARNING_TITLES: Record<string, string> = {
  no_trades: 'сделок нет',
  small_sample: 'мало сделок — метрики ненадёжны',
  step_price_missing: 'у части сделок нет step_price — ₽ посчитаны не по всем',
  no_windows: 'период короче одного окна',
  no_qualifying_params:
    'ни один набор параметров не набрал нужного числа сделок',
  some_windows_without_params: 'в части окон не нашлось подходящих параметров',
};

export const UNIT_TITLES: Record<MetricUnit, string> = {
  ticks: 'тики',
  points: 'пункты',
  rub: '₽',
};

const num = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined ? '—' : v.toFixed(digits);
const pct = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`;

type MetricsDict = Record<string, unknown>;

export interface MetricRow {
  title: string;
  value: string;
}

/** Строки метрик одной единицы из `result.metrics[unit]`. */
export function metricRows(metrics: MetricsDict | undefined): MetricRow[] {
  if (!metrics) {
    return [];
  }
  const n = (key: string) => metrics[key] as number | null | undefined;
  return [
    { title: 'Сделок', value: String(n('trades') ?? 0) },
    { title: 'Net', value: num(n('net')) },
    { title: 'Gross', value: num(n('gross')) },
    { title: 'Средняя сделка', value: num(n('average_trade')) },
    { title: 'Win rate', value: pct(n('win_rate')) },
    { title: 'Profit factor', value: num(n('profit_factor')) },
    { title: 'Max drawdown', value: num(n('max_drawdown')) },
    { title: 'Sharpe', value: num(n('sharpe')) },
    { title: 'Sortino', value: num(n('sortino')) },
    { title: 'Средний MFE', value: num(n('average_mfe')) },
    { title: 'Средний MAE', value: num(n('average_mae')) },
    { title: 'Неоднозначных баров', value: pct(n('ambiguous_share')) },
    { title: 'Сделок через ролл', value: pct(n('rolled_share')) },
  ];
}

export function metricWarnings(metrics: MetricsDict | undefined): string[] {
  const list = (metrics?.['warnings'] as string[] | undefined) ?? [];
  return list.map((w) => WARNING_TITLES[w] ?? w);
}

export interface Resolved {
  metrics: Record<string, MetricsDict>;
  equity: Record<string, { day: string; equity: number }[]>;
}

/** Метрики и equity основного результата (single — весь период, WF — validation). */
export function resolved(backtest: Backtest | null): Resolved {
  const result = (backtest?.result ?? {}) as {
    metrics?: Record<string, MetricsDict>;
    equity?: Resolved['equity'];
  };
  return { metrics: result.metrics ?? {}, equity: result.equity ?? {} };
}

export interface TradeRow {
  id: number;
  segment: string;
  side: string;
  ref: string;
  entry: string;
  exit: string;
  reason: string;
  net: string;
  flags: string;
}

const REASONS: Record<string, string> = {
  stop: 'стоп',
  target: 'цель',
  time: 'время',
  roll: 'ролл',
  end_of_data: 'конец данных',
};

const stamp = (iso: string): string => iso.slice(0, 16).replace('T', ' ');

export function tradeRows(
  trades: BacktestTrade[],
  unit: MetricUnit,
): TradeRow[] {
  return trades.map((t) => {
    const net =
      unit === 'ticks'
        ? t.gross_ticks - t.cost_ticks
        : unit === 'points'
          ? t.net_points
          : t.net_rub;
    const flags = [
      t.ambiguous_bar ? 'неоднозначный бар' : '',
      t.rolled ? 'ролл' : '',
      unit === 'rub' && t.step_price_estimated ? 'step_price оценён' : '',
    ]
      .filter(Boolean)
      .join(', ');
    return {
      id: t.id,
      segment: t.segment,
      side: t.side === 'long' ? 'лонг' : 'шорт',
      ref: t.ref ?? '—',
      entry: stamp(t.entry_time),
      exit: stamp(t.exit_time),
      reason: REASONS[t.reason] ?? t.reason,
      net: num(net),
      flags,
    };
  });
}

export interface WindowRow {
  id: number;
  train: string;
  valid: string;
  params: string;
  trainTrades: string;
  validTrades: string;
  validNet: string;
}

const paramsText = (params: Record<string, unknown>): string =>
  Object.keys(params).length
    ? Object.entries(params)
        .map(([k, v]) => `${k}=${String(v)}`)
        .join(', ')
    : '—';

export function windowRows(windows: BacktestWindow[]): WindowRow[] {
  return windows.map((w) => {
    const train = (w.train_metrics ?? {}) as MetricsDict;
    const valid = (w.valid_metrics ?? {}) as MetricsDict;
    return {
      id: w.id,
      train: `${w.train_from} … ${w.train_to}`,
      valid: `${w.valid_from} … ${w.valid_to}`,
      params: paramsText(w.params),
      trainTrades: String((train['trades'] as number | undefined) ?? '—'),
      validTrades: String((valid['trades'] as number | undefined) ?? '—'),
      validNet: num(valid['net'] as number | undefined),
    };
  });
}

export interface TestInfo {
  status: string;
  title: string;
  metrics?: MetricsDict;
  params?: string;
}

const TEST_TITLES: Record<string, string> = {
  evaluated: 'Test-период открыт и посчитан',
  rejected: 'Test-период этой связки уже открывали — результат не пересчитан',
  skipped_no_params: 'Параметры не выбраны — test не расходуется',
};

export function testInfo(
  backtest: Backtest | null,
  unit: MetricUnit,
): TestInfo | null {
  const test = (backtest?.result as { test?: Record<string, unknown> } | null)
    ?.test;
  if (!test) {
    return null;
  }
  const status = String(test['status']);
  const metrics = (
    test['metrics'] as Record<string, MetricsDict> | undefined
  )?.[unit];
  return {
    status,
    title: TEST_TITLES[status] ?? status,
    metrics,
    params: test['params']
      ? paramsText(test['params'] as Record<string, unknown>)
      : undefined,
  };
}

export interface EquityChart {
  width: number;
  height: number;
  zero: number;
  points: string;
  min: number;
  max: number;
}

const W = 420;
const H = 140;
const PAD = 8;

/** Кривая equity в координатах SVG; пусто — нет сделок. */
export function equityChart(
  points: { day: string; equity: number }[] | undefined,
): EquityChart | null {
  if (!points?.length) {
    return null;
  }
  const values = [0, ...points.map((p) => p.equity)];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number): number =>
    PAD + ((W - 2 * PAD) * i) / Math.max(points.length, 1);
  const y = (v: number): number => PAD + ((H - 2 * PAD) * (max - v)) / span;
  return {
    width: W,
    height: H,
    zero: y(0),
    min,
    max,
    points: [0, ...points.map((p) => p.equity)]
      .map((v, i) => `${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
      .join(' '),
  };
}

export const STATUS_TITLES: Record<string, string> = {
  queued: 'в очереди',
  running: 'выполняется',
  succeeded: 'готово',
  failed: 'ошибка',
};

/** Прогоны движков эксперимента (`versions.engines`): имя → id прогона. */
export function engineRuns(backtest: Backtest | null): Record<string, number> {
  const engines = (
    backtest?.versions as {
      engines?: Record<string, { run_id: number }>;
    } | null
  )?.engines;
  return Object.fromEntries(
    Object.entries(engines ?? {}).map(([name, info]) => [name, info.run_id]),
  );
}

/** Движок вхождения из ключа сделки: `движок:id` или `движок:id:seq`. */
export const engineOf = (ref: string | null | undefined): string | null =>
  ref ? (ref.split(':')[0] ?? null) : null;

export interface NearestLevel {
  level: LevelInfo;
  /** Расстояние до цены сигнала, % от цены. */
  distancePct: number;
  /** Цена сигнала выше (+) или ниже (−) уровня. */
  above: boolean;
}

/** Ближайший к цене сигнала уровень (в шкале continuous); `null` без уровней. */
export function nearestLevel(
  levels: LevelInfo[],
  price: number | null | undefined,
): NearestLevel | null {
  if (price === null || price === undefined || !levels.length || price === 0) {
    return null;
  }
  const best = levels.reduce((a, b) =>
    Math.abs(b.price - price) < Math.abs(a.price - price) ? b : a,
  );
  return {
    level: best,
    distancePct: (Math.abs(best.price - price) / Math.abs(price)) * 100,
    above: price >= best.price,
  };
}

const MSK_OFFSET_MS = 3 * 3600_000;

/** Дата (МСК, `YYYY-MM-DD`) момента ``iso`` — ключ перехода в Replay. */
export function mskDate(iso: string): string {
  return new Date(new Date(iso).getTime() + MSK_OFFSET_MS)
    .toISOString()
    .slice(0, 10);
}

export interface ReplayLink {
  path: string[];
  queryParams: { root: number; tf: string; date: string };
}

/** Переход к графику: Replay с началом в день входа (будущее скрыто). */
export function replayLink(
  backtest: Backtest,
  trade: BacktestTrade,
): ReplayLink {
  return {
    path: ['/replay'],
    queryParams: {
      root: backtest.root_id,
      tf: backtest.timeframe_code,
      date: mskDate(trade.entry_time),
    },
  };
}

export interface LegRow {
  contract: number;
  entry: string;
  exit: string;
  entryPrice: string;
  exitPrice: string;
  reason: string;
  ticks: string;
}

/** Ноги сделки: на ролле их несколько, у каждой свой контракт. */
export function legRows(trade: BacktestTrade): LegRow[] {
  return trade.legs.map((leg) => ({
    contract: Number(leg['contract_id']),
    entry: stamp(String(leg['entry_time'])),
    exit: stamp(String(leg['exit_time'])),
    entryPrice: num(leg['entry_price'] as number | undefined, 4),
    exitPrice: num(leg['exit_price'] as number | undefined, 4),
    reason: REASONS[String(leg['reason'])] ?? String(leg['reason']),
    ticks: num(leg['gross_ticks'] as number | undefined),
  }));
}
