import { DatePipe } from '@angular/common';
import {
  Component,
  computed,
  effect,
  inject,
  OnInit,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { TuiButton } from '@taiga-ui/core';
import { UiDate, UiNumber, UiSelect } from '../../core/ui';
import { InstrumentsStore } from './instruments.store';

const DAY_MS = 86_400_000;
const DEFAULT_YEARS = 2;

const isoDate = (moment: Date): string => moment.toISOString().slice(0, 10);

/** Data → Instruments: тикеры importer, заведённые инструменты, загрузка свечей. */
@Component({
  selector: 'app-instruments',
  imports: [DatePipe, FormsModule, TuiButton, UiDate, UiNumber, UiSelect],
  providers: [InstrumentsStore],
  template: `
    <h1>Instruments</h1>

    <div class="roots">
      @for (item of store.instruments(); track item.id) {
        <button
          tuiButton
          type="button"
          size="s"
          [appearance]="
            item.id === store.selectedId() ? 'primary' : 'secondary'
          "
          (click)="choose(item.id)"
        >
          {{ item.ticker }}
        </button>
      }
      <button
        tuiButton
        type="button"
        size="s"
        appearance="flat"
        (click)="toggleAdd()"
      >
        + Добавить
      </button>
    </div>

    @if (adding()) {
      <section class="card">
        <h3>Новый инструмент из importer</h3>
        @if (store.tickersError(); as error) {
          <p class="error">Importer недоступен: {{ error }}</p>
        } @else if (store.available().length === 0) {
          <p class="empty">Все тикеры importer уже добавлены.</p>
        } @else {
          <div class="inline">
            <app-select
              label="Тикер"
              name="ticker"
              [options]="tickerOptions()"
              [(ngModel)]="ticker"
            />
            <app-number
              label="Стоимость тика (для денег)"
              name="tickValue"
              [min]="0"
              [(ngModel)]="newTickValue"
            />
            <button
              tuiButton
              type="button"
              size="s"
              [disabled]="!ticker"
              (click)="add()"
            >
              Добавить
            </button>
          </div>
        }
      </section>
    }

    @if (store.selected(); as item) {
      <section class="card">
        <h2>{{ item.ticker }} — {{ item.name }}</h2>
        <dl>
          <dt>Валюта</dt>
          <dd>{{ item.currency }}</dd>
          <dt>Шаг цены</dt>
          <dd>{{ item.tick_size }}</dd>
          <dt>Часовой пояс</dt>
          <dd>{{ item.timezone }}</dd>
        </dl>
        <div class="inline">
          <app-number
            label="Стоимость тика"
            name="tickValueEdit"
            placeholder="не задана"
            [min]="0"
            [ngModel]="toNumber(item.tick_value)"
            (ngModelChange)="editedTickValue = $event"
          />
          <button
            tuiButton
            type="button"
            size="s"
            appearance="secondary"
            (click)="saveTickValue(item.id, item.tick_value)"
          >
            Сохранить
          </button>
        </div>

        <h3>Покрытие свечами</h3>
        @if (!item.coverage?.length) {
          <p class="empty">Свечей нет — загрузите период ниже.</p>
        } @else {
          <table class="table">
            <thead>
              <tr>
                <th>TF</th>
                <th>Свечей</th>
                <th>С</th>
                <th>По</th>
              </tr>
            </thead>
            <tbody>
              @for (c of item.coverage; track c.timeframe) {
                <tr>
                  <td>{{ c.timeframe }}</td>
                  <td>{{ c.count }}</td>
                  <td>{{ c.first | date: 'yyyy-MM-dd HH:mm' }}</td>
                  <td>{{ c.last | date: 'yyyy-MM-dd HH:mm' }}</td>
                </tr>
              }
            </tbody>
          </table>
        }

        <h3>Загрузить свечи из importer</h3>
        <div class="inline">
          <app-date label="С" name="from" [(ngModel)]="from" />
          <app-date label="По (исключительно)" name="to" [(ngModel)]="to" />
          <button
            tuiButton
            type="button"
            size="s"
            [disabled]="store.loadRunning() || !from || !to"
            (click)="loadCandles(item.id)"
          >
            Загрузить
          </button>
        </div>
        @if (store.loadJob(); as job) {
          <p class="status" aria-live="polite">
            Задача {{ job.id }}: {{ job.status }}
            @if (job.error) {
              — <span class="error">{{ job.error }}</span>
            }
          </p>
        }

        <h3>Журнал загрузок</h3>
        @if (store.loads().length === 0) {
          <p class="empty">Загрузок ещё не было.</p>
        } @else {
          <table class="table">
            <thead>
              <tr>
                <th>Когда</th>
                <th>TF</th>
                <th>Период</th>
                <th>Строк</th>
              </tr>
            </thead>
            <tbody>
              @for (l of store.loads(); track l.id) {
                <tr>
                  <td>{{ l.created_at | date: 'yyyy-MM-dd HH:mm' }}</td>
                  <td>{{ l.timeframe_code }}</td>
                  <td>
                    {{ l.period_from | date: 'yyyy-MM-dd' }} —
                    {{ l.period_to | date: 'yyyy-MM-dd' }}
                  </td>
                  <td>{{ l.rows }}</td>
                </tr>
              }
            </tbody>
          </table>
        }
      </section>
    } @else {
      <p>Инструментов пока нет: добавьте первый из списка importer.</p>
    }
  `,
  styleUrl: './instruments.css',
})
export class Instruments implements OnInit {
  protected readonly store = inject(InstrumentsStore);
  protected readonly adding = signal(false);
  protected readonly tickerOptions = computed(() =>
    this.store
      .available()
      .map((t) => ({ value: t.ticker, label: `${t.ticker} — ${t.name}` })),
  );
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  protected ticker = '';
  protected newTickValue: number | null = null;
  protected editedTickValue: number | null | undefined = undefined;
  protected from = isoDate(new Date(Date.now() - DEFAULT_YEARS * 365 * DAY_MS));
  protected to = isoDate(new Date(Date.now() + DAY_MS));

  constructor() {
    // Выбранный инструмент живёт в адресе (`#SBER`): обновление страницы его сохраняет.
    effect(() => {
      const ticker = this.store.selected()?.ticker;
      if (ticker && ticker !== this.route.snapshot.fragment) {
        void this.router.navigate([], {
          relativeTo: this.route,
          fragment: ticker,
          replaceUrl: true,
        });
      }
    });
    // Ручная правка `#TICKER` или переход по ссылке меняют выбор.
    this.route.fragment.pipe(takeUntilDestroyed()).subscribe((fragment) => {
      const item = this.store.instruments().find((i) => i.ticker === fragment);
      if (item && item.id !== this.store.selectedId()) {
        void this.choose(item.id);
      }
    });
  }

  ngOnInit(): void {
    void this.store.load(this.route.snapshot.fragment);
    void this.store.loadTickers();
  }

  protected toNumber(value: number | string | null): number | null {
    return value === null || value === '' ? null : Number(value);
  }

  protected toggleAdd(): void {
    this.adding.update((value) => !value);
    this.ticker = this.store.available()[0]?.ticker ?? '';
  }

  protected async choose(id: number): Promise<void> {
    this.editedTickValue = undefined;
    await this.store.select(id);
  }

  protected async add(): Promise<void> {
    await this.store.add(this.ticker, this.newTickValue || null);
    this.adding.set(false);
    this.newTickValue = null;
  }

  protected async saveTickValue(
    id: number,
    current: number | string | null,
  ): Promise<void> {
    const value =
      this.editedTickValue === undefined ? current : this.editedTickValue;
    await this.store.setTickValue(id, value ? Number(value) : null);
    this.editedTickValue = undefined;
  }

  protected async loadCandles(id: number): Promise<void> {
    await this.store.loadCandles(id, { from: this.from, to: this.to }, null);
  }
}
