import { TIMEFRAMES } from './chart-data';
import {
  DEFAULT_LEVEL_FILTER,
  LAYERS,
  type LayerKey,
  type LevelFilter,
} from './structure';

/** Что запоминается между визитами: инструмент, TF, слои и параметры расчёта. */
export interface ChartView {
  instrumentId: number | null;
  timeframe: string;
  layers: Partial<Record<LayerKey, boolean>>;
  levelFilter: Omit<LevelFilter, 'reference'>;
  lastBars: number;
  higherTimeframe: string | null;
  logScale: boolean;
}

/** Параметры фильтра уровней без опорной цены (она считается по свечам заново). */
export function persistedFilter(filter: LevelFilter): ChartView['levelFilter'] {
  return {
    maxDistanceAtr: filter.maxDistanceAtr,
    minScore: filter.minScore,
    minTouches: filter.minTouches,
    perSide: filter.perSide,
    derived: filter.derived,
  };
}

const KEY = 'trader.chart.view.v1';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const number = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) ? value : undefined;

/** Сохранённый вид; повреждённые и неизвестные поля отбрасываются. */
export function parseView(raw: string | null): Partial<ChartView> {
  if (!raw) {
    return {};
  }
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!isRecord(data)) {
    return {};
  }
  const view: Partial<ChartView> = {};
  const instrumentId = number(data['instrumentId']);
  if (instrumentId !== undefined) {
    view.instrumentId = instrumentId;
  }
  if (
    typeof data['timeframe'] === 'string' &&
    (TIMEFRAMES as readonly string[]).includes(data['timeframe'])
  ) {
    view.timeframe = data['timeframe'];
  }
  const layers = data['layers'];
  if (isRecord(layers)) {
    view.layers = Object.fromEntries(
      LAYERS.flatMap((key) =>
        typeof layers[key] === 'boolean' ? [[key, layers[key]]] : [],
      ),
    );
  }
  if (isRecord(data['levelFilter'])) {
    const f = data['levelFilter'];
    const base = DEFAULT_LEVEL_FILTER;
    view.levelFilter = {
      maxDistanceAtr:
        f['maxDistanceAtr'] === null
          ? null
          : (number(f['maxDistanceAtr']) ?? base.maxDistanceAtr),
      minScore: number(f['minScore']) ?? base.minScore,
      minTouches: number(f['minTouches']) ?? base.minTouches,
      perSide: number(f['perSide']) ?? base.perSide,
      derived: typeof f['derived'] === 'boolean' ? f['derived'] : base.derived,
    };
  }
  const lastBars = number(data['lastBars']);
  if (lastBars !== undefined && lastBars >= 0) {
    view.lastBars = Math.floor(lastBars);
  }
  if (
    typeof data['higherTimeframe'] === 'string' &&
    (TIMEFRAMES as readonly string[]).includes(data['higherTimeframe'])
  ) {
    view.higherTimeframe = data['higherTimeframe'];
  }
  if (typeof data['logScale'] === 'boolean') {
    view.logScale = data['logScale'];
  }
  return view;
}

export function loadView(): Partial<ChartView> {
  try {
    return parseView(localStorage.getItem(KEY));
  } catch {
    return {};
  }
}

export function saveView(view: ChartView): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(view));
  } catch {
    // хранилище недоступно (приватный режим): вид просто не запоминается
  }
}
