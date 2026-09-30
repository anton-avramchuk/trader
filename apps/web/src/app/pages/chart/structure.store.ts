import { computed, inject, Injectable, signal } from '@angular/core';
import { lastValueFrom } from 'rxjs';
import type { EngineEvent, FibGrid, Job, LevelZone } from '@trader/api-client';
import { ApiError, ApiService } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import {
  currentTrend,
  emptyLayers,
  EMPTY_OVERLAY,
  fibonacciSegments,
  LAYER_ENGINES,
  LAYERS,
  type LayerKey,
  type LayerState,
  chartLevels,
  levelInfos,
  levelSegments,
  manualFibSegments,
  type Overlay,
  type OverlayMarker,
  pivotSegments,
  structureMarkers,
  swingMarkers,
  PATTERN_ENGINES,
  zigzagSegments,
  zoneRects,
} from './structure';
import { toChartTime } from './chart-data';
import {
  chartPatterns,
  type PatternInfo,
  patternInfos,
  patternMarkers,
  patternSegments,
} from './pattern-layer';

/** Что рисовать: серия, chart TF и (для replay) момент знания. */
export interface StructureContext {
  root_id?: number;
  contract_id?: number;
  chartTimeframe: string;
  asOf?: string;
}

export interface FibPoint {
  time: number;
  price: number;
}

/**
 * Слои структуры: движки запускаются задачей `engine.run` (один раз на серию и TF),
 * события читаются «as-of» — в replay будущее не попадает на график.
 */
@Injectable()
export class StructureStore {
  private readonly api = inject(ApiService);
  private readonly jobs = inject(JobsService);

  readonly layers = signal<LayerState>(emptyLayers());
  /** Дополнительные source TF для confluence-зон (не младше chart TF). */
  readonly zoneSources = signal<string[]>([]);
  readonly events = signal<Record<string, EngineEvent[]>>({});
  readonly zones = signal<LevelZone[]>([]);
  readonly manual = signal<FibGrid[]>([]);
  readonly selectedLevel = signal<number | null>(null);
  /** Ключ выбранного паттерна (`движок:id`) и показ отменённых. */
  readonly selectedPattern = signal<string | null>(null);
  readonly showCancelled = signal(false);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  /** Режим рисования ручной сетки: первая и вторая точки. */
  readonly manualMode = signal(false);
  readonly pending = signal<FibPoint | null>(null);

  private context: StructureContext | null = null;
  private runs = new Map<string, number>();
  private sequence = 0;

  /** Сильнейшие активные уровни — они же на графике и в списке. */
  readonly levels = computed(() =>
    chartLevels(levelInfos(this.events()['levels'] ?? [])),
  );
  readonly trend = computed(() =>
    currentTrend(this.events()['market_structure'] ?? []),
  );
  readonly selected = computed(
    () => this.levels().find((l) => l.id === this.selectedLevel()) ?? null,
  );

  /** Все вхождения паттернов (последнее состояние каждой цепочки). */
  readonly patterns = computed<PatternInfo[]>(() =>
    PATTERN_ENGINES.flatMap((engine) =>
      patternInfos(engine, this.events()[engine] ?? []),
    ),
  );
  /** Вхождения на графике и в списке: новые, без отменённых (если не просили). */
  readonly shownPatterns = computed(() =>
    chartPatterns(
      this.patterns(),
      this.showCancelled(),
      this.selectedPattern(),
    ),
  );
  readonly selectedPatternInfo = computed(
    () => this.patterns().find((p) => p.key === this.selectedPattern()) ?? null,
  );

  readonly overlay = computed<Overlay>(() => {
    const layers = this.layers();
    const events = this.events();
    if (!LAYERS.some((key) => layers[key])) {
      return EMPTY_OVERLAY;
    }
    const zigzag = events['zigzag'] ?? [];
    const markers: OverlayMarker[] = [
      ...(layers.swings ? swingMarkers(zigzag) : []),
      ...(layers.structure
        ? structureMarkers(events['market_structure'] ?? [])
        : []),
      ...(layers.patterns ? patternMarkers(this.shownPatterns()) : []),
    ].sort((a, b) => a.time - b.time);
    return {
      markers,
      segments: [
        ...(layers.zigzag ? zigzagSegments(zigzag) : []),
        ...(layers.levels
          ? levelSegments(this.levels(), this.selectedLevel())
          : []),
        ...(layers.pivot ? pivotSegments(events['pivot'] ?? []) : []),
        ...(layers.patterns
          ? patternSegments(this.shownPatterns(), this.selectedPattern())
          : []),
        ...(layers.fibonacci
          ? [
              ...fibonacciSegments(events['fibonacci'] ?? []),
              ...manualFibSegments(this.visibleManual()),
            ]
          : []),
      ],
      zones: layers.zones ? zoneRects(this.zones()) : [],
    };
  });

  /** Ручные сетки, нарисованные к моменту `as_of` (в replay «будущих» не видно). */
  private visibleManual(): FibGrid[] {
    const asOf = this.context?.asOf;
    if (!asOf) {
      return this.manual();
    }
    const limit = toChartTime(asOf);
    return this.manual().filter((g) => toChartTime(g.end.time) <= limit);
  }

  setLayer(layer: LayerKey, enabled: boolean): Promise<void> {
    this.layers.update((state) => ({ ...state, [layer]: enabled }));
    return this.refresh();
  }

  /** Загрузка набора слоёв (профиль графика); неизвестные ключи пропускаются. */
  setLayers(enabled: Partial<Record<LayerKey, boolean>>): Promise<void> {
    this.layers.set({ ...emptyLayers(), ...enabled });
    return this.refresh();
  }

  setZoneSources(timeframes: string[]): Promise<void> {
    this.zoneSources.set(timeframes);
    return this.refresh();
  }

  selectPattern(key: string | null): void {
    this.selectedPattern.set(key === this.selectedPattern() ? null : key);
  }

  setShowCancelled(show: boolean): void {
    this.showCancelled.set(show);
  }

  selectLevel(id: number | null): void {
    this.selectedLevel.set(id === this.selectedLevel() ? null : id);
  }

  /** Сбросить кэш прогонов: следующее обновление пересчитает движки (появились новые бары). */
  recompute(): Promise<void> {
    this.runs.clear();
    return this.refresh();
  }

  private seriesQuery(): { root_id?: number; contract_id?: number } {
    const context = this.context;
    if (!context) {
      return {};
    }
    return context.contract_id !== undefined
      ? { contract_id: context.contract_id }
      : { root_id: context.root_id };
  }

  private seriesKey(): string {
    const q = this.seriesQuery();
    return q.contract_id !== undefined ? `c${q.contract_id}` : `r${q.root_id}`;
  }

  /** Обновляет данные включённых слоёв для контекста (или прежнего). */
  async refresh(context?: StructureContext): Promise<void> {
    if (context) {
      this.context = context;
    }
    const current = this.context;
    if (!current) {
      return;
    }
    const layers = this.layers();
    const engines = new Set(
      LAYERS.filter((key) => layers[key]).flatMap((key) => LAYER_ENGINES[key]),
    );
    const seq = ++this.sequence;
    if (!engines.size && !layers.fibonacci) {
      this.events.set({});
      this.zones.set([]);
      this.error.set(null);
      return;
    }
    this.loading.set(true);
    try {
      const events: Record<string, EngineEvent[]> = {};
      await Promise.all(
        [...engines].map(async (engine) => {
          const runId = await this.ensureRun(engine, current.chartTimeframe);
          events[engine] = await this.loadEvents(
            runId,
            current.asOf,
            PATTERN_ENGINES.includes(engine) ? 'history' : 'current',
          );
        }),
      );
      const zones = layers.zones ? await this.loadZones(current) : [];
      const manual = layers.fibonacci ? await this.loadManual(current) : [];
      if (seq !== this.sequence) {
        return; // пришёл ответ на устаревший запрос
      }
      this.events.set(events);
      this.zones.set(zones);
      this.manual.set(manual);
      this.error.set(null);
    } catch (error) {
      if (seq === this.sequence) {
        this.error.set(
          error instanceof ApiError || error instanceof Error
            ? error.message
            : String(error),
        );
      }
    } finally {
      if (seq === this.sequence) {
        this.loading.set(false);
      }
    }
  }

  /** Запускает `engine.run` (если ещё не запускали для серии и TF) и возвращает id прогона. */
  private async ensureRun(engine: string, timeframe: string): Promise<number> {
    const key = `${this.seriesKey()}:${engine}:${timeframe}`;
    const known = this.runs.get(key);
    if (known !== undefined) {
      return known;
    }
    const job = await this.api.call(
      this.api.client.POST('/jobs', {
        body: {
          type: 'engine.run',
          params: { engine, timeframe, ...this.seriesQuery() },
        },
      }),
    );
    const last: Job = await lastValueFrom(this.jobs.watch(job.id), {
      defaultValue: job,
    });
    if (last.status !== 'succeeded') {
      throw new Error(
        last.error ?? `Прогон ${engine} не выполнен: ${last.status}`,
      );
    }
    const runId = Number((last.result as { run_id: number }).run_id);
    this.runs.set(key, runId);
    return runId;
  }

  private loadEvents(
    runId: number,
    asOf: string | undefined,
    view: 'history' | 'current',
  ): Promise<EngineEvent[]> {
    return this.api.call(
      this.api.client.GET('/engine-runs/{run_id}/events', {
        params: {
          path: { run_id: runId },
          query: { view, as_of: asOf },
        },
      }),
    );
  }

  private async loadZones(current: StructureContext): Promise<LevelZone[]> {
    const timeframes = [
      ...new Set([current.chartTimeframe, ...this.zoneSources()]),
    ];
    await Promise.all(timeframes.map((tf) => this.ensureRun('levels', tf)));
    const result = await this.api.call(
      this.api.client.GET('/level-zones', {
        params: {
          query: {
            ...this.seriesQuery(),
            chart_timeframe: current.chartTimeframe,
            source_timeframes: timeframes,
            as_of: current.asOf,
          },
        },
      }),
    );
    return result.zones;
  }

  private loadManual(current: StructureContext): Promise<FibGrid[]> {
    return this.api.call(
      this.api.client.GET('/fib-grids', {
        params: {
          query: { ...this.seriesQuery(), timeframe: current.chartTimeframe },
        },
      }),
    );
  }

  // --- ручная сетка Fibonacci ---------------------------------------------

  toggleManual(): void {
    this.manualMode.update((on) => !on);
    this.pending.set(null);
  }

  /** Точка, выбранная кликом по графику: вторая точка сохраняет сетку. */
  async pickPoint(point: FibPoint): Promise<void> {
    const current = this.context;
    if (!this.manualMode() || !current) {
      return;
    }
    const first = this.pending();
    if (!first) {
      this.pending.set(point);
      return;
    }
    this.pending.set(null);
    this.manualMode.set(false);
    try {
      await this.api.call(
        this.api.client.POST('/fib-grids', {
          body: {
            ...this.seriesQuery(),
            timeframe: current.chartTimeframe,
            start: { time: iso(first.time), price: first.price },
            end: { time: iso(point.time), price: point.price },
          },
        }),
      );
      this.manual.set(await this.loadManual(current));
      this.error.set(null);
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : String(error));
    }
  }

  async removeManual(id: number): Promise<void> {
    const current = this.context;
    await this.api.call(
      this.api.client.DELETE('/fib-grids/{grid_id}', {
        params: { path: { grid_id: id } },
      }),
    );
    if (current) {
      this.manual.set(await this.loadManual(current));
    }
  }
}

const iso = (seconds: number): string => new Date(seconds * 1000).toISOString();
