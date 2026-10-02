import { Component, forwardRef, input, signal } from '@angular/core';
import {
  type ControlValueAccessor,
  FormsModule,
  NG_VALUE_ACCESSOR,
} from '@angular/forms';
import { TuiInput } from '@taiga-ui/core';

/** Текстовое поле в стиле Taiga. */
@Component({
  selector: 'app-text',
  imports: [FormsModule, TuiInput],
  providers: [
    {
      provide: NG_VALUE_ACCESSOR,
      useExisting: forwardRef(() => UiText),
      multi: true,
    },
  ],
  template: `
    <tui-textfield [tuiTextfieldCleaner]="false">
      @if (label()) {
        <label tuiLabel>{{ label() }}</label>
      }
      <input
        tuiInput
        [name]="name()"
        [placeholder]="placeholder()"
        [ngModel]="value()"
        [disabled]="disabled()"
        (ngModelChange)="change($event)"
      />
    </tui-textfield>
  `,
  styles: `
    :host {
      display: inline-block;
      min-width: 12rem;
    }
    tui-textfield {
      width: 100%;
    }
  `,
})
export class UiText implements ControlValueAccessor {
  readonly label = input('');
  readonly name = input('');
  readonly placeholder = input('');

  protected readonly value = signal('');
  protected readonly disabled = signal(false);

  private onChange: (value: string) => void = () => undefined;
  private onTouched: () => void = () => undefined;

  change(value: string): void {
    this.value.set(value);
    this.onChange(value);
    this.onTouched();
  }

  writeValue(value: string | null): void {
    this.value.set(value ?? '');
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
