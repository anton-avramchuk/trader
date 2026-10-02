import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';
import { TuiTextarea } from '@taiga-ui/kit/components/textarea';
import { UiDate, UiNumber, UiSelect, UiText } from '../../core/ui';
import type { BacktestLock } from '@trader/api-client';
import {
  equityChart,
  fillRow,
  metricRows,
  metricWarnings,
  type MetricUnit,
  replayLink,
  resolved,
  SOURCE_TITLES,
  STATUS_TITLES,
  STOP_KINDS,
  TARGET_KINDS,
  testInfo,
  tradeRows,
  UNIT_TITLES,
  windowRows,
  GRID_KEYS,
} from './backtest-model';
import { forecastRows, METHOD_TITLES } from '../chart/forecast-layer';
import { BacktestStore } from './backtest.store';

const TIMEFRAMES = ['15m', '1h', '4h', '1d', '1w'];
const MIN_NOTE = 5;
const REASON_TITLES: Record<string, string> = {
  stop: 'стоп',
  target: 'цель',
  time: 'время',
  end_of_data: 'конец данных',
};

/** Backtest: параметры стратегии и издержек, walk-forward, результат и сделки (ADR-0027). */
@Component({
  selector: 'app-backtest',
  imports: [
    DecimalPipe,
    FormsModule,
    RouterLink,
    TuiButton,
    TuiTextarea,
    UiDate,
    UiNumber,
    UiSelect,
    UiText,
  ],
  providers: [BacktestStore],
  template: `
    <h1>Backtest</h1>
    <p class="note">
      Стратегия «вход по событию»: сигнал на закрытии бара, вход по open
      следующего бара по ценам свечей инструмента. Метрики — не единственный
      критерий оценки: смотрите число сделок и предупреждения.
    </p>

    <form class="stack" (ngSubmit)="store.run()">
      <fieldset class="card">
        <legend class="section-title">Серия и период</legend>
        <div class="form-grid">
          <app-select
            label="Инструмент"
            name="instrument"
            [options]="instrumentOptions()"
            [ngModel]="form().instrumentId"
            (ngModelChange)="store.patch({ instrumentId: $event })"
          />
          <app-select
            label="TF"
            name="tf"
            [options]="timeframeOptions"
            [ngModel]="form().timeframe"
            (ngModelChange)="store.patch({ timeframe: $event })"
          />
          <app-select
            label="Режим"
            name="kind"
            [options]="kindOptions"
            [ngModel]="form().kind"
            (ngModelChange)="store.patch({ kind: $event })"
          />
          <app-date
            label="С"
            name="from"
            [ngModel]="form().periodFrom"
            (ngModelChange)="store.patch({ periodFrom: $event })"
          />
          <app-date
            label="По"
            name="to"
            [ngModel]="form().periodTo"
            (ngModelChange)="store.patch({ periodTo: $event })"
          />
        </div>
      </fieldset>

      <fieldset class="card">
        <legend class="section-title">Стратегия</legend>
        <div class="form-grid">
          <app-select
            label="Сигнал"
            name="source"
            [options]="sourceOptions"
            [ngModel]="form().source"
            (ngModelChange)="store.patch({ source: $event })"
          />
          <app-text
            label="Типы (через запятую)"
            name="groups"
            placeholder="все"
            [ngModel]="form().groups"
            (ngModelChange)="store.patch({ groups: $event })"
          />
          <app-select
            label="Сторона"
            name="side"
            [options]="sideOptions"
            [ngModel]="form().side"
            (ngModelChange)="store.patch({ side: $event })"
          />
          <app-select
            label="Стоп"
            name="stopKind"
            [options]="stopOptions"
            [ngModel]="form().stopKind"
            (ngModelChange)="store.patch({ stopKind: $event })"
          />
          <app-number
            label="Стоп, значение"
            name="stopValue"
            [ngModel]="form().stopValue"
            (ngModelChange)="store.patch({ stopValue: $event })"
          />
          <app-select
            label="Цель"
            name="targetKind"
            [options]="targetOptions"
            [ngModel]="form().targetKind"
            (ngModelChange)="store.patch({ targetKind: $event })"
          />
          <app-number
            label="Цель, значение"
            name="targetValue"
            [ngModel]="form().targetValue"
            (ngModelChange)="store.patch({ targetValue: $event })"
          />
          <app-number
            label="Макс. баров"
            name="maxBars"
            [min]="0"
            [ngModel]="form().maxBars"
            (ngModelChange)="store.patch({ maxBars: $event || null })"
          />
          <app-number
            label="Качество от"
            name="qualityMin"
            [min]="0"
            [max]="100"
            [ngModel]="form().qualityMin"
            (ngModelChange)="store.patch({ qualityMin: $event || null })"
          />
        </div>
      </fieldset>

      <fieldset class="card">
        <legend class="section-title">Издержки</legend>
        <div class="form-grid">
          <app-number
            label="Количество"
            name="quantity"
            [min]="1"
            [ngModel]="form().quantity"
            (ngModelChange)="store.patch({ quantity: $event })"
          />
          <app-number
            label="Полуспред, тиков"
            name="halfSpread"
            [min]="0"
            [ngModel]="form().halfSpread"
            (ngModelChange)="store.patch({ halfSpread: $event })"
          />
          <app-number
            label="Проскальзывание, тиков"
            name="slippage"
            [min]="0"
            [ngModel]="form().slippage"
            (ngModelChange)="store.patch({ slippage: $event })"
          />
          <app-number
            label="Комиссия за единицу"
            name="commission"
            [min]="0"
            [ngModel]="form().commission"
            (ngModelChange)="store.patch({ commission: $event })"
          />
        </div>
      </fieldset>

      @if (form().kind === 'walk_forward') {
        <fieldset class="card">
          <legend class="section-title">Walk-forward</legend>
          <div class="form-grid">
            <app-number
              label="Train, дней"
              name="train"
              [min]="1"
              [ngModel]="form().trainDays"
              (ngModelChange)="store.patch({ trainDays: $event })"
            />
            <app-number
              label="Validation, дней"
              name="valid"
              [min]="1"
              [ngModel]="form().validDays"
              (ngModelChange)="store.patch({ validDays: $event })"
            />
            <app-number
              label="Шаг, дней"
              name="step"
              [min]="1"
              [ngModel]="form().stepDays"
              (ngModelChange)="store.patch({ stepDays: $event })"
            />
            <app-select
              label="Цель подбора"
              name="objective"
              [options]="objectiveOptions"
              [ngModel]="form().objective"
              (ngModelChange)="store.patch({ objective: $event })"
            />
            <app-number
              label="Мин. сделок на train"
              name="minTrades"
              [min]="1"
              [ngModel]="form().minTrades"
              (ngModelChange)="store.patch({ minTrades: $event })"
            />
            <app-date
              label="Test с"
              name="testFrom"
              [ngModel]="form().testFrom"
              (ngModelChange)="store.patch({ testFrom: $event })"
            />
            <app-date
              label="Test по"
              name="testTo"
              [ngModel]="form().testTo"
              (ngModelChange)="store.patch({ testTo: $event })"
            />
          </div>
          <tui-textfield class="wide">
            <label tuiLabel>Сетка ({{ gridKeys }})</label>
            <textarea
              tuiTextarea
              name="grid"
              [min]="3"
              [ngModel]="form().grid"
              (ngModelChange)="store.patch({ grid: $event })"
            ></textarea>
          </tui-textfield>
        </fieldset>
      }
      <div class="actions">
        <button tuiButton type="submit" size="m" [disabled]="store.running()">
          Запустить
        </button>
        @if (form().kind === 'walk_forward') {
          <span class="hint"
            >Test открывается один раз на связку инструмент/TF/сигнал.</span
          >
        }
      </div>
    </form>

    @if (store.error(); as error) {
      <p class="error" role="alert">{{ error }}</p>
    }
    @if (store.progress(); as progress) {
      <p class="hint" role="status">
        {{ progress.message || 'выполняется' }} ·
        {{ progress.fraction * 100 | number: '1.0-0' }}%
      </p>
    }

    @if (store.current(); as backtest) {
      <section class="result" aria-label="Результат">
        <h2>Бэктест №{{ backtest.id }} — {{ statusTitle(backtest.status) }}</h2>
        <p class="hint">
          {{ backtest.timeframe_code }} · {{ backtest.period_from }} …
          {{ backtest.period_to }} · хеш
          {{ backtest.params_hash.slice(0, 8) }}
          @if (runsForSeries() !== null) {
            · запусков по связке: {{ runsForSeries() }}
          }
        </p>
        @if (backtest.status === 'succeeded') {
          <div class="units" role="group" aria-label="Единицы">
            @for (u of units; track u.value) {
              <button
                type="button"
                class="unit"
                [class.active]="store.unit() === u.value"
                [attr.aria-pressed]="store.unit() === u.value"
                (click)="store.unit.set(u.value)"
              >
                {{ u.title }}
              </button>
            }
          </div>
          @if (backtest.kind === 'walk_forward') {
            <p class="hint">
              Метрики ниже — по validation-окнам (параметры выбраны только по
              train).
              @if (finalParams(); as final) {
                Финальные параметры: <strong>{{ final }}</strong
                >.
              }
            </p>
          }
          <table class="metrics">
            <tbody>
              @for (row of rows(); track row.title) {
                <tr>
                  <th>{{ row.title }}</th>
                  <td>{{ row.value }}</td>
                </tr>
              }
            </tbody>
          </table>
          @for (warning of warnings(); track warning) {
            <p class="warning">⚠ {{ warning }}</p>
          }
          @if (equity(); as chart) {
            <svg
              class="equity"
              role="img"
              aria-label="Equity по сделкам"
              [attr.viewBox]="'0 0 ' + chart.width + ' ' + chart.height"
            >
              <line
                class="zero"
                x1="0"
                [attr.x2]="chart.width"
                [attr.y1]="chart.zero"
                [attr.y2]="chart.zero"
              />
              <polyline class="curve" [attr.points]="chart.points" />
            </svg>
            <p class="hint">
              Накопленный результат по закрытым сделкам ({{
                unitTitle(store.unit())
              }}).
            </p>
          }

          @if (windows().length) {
            <h3>Окна walk-forward</h3>
            <table>
              <thead>
                <tr>
                  <th>Train</th>
                  <th>Validation</th>
                  <th>Параметры</th>
                  <th>Сделок train</th>
                  <th>Сделок valid</th>
                  <th>Net valid (тики)</th>
                </tr>
              </thead>
              <tbody>
                @for (w of windows(); track w.id) {
                  <tr>
                    <td>{{ w.train }}</td>
                    <td>{{ w.valid }}</td>
                    <td>{{ w.params }}</td>
                    <td>{{ w.trainTrades }}</td>
                    <td>{{ w.validTrades }}</td>
                    <td>{{ w.validNet }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
          @if (test(); as t) {
            <h3>Test</h3>
            <p [class.warning]="t.status !== 'evaluated'">{{ t.title }}</p>
            @if (t.params) {
              <p class="hint">Параметры: {{ t.params }}</p>
            }
            @if (testRows().length) {
              <table class="metrics">
                <tbody>
                  @for (row of testRows(); track row.title) {
                    <tr>
                      <th>{{ row.title }}</th>
                      <td>{{ row.value }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            }
          }

          <h3>Сделки ({{ store.trades().length }})</h3>
          <p class="hint">Нажмите на сделку — детали и переход к графику.</p>
          @if (!tradeList().length) {
            <p class="hint">Сделок нет.</p>
          } @else {
            <table class="trades">
              <thead>
                <tr>
                  <th>Сегмент</th>
                  <th>Сторона</th>
                  <th>Событие</th>
                  <th>Вход</th>
                  <th>Выход</th>
                  <th>Причина</th>
                  <th>Net</th>
                  <th>Флаги</th>
                </tr>
              </thead>
              <tbody>
                @for (t of tradeList(); track t.id) {
                  <tr
                    class="pick"
                    [class.selected]="store.drill()?.trade?.id === t.id"
                    (click)="select(t.id)"
                  >
                    <td>{{ t.segment }}</td>
                    <td>{{ t.side }}</td>
                    <td>{{ t.ref }}</td>
                    <td>{{ t.entry }}</td>
                    <td>{{ t.exit }}</td>
                    <td>{{ t.reason }}</td>
                    <td>{{ t.net }}</td>
                    <td>{{ t.flags }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
          @if (store.drill(); as drill) {
            <section class="drill" aria-label="Сделка">
              <h3>
                Сделка {{ drill.trade.side === 'long' ? 'лонг' : 'шорт' }} ·
                {{ drill.trade.ref ?? '—' }}
                <button type="button" class="unit" (click)="store.closeTrade()">
                  Закрыть
                </button>
              </h3>
              <table class="metrics">
                <tbody>
                  <tr>
                    <th>Вход / выход</th>
                    <td>
                      {{ stamp(drill.trade.entry_time) }} →
                      {{ stamp(drill.trade.exit_time) }}
                    </td>
                  </tr>
                  <tr>
                    <th>Причина выхода</th>
                    <td>{{ reasonTitle(drill.trade.reason) }}</td>
                  </tr>
                  <tr>
                    <th>Gross / издержки / net, тики</th>
                    <td>
                      {{ drill.trade.gross_ticks | number: '1.2-2' }} /
                      {{ drill.trade.cost_ticks | number: '1.2-2' }} /
                      {{
                        drill.trade.gross_ticks - drill.trade.cost_ticks
                          | number: '1.2-2'
                      }}
                    </td>
                  </tr>
                  <tr>
                    <th>MFE / MAE, тики</th>
                    <td>
                      {{ drill.trade.mfe_ticks | number: '1.2-2' }} /
                      {{ drill.trade.mae_ticks | number: '1.2-2' }}
                    </td>
                  </tr>
                  @if (drill.occurrence; as occ) {
                    <tr>
                      <th>Событие</th>
                      <td>
                        {{ occ.occurrence.group }} ·
                        {{ occ.occurrence.direction }}
                        @if (occ.occurrence.quality !== null) {
                          · качество {{ occ.occurrence.quality }}
                        }
                      </td>
                    </tr>
                    <tr>
                      <th>Режим на входе</th>
                      <td>{{ occ.regime }}</td>
                    </tr>
                  }
                  @if (drill.level; as nearest) {
                    <tr>
                      <th>Ближайший уровень</th>
                      <td>
                        {{ nearest.level.price | number: '1.0-6' }}
                        ({{
                          nearest.level.role === 'support'
                            ? 'поддержка'
                            : 'сопротивление'
                        }}, {{ nearest.level.source }}, сила
                        {{ nearest.level.score }}) · цена
                        {{ nearest.above ? 'выше' : 'ниже' }} на
                        {{ nearest.distancePct | number: '1.2-2' }}%
                      </td>
                    </tr>
                  } @else if (!drill.loading) {
                    <tr>
                      <th>Ближайший уровень</th>
                      <td>активных уровней на момент входа нет</td>
                    </tr>
                  }
                </tbody>
              </table>
              @if (drill.loading) {
                <p class="hint">Загрузка деталей…</p>
              }
              @for (note of drill.notes; track note) {
                <p class="warning">⚠ {{ note }}</p>
              }
              <table>
                <thead>
                  <tr>
                    <th>Кол-во</th>
                    <th>Вход</th>
                    <th>Выход</th>
                    <th>Цена входа</th>
                    <th>Цена выхода</th>
                    <th>Причина</th>
                    <th>Тики</th>
                  </tr>
                </thead>
                <tbody>
                  @for (leg of legs(); track leg.entry) {
                    <tr>
                      <td>{{ leg.quantity }}</td>
                      <td>{{ leg.entry }}</td>
                      <td>{{ leg.exit }}</td>
                      <td>{{ leg.entryPrice }}</td>
                      <td>{{ leg.exitPrice }}</td>
                      <td>{{ leg.reason }}</td>
                      <td>{{ leg.ticks }}</td>
                    </tr>
                  }
                </tbody>
              </table>
              <div class="actions">
                @if (link(); as target) {
                  <a
                    tuiButton
                    size="xs"
                    appearance="secondary"
                    [routerLink]="target.path"
                    [queryParams]="target.queryParams"
                    >Открыть на графике (Replay)</a
                  >
                }
                <button
                  tuiButton
                  type="button"
                  size="xs"
                  [disabled]="drill.forecast.loading"
                  (click)="store.loadForecast()"
                >
                  Прогноз на момент входа
                </button>
              </div>
              @if (drill.forecast.loading) {
                <p class="hint">Расчёт прогноза…</p>
              }
              @if (drill.forecast.error; as forecastError) {
                <p class="error" role="alert">{{ forecastError }}</p>
              }
              @for (method of forecastMethods(); track method.key) {
                <div class="method">
                  <div class="hint">
                    {{ method.title }} · выборка {{ method.sample }}
                  </div>
                  <table>
                    <thead>
                      <tr>
                        <th>Горизонт</th>
                        <th>Событий</th>
                        <th>↑ ≥1</th>
                        <th>↓ ≥1</th>
                        <th>Медиана</th>
                        <th>Разброс 10–90%</th>
                      </tr>
                    </thead>
                    <tbody>
                      @for (row of method.rows; track row.horizon) {
                        <tr [class.unreliable]="row.unreliable">
                          <td>{{ row.horizon }}</td>
                          <td>{{ row.sample }}</td>
                          <td>{{ row.up[1] ?? '—' }}</td>
                          <td>{{ row.down[1] ?? '—' }}</td>
                          <td>{{ row.median }}</td>
                          <td>{{ row.range }}</td>
                        </tr>
                      }
                    </tbody>
                  </table>
                </div>
              }
              @if (drill.forecast.data) {
                <p class="hint">
                  Прогноз построен только по данным, известным на момент входа.
                  Статистика прошлого, а не гарантия.
                </p>
              }
            </section>
          }
        }
      </section>
    }

    @if (store.locks().length) {
      <section class="locks" aria-label="Заблокированные test-периоды">
        <h3>Заблокированные test-периоды</h3>
        @for (lock of store.locks(); track lock.id) {
          <div class="lock">
            {{ lock.family }} · {{ lock.timeframe_code }} ·
            {{ lock.test_from }} … {{ lock.test_to }}
            <button
              type="button"
              class="unit"
              (click)="startUnlock(lock)"
              [attr.aria-label]="'Разблокировать ' + lock.family"
            >
              Разблокировать
            </button>
          </div>
        }
        @if (unlocking(); as lock) {
          <div class="unlock">
            <app-text
              label="Причина разблокировки (попадёт в журнал)"
              name="note"
              [ngModel]="note()"
              (ngModelChange)="note.set($event)"
            />
            <button
              tuiButton
              type="button"
              size="xs"
              [disabled]="note().trim().length < minNote"
              (click)="confirmUnlock(lock)"
            >
              Разблокировать
            </button>
          </div>
        }
      </section>
    }

    @if (store.history().length) {
      <section class="history" aria-label="История">
        <h3>Последние бэктесты</h3>
        <table>
          <tbody>
            @for (b of store.history(); track b.id) {
              <tr>
                <td>№{{ b.id }}</td>
                <td>{{ b.kind === 'single' ? 'период' : 'walk-forward' }}</td>
                <td>{{ b.timeframe_code }}</td>
                <td>{{ b.family }}</td>
                <td>{{ statusTitle(b.status) }}</td>
                <td>
                  <button type="button" class="unit" (click)="store.open(b.id)">
                    Открыть
                  </button>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </section>
    }
  `,
  styles: `
    .note,
    .hint {
      opacity: 0.7;
      font-size: 0.85rem;
      margin: 0.2rem 0;
    }
    .note {
      margin-bottom: 1rem;
    }
    fieldset {
      min-width: 0;
      margin: 0;
    }
    legend {
      padding: 0 0.4rem;
    }
    .wide {
      display: block;
      margin-top: 0.75rem;
    }
    .actions {
      display: flex;
      gap: 0.75rem;
      align-items: center;
    }
    .units {
      display: inline-flex;
      margin: 0.4rem 0;
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
    table {
      border-collapse: collapse;
      font-size: 0.85rem;
    }
    th,
    td {
      padding: 0.1rem 0.6rem;
      text-align: left;
      white-space: nowrap;
    }
    .metrics th {
      font-weight: 500;
      opacity: 0.7;
    }
    .metrics td {
      text-align: right;
    }
    .equity {
      width: 100%;
      max-width: 28rem;
      height: auto;
      display: block;
    }
    .zero {
      stroke: var(--tui-border-normal);
      stroke-dasharray: 3 3;
    }
    .curve {
      fill: none;
      stroke: var(--tui-text-action, #3b82f6);
      stroke-width: 2;
    }
    .warning {
      color: var(--tui-text-warning, #b26a00);
      font-size: 0.8rem;
      margin: 0.15rem 0;
    }
    .error {
      color: var(--tui-text-negative);
    }
    .pick {
      cursor: pointer;
    }
    .pick:hover,
    .pick.selected {
      background: var(--tui-background-neutral-1);
    }
    .drill {
      margin-top: 0.75rem;
      padding-top: 0.5rem;
      border-top: 1px solid var(--tui-border-normal);
    }
    tr.unreliable td {
      opacity: 0.5;
    }
    .lock {
      margin: 0.2rem 0;
      font-size: 0.85rem;
    }
    .unlock {
      display: flex;
      gap: 0.5rem;
      align-items: end;
      margin-top: 0.4rem;
    }
  `,
})
export class Backtest implements OnInit {
  protected readonly store = inject(BacktestStore);
  protected readonly timeframeOptions = TIMEFRAMES.map((tf) => ({
    value: tf,
    label: tf,
  }));
  protected readonly instrumentOptions = computed(() =>
    this.store.instruments().map((r) => ({ value: r.id, label: r.ticker })),
  );
  protected readonly kindOptions = [
    { value: 'single' as const, label: 'Один период' },
    { value: 'walk_forward' as const, label: 'Walk-forward' },
  ];
  protected readonly sideOptions = [
    { value: 'follow' as const, label: 'по событию' },
    { value: 'fade' as const, label: 'обратная' },
  ];
  protected readonly objectiveOptions = [
    { value: 'profit_factor' as const, label: 'Profit factor' },
    { value: 'net' as const, label: 'Net' },
  ];
  protected readonly stopOptions = STOP_KINDS.map((k) => ({
    value: k.value,
    label: k.title,
  }));
  protected readonly targetOptions = TARGET_KINDS.map((k) => ({
    value: k.value,
    label: k.title,
  }));
  protected readonly gridKeys = GRID_KEYS.join(', ');
  protected readonly minNote = MIN_NOTE;
  protected readonly sourceOptions = Object.entries(SOURCE_TITLES).map(
    ([value, label]) => ({ value, label }),
  );
  protected readonly units = (
    Object.entries(UNIT_TITLES) as [MetricUnit, string][]
  ).map(([value, title]) => ({ value, title }));

  protected readonly form = this.store.form;
  protected readonly note = signal('');
  protected readonly unlocking = signal<BacktestLock | null>(null);

  private readonly data = computed(() => resolved(this.store.current()));
  protected readonly rows = computed(() =>
    metricRows(this.data().metrics[this.store.unit()]),
  );
  protected readonly warnings = computed(() =>
    metricWarnings(this.data().metrics[this.store.unit()]),
  );
  protected readonly equity = computed(() =>
    equityChart(
      this.data().equity[this.store.unit() === 'money' ? 'money' : 'ticks'],
    ),
  );
  protected readonly windows = computed(() => windowRows(this.store.windows()));
  protected readonly tradeList = computed(() =>
    tradeRows(this.store.trades(), this.store.unit()),
  );
  protected readonly test = computed(() =>
    testInfo(this.store.current(), this.store.unit()),
  );
  protected readonly testRows = computed(() =>
    metricRows(this.test()?.metrics),
  );
  protected readonly legs = computed(() => {
    const trade = this.store.drill()?.trade;
    return trade ? [fillRow(trade)] : [];
  });
  protected readonly link = computed(() => {
    const backtest = this.store.current();
    const trade = this.store.drill()?.trade;
    return backtest && trade ? replayLink(backtest, trade) : null;
  });
  protected readonly forecastMethods = computed(() => {
    const data = this.store.drill()?.forecast.data;
    if (!data) {
      return [];
    }
    return [data.empirical, data.knn]
      .filter((m): m is NonNullable<typeof m> => !!m)
      .map((m) => ({
        key: m.method,
        title: METHOD_TITLES[m.method] ?? m.method,
        sample: m.sample,
        rows: forecastRows(m, 'atr'),
      }));
  });
  protected readonly runsForSeries = computed(() => {
    const result = this.store.current()?.result as {
      runs_for_series?: number;
    } | null;
    return result?.runs_for_series ?? null;
  });
  protected readonly finalParams = computed(() => {
    const result = this.store.current()?.result as {
      walk_forward?: { final_params?: Record<string, unknown> | null };
    } | null;
    const params = result?.walk_forward?.final_params;
    return params && Object.keys(params).length
      ? Object.entries(params)
          .map(([k, v]) => `${k}=${String(v)}`)
          .join(', ')
      : null;
  });

  ngOnInit(): void {
    void this.store.init();
  }

  protected select(id: number): void {
    const trade = this.store.trades().find((t) => t.id === id);
    if (trade) {
      void this.store.selectTrade(trade);
    }
  }

  protected stamp(iso: string): string {
    return iso.slice(0, 16).replace('T', ' ');
  }

  protected reasonTitle(reason: string): string {
    return REASON_TITLES[reason] ?? reason;
  }

  protected statusTitle(status: string): string {
    return STATUS_TITLES[status] ?? status;
  }

  protected unitTitle(unit: MetricUnit): string {
    return UNIT_TITLES[unit];
  }

  protected startUnlock(lock: BacktestLock): void {
    this.note.set('');
    this.unlocking.set(lock);
  }

  protected async confirmUnlock(lock: BacktestLock): Promise<void> {
    if (await this.store.unlock(lock, this.note().trim())) {
      this.unlocking.set(null);
    }
  }
}
