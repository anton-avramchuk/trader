import { Component, inject, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton, TuiInput } from '@taiga-ui/core';
import { MskPipe } from '../../core/time/msk';
import { ConflictsView } from './conflicts-view';
import { FileSource } from './file-source';
import { ImportStore } from './import.store';
import { ReportView } from './report-view';

/** Data → Import: загрузка истории из ISS или файла, прогресс, отчёт и конфликты. */
@Component({
  selector: 'app-import',
  imports: [
    ConflictsView,
    FileSource,
    FormsModule,
    MskPipe,
    ReportView,
    TuiButton,
    TuiInput,
  ],
  providers: [ImportStore],
  template: `
    <h1>Import</h1>

    <div class="inline">
      <label class="check">
        Инструмент:
        <select
          [ngModel]="store.rootId()"
          (ngModelChange)="store.selectRoot($event)"
        >
          @for (r of store.roots(); track r.id) {
            <option [ngValue]="r.id">{{ r.code }} — {{ r.name }}</option>
          }
        </select>
      </label>
      <label class="check">
        Контракт:
        <select
          [ngModel]="store.contractId()"
          (ngModelChange)="store.selectContract($event)"
        >
          @for (c of store.contracts(); track c.id) {
            <option [ngValue]="c.id">
              {{ c.secid ?? c.expiration_date }} ({{ c.expiration_date }})
            </option>
          }
        </select>
      </label>
    </div>
    @if (!store.contracts().length) {
      <p class="hint">
        У инструмента нет контрактов — заведите их в разделе Instruments.
      </p>
    }

    <div class="inline tabs" role="tablist">
      <button
        tuiButton
        type="button"
        size="s"
        role="tab"
        [appearance]="store.source() === 'iss' ? 'primary' : 'secondary'"
        (click)="store.source.set('iss')"
      >
        MOEX ISS
      </button>
      <button
        tuiButton
        type="button"
        size="s"
        role="tab"
        [appearance]="store.source() === 'file' ? 'primary' : 'secondary'"
        (click)="openFile()"
      >
        Файл (CSV / JSON / Parquet)
      </button>
    </div>

    @if (store.source() === 'iss') {
      <div class="inline">
        <tui-textfield>
          <label tuiLabel>С даты (необязательно)</label>
          <input tuiInput type="date" name="from" [(ngModel)]="from" />
        </tui-textfield>
        <tui-textfield>
          <label tuiLabel>По дату (необязательно)</label>
          <input tuiInput type="date" name="till" [(ngModel)]="till" />
        </tui-textfield>
        <button
          tuiButton
          type="button"
          [disabled]="store.running() || !store.contractId()"
          (click)="store.startIss(from, till)"
        >
          Загрузить из ISS
        </button>
      </div>
      <p class="hint">
        Загрузка возобновляемая: повторный запуск догружает только недостающее;
        сегодняшний день не грузится.
      </p>
    } @else {
      <app-file-source />
    }

    @if (store.progress(); as p) {
      <section class="progress">
        <strong>Задача №{{ p.job.id }}: {{ p.job.status }}</strong>
        <progress max="1" [value]="p.job.progress"></progress>
        {{ (p.job.progress * 100).toFixed(0) }}%
        @if (p.job.progress_message) {
          <span class="hint">{{ p.job.progress_message }}</span>
        }
        @if (p.job.error) {
          <p class="error">{{ p.job.error }}</p>
        }
        @if (p.watchError) {
          <p class="warn">
            {{ p.watchError }} — обновите страницу, задача продолжает работу.
          </p>
        }
      </section>
    }

    <app-report-view />
    <app-conflicts-view />

    <h3>История импортов контракта</h3>
    <table class="table">
      <thead>
        <tr>
          <th>№</th>
          <th>Когда (МСК)</th>
          <th>Источник</th>
          <th>Статус</th>
          <th>Принято</th>
          <th>Конфликты</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (i of store.history(); track i.id) {
          <tr>
            <td>{{ i.id }}</td>
            <td>{{ i.created_at | msk }}</td>
            <td>{{ i.provider }} · {{ i.source_name ?? i.kind }}</td>
            <td>{{ i.status }}</td>
            <td>{{ i.report['inserted'] ?? '—' }}</td>
            <td>{{ i.report['conflicts'] ?? '—' }}</td>
            <td>
              <button
                tuiButton
                type="button"
                size="xs"
                appearance="flat"
                (click)="store.openReport(i.id)"
              >
                Отчёт
              </button>
            </td>
          </tr>
        } @empty {
          <tr>
            <td colspan="7" class="empty">Импортов пока не было.</td>
          </tr>
        }
      </tbody>
    </table>
  `,
  styleUrl: './import.css',
})
export class Import implements OnInit {
  protected readonly store = inject(ImportStore);
  protected from = '';
  protected till = '';

  ngOnInit(): void {
    void this.store.loadRoots();
  }

  protected async openFile(): Promise<void> {
    this.store.source.set('file');
    await this.store.loadFileSetup();
  }
}
