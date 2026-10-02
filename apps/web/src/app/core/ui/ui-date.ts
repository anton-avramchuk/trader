import { Component, forwardRef, input, signal } from '@angular/core';
import {
  type ControlValueAccessor,
  FormsModule,
  NG_VALUE_ACCESSOR,
} from '@angular/forms';
import { TuiDay } from '@taiga-ui/cdk';
import { TuiInputDate } from '@taiga-ui/kit/components/input-date';

/** ISO-дата `YYYY-MM-DD` → день календаря (или `null`). */
export function isoToDay(value: string | null): TuiDay | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? '');
  return match
    ? new TuiDay(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
    : null;
}

export function dayToIso(day: TuiDay | null): string {
  if (!day) {
    return '';
  }
  const two = (n: number): string => String(n).padStart(2, '0');
  return `${day.year}-${two(day.month + 1)}-${two(day.day)}`;
}

/** Выбор даты с календарём вместо `<input type="date">`; значение — строка `YYYY-MM-DD`. */
@Component({
  selector: 'app-date',
  imports: [FormsModule, TuiInputDate],
  providers: [
    {
      provide: NG_VALUE_ACCESSOR,
      useExisting: forwardRef(() => UiDate),
      multi: true,
    },
  ],
  template: `
    <tui-textfield [tuiTextfieldCleaner]="false">
      @if (label()) {
        <label tuiLabel>{{ label() }}</label>
      }
      <input
        tuiInputDate
        [name]="name()"
        [ngModel]="day()"
        [disabled]="disabled()"
        (ngModelChange)="change($event)"
      />
      <tui-calendar *tuiDropdown />
    </tui-textfield>
  `,
  styles: `
    :host {
      display: inline-block;
      min-width: 11rem;
    }
    tui-textfield {
      width: 100%;
    }
  `,
})
export class UiDate implements ControlValueAccessor {
  readonly label = input('');
  readonly name = input('');

  protected readonly day = signal<TuiDay | null>(null);
  protected readonly disabled = signal(false);

  private onChange: (value: string) => void = () => undefined;
  private onTouched: () => void = () => undefined;

  change(day: TuiDay | null): void {
    this.day.set(day);
    this.onChange(dayToIso(day));
    this.onTouched();
  }

  writeValue(value: string | null): void {
    this.day.set(isoToDay(value));
  }

  registerOnChange(fn: (value: string) => void): void {
    this.onChange = fn;
  }

  registerOnTouched(fn: () => void): void {
    this.onTouched = fn;
  }

  setDisabledState(disabled: boolean): void {
    this.disabled.set(disabled);
  }
}
