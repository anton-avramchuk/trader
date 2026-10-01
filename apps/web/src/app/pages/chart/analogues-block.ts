import { Component, computed, input, output } from '@angular/core';
import {
  analogueRows,
  analoguesWarnings,
  fanChart,
  listHorizon,
  MODE_TITLES,
  type AnaloguesMode,
  type AnaloguesView,
} from './analogues-layer';
import type { StatsUnit } from './stats-layer';

/** Блок «Аналоги»: ближайшие по DTW исторические формации и веер их траекторий. */
@Component({
  selector: 'app-analogues-block',
  template: `
    <section class="analogues" aria-label="Аналоги">
      <div class="head">
        <strong>Аналоги</strong>
        <button
          type="button"
          class="action"
          [disabled]="!canPattern()"
          [attr.title]="canPattern() ? null : 'Выберите паттерн на графике'"
          (click)="lookup.emit('pattern')"
        >
          {{ modeTitles.pattern }}
        </button>
        <button type="button" class="action" (click)="lookup.emit('window')">
          {{ modeTitles.window }}
        </button>
      </div>
      @if (view(); as current) {
        <div class="title">{{ modeTitles[current.mode] }}</div>
        @if (current.loading) {
          <p class="hint">Поиск…</p>
        } @else if (current.error) {
          <p class="error" role="alert">{{ current.error }}</p>
        } @else if (!rows().length) {
          <p class="hint">Аналогов не найдено.</p>
        } @else {
          @if (fan(); as chart) {
            <svg
              class="fan"
              role="img"
              aria-label="Траектории аналогов после входа, перцентили 25, 50, 75"
              [attr.viewBox]="'0 0 ' + chart.width + ' ' + chart.height"
            >
              <line
                class="zero"
                x1="0"
                [attr.x2]="chart.width"
                [attr.y1]="chart.zero"
                [attr.y2]="chart.zero"
              />
              @for (path of chart.paths; track $index) {
                <polyline class="path" [attr.points]="path.points" />
              }
              @for (band of chart.bands; track band.quartile) {
                <polyline
                  class="band"
                  [class.median]="band.quartile === 50"
                  [attr.points]="band.points"
                />
              }
            </svg>
            <p class="hint">
              Сдвиг close после входа, баров 0…{{ chart.steps }}; жирные линии —
              перцентили 25/50/75.
            </p>
          }
          <table>
            <thead>
              <tr>
                <th>Формация</th>
                <th>Вход</th>
                <th title="1 / (1 + расстояние DTW)">Сходство</th>
                <th [attr.title]="'Горизонт ' + horizon() + ' баров'">Доход</th>
                <th>MFE</th>
                <th>MAE</th>
              </tr>
            </thead>
            <tbody>
              @for (row of rows(); track row.key) {
                <tr>
                  <td>{{ row.title }}</td>
                  <td>{{ row.date }}</td>
                  <td>{{ row.similarity }}</td>
                  <td>{{ row.ret }}</td>
                  <td>{{ row.mfe }}</td>
                  <td>{{ row.mae }}</td>
                </tr>
              }
            </tbody>
          </table>
          @for (warning of warnings(); track warning) {
            <p class="warning">⚠ {{ warning }}</p>
          }
        }
      }
    </section>
  `,
  styles: `
    .analogues {
      font-size: 0.85rem;
      margin-bottom: 0.5rem;
    }
    .head {
      display: flex;
      gap: 0.5rem;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 0.3rem;
    }
    .action {
      border: 1px solid var(--tui-border-normal);
      background: none;
      color: inherit;
      padding: 0 0.6rem;
      cursor: pointer;
      font-size: 0.8rem;
    }
    .action:disabled {
      opacity: 0.5;
      cursor: default;
    }
    .title {
      opacity: 0.8;
      margin-bottom: 0.2rem;
    }
    .fan {
      width: 100%;
      max-width: 28rem;
      height: auto;
      display: block;
    }
    .zero {
      stroke: var(--tui-border-normal);
      stroke-dasharray: 3 3;
    }
    .path {
      fill: none;
      stroke: currentColor;
      opacity: 0.18;
    }
    .band {
      fill: none;
      stroke: var(--tui-text-action, #3b82f6);
      stroke-width: 1.5;
    }
    .band.median {
      stroke-width: 2.5;
    }
    table {
      border-collapse: collapse;
    }
    th,
    td {
      padding: 0.1rem 0.6rem;
      text-align: right;
      white-space: nowrap;
    }
    th:first-child,
    td:first-child,
    th:nth-child(2),
    td:nth-child(2) {
      text-align: left;
    }
    th {
      font-weight: 500;
      opacity: 0.7;
      font-size: 0.78rem;
    }
    .hint {
      opacity: 0.7;
      font-size: 0.8rem;
      margin: 0.15rem 0;
    }
    .warning {
      color: var(--tui-text-warning, #b26a00);
      font-size: 0.78rem;
      margin: 0.15rem 0 0;
    }
    .error {
      color: var(--tui-text-negative);
      font-size: 0.8rem;
      margin: 0;
    }
  `,
})
export class AnaloguesBlock {
  readonly view = input<AnaloguesView | null>(null);
  readonly unit = input<StatsUnit>('atr');
  readonly canPattern = input(false);
  readonly lookup = output<AnaloguesMode>();

  protected readonly modeTitles = MODE_TITLES;
  protected readonly rows = computed(() =>
    analogueRows(this.view()?.data ?? null, this.unit()),
  );
  protected readonly fan = computed(() => fanChart(this.view()?.data ?? null));
  protected readonly horizon = computed(() =>
    listHorizon(this.view()?.data ?? null),
  );
  protected readonly warnings = computed(() =>
    analoguesWarnings(this.view()?.data ?? null),
  );
}
