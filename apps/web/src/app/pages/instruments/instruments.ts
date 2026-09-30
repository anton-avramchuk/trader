import { Component, inject, OnInit, signal } from '@angular/core';
import { TuiButton } from '@taiga-ui/core';
import type { RootIn } from '@trader/api-client';
import { CalendarPanel } from './calendar-panel';
import { ContractsPanel } from './contracts-panel';
import { InstrumentsStore } from './instruments.store';
import { RootForm } from './root-form';

/** Data → Instruments: базовые активы, их контракты и календарь. */
@Component({
  selector: 'app-instruments',
  imports: [CalendarPanel, ContractsPanel, RootForm, TuiButton],
  providers: [InstrumentsStore],
  template: `
    <h1>Instruments</h1>

    <div class="roots">
      @for (root of store.roots(); track root.id) {
        <button
          tuiButton
          type="button"
          size="s"
          [appearance]="
            root.id === store.selectedId() ? 'primary' : 'secondary'
          "
          (click)="choose(root.id)"
        >
          {{ root.code }}
        </button>
      }
      <button
        tuiButton
        type="button"
        size="s"
        appearance="flat"
        (click)="startCreate()"
      >
        + Новый root
      </button>
    </div>

    @if (mode() === 'create') {
      <h3>Новый базовый актив</h3>
      <app-root-form (save)="create($event)" (dismissed)="mode.set('view')" />
    }

    @if (store.selected(); as root) {
      @if (mode() === 'edit') {
        <h3>{{ root.code }}: настройки</h3>
        <app-root-form
          [root]="root"
          (save)="update($event)"
          (dismissed)="mode.set('view')"
        />
      } @else if (mode() === 'view') {
        <section class="card">
          <h2>{{ root.code }} — {{ root.name }}</h2>
          <dl>
            <dt>Биржа</dt>
            <dd>{{ root.exchange }}</dd>
            <dt>Валюта</dt>
            <dd>{{ root.quote_currency }}</dd>
            <dt>Шаг цены</dt>
            <dd>{{ root.tick_size }}</dd>
            <dt>Календарь</dt>
            <dd>{{ root.calendar_code }}</dd>
            <dt>Ролл</dt>
            <dd>
              за {{ root.roll_trading_days }} торг. дн. до экспирации, в начале
              недели
            </dd>
            <dt>Выходные сессии</dt>
            <dd>
              {{ root.include_weekend_sessions ? 'включены' : 'исключены' }}
            </dd>
          </dl>
          <button
            tuiButton
            type="button"
            size="s"
            appearance="secondary"
            (click)="mode.set('edit')"
          >
            Изменить
          </button>
          <button
            tuiButton
            type="button"
            size="s"
            appearance="flat-destructive"
            (click)="remove(root.id, root.code)"
          >
            Удалить
          </button>
        </section>
        <app-contracts-panel />
        <app-calendar-panel />
      }
    } @else if (mode() === 'view') {
      <p>Базовых активов пока нет: создайте первый (например, NG для газа).</p>
    }
  `,
  styleUrl: './instruments.css',
})
export class Instruments implements OnInit {
  protected readonly store = inject(InstrumentsStore);
  protected readonly mode = signal<'view' | 'create' | 'edit'>('view');

  ngOnInit(): void {
    void this.store.loadRoots();
  }

  protected startCreate(): void {
    this.mode.set('create');
  }

  protected async choose(id: number): Promise<void> {
    this.mode.set('view');
    await this.store.select(id);
  }

  protected async create(body: RootIn): Promise<void> {
    await this.store.createRoot(body);
    this.mode.set('view');
  }

  protected async update(body: RootIn): Promise<void> {
    const id = this.store.selectedId();
    if (id === null) {
      return;
    }
    await this.store.updateRoot(id, {
      name: body.name,
      exchange: body.exchange,
      quote_currency: body.quote_currency,
      tick_size: body.tick_size,
      roll_trading_days: body.roll_trading_days,
      include_weekend_sessions: body.include_weekend_sessions,
    });
    this.mode.set('view');
  }

  protected async remove(id: number, code: string): Promise<void> {
    if (confirm(`Удалить root ${code}? Возможно только без контрактов.`)) {
      await this.store.deleteRoot(id);
    }
  }
}
