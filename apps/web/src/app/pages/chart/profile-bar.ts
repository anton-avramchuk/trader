import {
  Component,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton } from '@taiga-ui/core';
import { ProfilesStore, type ProfileScope } from './profiles.store';

/** Профили графика: выбор, сохранение, переименование, удаление; последний запоминается. */
@Component({
  selector: 'app-profile-bar',
  imports: [FormsModule, TuiButton],
  template: `
    <div class="bar" role="group" aria-label="Профили графика">
      <label class="field">
        Профиль
        <select
          [ngModel]="store.selectedId()"
          (ngModelChange)="choose($event)"
          aria-label="Профиль"
        >
          <option [ngValue]="null">— без профиля —</option>
          @for (p of store.profiles(); track p.id) {
            <option [ngValue]="p.id">
              {{ p.name }}{{ p.instrument_id === null ? ' (глобальный)' : '' }}
            </option>
          }
        </select>
      </label>

      @if (store.selected()) {
        <button
          tuiButton
          type="button"
          size="xs"
          appearance="secondary"
          (click)="update()"
        >
          Сохранить изменения
        </button>
        <label class="field">
          Новое имя
          <input type="text" [(ngModel)]="renameTo" aria-label="Новое имя" />
        </label>
        <button
          tuiButton
          type="button"
          size="xs"
          appearance="flat"
          [disabled]="!renameTo.trim()"
          (click)="rename()"
        >
          Переименовать
        </button>
        <button
          tuiButton
          type="button"
          size="xs"
          appearance="flat-destructive"
          (click)="remove()"
        >
          Удалить
        </button>
      }

      <label class="field">
        Сохранить как
        <input
          type="text"
          [(ngModel)]="newName"
          aria-label="Имя нового профиля"
        />
      </label>
      <label class="field">
        Область
        <select [(ngModel)]="scope" aria-label="Область профиля">
          <option value="global">Глобальный</option>
          <option value="instrument" [disabled]="instrumentId() === null">
            Для этого инструмента
          </option>
        </select>
      </label>
      <button
        tuiButton
        type="button"
        size="xs"
        [disabled]="!newName.trim()"
        (click)="saveNew()"
      >
        Сохранить
      </button>
    </div>
  `,
  styles: `
    .bar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      align-items: end;
      margin-bottom: 0.5rem;
      font-size: 0.8rem;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 0.15rem;
    }
  `,
})
export class ProfileBar {
  protected readonly store = inject(ProfilesStore);

  readonly instrumentId = input<number | null>(null);
  readonly chartTimeframe = input.required<string>();
  /** Профиль просит другой TF графика (родитель решает, можно ли). */
  readonly timeframeRequested = output<string>();

  protected newName = '';
  protected renameTo = '';
  protected scope: ProfileScope = 'global';
  protected readonly ready = signal(false);

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
    if (confirm(`Удалить профиль «${this.store.selected()?.name ?? ''}»?`)) {
      await this.store.remove();
    }
  }
}
