import {
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton, TuiCheckbox } from '@taiga-ui/core';
import { UiNumber, UiSelect, UiText } from '../../core/ui';
import {
  allowedSourceTimeframes,
  collectParams,
  describeParams,
  type ParamField,
  paramFields,
} from './indicators';
import { IndicatorsStore } from './indicators.store';

/** Панель индикаторов: «Добавить индикатор» (форма из схемы), активные, подсказки. */
@Component({
  selector: 'app-indicator-panel',
  imports: [FormsModule, TuiButton, TuiCheckbox, UiNumber, UiSelect, UiText],
  template: `
    <div class="add card" role="group" aria-label="Добавить индикатор">
      <app-select
        label="Индикатор"
        aria-label="Тип индикатора"
        [options]="typeOptions()"
        [ngModel]="selectedName()"
        (ngModelChange)="select($event)"
      />
      @for (field of fields(); track field.key) {
        @if (field.kind === 'boolean') {
          <label class="check">
            <input type="checkbox" tuiCheckbox [(ngModel)]="field.value" />
            {{ field.label }}
          </label>
        } @else if (field.kind === 'string') {
          <app-text [label]="field.label" [(ngModel)]="field.value" />
        } @else {
          <app-number
            [label]="field.label"
            [min]="field.min ?? -1000000"
            [max]="field.max ?? 1000000"
            [(ngModel)]="field.value"
          />
        }
      }
      @if (selectedName()) {
        <app-select
          label="Source TF"
          aria-label="Source TF"
          [options]="sourceChoices()"
          [(ngModel)]="sourceTf"
        />
        <button tuiButton type="button" size="m" (click)="add()">
          Добавить
        </button>
      }
    </div>

    @if (store.active().length) {
      <ul class="active">
        @for (item of store.active(); track item.id) {
          <li>
            <span class="chip">
              {{ shortTitle(item.title) }}{{ params(item.params) }} ·
              {{ item.sourceTimeframe }}
              <button
                type="button"
                class="remove"
                [attr.aria-label]="'Убрать ' + item.name"
                (click)="store.remove(item.id)"
              >
                ×
              </button>
            </span>
            @if (store.errors()[item.id]; as error) {
              <span class="error">{{ error }}</span>
            }
          </li>
        }
      </ul>
    }
    <div class="verify">
      <button
        tuiButton
        type="button"
        size="xs"
        appearance="flat"
        [disabled]="verifying()"
        (click)="verify()"
      >
        Verify: проверить на look-ahead
      </button>
      @if (store.verifyJob(); as job) {
        @if (verifying()) {
          <span class="hint"
            >{{ job.status }} · {{ job.progress_message ?? '' }}</span
          >
        }
      }
      @if (store.verifyError(); as error) {
        <span class="error">{{ error }}</span>
      }
    </div>
    @if (store.verifyResult(); as result) {
      <div class="report" role="status">
        <strong [class.good]="result.ok" [class.bad]="!result.ok">
          {{ result.ok ? 'Look-ahead не найден' : 'Найдены расхождения' }}
        </strong>
        <span class="hint">
          — {{ result.bars }} баров {{ result.timeframe }}, индикаторов:
          {{ result.reports.length }}
        </span>
        <ul>
          @for (r of result.reports; track r.indicator + $index) {
            <li [class.bad]="!r.ok">
              {{ r.indicator }}{{ params(r.params) }}:
              @if (r.ok) {
                совпало {{ r.values_checked }} значений в
                {{ r.positions_checked }} позициях
              } @else {
                расхождений {{ r.mismatch_count }}; первое — бар
                {{ r.mismatches[0].index }}, {{ r.mismatches[0].output }}: batch
                {{ r.mismatches[0].batch }} ≠ online
                {{ r.mismatches[0].online }}
              }
            </li>
          }
        </ul>
      </div>
    }
    @for (hint of store.hints(); track hint.id) {
      <p class="hint" role="note">{{ hint.text }}</p>
    }
  `,
  styles: `
    .add {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
    }
    .check {
      display: flex;
      gap: 0.4rem;
      align-items: center;
      font-size: 0.85rem;
    }
    .active {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      list-style: none;
      padding: 0;
      margin: 0 0 0.5rem;
    }
    .chip {
      display: inline-flex;
      gap: 0.4rem;
      align-items: center;
      padding: 0.1rem 0.6rem;
      border-radius: 1rem;
      background: var(--tui-background-neutral-1);
      font-size: 0.85rem;
    }
    .remove {
      border: 0;
      background: none;
      cursor: pointer;
      color: inherit;
    }
    .error {
      color: var(--tui-text-negative);
      font-size: 0.8rem;
    }
    .verify {
      display: flex;
      gap: 0.6rem;
      align-items: center;
      margin: 0.25rem 0;
    }
    .report {
      font-size: 0.8rem;
      margin-bottom: 0.4rem;
    }
    .report ul {
      margin: 0.2rem 0 0;
      padding-left: 1.2rem;
    }
    .good {
      color: #2e7d32;
    }
    .bad {
      color: var(--tui-text-negative);
    }
    .hint {
      margin: 0.1rem 0;
      opacity: 0.75;
      font-size: 0.8rem;
    }
  `,
})
export class IndicatorPanel {
  protected readonly store = inject(IndicatorsStore);
  readonly chartTimeframe = input.required<string>();

  protected readonly selectedName = signal('');
  protected readonly verifying = signal(false);
  protected readonly fields = signal<ParamField[]>([]);
  protected sourceTf = '';

  protected readonly sourceOptions = computed(() =>
    allowedSourceTimeframes(this.chartTimeframe()),
  );
  protected readonly sourceChoices = computed(() =>
    this.sourceOptions().map((tf) => ({ value: tf, label: tf })),
  );
  protected readonly typeOptions = computed(() => [
    { value: '', label: '— выберите —' },
    ...this.store.catalog().map((i) => ({ value: i.name, label: i.title })),
  ]);

  constructor() {
    // Смена chart TF: source TF по умолчанию — тот же, старший остаётся допустимым.
    effect(() => {
      const chart = this.chartTimeframe();
      if (!this.sourceOptions().includes(this.sourceTf)) {
        this.sourceTf = chart;
      }
    });
  }

  protected select(name: string): void {
    this.selectedName.set(name);
    const info = this.store.catalog().find((i) => i.name === name);
    this.fields.set(info ? paramFields(info) : []);
    this.sourceTf = this.chartTimeframe();
  }

  protected async add(): Promise<void> {
    const info = this.store
      .catalog()
      .find((i) => i.name === this.selectedName());
    if (!info) {
      return;
    }
    await this.store.add(info, collectParams(this.fields()), this.sourceTf);
    this.selectedName.set('');
    this.fields.set([]);
  }

  protected async verify(): Promise<void> {
    this.verifying.set(true);
    try {
      await this.store.verify();
    } finally {
      this.verifying.set(false);
    }
  }

  protected shortTitle(title: string): string {
    return title.split(' — ')[0];
  }

  protected params(params: Record<string, unknown>): string {
    return describeParams(params);
  }
}
