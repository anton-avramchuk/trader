import { computed, inject, Injectable, signal } from '@angular/core';
import type { ChartProfile, ProfileConfig } from '@trader/api-client';
import { ApiService } from '../../core/api/api';
import { IndicatorsStore } from './indicators.store';

export type ProfileScope = 'global' | 'root';

/** Профили графика: список для root, применение, сохранение, переименование. */
@Injectable()
export class ProfilesStore {
  private readonly api = inject(ApiService);
  private readonly indicators = inject(IndicatorsStore);

  readonly profiles = signal<ChartProfile[]>([]);
  readonly selectedId = signal<number | null>(null);
  readonly busy = signal(false);

  private rootId: number | null = null;
  private autoApplied = false;

  readonly selected = computed(
    () => this.profiles().find((p) => p.id === this.selectedId()) ?? null,
  );

  /** Профили root (и глобальные); при первой загрузке применяет последний использованный. */
  async load(
    rootId: number | null,
    onTimeframe?: (timeframe: string) => void,
  ): Promise<void> {
    if (rootId !== this.rootId) {
      this.rootId = rootId;
      this.autoApplied = false;
    }
    this.profiles.set(
      await this.api.call(
        this.api.client.GET('/chart-profiles', {
          params: { query: rootId === null ? {} : { root_id: rootId } },
        }),
      ),
    );
    // Выбранный профиль остаётся, пока он виден для этого root (глобальные видны всегда).
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
    await this.api.call(
      this.api.client.POST('/chart-profiles/{profile_id}/use', {
        params: { path: { profile_id: profileId } },
      }),
    );
  }

  /** Конфигурация из текущего набора индикаторов графика. */
  currentConfig(chartTimeframe: string): ProfileConfig {
    return {
      chart_timeframe: chartTimeframe,
      indicators: this.indicators.active().map((i) => ({
        name: i.name,
        params: i.params,
        source_timeframe: i.sourceTimeframe,
      })),
      layers: {},
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
          root_id: scope === 'root' ? this.rootId : null,
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
            query: this.rootId === null ? {} : { root_id: this.rootId },
          },
        }),
      ),
    );
  }
}
