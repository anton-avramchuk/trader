import {
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton } from '@taiga-ui/core';
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
  imports: [FormsModule, TuiButton],
  template: `
    <div class="add" role="group" aria-label="Добавить индикатор">
      <label class="field">
        Индикатор
        <select
          [ngModel]="selectedName()"
          (ngModelChange)="select($event)"
          aria-label="Тип индикатора"
        >
          <option value="">— выберите —</option>
          @for (info of store.catalog(); track info.name) {
            <option [value]="info.name">{{ info.title }}</option>
          }
        </select>
      </label>
      @for (field of fields(); track field.key) {
        <label class="field">
          {{ field.label }}
          @if (field.kind === 'boolean') {
            <input type="checkbox" [(ngModel)]="field.value" />
          } @else if (field.kind === 'string') {
            <input type="text" [(ngModel)]="field.value" />
          } @else {
            <input
              type="number"
              [min]="field.min ?? null"
              [max]="field.max ?? null"
              [step]="field.step"
              [(ngModel)]="field.value"
            />
          }
        </label>
      }
      @if (selectedName()) {
        <label class="field">
          Source TF
          <select [(ngModel)]="sourceTf" aria-label="Source TF">
            @for (tf of sourceOptions(); track tf) {
              <option [value]="tf">{{ tf }}</option>
            }
          </select>
        </label>
        <button tuiButton type="button" size="s" (click)="add()">
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
    @for (hint of store.hints(); track hint.id) {
      <p class="hint" role="note">{{ hint.text }}</p>
    }
  `,
  styles: `
    .add {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: end;
      margin-bottom: 0.5rem;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 0.15rem;
      font-size: 0.8rem;
    }
    .field input[type='number'] {
      width: 6rem;
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
  protected readonly fields = signal<ParamField[]>([]);
  protected sourceTf = '';

  protected readonly sourceOptions = computed(() =>
    allowedSourceTimeframes(this.chartTimeframe()),
  );

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

  protected shortTitle(title: string): string {
    return title.split(' — ')[0];
  }

  protected params(params: Record<string, unknown>): string {
    return describeParams(params);
  }
}
