import { Component, computed, input, output } from '@angular/core';
import {
  statsRows,
  statsWarnings,
  type StatsUnit,
  type StatsView,
} from './stats-layer';

/** Блок «Исторически»: исходы прошлых событий по горизонтам (ADR-0023). */
@Component({
  selector: 'app-stats-block',
  template: `
    <section class="stats" aria-label="Исторически">
      <div class="head">
        <strong>Исторически</strong>
        <span class="units" role="group" aria-label="Единицы">
          @for (option of units; track option.value) {
            <button
              type="button"
              class="unit"
              [class.active]="unit() === option.value"
              [attr.aria-pressed]="unit() === option.value"
              (click)="unitChange.emit(option.value)"
            >
              {{ option.title }}
            </button>
          }
        </span>
      </div>
      @for (view of shown(); track view.title) {
        <div class="view" [attr.data-view]="view.title">
          <div class="title">{{ view.title }}</div>
          @if (view.loading) {
            <p class="hint">Расчёт…</p>
          } @else if (view.error) {
            <p class="error" role="alert">{{ view.error }}</p>
          } @else if (!view.rows.length) {
            <p class="hint">Нет данных.</p>
          } @else {
            <table>
              <thead>
                <tr>
                  <th>Горизонт, баров</th>
                  <th title="Непересекающиеся события из всех">Событий</th>
                  <th title="Медиана, в направлении события">Доход</th>
                  <th title="Максимальная благоприятная экскурсия">MFE</th>
                  <th title="Максимальная неблагоприятная экскурсия">MAE</th>
                  <th>В плюс</th>
                  @if (view.pattern) {
                    <th>Цель раньше</th>
                    <th>Отмена раньше</th>
                  }
                  <th
                    title="Разница со случайными точками того же режима, 95% CI"
                  >
                    Против случайных
                  </th>
                </tr>
              </thead>
              <tbody>
                @for (row of view.rows; track row.horizon) {
                  <tr [class.unreliable]="row.unreliable">
                    <td>{{ row.horizon }}</td>
                    <td>{{ row.sample }}</td>
                    <td>{{ row.ret }}</td>
                    <td>{{ row.mfe }}</td>
                    <td>{{ row.mae }}</td>
                    <td>{{ row.win }}</td>
                    @if (view.pattern) {
                      <td>{{ row.target }}</td>
                      <td>{{ row.invalidated }}</td>
                    }
                    <td>{{ row.edge }}</td>
                  </tr>
                }
              </tbody>
            </table>
            @for (warning of view.warnings; track warning) {
              <p class="warning">⚠ {{ warning }}</p>
            }
          }
        </div>
      }
    </section>
  `,
  styles: `
    .stats {
      font-size: 0.85rem;
      margin-bottom: 0.5rem;
    }
    .head {
      display: flex;
      gap: 0.75rem;
      align-items: center;
      margin-bottom: 0.3rem;
    }
    .units {
      display: inline-flex;
    }
    .unit {
      border: 1px solid var(--tui-border-normal);
      background: none;
      color: inherit;
      padding: 0 0.6rem;
      cursor: pointer;
      font-size: 0.8rem;
    }
    .unit.active {
      background: var(--tui-background-neutral-1);
      font-weight: 600;
    }
    .view {
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
      padding: 0.1rem 0.6rem;
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
    .hint {
      opacity: 0.7;
      font-size: 0.8rem;
      margin: 0;
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
export class StatsBlock {
  readonly views = input.required<StatsView[]>();
  readonly unit = input<StatsUnit>('atr');
  readonly unitChange = output<StatsUnit>();

  protected readonly units: { value: StatsUnit; title: string }[] = [
    { value: 'atr', title: 'ATR' },
    { value: 'pct', title: '%' },
  ];

  protected readonly shown = computed(() =>
    this.views().map((view) => ({
      ...view,
      rows: statsRows(view.data, this.unit()),
      warnings: statsWarnings(view.data),
    })),
  );
}
