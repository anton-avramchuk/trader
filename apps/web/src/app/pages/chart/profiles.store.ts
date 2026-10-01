import { computed, inject, Injectable, signal } from '@angular/core';
import type { ChartProfile, ProfileConfig } from '@trader/api-client';
import { ApiService } from '../../core/api/api';
import { IndicatorsStore } from './indicators.store';
import { LAYERS, profileKey } from './structure';
import { StructureStore } from './structure.store';

export type ProfileScope = 'global' | 'instrument';

/** Профили графика: список для инструмента, применение, сохранение, переименование. */
@Injectable()
export class ProfilesStore {
  private readonly api = inject(ApiService);
  private readonly indicators = inject(IndicatorsStore);
  private readonly structure = inject(StructureStore, { optional: true });

  readonly profiles = signal<ChartProfile[]>([]);
  readonly selectedId = signal<number | null>(null);
  readonly busy = signal(false);

  private instrumentId: number | null = null;
  private autoApplied = false;

  readonly selected = computed(
    () => this.profiles().find((p) => p.id === this.selectedId()) ?? null,
  );

  /** Профили инструмента (и глобальные); при первой загрузке применяет последний использованный. */
  async load(
    instrumentId: number | null,
    onTimeframe?: (timeframe: string) => void,
  ): Promise<void> {
    if (instrumentId !== this.instrumentId) {
      this.instrumentId = instrumentId;
      this.autoApplied = false;
    }
    this.profiles.set(
      await this.api.call(
        this.api.client.GET('/chart-profiles', {
          params: {
            query: instrumentId === null ? {} : { instrument_id: instrumentId },
          },
        }),
      ),
    );
    // Выбранный профиль остаётся, пока он виден для этого инструмента (глобальные видны всегда).
    if (!this.profiles().some((p) => p.id === this.selectedId())) {
      this.selectedId.set(null);
    }
    if (!this.autoApplied) {
      this.autoApplied = true;
      const last = this.profiles().find((p) => p.last_used_at);
      if (last && !this.indicators.active().length) {
        await this.apply(last.id, onTimeframe);
      }
    }
  }

  /** Применяет профиль: набор индикаторов (и TF графика — через колбэк) и запоминает как последний. */
  async apply(
    profileId: number,
    onTimeframe?: (timeframe: string) => void,
  ): Promise<void> {
    const profile = this.profiles().find((p) => p.id === profileId);
    if (!profile) {
      return;
    }
    this.selectedId.set(profileId);
    const timeframe = profile.config.chart_timeframe;
    if (timeframe && onTimeframe) {
      onTimeframe(timeframe);
    }
    const titles = Object.fromEntries(
      this.indicators.catalog().map((i) => [i.name, i.title]),
    );
    await this.indicators.replaceAll(
      (profile.config.indicators ?? []).map((item) => ({
        name: item.name,
        title: titles[item.name] ?? item.name,
        params: item.params ?? {},
        sourceTimeframe: item.source_timeframe,
      })),
      titles,
    );
    const saved = profile.config.layers ?? {};
    if (this.structure && LAYERS.some((layer) => profileKey(layer) in saved)) {
      await this.structure.setLayers(
        Object.fromEntries(
          LAYERS.map((layer) => [layer, saved[profileKey(layer)] === true]),
        ),
      );
    }
    await this.api.call(
      this.api.client.POST('/chart-profiles/{profile_id}/use', {
        params: { path: { profile_id: profileId } },
      }),
    );
  }

  /** Конфигурация из текущего набора индикаторов графика. */
  currentConfig(chartTimeframe: string): ProfileConfig {
    const structure = this.structure;
    return {
      chart_timeframe: chartTimeframe,
      indicators: this.indicators.active().map((i) => ({
        name: i.name,
        params: i.params,
        source_timeframe: i.sourceTimeframe,
      })),
      layers: structure
        ? Object.fromEntries(
            LAYERS.map((layer) => [
              profileKey(layer),
              structure.layers()[layer],
            ]),
          )
        : {},
      style: {},
    };
  }

  async saveNew(
    name: string,
    scope: ProfileScope,
    chartTimeframe: string,
  ): Promise<void> {
    const profile = await this.api.call(
      this.api.client.POST('/chart-profiles', {
        body: {
          name: name.trim(),
          instrument_id: scope === 'instrument' ? this.instrumentId : null,
          config: this.currentConfig(chartTimeframe),
        },
      }),
    );
    await this.reload();
    this.selectedId.set(profile.id);
    await this.markUsed(profile.id);
  }

  async saveCurrent(chartTimeframe: string): Promise<void> {
    const id = this.selectedId();
    if (id === null) {
      return;
    }
    await this.api.call(
      this.api.client.PATCH('/chart-profiles/{profile_id}', {
        params: { path: { profile_id: id } },
        body: { config: this.currentConfig(chartTimeframe) },
      }),
    );
    await this.reload();
  }

  async rename(name: string): Promise<void> {
    const id = this.selectedId();
    if (id === null || !name.trim()) {
      return;
    }
    await this.api.call(
      this.api.client.PATCH('/chart-profiles/{profile_id}', {
        params: { path: { profile_id: id } },
        body: { name: name.trim() },
      }),
    );
    await this.reload();
  }

  async remove(): Promise<void> {
    const id = this.selectedId();
    if (id === null) {
      return;
    }
    await this.api.call(
      this.api.client.DELETE('/chart-profiles/{profile_id}', {
        params: { path: { profile_id: id } },
      }),
    );
    this.selectedId.set(null);
    await this.reload();
  }

  private async markUsed(profileId: number): Promise<void> {
    await this.api.call(
      this.api.client.POST('/chart-profiles/{profile_id}/use', {
        params: { path: { profile_id: profileId } },
      }),
    );
  }

  private async reload(): Promise<void> {
    this.profiles.set(
      await this.api.call(
        this.api.client.GET('/chart-profiles', {
          params: {
            query:
              this.instrumentId === null
                ? {}
                : { instrument_id: this.instrumentId },
          },
        }),
      ),
    );
  }
}
