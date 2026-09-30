import { Component, effect, input, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton, TuiInput } from '@taiga-ui/core';
import { TuiInputNumber } from '@taiga-ui/kit/components/input-number';
import type { Root, RootIn } from '@trader/api-client';

const DEFAULTS: RootIn = {
  code: '',
  name: '',
  exchange: 'MOEX',
  quote_currency: 'USD',
  tick_size: '0.01',
  calendar_code: 'moex_forts',
  roll_trading_days: 5,
  include_weekend_sessions: false,
};

/** Форма root: создание (код редактируется) и правка (код неизменяем). */
@Component({
  selector: 'app-root-form',
  imports: [FormsModule, TuiButton, TuiInput, TuiInputNumber],
  template: `
    <form class="form" (ngSubmit)="submit()" #f="ngForm">
      <tui-textfield>
        <label tuiLabel>Код (ASSETCODE ISS)</label>
        <input
          tuiInput
          name="code"
          required
          [disabled]="!!root()"
          [(ngModel)]="model.code"
        />
      </tui-textfield>
      <tui-textfield>
        <label tuiLabel>Название</label>
        <input tuiInput name="name" required [(ngModel)]="model.name" />
      </tui-textfield>
      <tui-textfield>
        <label tuiLabel>Биржа</label>
        <input tuiInput name="exchange" required [(ngModel)]="model.exchange" />
      </tui-textfield>
      <tui-textfield>
        <label tuiLabel>Валюта котировки</label>
        <input
          tuiInput
          name="quote_currency"
          required
          maxlength="3"
          [(ngModel)]="model.quote_currency"
        />
      </tui-textfield>
      <tui-textfield>
        <label tuiLabel>Шаг цены (tick size)</label>
        <input
          tuiInput
          name="tick_size"
          required
          [(ngModel)]="model.tick_size"
        />
      </tui-textfield>
      <tui-textfield>
        <label tuiLabel>Ролл за N торговых дней до экспирации</label>
        <input
          tuiInputNumber
          name="roll"
          [min]="0"
          [max]="60"
          [(ngModel)]="model.roll_trading_days"
        />
      </tui-textfield>
      <label class="check">
        <input
          type="checkbox"
          name="weekend"
          [(ngModel)]="model.include_weekend_sessions"
        />
        Включать выходные сессии в continuous-серию
      </label>
      <div class="actions">
        <button tuiButton type="submit" [disabled]="!f.valid">
          {{ root() ? 'Сохранить' : 'Создать' }}
        </button>
        <button
          tuiButton
          type="button"
          appearance="flat"
          (click)="dismissed.emit()"
        >
          Отмена
        </button>
      </div>
    </form>
  `,
  styles: `
    .form {
      display: grid;
      gap: 0.75rem;
      max-width: 28rem;
    }
    .actions {
      display: flex;
      gap: 0.5rem;
    }
    .check {
      display: flex;
      gap: 0.5rem;
      align-items: center;
    }
  `,
})
export class RootForm {
  readonly root = input<Root | null>(null);
  readonly save = output<RootIn>();
  readonly dismissed = output<void>();

  protected model: RootIn = { ...DEFAULTS };

  constructor() {
    effect(() => {
      const root = this.root();
      this.model = root
        ? {
            code: root.code,
            name: root.name,
            exchange: root.exchange,
            quote_currency: root.quote_currency,
            tick_size: String(Number(root.tick_size)),
            calendar_code: root.calendar_code,
            roll_trading_days: root.roll_trading_days,
            include_weekend_sessions: root.include_weekend_sessions,
          }
        : { ...DEFAULTS };
    });
  }

  protected submit(): void {
    this.save.emit({ ...this.model, code: this.model.code.trim() });
  }
}
