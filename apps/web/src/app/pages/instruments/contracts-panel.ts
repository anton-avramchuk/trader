import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton, TuiInput } from '@taiga-ui/core';
import type { Contract } from '@trader/api-client';
import { InstrumentsStore } from './instruments.store';

/** Контракты выбранного root: список, ручное добавление и правка, запрос из ISS. */
@Component({
  selector: 'app-contracts-panel',
  imports: [FormsModule, TuiButton, TuiInput],
  template: `
    <h3>Контракты</h3>
    <table class="table">
      <thead>
        <tr>
          <th>Экспирация</th>
          <th>Последний день торгов</th>
          <th>SECID</th>
          <th>Коды у поставщиков</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (c of store.contracts(); track c.id) {
          <tr>
            <td>{{ c.expiration_date }}</td>
            <td>{{ c.last_trade_date ?? '—' }}</td>
            <td>{{ c.secid ?? '—' }}</td>
            <td>
              @for (p of c.provider_ids; track p.provider + p.id_type) {
                <span class="tag">{{ p.provider }}: {{ p.external_id }}</span>
              }
            </td>
            <td class="row-actions">
              <button
                tuiButton
                size="xs"
                appearance="flat"
                type="button"
                (click)="startEdit(c)"
              >
                Правка
              </button>
              <button
                tuiButton
                size="xs"
                appearance="flat-destructive"
                type="button"
                (click)="remove(c)"
              >
                Удалить
              </button>
            </td>
          </tr>
        } @empty {
          <tr>
            <td colspan="5" class="empty">
              Контрактов нет: запросите из ISS или добавьте вручную.
            </td>
          </tr>
        }
      </tbody>
    </table>

    @if (editing(); as contract) {
      <form class="inline" (ngSubmit)="saveEdit(contract)">
        <strong>Контракт {{ contract.expiration_date }}</strong>
        <tui-textfield>
          <label tuiLabel>SECID</label>
          <input tuiInput name="secid" [(ngModel)]="editSecid" />
        </tui-textfield>
        <tui-textfield>
          <label tuiLabel>Последний день торгов</label>
          <input tuiInput type="date" name="last" [(ngModel)]="editLast" />
        </tui-textfield>
        <button tuiButton type="submit" size="s">Сохранить</button>
        <button
          tuiButton
          type="button"
          size="s"
          appearance="flat"
          (click)="editing.set(null)"
        >
          Отмена
        </button>
      </form>
    }

    <form class="inline" (ngSubmit)="add()" #f="ngForm">
      <strong>Добавить вручную</strong>
      <tui-textfield>
        <label tuiLabel>Экспирация</label>
        <input
          tuiInput
          type="date"
          name="expiration"
          required
          [(ngModel)]="newExpiration"
        />
      </tui-textfield>
      <tui-textfield>
        <label tuiLabel>Последний день торгов</label>
        <input tuiInput type="date" name="newLast" [(ngModel)]="newLast" />
      </tui-textfield>
      <tui-textfield>
        <label tuiLabel>SECID</label>
        <input tuiInput name="newSecid" [(ngModel)]="newSecid" />
      </tui-textfield>
      <button tuiButton type="submit" size="s" [disabled]="!f.valid">
        Добавить
      </button>
    </form>

    <h3>Контракты из ISS</h3>
    <div class="inline">
      <tui-textfield>
        <label tuiLabel>С года</label>
        <input tuiInput type="number" name="year" [(ngModel)]="fromYear" />
      </tui-textfield>
      <button
        tuiButton
        type="button"
        size="s"
        [disabled]="requesting()"
        (click)="requestIss()"
      >
        Запросить у ISS
      </button>
      @if (store.issJob(); as job) {
        <span class="status"
          >{{ job.status }}
          @if (job.progress_message) {
            · {{ job.progress_message }}
          }
          · {{ (job.progress * 100).toFixed(0) }}%</span
        >
      }
    </div>
    @if (store.issError(); as error) {
      <p class="error">{{ error }}</p>
    }
    @if (store.issCandidates().length) {
      <table class="table">
        <thead>
          <tr>
            <th>
              <input
                type="checkbox"
                aria-label="Выбрать все"
                [checked]="
                  store.issSelectedCount() === store.issCandidates().length
                "
                (change)="store.setAllCandidates($any($event.target).checked)"
              />
            </th>
            <th>SECID</th>
            <th>Экспирация</th>
            <th>Последний день торгов</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>
          @for (c of store.issCandidates(); track c.secid) {
            <tr>
              <td>
                <input
                  type="checkbox"
                  [attr.aria-label]="'Выбрать ' + c.secid"
                  [checked]="c.selected"
                  (change)="
                    store.toggleCandidate(c.secid, $any($event.target).checked)
                  "
                />
              </td>
              <td>{{ c.secid }}</td>
              <td>{{ c.expiration_date }}</td>
              <td>{{ c.last_trade_date ?? '—' }}</td>
              <td>{{ c.exists ? 'уже есть' : 'новый' }}</td>
            </tr>
          }
        </tbody>
      </table>
      <div class="inline">
        <label class="check">
          <input type="checkbox" name="enqueue" [(ngModel)]="enqueueImports" />
          Сразу загрузить историю
        </label>
        <button
          tuiButton
          type="button"
          [disabled]="store.issSelectedCount() === 0"
          (click)="confirm()"
        >
          Создать выбранные ({{ store.issSelectedCount() }})
        </button>
        <button
          tuiButton
          type="button"
          appearance="flat"
          (click)="store.resetIss()"
        >
          Отмена
        </button>
      </div>
    }
  `,
  styleUrl: './instruments.css',
})
export class ContractsPanel {
  protected readonly store = inject(InstrumentsStore);

  protected readonly editing = signal<Contract | null>(null);
  protected editSecid = '';
  protected editLast = '';
  protected newExpiration = '';
  protected newLast = '';
  protected newSecid = '';
  protected fromYear = new Date().getFullYear() - 1;
  protected enqueueImports = true;
  protected readonly requesting = signal(false);

  protected startEdit(contract: Contract): void {
    this.editing.set(contract);
    this.editSecid = contract.secid ?? '';
    this.editLast = contract.last_trade_date ?? '';
  }

  protected async saveEdit(contract: Contract): Promise<void> {
    await this.store.updateContract(contract.id, {
      secid: this.editSecid.trim() || null,
      last_trade_date: this.editLast || null,
    });
    this.editing.set(null);
  }

  protected async add(): Promise<void> {
    const rootId = this.store.selectedId();
    if (rootId === null) {
      return;
    }
    await this.store.addContract(rootId, {
      expiration_date: this.newExpiration,
      last_trade_date: this.newLast || null,
      secid: this.newSecid.trim() || null,
    });
    this.newExpiration = this.newLast = this.newSecid = '';
  }

  protected async remove(contract: Contract): Promise<void> {
    if (
      confirm(`Удалить контракт ${contract.secid ?? contract.expiration_date}?`)
    ) {
      await this.store.deleteContract(contract.id);
    }
  }

  protected async requestIss(): Promise<void> {
    this.requesting.set(true);
    try {
      await this.store.requestIssContracts(this.fromYear);
    } finally {
      this.requesting.set(false);
    }
  }

  protected async confirm(): Promise<void> {
    await this.store.confirmIssContracts(this.enqueueImports);
  }
}
