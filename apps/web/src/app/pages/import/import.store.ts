import { computed, inject, Injectable, signal } from '@angular/core';
import type {
  Conflict,
  Contract,
  FilePreview,
  ImportedFile,
  ImportPreset,
  ImportRecord,
  Job,
  Root,
} from '@trader/api-client';
import { lastValueFrom } from 'rxjs';
import { ApiService } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import {
  EMPTY_FORM,
  fromMapping,
  type MappingForm,
  toMapping,
  validateForm,
} from './mapping';

export type Source = 'iss' | 'file';

export interface ImportProgress {
  job: Job;
  /** Ошибка ожидания (обрыв WebSocket и т. п.); сама задача может быть жива. */
  watchError: string | null;
}

/** Состояние страницы Data → Import. */
@Injectable()
export class ImportStore {
  private readonly api = inject(ApiService);
  private readonly jobs = inject(JobsService);

  readonly roots = signal<Root[]>([]);
  readonly contracts = signal<Contract[]>([]);
  readonly rootId = signal<number | null>(null);
  readonly contractId = signal<number | null>(null);
  readonly source = signal<Source>('iss');

  // Файл
  readonly files = signal<ImportedFile[]>([]);
  readonly fileName = signal<string | null>(null);
  readonly preview = signal<FilePreview | null>(null);
  readonly presets = signal<ImportPreset[]>([]);
  readonly form = signal<MappingForm>({ ...EMPTY_FORM });
  readonly uploading = signal(false);

  // Ход и итог
  readonly progress = signal<ImportProgress | null>(null);
  readonly history = signal<ImportRecord[]>([]);
  readonly report = signal<ImportRecord | null>(null);
  readonly rejected = signal<
    { row_number: number | null; reason_code: string; message: string }[]
  >([]);
  readonly conflicts = signal<Conflict[]>([]);
  readonly chosenConflicts = signal<ReadonlySet<number>>(new Set());

  readonly running = computed(() => {
    const status = this.progress()?.job.status;
    return status === 'queued' || status === 'running';
  });

  readonly formProblems = computed(() => validateForm(this.form()));

  // --- справочники -----------------------------------------------------------

  async loadRoots(): Promise<void> {
    this.roots.set(await this.api.call(this.api.client.GET('/roots')));
    const first = this.roots()[0];
    if (this.rootId() === null && first) {
      await this.selectRoot(first.id);
    }
  }

  async selectRoot(rootId: number | null): Promise<void> {
    this.rootId.set(rootId);
    this.contractId.set(null);
    this.contracts.set([]);
    if (rootId === null) {
      return;
    }
    const contracts = await this.api.call(
      this.api.client.GET('/roots/{root_id}/contracts', {
        params: { path: { root_id: rootId } },
      }),
    );
    this.contracts.set(contracts);
    await this.selectContract(contracts.at(-1)?.id ?? null);
  }

  async selectContract(contractId: number | null): Promise<void> {
    this.contractId.set(contractId);
    this.progress.set(null);
    this.report.set(null);
    this.conflicts.set([]);
    this.rejected.set([]);
    await this.loadHistory();
  }

  async loadHistory(): Promise<void> {
    const contractId = this.contractId();
    this.history.set(
      contractId === null
        ? []
        : await this.api.call(
            this.api.client.GET('/imports', {
              params: { query: { contract_id: contractId, limit: 30 } },
            }),
          ),
    );
  }

  // --- файл ------------------------------------------------------------------

  async loadFileSetup(): Promise<void> {
    const [files, presets] = await Promise.all([
      this.api.call(this.api.client.GET('/import-files')),
      this.api.call(this.api.client.GET('/import-presets')),
    ]);
    this.files.set(files);
    this.presets.set(presets);
  }

  async upload(file: File): Promise<void> {
    this.uploading.set(true);
    try {
      await this.api.upload(
        `/import-files/${encodeURIComponent(file.name)}`,
        file,
      );
      await this.loadFileSetup();
      await this.selectFile(file.name);
    } finally {
      this.uploading.set(false);
    }
  }

  async selectFile(name: string | null): Promise<void> {
    this.fileName.set(name);
    this.preview.set(null);
    if (name !== null) {
      await this.loadPreview();
    }
  }

  async loadPreview(): Promise<void> {
    const name = this.fileName();
    if (name === null) {
      return;
    }
    const preview = await this.api.call(
      this.api.client.GET('/import-files/{name}/preview', {
        params: {
          path: { name },
          query: { encoding: this.form().encoding, lines: 15 },
        },
      }),
    );
    this.preview.set(preview);
    // Разделитель подсказывается по файлу, пока пользователь не выбрал пресет.
    if (preview.delimiter && !this.form().datetimeColumns) {
      this.patchForm({ delimiter: preview.delimiter });
    }
  }

  patchForm(changes: Partial<MappingForm>): void {
    this.form.update((form) => ({ ...form, ...changes }));
  }

  applyPreset(name: string): void {
    const preset = this.presets().find((p) => p.name === name);
    if (preset) {
      this.form.set(fromMapping(preset.mapping));
    }
  }

  async savePreset(name: string, description: string | null): Promise<void> {
    const problems = this.formProblems();
    if (problems.length) {
      throw new Error(problems.join('; '));
    }
    await this.api.call(
      this.api.client.PUT('/import-presets/{name}', {
        params: { path: { name } },
        body: { description, mapping: toMapping(this.form()) },
      }),
    );
    await this.loadFileSetup();
  }

  // --- запуск ----------------------------------------------------------------

  async startIss(from: string | null, till: string | null): Promise<void> {
    const contractId = this.requireContract();
    const job = await this.api.call(
      this.api.client.POST('/contracts/{contract_id}/imports/iss', {
        params: { path: { contract_id: contractId } },
        body: { from: from || null, till: till || null },
      }),
    );
    await this.follow(job);
  }

  async startFile(): Promise<void> {
    const contractId = this.requireContract();
    const file = this.fileName();
    if (file === null) {
      throw new Error('Выберите файл');
    }
    const problems = this.formProblems();
    if (problems.length) {
      throw new Error(problems.join('; '));
    }
    const job = await this.api.call(
      this.api.client.POST('/contracts/{contract_id}/imports/file', {
        params: { path: { contract_id: contractId } },
        body: { file, mapping: toMapping(this.form()) },
      }),
    );
    await this.follow(job);
  }

  private requireContract(): number {
    const id = this.contractId();
    if (id === null) {
      throw new Error('Выберите контракт');
    }
    return id;
  }

  /** Следит за задачей по WebSocket и по завершении показывает отчёт. */
  private async follow(job: Job): Promise<void> {
    this.report.set(null);
    this.conflicts.set([]);
    this.rejected.set([]);
    this.progress.set({ job, watchError: null });
    let last = job;
    try {
      await new Promise<void>((resolve, reject) => {
        this.jobs.watch(job.id).subscribe({
          next: (state) => {
            last = state;
            this.progress.set({ job: state, watchError: null });
          },
          error: reject,
          complete: resolve,
        });
      });
    } catch (error) {
      this.progress.set({ job: last, watchError: (error as Error).message });
      return;
    }
    await this.loadHistory();
    const importId = (last.result as { import_id?: number } | null)?.import_id;
    if (last.status === 'succeeded' && importId !== undefined) {
      await this.openReport(importId);
    }
  }

  /** Ждёт завершения задачи (для тестов и простых сценариев). */
  async waitFor(jobId: number): Promise<Job> {
    return lastValueFrom(this.jobs.watch(jobId));
  }

  // --- отчёт и конфликты -----------------------------------------------------

  async openReport(importId: number): Promise<void> {
    const path = { params: { path: { import_id: importId } } };
    const record = await this.api.call(
      this.api.client.GET('/imports/{import_id}', path),
    );
    this.report.set(record);
    const [errors, conflicts] = await Promise.all([
      this.api.call(
        this.api.client.GET('/imports/{import_id}/errors', {
          params: { path: { import_id: importId }, query: { limit: 100 } },
        }),
      ),
      this.api.call(
        this.api.client.GET('/imports/{import_id}/conflicts', {
          params: {
            path: { import_id: importId },
            query: { status: 'pending', limit: 200 },
          },
        }),
      ),
    ]);
    this.rejected.set(errors);
    this.conflicts.set(conflicts);
    this.chosenConflicts.set(new Set());
  }

  toggleConflict(id: number, chosen: boolean): void {
    this.chosenConflicts.update((current) => {
      const next = new Set(current);
      if (chosen) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }

  /** Принять или отклонить отмеченные конфликты (если ничего не отмечено — все ожидающие). */
  async resolveConflicts(accept: boolean): Promise<void> {
    const report = this.report();
    if (!report) {
      return;
    }
    const chosen = [...this.chosenConflicts()];
    await this.api.call(
      this.api.client.POST('/imports/{import_id}/conflicts/resolve', {
        params: { path: { import_id: report.id } },
        body: { accept, conflict_ids: chosen.length ? chosen : null },
      }),
    );
    await this.openReport(report.id);
    await this.loadHistory();
  }
}
