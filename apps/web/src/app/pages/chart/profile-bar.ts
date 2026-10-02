import {
  Component,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { TuiButton, TuiDialogService } from '@taiga-ui/core';
import { TUI_CONFIRM } from '@taiga-ui/kit/components/confirm';
import { UiSelect, UiText } from '../../core/ui';
import { ProfilesStore, type ProfileScope } from './profiles.store';

/** Профили графика: выбор, сохранение, переименование, удаление; последний запоминается. */
@Component({
  selector: 'app-profile-bar',
  imports: [FormsModule, TuiButton, UiSelect, UiText],
  template: `
    <div class="bar" role="group" aria-label="Профили графика">
      <app-select
        label="Профиль"
        aria-label="Профиль"
        [options]="profileOptions()"
        [ngModel]="store.selectedId()"
        (ngModelChange)="choose($event)"
      />
      <button
        tuiButton
        type="button"
        size="s"
        appearance="secondary"
        iconStart="@tui.settings-2"
        (click)="manage.set(!manage())"
      >
        {{ manage() ? 'Скрыть' : 'Управление' }}
      </button>
    </div>

    @if (manage()) {
      <div class="manage card" role="group" aria-label="Управление профилями">
        @if (store.selected()) {
          <div class="row">
            <button
              tuiButton
              type="button"
              size="s"
              appearance="secondary"
              (click)="update()"
            >
              Сохранить изменения
            </button>
            <app-text
              label="Новое имя"
              aria-label="Новое имя"
              [(ngModel)]="renameTo"
            />
            <button
              tuiButton
              type="button"
              size="s"
              appearance="flat"
              [disabled]="!renameTo.trim()"
              (click)="rename()"
            >
              Переименовать
            </button>
            <button
              tuiButton
              type="button"
              size="s"
              appearance="flat-destructive"
              (click)="remove()"
            >
              Удалить
            </button>
          </div>
        }
        <div class="row">
          <app-text
            label="Сохранить как"
            aria-label="Имя нового профиля"
            [(ngModel)]="newName"
          />
          <app-select
            label="Область"
            aria-label="Область профиля"
            [options]="scopeOptions()"
            [(ngModel)]="scope"
          />
          <button
            tuiButton
            type="button"
            size="s"
            [disabled]="!newName.trim()"
            (click)="saveNew()"
          >
            Сохранить
          </button>
        </div>
      </div>
    }
  `,
  styles: `
    :host {
      position: relative;
    }
    .bar app-select {
      min-width: 16rem;
    }
    .bar {
      display: flex;
      gap: 0.5rem;
      align-items: center;
    }
    .manage {
      position: absolute;
      inset-inline-end: 0;
      top: calc(100% + 0.5rem);
      z-index: 5;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      min-width: 32rem;
      box-shadow: var(--tui-shadow-medium);
    }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
    }
  `,
})
export class ProfileBar {
  protected readonly store = inject(ProfilesStore);
  private readonly dialogs = inject(TuiDialogService);

  readonly instrumentId = input<number | null>(null);
  readonly chartTimeframe = input.required<string>();
  /** Профиль просит другой TF графика (родитель решает, можно ли). */
  readonly timeframeRequested = output<string>();

  protected newName = '';
  protected renameTo = '';
  protected scope: ProfileScope = 'global';
  protected readonly ready = signal(false);
  protected readonly manage = signal(false);

  protected readonly profileOptions = computed(() => [
    { value: null as number | null, label: '— без профиля —' },
    ...this.store.profiles().map((p) => ({
      value: p.id as number | null,
      label: `${p.name}${p.instrument_id === null ? ' (глобальный)' : ''}`,
    })),
  ]);
  protected readonly scopeOptions = computed(() => [
    { value: 'global' as ProfileScope, label: 'Глобальный' },
    ...(this.instrumentId() === null
      ? []
      : [
          {
            value: 'instrument' as ProfileScope,
            label: 'Для этого инструмента',
          },
        ]),
  ]);

  constructor() {
    effect(() => {
      const instrumentId = this.instrumentId();
      if (instrumentId === null) {
        return;
      }
      untracked(() =>
        this.store.load(instrumentId, (timeframe) =>
          this.timeframeRequested.emit(timeframe),
        ),
      );
    });
  }

  protected async choose(id: number | null): Promise<void> {
    if (id === null) {
      this.store.selectedId.set(null);
      return;
    }
    this.renameTo = '';
    await this.store.apply(id, (timeframe) =>
      this.timeframeRequested.emit(timeframe),
    );
  }

  protected async saveNew(): Promise<void> {
    await this.store.saveNew(this.newName, this.scope, this.chartTimeframe());
    this.newName = '';
  }

  protected async update(): Promise<void> {
    await this.store.saveCurrent(this.chartTimeframe());
  }

  protected async rename(): Promise<void> {
    await this.store.rename(this.renameTo);
    this.renameTo = '';
  }

  protected async remove(): Promise<void> {
    const name = this.store.selected()?.name ?? '';
    const confirmed = await firstValueFrom(
      this.dialogs.open<boolean>(TUI_CONFIRM, {
        label: `Удалить профиль «${name}»?`,
        size: 's',
        data: {
          content: 'Это действие нельзя отменить.',
          yes: 'Удалить',
          no: 'Отмена',
        },
      }),
      { defaultValue: false },
    );
    if (confirmed) {
      await this.store.remove();
    }
  }
}
