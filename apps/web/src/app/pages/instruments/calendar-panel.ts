import { Component, computed, inject } from '@angular/core';
import { InstrumentsStore } from './instruments.store';

interface WindowView {
  name: string;
  start: string;
  end: string;
  nextDay: boolean;
}

function windows(raw: Record<string, unknown>[]): WindowView[] {
  return raw.map((w) => ({
    name: String(w['name']),
    start: String(w['start']).slice(0, 5),
    end: String(w['end']).slice(0, 5),
    nextDay: Boolean(w['next_day']),
  }));
}

/** Просмотр календаря root: эпохи расписания (МСК), праздники и особые дни. */
@Component({
  selector: 'app-calendar-panel',
  template: `
    @if (store.calendar(); as calendar) {
      <h3>Календарь {{ calendar.name }}</h3>
      <p class="hint">
        Расписание откалибровано по данным ISS и меняется миграциями (ADR-0014);
        время окон — московское. Отпечаток:
        <code>{{ calendar.fingerprint.slice(0, 12) }}</code>
      </p>
      <table class="table">
        <thead>
          <tr>
            <th>Действует</th>
            <th>Будни</th>
            <th>Выходные</th>
            <th>Сетка баров от</th>
          </tr>
        </thead>
        <tbody>
          @for (rule of rules(); track rule.from) {
            <tr>
              <td>{{ rule.from }} — {{ rule.to ?? 'н. в.' }}</td>
              <td>
                @for (w of rule.weekday; track w.name) {
                  <div>
                    {{ w.start }}–{{ w.end }} {{ w.name
                    }}{{ w.nextDay ? ' →' : '' }}
                  </div>
                }
              </td>
              <td>
                @for (w of rule.weekend; track w.name) {
                  <div>
                    {{ w.start }}–{{ w.end }} {{ w.name
                    }}{{ w.nextDay ? ' →' : '' }}
                  </div>
                } @empty {
                  —
                }
              </td>
              <td>{{ rule.anchor }}</td>
            </tr>
          }
        </tbody>
      </table>
      @if (store.calendarDays(); as days) {
        <details>
          <summary>Праздники (нет торгов): {{ days.holidays.length }}</summary>
          <p class="days">{{ days.holidays.join(', ') }}</p>
        </details>
        <details>
          <summary>
            Рабочие выходные (торги идут): {{ days.special_days.length }}
          </summary>
          <p class="days">{{ days.special_days.join(', ') }}</p>
        </details>
      }
    }
  `,
  styleUrl: './instruments.css',
})
export class CalendarPanel {
  protected readonly store = inject(InstrumentsStore);

  protected readonly rules = computed(() =>
    (this.store.calendar()?.rule_list ?? []).map((rule) => ({
      from: rule.effective_from,
      to: rule.effective_to,
      anchor: rule.bar_anchor.slice(0, 5),
      weekday: windows(rule.weekday_windows),
      weekend: windows(rule.weekend_windows),
    })),
  );
}
