import { Component, computed, inject } from '@angular/core';
import { MskPipe } from '../../core/time/msk';
import { ImportStore } from './import.store';

interface Missing {
  intervals: number;
  minutes: number;
  sample: { start: string; end: string; minutes: number }[];
}

interface ReportData {
  rows_total: number;
  rows_valid: number;
  rows_rejected: number;
  inserted: number;
  duplicates: number;
  duplicates_in_file: number;
  conflicts: number;
  error_counts: Record<string, number>;
  warnings: { out_of_order_rows: number; outside_session_rows: number };
  outside_session_sample: string[];
  range: { start: string; end: string } | null;
  min_price: string | null;
  max_price: string | null;
  missing: Missing;
  dataset_version_id: number | null;
}

/** Отчёт импорта (ADR-0015): строки, дубликаты, конфликты, ошибки, покрытие, диапазон. */
@Component({
  selector: 'app-report-view',
  imports: [MskPipe],
  template: `
    @if (record(); as rec) {
      <h3>
        Отчёт импорта №{{ rec.id }}
        <span class="badge" [class.bad]="rec.status !== 'completed'">{{
          rec.status
        }}</span>
      </h3>
      <p class="hint">
        {{ rec.provider }} · {{ rec.source_name ?? '—' }} ·
        {{ rec.created_at | msk: 'full' }} МСК
      </p>
      @if (rec.error) {
        <p class="error">{{ rec.error }}</p>
      }
      @if (data(); as r) {
        <dl class="stats">
          <dt>Строк в источнике</dt>
          <dd>{{ r.rows_total }}</dd>
          <dt>Принято</dt>
          <dd>{{ r.inserted }}</dd>
          <dt>Дубликаты</dt>
          <dd>{{ r.duplicates }} (в файле: {{ r.duplicates_in_file }})</dd>
          <dt>Конфликты</dt>
          <dd [class.bad]="r.conflicts > 0">{{ r.conflicts }}</dd>
          <dt>Отклонено строк</dt>
          <dd [class.bad]="r.rows_rejected > 0">{{ r.rows_rejected }}</dd>
          <dt>Диапазон</dt>
          <dd>
            @if (r.range) {
              {{ r.range.start | msk }} — {{ r.range.end | msk }} МСК
            } @else {
              —
            }
          </dd>
          <dt>Цена min / max</dt>
          <dd>{{ r.min_price ?? '—' }} / {{ r.max_price ?? '—' }}</dd>
          <dt>Версия датасета</dt>
          <dd>{{ r.dataset_version_id ?? 'не создана (нет новых данных)' }}</dd>
        </dl>

        @if (r.warnings.outside_session_rows) {
          <p class="warn">
            Вне сессий календаря: {{ r.warnings.outside_session_rows }} свечей —
            проверьте часовой пояс файла.
          </p>
        }
        @if (r.warnings.out_of_order_rows) {
          <p class="warn">
            Строки не по порядку времени: {{ r.warnings.out_of_order_rows }}.
          </p>
        }

        <h4>
          Пропуски по календарю: {{ r.missing.intervals }} интервалов,
          {{ r.missing.minutes }} мин
        </h4>
        <p class="hint">
          ISS не отдаёт минуты без сделок, поэтому короткие пропуски у
          неликвидных контрактов ожидаемы.
        </p>
        @if (r.missing.sample.length) {
          <table class="table">
            <thead>
              <tr>
                <th>С (МСК)</th>
                <th>По (МСК)</th>
                <th>Минут</th>
              </tr>
            </thead>
            <tbody>
              @for (m of r.missing.sample; track m.start) {
                <tr>
                  <td>{{ m.start | msk }}</td>
                  <td>{{ m.end | msk }}</td>
                  <td>{{ m.minutes }}</td>
                </tr>
              }
            </tbody>
          </table>
          @if (r.missing.intervals > r.missing.sample.length) {
            <p class="hint">
              Показаны первые {{ r.missing.sample.length }} из
              {{ r.missing.intervals }}.
            </p>
          }
        }

        @if (errorKinds().length) {
          <h4>Причины отклонения строк</h4>
          <ul>
            @for (e of errorKinds(); track e.code) {
              <li>
                <code>{{ e.code }}</code
                >: {{ e.count }}
              </li>
            }
          </ul>
        }
        @if (store.rejected().length) {
          <details>
            <summary>
              Отклонённые строки (первые {{ store.rejected().length }})
            </summary>
            <table class="table">
              <thead>
                <tr>
                  <th>Строка</th>
                  <th>Причина</th>
                  <th>Сообщение</th>
                </tr>
              </thead>
              <tbody>
                @for (e of store.rejected(); track $index) {
                  <tr>
                    <td>{{ e.row_number ?? '—' }}</td>
                    <td>
                      <code>{{ e.reason_code }}</code>
                    </td>
                    <td>{{ e.message }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </details>
        }
      }
    }
  `,
  styleUrl: './import.css',
})
export class ReportView {
  protected readonly store = inject(ImportStore);
  protected readonly record = this.store.report;

  protected readonly data = computed(
    () => (this.record()?.report ?? null) as unknown as ReportData | null,
  );

  protected readonly errorKinds = computed(() =>
    Object.entries(this.data()?.error_counts ?? {}).map(([code, count]) => ({
      code,
      count,
    })),
  );
}
