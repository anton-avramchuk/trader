import { Component, forwardRef, input, signal } from '@angular/core';
import {
  type ControlValueAccessor,
  FormsModule,
  NG_VALUE_ACCESSOR,
} from '@angular/forms';
import { TuiInputNumber } from '@taiga-ui/kit/components/input-number';

/** Числовое поле в стиле Taiga; значение — число или `null` (пусто). */
@Component({
  selector: 'app-number',
  imports: [FormsModule, TuiInputNumber],
  providers: [
    {
      provide: NG_VALUE_ACCESSOR,
      useExisting: forwardRef(() => UiNumber),
      multi: true,
    },
  ],
  template: `
    <tui-textfield [tuiTextfieldCleaner]="false">
      @if (label()) {
        <label tuiLabel>{{ label() }}</label>
      }
      <input
        tuiInputNumber
        [name]="name()"
        [placeholder]="placeholder()"
        [min]="min()"
        [max]="max()"
        [ngModel]="value()"
        [disabled]="disabled()"
        (ngModelChange)="change($event)"
      />
    </tui-textfield>
  `,
  styles: `
    :host {
      display: inline-block;
      min-width: 9rem;
    }
    tui-textfield {
      width: 100%;
    }
  `,
})
export class UiNumber implements ControlValueAccessor {
  readonly label = input('');
  readonly name = input('');
  readonly placeholder = input('');
  readonly min = input<number>(Number.MIN_SAFE_INTEGER);
  readonly max = input<number>(Number.MAX_SAFE_INTEGER);

  protected readonly value = signal<number | null>(null);
  protected readonly disabled = signal(false);

  private onChange: (value: number | null) => void = () => undefined;
  private onTouched: () => void = () => undefined;

  change(value: number | null): void {
    this.value.set(value);
    this.onChange(value);
    this.onTouched();
  }

  writeValue(value: number | null): void {
    this.value.set(value);
  }

  registerOnChange(fn: (value: number | null) => void): void {
    this.onChange = fn;
  }

  registerOnTouched(fn: () => void): void {
    this.onTouched = fn;
  }

  setDisabledState(disabled: boolean): void {
    this.disabled.set(disabled);
  }
}
