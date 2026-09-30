import { Component, computed, inject, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton, TuiInput } from '@taiga-ui/core';
import { MskPipe } from '../../core/time/msk';
import { describeBar } from '../chart/chart-data';
import { PriceChart } from '../chart/price-chart';
import {
  REPLAY_TIMEFRAMES,
  type ReplayTimeframe,
  ReplayStore,
  SPEEDS,
} from './replay.store';

/** Visual replay: по одной свече или ускоренно; будущее скрыто, видно только известное на момент. */
@Component({
  selector: 'app-replay',
  imports: [FormsModule, MskPipe, PriceChart, TuiButton, TuiInput],
  providers: [ReplayStore],
  template: `
    <h1>Replay</h1>

    <div class="inline">
      <label class="check">
        Инструмент:
        <select
          [ngModel]="store.rootId()"
          (ngModelChange)="store.selectRoot($event)"
        >
          @for (r of store.roots(); track r.id) {
            <option [ngValue]="r.id">{{ r.code }}</option>
          }
        </select>
      </label>
      <label class="check">
        Серия:
        <select [ngModel]="targetKey()" (ngModelChange)="onTarget($event)">
          <option value="root">Continuous</option>
          @for (c of store.contracts(); track c.id) {
            <option [value]="'c' + c.id">
              {{ c.secid ?? c.expiration_date }}
            </option>
          }
        </select>
      </label>
      <label class="check">
        TF:
        <select
          [ngModel]="store.timeframe()"
          (ngModelChange)="store.selectTimeframe($event)"
        >
          @for (tf of timeframes; track tf) {
            <option [value]="tf">{{ tf }}</option>
          }
        </select>
      </label>
      <tui-textfield>
        <label tuiLabel>Дата старта (МСК)</label>
        <input tuiInput type="date" name="date" [(ngModel)]="date" />
      </tui-textfield>
      <button
        tuiButton
        type="button"
        size="s"
        [disabled]="!date || store.loading()"
        (click)="store.start(date)"
      >
        {{ store.timeline().length ? 'Перейти к дате' : 'Начать' }}
      </button>
      @if (store.loading()) {
        <span class="status">Загрузка…</span>
      }
    </div>

    <div class="inline controls" role="toolbar" aria-label="Управление replay">
      <button
        tuiButton
        type="button"
        size="s"
        appearance="secondary"
        [disabled]="!ready()"
        (click)="store.restart()"
      >
        Restart
      </button>
      <button
        tuiButton
        type="button"
        size="s"
        appearance="secondary"
        [disabled]="!ready()"
        (click)="store.move(-100)"
      >
        −100
      </button>
      <button
        tuiButton
        type="button"
        size="s"
        appearance="secondary"
        [disabled]="!ready()"
        (click)="store.move(-10)"
      >
        −10
      </button>
      <button
        tuiButton
        type="button"
        size="s"
        appearance="secondary"
        [disabled]="!ready()"
        (click)="store.move(-1)"
      >
        ◀ Prev
      </button>
      @if (store.playing()) {
        <button tuiButton type="button" size="s" (click)="store.pause()">
          Pause
        </button>
      } @else {
        <button
          tuiButton
          type="button"
          size="s"
          [disabled]="!ready() || store.atEnd()"
          (click)="store.play()"
        >
          Play
        </button>
      }
      <button
        tuiButton
        type="button"
        size="s"
        appearance="secondary"
        [disabled]="!ready() || store.atEnd()"
        (click)="store.move(1)"
      >
        Next candle ▶
      </button>
      <button
        tuiButton
        type="button"
        size="s"
        appearance="secondary"
        [disabled]="!ready() || store.atEnd()"
        (click)="store.move(10)"
      >
        +10
      </button>
      <button
        tuiButton
        type="button"
        size="s"
        appearance="secondary"
        [disabled]="!ready() || store.atEnd()"
        (click)="store.move(100)"
      >
        +100
      </button>
      <label class="check">
        Speed:
        <select
          [ngModel]="store.speed()"
          (ngModelChange)="store.setSpeed($event)"
        >
          @for (s of speeds; track s) {
            <option [ngValue]="s">{{ s }} бар/с</option>
          }
        </select>
      </label>
    </div>

    <p class="known" aria-live="polite">
      @if (store.asOf(); as t) {
        <strong>Система знает на {{ t | msk: 'full' }} МСК:</strong>
        баров {{ store.visible().length }} из
        {{ store.timeline().length }} (будущее скрыто), роллов
        {{ store.known().length }}.
        @if (store.atEnd()) {
          Данные закончились.
        }
      } @else {
        Выберите дату и нажмите «Начать».
      }
    </p>
    <p class="legend">{{ legend() }}</p>

    <div class="chart">
      <app-price-chart
        [candles]="store.visible()"
        [rolls]="store.known()"
        [labels]="store.labels()"
        [datasetKey]="store.datasetKey()"
        [follow]="true"
      />
    </div>

    <div class="inline">
      <button
        tuiButton
        type="button"
        size="s"
        appearance="flat"
        [disabled]="!ready()"
        (click)="store.verifyWithServer()"
      >
        Сверить с серверным snapshot
      </button>
      @if (store.verify(); as v) {
        <span [class.bad]="v.mismatches > 0" [class.good]="v.mismatches === 0">
          {{
            v.mismatches === 0 ? 'Совпадает' : 'Расхождения: ' + v.mismatches
          }}
          (проверено {{ v.checked }} баров на {{ v.asOf | msk: 'full' }} МСК)
        </span>
      }
    </div>
  `,
  styles: `
    .inline {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
      margin-bottom: 0.75rem;
    }
    .check {
      display: flex;
      gap: 0.4rem;
      align-items: center;
    }
    .known,
    .legend {
      font-size: 0.9rem;
      min-height: 1.4rem;
    }
    .legend {
      font-family: monospace;
    }
    .chart {
      height: 55vh;
      min-height: 24rem;
    }
    .status {
      opacity: 0.7;
    }
    .good {
      color: #2e7d32;
    }
    .bad {
      color: var(--tui-text-negative);
    }
  `,
})
export class Replay implements OnInit {
  protected readonly store = inject(ReplayStore);
  protected readonly timeframes: readonly ReplayTimeframe[] = REPLAY_TIMEFRAMES;
  protected readonly speeds = SPEEDS;
  protected date = '';

  protected readonly ready = computed(() => this.store.timeline().length > 0);

  protected readonly targetKey = computed(() => {
    const target = this.store.target();
    return target.kind === 'root' ? 'root' : `c${target.id}`;
  });

  protected readonly legend = computed(() => {
    const candle = this.store.visible().at(-1);
    return candle ? describeBar(candle, this.store.labels()) : '';
  });

  ngOnInit(): void {
    void this.store.loadRoots();
  }

  protected onTarget(key: string): void {
    this.store.selectTarget(
      key === 'root'
        ? { kind: 'root' }
        : { kind: 'contract', id: Number(key.slice(1)) },
    );
  }
}
