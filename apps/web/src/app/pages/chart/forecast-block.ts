import { Component, computed, input, output } from '@angular/core';
import {
  boxChart,
  calibrationRows,
  calibrationWarnings,
  forecastRows,
  forecastWarnings,
  FORECAST_TITLES,
  METHOD_TITLES,
  methodWarnings,
  type CalibrationView,
  type ForecastMode,
  type ForecastView,
} from './forecast-layer';
import type { StatsUnit } from './stats-layer';

/** Блок Forecast: Empirical и KNN рядом и «Надёжность модели» (ADR-0026). */
@Component({
  selector: 'app-forecast-block',
  template: `
    <section class="forecast" aria-label="Прогноз">
      <div class="head">
        <strong>Прогноз</strong>
        <button
          type="button"
          class="action"
          [disabled]="!canPattern()"
          [attr.title]="canPattern() ? null : 'Выберите паттерн на графике'"
          (click)="lookup.emit('pattern')"
        >
          {{ titles.pattern }}
        </button>
        <button type="button" class="action" (click)="lookup.emit('window')">
          {{ titles.window }}
        </button>
      </div>
      <p class="note">
        Статистика прошлого, а не гарантия: смотрите размер выборки и
        предупреждения. Рост и падение — по цене.
      </p>
      @if (view(); as current) {
        @if (current.loading) {
          <p class="hint">Расчёт…</p>
        } @else if (current.error) {
          <p class="error" role="alert">{{ current.error }}</p>
        } @else {
          @for (warning of general(); track warning) {
            <p class="warning">⚠ {{ warning }}</p>
          }
          @for (method of methods(); track method.key) {
            <div class="method" [attr.data-method]="method.key">
              <div class="title">
                {{ method.title }} · выборка {{ method.sample }}
              </div>
              @if (!method.rows.length) {
                <p class="hint">Нет данных.</p>
              } @else {
                <table>
                  <thead>
                    <tr>
                      <th>Горизонт, баров</th>
                      <th title="Непересекающиеся из всех">Событий</th>
                      @for (t of thresholds(); track t) {
                        <th>↑ ≥{{ t }}</th>
                      }
                      @for (t of thresholds(); track t) {
                        <th>↓ ≥{{ t }}</th>
                      }
                      <th>Медиана</th>
                      <th title="Квантили 10% … 90%">Разброс 10–90%</th>
                      <th>MFE</th>
                      <th>MAE</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (row of method.rows; track row.horizon) {
                      <tr [class.unreliable]="row.unreliable">
                        <td>{{ row.horizon }}</td>
                        <td>{{ row.sample }}</td>
                        @for (cell of row.up; track $index) {
                          <td>{{ cell }}</td>
                        }
                        @for (cell of row.down; track $index) {
                          <td>{{ cell }}</td>
                        }
                        <td>{{ row.median }}</td>
                        <td>{{ row.range }}</td>
                        <td>{{ row.mfe }}</td>
                        <td>{{ row.mae }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
                @if (method.box; as box) {
                  <svg
                    class="box"
                    role="img"
                    aria-label="Квантили дохода по горизонтам"
                    [attr.viewBox]="'0 0 ' + box.width + ' ' + box.height"
                  >
                    <line
                      class="zero"
                      [attr.x1]="box.zero"
                      [attr.x2]="box.zero"
                      y1="0"
                      [attr.y2]="box.height"
                    />
                    @for (row of box.rows; track row.horizon) {
                      <line
                        class="whisker"
                        [attr.x1]="row.low"
                        [attr.x2]="row.high"
                        [attr.y1]="row.y"
                        [attr.y2]="row.y"
                      />
                      <rect
                        class="quartiles"
                        [attr.x]="row.q1"
                        [attr.y]="row.y - 6"
                        [attr.width]="row.q3 - row.q1"
                        height="12"
                      />
                      <line
                        class="median"
                        [attr.x1]="row.median"
                        [attr.x2]="row.median"
                        [attr.y1]="row.y - 7"
                        [attr.y2]="row.y + 7"
                      />
                    }
                  </svg>
                }
                @for (warning of method.warnings; track warning) {
                  <p class="warning">⚠ {{ warning }}</p>
                }
              }
            </div>
          }
        }
      }
      <div class="calibration">
        <div class="head">
          <strong>Надёжность модели</strong>
          <button
            type="button"
            class="action"
            [disabled]="!canPattern()"
            [attr.title]="canPattern() ? null : 'Выберите паттерн на графике'"
            (click)="calibrate.emit()"
          >
            Проверить Empirical на истории
          </button>
        </div>
        @if (calibration(); as cal) {
          @if (cal.loading) {
            <p class="hint">Расчёт…</p>
          } @else if (cal.error) {
            <p class="error" role="alert">{{ cal.error }}</p>
          } @else if (cal.data) {
            <p class="hint">
              Walk-forward: каждый прогноз — только по более ранним вхождениям.
              Проверено {{ cal.data.tested }} из {{ cal.data.occurrences }},
              горизонт {{ cal.data.horizon }}. Навык ≈ 0 — не лучше общей
              частоты.
            </p>
            @if (calRows().length) {
              <table>
                <thead>
                  <tr>
                    <th>Порог</th>
                    <th>Событие</th>
                    <th>N</th>
                    <th title="Чем меньше, тем лучше">Brier</th>
                    <th title="Brier константы = общей частоте">
                      Brier конст.
                    </th>
                    <th title="1 − Brier / Brier конст.">Навык</th>
                    <th title="Прогноз → факт (n) по корзинам 0–20…80–100%">
                      Калибровка по корзинам
                    </th>
                  </tr>
                </thead>
                <tbody>
                  @for (row of calRows(); track row.key) {
                    <tr>
                      <td>{{ row.threshold }}</td>
                      <td>{{ row.side }}</td>
                      <td>{{ row.n }}</td>
                      <td>{{ row.brier }}</td>
                      <td>{{ row.climatology }}</td>
                      <td>{{ row.skill }}</td>
                      <td class="bins">{{ row.bins.join(' · ') }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            }
            @for (warning of calWarnings(); track warning) {
              <p class="warning">⚠ {{ warning }}</p>
            }
          }
        }
      </div>
    </section>
  `,
  styles: `
    .forecast {
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
    .method,
    .calibration {
      margin-bottom: 0.6rem;
    }
    .title {
      opacity: 0.8;
      margin-bottom: 0.2rem;
    }
    table {
      border-collapse: collapse;
    }
    th,
    td {
      padding: 0.1rem 0.5rem;
      text-align: right;
      white-space: nowrap;
    }
    th:first-child,
    td:first-child {
      text-align: left;
    }
    th {
      font-weight: 500;
      opacity: 0.7;
      font-size: 0.78rem;
    }
    tr.unreliable td {
      opacity: 0.5;
    }
    .bins {
      text-align: left;
      font-size: 0.78rem;
    }
    .box {
      width: 100%;
      max-width: 26rem;
      height: auto;
      display: block;
      margin-top: 0.3rem;
    }
    .zero {
      stroke: var(--tui-border-normal);
      stroke-dasharray: 3 3;
    }
    .whisker,
    .median {
      stroke: currentColor;
      stroke-width: 1.5;
    }
    .quartiles {
      fill: var(--tui-text-action, #3b82f6);
      opacity: 0.35;
    }
    .note,
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
export class ForecastBlock {
  readonly view = input<ForecastView | null>(null);
  readonly calibration = input<CalibrationView | null>(null);
  readonly unit = input<StatsUnit>('atr');
  readonly canPattern = input(false);
  readonly lookup = output<ForecastMode>();
  readonly calibrate = output<void>();

  protected readonly titles = FORECAST_TITLES;
  protected readonly thresholds = computed(
    () => this.view()?.data?.thresholds ?? [],
  );
  protected readonly general = computed(() =>
    forecastWarnings(this.view()?.data ?? null),
  );
  protected readonly methods = computed(() => {
    const data = this.view()?.data;
    if (!data) {
      return [];
    }
    return [data.empirical, data.knn]
      .filter((m): m is NonNullable<typeof m> => !!m)
      .map((m) => ({
        key: m.method,
        title: METHOD_TITLES[m.method] ?? m.method,
        sample: m.sample,
        rows: forecastRows(m, this.unit()),
        box: boxChart(m),
        warnings: methodWarnings(m),
      }));
  });
  protected readonly calRows = computed(() =>
    calibrationRows(this.calibration()?.data ?? null, this.unit()),
  );
  protected readonly calWarnings = computed(() =>
    calibrationWarnings(this.calibration()?.data ?? null),
  );
}
