import { Component, computed, forwardRef, input, signal } from '@angular/core';
import {
  type ControlValueAccessor,
  FormsModule,
  NG_VALUE_ACCESSOR,
} from '@angular/forms';
import { TuiDataListWrapper } from '@taiga-ui/kit/components/data-list-wrapper';
import { TuiSelect } from '@taiga-ui/kit/components/select';
import { TuiChevron } from '@taiga-ui/kit/directives/chevron';

export interface UiOption<T = unknown> {
  value: T;
  label: string;
}

/**
 * Выпадающий список в стиле Taiga вместо браузерного `<select>`: работает с
 * `ngModel`/формами и любым типом значения (пары «значение — подпись»).
 */
@Component({
  selector: 'app-select',
  imports: [FormsModule, TuiSelect, TuiDataListWrapper, TuiChevron],
  providers: [
    {
      provide: NG_VALUE_ACCESSOR,
      useExisting: forwardRef(() => UiSelect),
      multi: true,
    },
  ],
  template: `
    <tui-textfield tuiChevron [tuiTextfieldCleaner]="false">
      @if (label()) {
        <label tuiLabel>{{ label() }}</label>
      }
      <input
        tuiSelect
        [name]="name()"
        [placeholder]="placeholder()"
        [ngModel]="text()"
        [disabled]="disabled()"
        (ngModelChange)="pick($event)"
      />
      <tui-data-list-wrapper *tuiDropdown [items]="labels()" />
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
export class UiSelect<T = unknown> implements ControlValueAccessor {
  readonly label = input('');
  readonly name = input('');
  readonly placeholder = input('');
  readonly options = input.required<readonly UiOption<T>[]>();

  protected readonly value = signal<T | null>(null);
  protected readonly disabled = signal(false);
  protected readonly labels = computed(() =>
    this.options().map((o) => o.label),
  );
  protected readonly text = computed(
    () => this.options().find((o) => o.value === this.value())?.label ?? null,
  );

  private onChange: (value: T) => void = () => undefined;
  private onTouched: () => void = () => undefined;

  pick(label: string | null): void {
    const option = this.options().find((o) => o.label === label);
    if (option) {
      this.value.set(option.value);
      this.onChange(option.value);
    }
    this.onTouched();
  }

  writeValue(value: T | null): void {
    this.value.set(value);
  }

  registerOnChange(fn: (value: T) => void): void {
    this.onChange = fn;
  }

  registerOnTouched(fn: () => void): void {
    this.onTouched = fn;
  }

  setDisabledState(disabled: boolean): void {
    this.disabled.set(disabled);
  }
}
