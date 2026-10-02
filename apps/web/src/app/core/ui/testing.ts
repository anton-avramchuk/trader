import type { ComponentFixture } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { isoToDay, type UiDate } from './ui-date';
import type { UiNumber } from './ui-number';
import type { UiText } from './ui-text';
import type { UiOption, UiSelect } from './ui-select';

/** Тестовые помощники: управляют обёртками `ui-*` так же, как пользователь. */
function instance<T>(fixture: ComponentFixture<unknown>, selector: string): T {
  const element = fixture.debugElement.query(By.css(selector));
  if (!element) {
    throw new Error(`Нет элемента ${selector}`);
  }
  return element.componentInstance as T;
}

export function chooseOption(
  fixture: ComponentFixture<unknown>,
  selector: string,
  label: string,
): void {
  instance<UiSelect>(fixture, selector).pick(label);
}

export function optionLabels(
  fixture: ComponentFixture<unknown>,
  selector: string,
): string[] {
  const options = instance<UiSelect>(fixture, selector).options();
  return (options as readonly UiOption[]).map((o) => o.label);
}

export function enterNumber(
  fixture: ComponentFixture<unknown>,
  selector: string,
  value: number | null,
): void {
  instance<UiNumber>(fixture, selector).change(value);
}

export function enterDate(
  fixture: ComponentFixture<unknown>,
  selector: string,
  iso: string,
): void {
  instance<UiDate>(fixture, selector).change(isoToDay(iso));
}

export function enterText(
  fixture: ComponentFixture<unknown>,
  selector: string,
  value: string,
): void {
  instance<UiText>(fixture, selector).change(value);
}
