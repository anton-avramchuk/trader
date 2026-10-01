import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton } from '@taiga-ui/core';
import { MskPipe } from '../../core/time/msk';
import { allowedSourceTimeframes } from './indicators';
import { PATTERN_TITLES, type PatternInfo, shortName } from './pattern-layer';
import { PatternGallery } from './pattern-gallery';
import { AnaloguesBlock } from './analogues-block';
import { StatsBlock } from './stats-block';
import { LAYER_TITLES, LAYERS } from './structure';
import { StructureStore } from './structure.store';

const TREND_TITLES: Record<string, string> = {
  uptrend: 'восходящий',
  downtrend: 'нисходящий',
  range: 'боковик',
};
const COMPONENT_TITLES: Record<string, string> = {
  touches: 'касания',
  source: 'важность источника',
  rejection: 'отбой, ATR',
  confluence: 'независимые источники рядом',
  age: 'возраст, баров',
  precision: 'точность',
  symmetry: 'симметрия',
  height: 'высота',
  duration: 'ширина',
};
const LEVELS_SHOWN = 8;
const REASONS: Record<string, string> = {
  broken: 'слом геометрии',
  expired: 'истёк срок',
  false_breakout: 'ложный пробой',
};

/** Слои структуры и уровней: переключатели, зоны по TF, детали уровня, ручная Fibonacci. */
@Component({
  selector: 'app-structure-panel',
  imports: [
    DecimalPipe,
    FormsModule,
    MskPipe,
    PatternGallery,
    AnaloguesBlock,
    StatsBlock,
    TuiButton,
  ],
  template: `
    <div class="layers" role="group" aria-label="Слои структуры">
      @for (layer of layers; track layer) {
        <label class="check">
          <input
            type="checkbox"
            [ngModel]="store.layers()[layer]"
            (ngModelChange)="store.setLayer(layer, $event)"
          />
          {{ titles[layer] }}
        </label>
      }
      <button
        tuiButton
        type="button"
        size="xs"
        appearance="flat"
        [disabled]="store.loading()"
        (click)="store.recompute()"
      >
        Пересчитать
      </button>
      @if (store.loading()) {
        <span class="hint">Расчёт…</span>
      }
    </div>

    @if (store.layers().zones && sources().length) {
      <div class="row" role="group" aria-label="Source TF зон">
        <span class="hint">Уровни старших TF в зонах:</span>
        @for (tf of sources(); track tf) {
          <label class="check">
            <input
              type="checkbox"
              [ngModel]="store.zoneSources().includes(tf)"
              (ngModelChange)="toggleSource(tf, $event)"
            />
            {{ tf }}
          </label>
        }
      </div>
    }

    @if (store.error(); as error) {
      <p class="error" role="alert">{{ error }}</p>
    }

    @if (store.layers().structure && store.trend(); as trend) {
      <p class="hint">Состояние тренда: {{ trendTitles[trend] }}</p>
    }

    @if (store.layers().levels && store.levels().length) {
      <div class="levels" role="list" aria-label="Уровни">
        @for (level of shownLevels(); track level.id) {
          <button
            type="button"
            role="listitem"
            class="level"
            [class.selected]="level.id === store.selectedLevel()"
            [class.broken]="level.state === 'broken'"
            (click)="store.selectLevel(level.id)"
          >
            {{ level.price | number: '1.0-6' }} · {{ level.source }} ·
            {{ level.score }}
          </button>
        }
      </div>
    }
    @if (store.selected(); as level) {
      <div class="details" role="region" aria-label="Детали уровня">
        <strong>{{ level.price | number: '1.0-6' }}</strong>
        — {{ level.role === 'resistance' ? 'сопротивление' : 'поддержка' }},
        {{ level.state === 'active' ? 'активен' : 'пробит' }}; источник
        {{ level.source }}; касаний {{ level.touches }}; появился
        {{ level.createdAt | msk: 'datetime' }} МСК.
        <div>
          Сила v{{ level.version }}: <strong>{{ level.score }}</strong> из 100
        </div>
        <ul>
          @for (item of components(level.components); track item.name) {
            <li>{{ item.title }}: {{ item.value }}</li>
          }
        </ul>
      </div>
    }

    @if (store.layers().patterns) {
      <div class="row">
        <label class="check">
          <input
            type="checkbox"
            [ngModel]="store.showCancelled()"
            (ngModelChange)="store.setShowCancelled($event)"
          />
          Показывать отменённые
        </label>
        @if (!store.shownPatterns().length) {
          <span class="hint">Паттернов на этом участке нет</span>
        }
      </div>
      <div class="levels" role="list" aria-label="Паттерны">
        @for (info of store.shownPatterns(); track info.key) {
          <button
            type="button"
            role="listitem"
            class="level"
            [class.selected]="info.key === store.selectedPattern()"
            [class.broken]="info.state === 'invalidated'"
            (click)="store.selectPattern(info.key)"
          >
            {{ name(info) }} {{ info.direction === 'bullish' ? '↑' : '↓' }} ·
            {{ stateTitle(info) }} · {{ info.score }}
          </button>
        }
      </div>
    }
    @if (store.layers().patterns && store.selectedPatternInfo(); as info) {
      <div class="details" role="region" aria-label="Детали паттерна">
        <strong>{{ name(info) }}</strong>
        ({{ short(info) }}) —
        {{ info.direction === 'bullish' ? 'бычий' : 'медвежий' }},
        {{ stateTitle(info) }}; замечен
        {{ info.detectedAt | msk: 'datetime' }} МСК.
        @if (info.target !== null) {
          <div>
            Цель: <strong>{{ info.target | number: '1.0-6' }}</strong
            >, высота {{ info.height | number: '1.0-6' }}
          </div>
        }
        <div>
          Качество v1: <strong>{{ info.score }}</strong> из 100
        </div>
        <ul>
          @for (item of components(info.components); track item.name) {
            <li>{{ item.title }}: {{ item.value }}</li>
          }
          @for (point of info.points; track point.index) {
            <li>{{ point.role }}: {{ point.price | number: '1.0-6' }}</li>
          }
        </ul>
      </div>
    }
    @if (store.statsViews().length) {
      <app-stats-block
        [views]="store.statsViews()"
        [unit]="store.statsUnit()"
        (unitChange)="store.setStatsUnit($event)"
      />
    }
    @if (store.layers().patterns || store.layers().levels) {
      <app-analogues-block
        [view]="store.analogues()"
        [unit]="store.statsUnit()"
        [canPattern]="!!store.selectedPatternInfo()"
        (lookup)="store.findAnalogues($event)"
      />
    }
    @if (store.layers().patterns) {
      <app-pattern-gallery />
    }

    @if (store.layers().fibonacci) {
      <div class="row">
        <button
          tuiButton
          type="button"
          size="xs"
          [appearance]="store.manualMode() ? 'primary' : 'secondary'"
          (click)="store.toggleManual()"
        >
          Ручная Fibonacci
        </button>
        @if (store.manualMode()) {
          <span class="hint">
            {{
              store.pending()
                ? 'Кликните вторую точку на графике'
                : 'Кликните первую точку на графике'
            }}
          </span>
        }
        @for (grid of store.manual(); track grid.id) {
          <span class="chip">
            {{ grid.start.price | number: '1.0-6' }} →
            {{ grid.end.price | number: '1.0-6' }}
            <button
              type="button"
              class="remove"
              [attr.aria-label]="'Удалить сетку ' + grid.id"
              (click)="store.removeManual(grid.id)"
            >
              ×
            </button>
          </span>
        }
      </div>
    }
  `,
  styles: `
    .layers,
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
      margin-bottom: 0.5rem;
    }
    .check {
      display: flex;
      gap: 0.3rem;
      align-items: center;
      font-size: 0.85rem;
    }
    .hint {
      opacity: 0.7;
      font-size: 0.8rem;
    }
    .error {
      color: var(--tui-text-negative);
      font-size: 0.8rem;
    }
    .levels {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin-bottom: 0.5rem;
    }
    .level {
      border: 1px solid var(--tui-border-normal);
      border-radius: 1rem;
      background: none;
      color: inherit;
      padding: 0.1rem 0.6rem;
      cursor: pointer;
      font-size: 0.8rem;
    }
    .level.selected {
      background: var(--tui-background-neutral-1);
      font-weight: 600;
    }
    .level.broken {
      opacity: 0.6;
    }
    .details {
      font-size: 0.85rem;
      margin-bottom: 0.5rem;
    }
    .details ul {
      margin: 0.2rem 0 0;
      padding-left: 1.2rem;
    }
    .chip {
      display: inline-flex;
      gap: 0.4rem;
      align-items: center;
      padding: 0.1rem 0.6rem;
      border-radius: 1rem;
      background: var(--tui-background-neutral-1);
      font-size: 0.85rem;
    }
    .remove {
      border: 0;
      background: none;
      cursor: pointer;
      color: inherit;
    }
  `,
})
export class StructurePanel {
  protected readonly store = inject(StructureStore);
  readonly chartTimeframe = input('1d');
  protected readonly layers = LAYERS;
  protected readonly titles = LAYER_TITLES;
  protected readonly trendTitles = TREND_TITLES;

  /** Старшие TF, уровни которых можно добавить в зоны (chart TF используется всегда). */
  protected readonly sources = computed(() =>
    allowedSourceTimeframes(this.chartTimeframe()).filter(
      (tf) => tf !== this.chartTimeframe(),
    ),
  );

  protected readonly shownLevels = computed(() =>
    this.store.levels().slice(0, LEVELS_SHOWN),
  );

  protected toggleSource(timeframe: string, on: boolean): void {
    const current = this.store.zoneSources();
    void this.store.setZoneSources(
      on
        ? [...new Set([...current, timeframe])]
        : current.filter((tf) => tf !== timeframe),
    );
  }

  protected name(info: PatternInfo): string {
    return PATTERN_TITLES[info.pattern] ?? info.pattern;
  }

  protected short(info: PatternInfo): string {
    return shortName(info.pattern);
  }

  protected stateTitle(info: PatternInfo): string {
    if (info.state === 'candidate') {
      return 'кандидат';
    }
    if (info.state === 'confirmed') {
      return 'подтверждён';
    }
    return `отменён (${REASONS[info.reason ?? ''] ?? info.reason ?? '—'})`;
  }

  protected components(
    values: Record<string, number>,
  ): { name: string; title: string; value: number }[] {
    return Object.entries(values).map(([name, value]) => ({
      name,
      title: COMPONENT_TITLES[name] ?? name,
      value: Math.round(value * 100) / 100,
    }));
  }
}
