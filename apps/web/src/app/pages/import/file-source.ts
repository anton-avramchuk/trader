import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton, TuiInput } from '@taiga-ui/core';
import { ImportStore } from './import.store';
import { type MappingForm, splitPreview } from './mapping';

/** Импорт из файла: загрузка, предпросмотр, сопоставление колонок, пресеты. */
@Component({
  selector: 'app-file-source',
  imports: [FormsModule, TuiButton, TuiInput],
  template: `
    <div class="inline">
      <input
        type="file"
        accept=".csv,.txt,.json,.ndjson,.parquet"
        aria-label="Файл со свечами"
        (change)="onFile($any($event.target).files)"
      />
      @if (store.uploading()) {
        <span class="status">Загрузка…</span>
      }
    </div>

    @if (store.files().length) {
      <div class="inline">
        <label class="check">
          Загруженные файлы:
          <select
            [ngModel]="store.fileName()"
            (ngModelChange)="store.selectFile($event)"
          >
            <option [ngValue]="null">— выберите —</option>
            @for (f of store.files(); track f.name) {
              <option [ngValue]="f.name">
                {{ f.name }} ({{ (f.size / 1024).toFixed(0) }} КБ)
              </option>
            }
          </select>
        </label>
      </div>
    }

    @if (store.preview(); as preview) {
      <h4>Предпросмотр ({{ preview.name }})</h4>
      <div class="preview">
        <table class="table small">
          <thead>
            <tr>
              <th>#</th>
              @for (c of columns(); track $index) {
                <th>{{ $index }}</th>
              }
            </tr>
          </thead>
          <tbody>
            @for (row of rows(); track $index) {
              <tr [class.header]="$index === 0 && store.form().hasHeader">
                <td>{{ $index + 1 }}</td>
                @for (cell of row; track $index) {
                  <td>{{ cell }}</td>
                }
              </tr>
            }
          </tbody>
        </table>
      </div>
      <p class="hint">
        Номера колонок в таблице — для файлов без заголовка; с заголовком
        указывайте имена как в первой строке.
      </p>

      <h4>Сопоставление колонок</h4>
      <div class="inline">
        <label class="check">
          Пресет:
          <select #p (change)="store.applyPreset(p.value)">
            <option value="">— выберите —</option>
            @for (preset of store.presets(); track preset.name) {
              <option [value]="preset.name">
                {{ preset.name }}{{ preset.builtin ? ' (встроенный)' : '' }}
              </option>
            }
          </select>
        </label>
      </div>
      <div class="grid">
        @for (f of textFields; track f.key) {
          <tui-textfield>
            <label tuiLabel>{{ f.label }}</label>
            <input
              tuiInput
              [name]="f.key"
              [ngModel]="text(f.key)"
              (ngModelChange)="set(f.key, $event)"
            />
          </tui-textfield>
        }
        <tui-textfield>
          <label tuiLabel>Пропустить строк</label>
          <input
            tuiInput
            type="number"
            min="0"
            name="skip"
            [ngModel]="store.form().skipRows"
            (ngModelChange)="set('skipRows', $event)"
          />
        </tui-textfield>
      </div>
      <div class="inline">
        <label class="check">
          <input
            type="checkbox"
            [ngModel]="store.form().hasHeader"
            (ngModelChange)="set('hasHeader', $event)"
          />
          Есть строка заголовка
        </label>
        <label class="check">
          Десятичный разделитель:
          <select
            [ngModel]="store.form().decimalSeparator"
            (ngModelChange)="set('decimalSeparator', $event)"
          >
            <option value=".">точка</option>
            <option value=",">запятая</option>
          </select>
        </label>
        <label class="check">
          <input
            type="checkbox"
            [ngModel]="store.form().timestampIsClose"
            (ngModelChange)="set('timestampIsClose', $event)"
          />
          Время в файле — конец свечи
        </label>
        <button
          tuiButton
          type="button"
          size="xs"
          appearance="flat"
          (click)="reload()"
        >
          Обновить предпросмотр (кодировка)
        </button>
      </div>

      @if (store.formProblems().length) {
        <ul class="problems">
          @for (p of store.formProblems(); track p) {
            <li>{{ p }}</li>
          }
        </ul>
      }

      <div class="inline">
        <tui-textfield>
          <label tuiLabel>Сохранить как пресет</label>
          <input tuiInput name="preset" [(ngModel)]="presetName" />
        </tui-textfield>
        <button
          tuiButton
          type="button"
          size="s"
          appearance="secondary"
          [disabled]="!presetName.trim() || store.formProblems().length > 0"
          (click)="savePreset()"
        >
          Сохранить пресет
        </button>
      </div>
      <button
        tuiButton
        type="button"
        [disabled]="
          store.running() ||
          store.formProblems().length > 0 ||
          !store.contractId()
        "
        (click)="store.startFile()"
      >
        Импортировать
      </button>
    }
  `,
  styleUrl: './import.css',
})
export class FileSource {
  protected readonly store = inject(ImportStore);
  protected presetName = '';

  protected readonly textFields: { key: keyof MappingForm; label: string }[] = [
    { key: 'delimiter', label: 'Разделитель (\\t — табуляция)' },
    { key: 'encoding', label: 'Кодировка (utf-8-sig, cp1251)' },
    { key: 'timezone', label: 'Часовой пояс времени в файле' },
    { key: 'datetimeColumns', label: 'Колонки даты/времени (через запятую)' },
    {
      key: 'datetimeFormat',
      label: 'Формат: iso, epoch_s, epoch_ms или %Y%m%d %H%M%S',
    },
    { key: 'open', label: 'open' },
    { key: 'high', label: 'high' },
    { key: 'low', label: 'low' },
    { key: 'close', label: 'close' },
    { key: 'volume', label: 'volume' },
    { key: 'tradeCount', label: 'trade_count (необязательно)' },
  ];

  private readonly rowsCache = signal(0);

  protected readonly rows = computed(() => {
    this.rowsCache();
    const preview = this.store.preview();
    const form = this.store.form();
    return preview ? splitPreview(preview.lines, form.delimiter) : [];
  });

  protected readonly columns = computed(() => this.rows()[0] ?? []);

  protected text(key: keyof MappingForm): string {
    return String(this.store.form()[key]);
  }

  protected set<K extends keyof MappingForm>(
    key: K,
    value: MappingForm[K],
  ): void {
    this.store.patchForm({ [key]: value } as Partial<MappingForm>);
  }

  protected async onFile(files: FileList | null): Promise<void> {
    const file = files?.item(0);
    if (file) {
      await this.store.upload(file);
    }
  }

  protected async reload(): Promise<void> {
    await this.store.loadPreview();
    this.rowsCache.update((n) => n + 1);
  }

  protected async savePreset(): Promise<void> {
    await this.store.savePreset(this.presetName.trim(), null);
    this.presetName = '';
  }
}
