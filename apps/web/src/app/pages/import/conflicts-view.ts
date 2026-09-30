import { Component, inject } from '@angular/core';
import { TuiButton } from '@taiga-ui/core';
import { MskPipe } from '../../core/time/msk';
import { ImportStore } from './import.store';

/** Конфликты импорта: свечи, отличающиеся от загруженных ранее (принять/отклонить пакетно). */
@Component({
  selector: 'app-conflicts-view',
  imports: [MskPipe, TuiButton],
  template: `
    @if (store.conflicts().length) {
      <h3>Конфликты ({{ store.conflicts().length }})</h3>
      <p class="hint">
        Новые значения не применяются без решения. Принятие создаёт новую версию
        датасета и пересборку баров; старые версии остаются воспроизводимыми.
      </p>
      <table class="table">
        <thead>
          <tr>
            <th></th>
            <th>Время (МСК)</th>
            <th>Сейчас O / H / L / C / V</th>
            <th>Из импорта O / H / L / C / V</th>
          </tr>
        </thead>
        <tbody>
          @for (c of store.conflicts(); track c.id) {
            <tr>
              <td>
                <input
                  type="checkbox"
                  [attr.aria-label]="'Выбрать конфликт ' + c.id"
                  [checked]="store.chosenConflicts().has(c.id)"
                  (change)="
                    store.toggleConflict(c.id, $any($event.target).checked)
                  "
                />
              </td>
              <td>{{ c.timestamp | msk: 'full' }}</td>
              <td>
                {{ c.existing.open }} / {{ c.existing.high }} /
                {{ c.existing.low }} / {{ c.existing.close }} /
                {{ c.existing.volume }}
              </td>
              <td>
                {{ c.incoming.open }} / {{ c.incoming.high }} /
                {{ c.incoming.low }} / {{ c.incoming.close }} /
                {{ c.incoming.volume }}
              </td>
            </tr>
          }
        </tbody>
      </table>
      <div class="inline">
        <button
          tuiButton
          type="button"
          size="s"
          (click)="store.resolveConflicts(true)"
        >
          Принять {{ scope() }}
        </button>
        <button
          tuiButton
          type="button"
          size="s"
          appearance="secondary"
          (click)="store.resolveConflicts(false)"
        >
          Отклонить {{ scope() }}
        </button>
      </div>
    }
  `,
  styleUrl: './import.css',
})
export class ConflictsView {
  protected readonly store = inject(ImportStore);

  protected scope(): string {
    const chosen = this.store.chosenConflicts().size;
    return chosen ? `выбранные (${chosen})` : 'все';
  }
}
