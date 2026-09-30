import { computed, inject, Injectable, signal } from '@angular/core';
import { lastValueFrom } from 'rxjs';
import type { IndicatorInfo, IndicatorValues, Job } from '@trader/api-client';
import { ApiError, ApiService } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import {
  type ActiveIndicator,
  allowedSourceTimeframes,
  type ChartIndicator,
  type VerifyResult,
  toChartIndicator,
  type WarmupHint,
  warmupHint,
} from './indicators';

/** Что запрашивать: серия, chart TF, диапазон и (для replay) момент знания. */
export interface IndicatorContext {
  root_id?: number;
  contract_id?: number;
  chartTimeframe: string;
  start?: string;
  end?: string;
  asOf?: string;
}

const MAX_BARS = 20000;

/** Индикаторы на графике: каталог, активные, значения и подсказки о прогреве. */
@Injectable()
export class IndicatorsStore {
  private readonly api = inject(ApiService);
  private readonly jobs = inject(JobsService);

  readonly catalog = signal<IndicatorInfo[]>([]);
  readonly active = signal<ActiveIndicator[]>([]);
  readonly values = signal<Record<string, IndicatorValues>>({});
  readonly errors = signal<Record<string, string>>({});
  readonly loading = signal(false);
  // Проверка online replay (Verify).
  readonly verifyJob = signal<Job | null>(null);
  readonly verifyResult = signal<VerifyResult | null>(null);
  readonly verifyError = signal<string | null>(null);

  private context: IndicatorContext | null = null;
  private counter = 0;
  private sequence = 0;

  readonly series = computed<ChartIndicator[]>(() => {
    const values = this.values();
    return this.active().flatMap((indicator, index) => {
      const result = values[indicator.id];
      return result ? [toChartIndicator(indicator, result, index)] : [];
    });
  });

  readonly hints = computed<WarmupHint[]>(() => {
    const values = this.values();
    return this.active().flatMap((indicator) => {
      const result = values[indicator.id];
      const hint = result ? warmupHint(indicator, result) : null;
      return hint ? [hint] : [];
    });
  });

  /**
   * Запускает `verify.indicators` для текущего диапазона графика (в replay — до
   * момента знания): индикаторы пересчитываются «с нуля» на префиксах и
   * сравниваются с batch. Без активных индикаторов проверяются все из каталога.
   */
  async verify(): Promise<void> {
    const context = this.context;
    if (!context) {
      return;
    }
    this.verifyResult.set(null);
    this.verifyError.set(null);
    const active = this.active();
    const job = await this.api.call(
      this.api.client.POST('/jobs', {
        body: {
          type: 'verify.indicators',
          params: {
            ...(context.contract_id !== undefined
              ? { contract_id: context.contract_id }
              : { root_id: context.root_id }),
            timeframe: context.chartTimeframe,
            start: context.start,
            end: context.asOf ?? context.end,
            ...(active.length
              ? {
                  indicators: active.map((i) => ({
                    name: i.name,
                    params: i.params,
                  })),
                }
              : {}),
          },
        },
      }),
    );
    this.verifyJob.set(job);
    let last: Job = job;
    try {
      last = await lastValueFrom(this.jobs.watch(job.id), {
        defaultValue: job,
      });
    } catch (error) {
      this.verifyError.set((error as Error).message);
      return;
    }
    this.verifyJob.set(last);
    if (last.status !== 'succeeded') {
      this.verifyError.set(
        last.error ?? `Проверка завершилась: ${last.status}`,
      );
      return;
    }
    this.verifyResult.set(last.result as unknown as VerifyResult);
  }

  async loadCatalog(): Promise<void> {
    this.catalog.set(await this.api.call(this.api.client.GET('/indicators')));
  }

  /** Добавляет индикатор и, если контекст известен, сразу считает его. */
  async add(
    info: IndicatorInfo,
    params: Record<string, unknown>,
    sourceTimeframe: string,
  ): Promise<void> {
    this.active.update((list) => [
      ...list,
      {
        id: `ind-${++this.counter}`,
        name: info.name,
        title: info.title,
        params,
        sourceTimeframe,
      },
    ]);
    await this.refresh();
  }

  remove(id: string): void {
    this.active.update((list) => list.filter((i) => i.id !== id));
    this.values.update(({ [id]: _removed, ...rest }) => rest);
    this.errors.update(({ [id]: _removed, ...rest }) => rest);
  }

  /** Заменяет набор индикаторов (загрузка профиля). */
  async replaceAll(
    items: Omit<ActiveIndicator, 'id'>[],
    catalogTitles?: Record<string, string>,
  ): Promise<void> {
    this.active.set(
      items.map((item) => ({
        ...item,
        title: catalogTitles?.[item.name] ?? item.title,
        id: `ind-${++this.counter}`,
      })),
    );
    this.values.set({});
    this.errors.set({});
    await this.refresh();
  }

  /** Пересчитывает все активные индикаторы для контекста (или прежнего). */
  async refresh(context?: IndicatorContext): Promise<void> {
    if (context) {
      this.context = context;
    }
    const current = this.context;
    if (!current || !this.active().length) {
      return;
    }
    // Source TF не может стать младше chart TF после смены графика.
    const allowed = allowedSourceTimeframes(current.chartTimeframe);
    this.active.update((list) =>
      list.map((i) =>
        allowed.includes(i.sourceTimeframe)
          ? i
          : { ...i, sourceTimeframe: current.chartTimeframe },
      ),
    );

    const seq = ++this.sequence;
    this.loading.set(true);
    const results = await Promise.all(
      this.active().map(async (indicator) => {
        try {
          const data = await this.api.call(
            this.api.client.GET('/indicator-values', {
              params: {
                query: {
                  indicator: indicator.name,
                  chart_timeframe: current.chartTimeframe,
                  source_timeframe: indicator.sourceTimeframe,
                  params: JSON.stringify(indicator.params),
                  root_id: current.root_id,
                  contract_id: current.contract_id,
                  start: current.start,
                  end: current.end,
                  as_of: current.asOf,
                  limit: MAX_BARS,
                },
              },
            }),
          );
          return { id: indicator.id, data, error: null };
        } catch (error) {
          const message =
            error instanceof ApiError ? error.message : String(error);
          return { id: indicator.id, data: null, error: message };
        }
      }),
    );
    if (seq !== this.sequence) {
      return; // пришёл ответ на устаревший запрос
    }
    const values: Record<string, IndicatorValues> = {};
    const errors: Record<string, string> = {};
    for (const result of results) {
      if (result.data) {
        values[result.id] = result.data;
      } else if (result.error) {
        errors[result.id] = result.error;
      }
    }
    this.values.set(values);
    this.errors.set(errors);
    this.loading.set(false);
  }
}
