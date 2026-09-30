import { computed, inject, Injectable, signal } from '@angular/core';
import type {
  CalendarDays,
  CalendarDetail,
  Contract,
  ContractIn,
  ContractPatch,
  IssContractIn,
  Job,
  Root,
  RootIn,
  RootPatch,
} from '@trader/api-client';
import { lastValueFrom } from 'rxjs';
import { ApiService } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';

/** Контракт, найденный в ISS, и его статус относительно уже заведённых. */
export interface IssCandidate extends IssContractIn {
  exists: boolean;
  selected: boolean;
}

interface IssJobResult {
  contracts: {
    secid: string;
    expiration_date: string;
    last_trade_date: string | null;
  }[];
}

/** Состояние и действия страницы Data → Instruments. */
@Injectable()
export class InstrumentsStore {
  private readonly api = inject(ApiService);
  private readonly jobs = inject(JobsService);

  readonly roots = signal<Root[]>([]);
  readonly selectedId = signal<number | null>(null);
  readonly contracts = signal<Contract[]>([]);
  readonly calendar = signal<CalendarDetail | null>(null);
  readonly calendarDays = signal<CalendarDays | null>(null);
  readonly loading = signal(false);

  readonly selected = computed(
    () => this.roots().find((root) => root.id === this.selectedId()) ?? null,
  );

  // Запрос контрактов ISS: ход задачи и найденные серии.
  readonly issJob = signal<Job | null>(null);
  readonly issCandidates = signal<IssCandidate[]>([]);
  readonly issError = signal<string | null>(null);

  readonly issSelectedCount = computed(
    () => this.issCandidates().filter((c) => c.selected).length,
  );

  async loadRoots(): Promise<void> {
    this.loading.set(true);
    try {
      this.roots.set(await this.api.call(this.api.client.GET('/roots')));
    } finally {
      this.loading.set(false);
    }
    const current = this.selectedId();
    if (current === null || !this.selected()) {
      const first = this.roots()[0];
      await this.select(first ? first.id : null);
    }
  }

  async select(rootId: number | null): Promise<void> {
    this.selectedId.set(rootId);
    this.resetIss();
    if (rootId === null) {
      this.contracts.set([]);
      this.calendar.set(null);
      return;
    }
    await Promise.all([this.loadContracts(rootId), this.loadCalendar()]);
  }

  async createRoot(body: RootIn): Promise<Root> {
    const root = await this.api.call(this.api.client.POST('/roots', { body }));
    await this.loadRoots();
    await this.select(root.id);
    return root;
  }

  async updateRoot(rootId: number, body: RootPatch): Promise<void> {
    await this.api.call(
      this.api.client.PATCH('/roots/{root_id}', {
        params: { path: { root_id: rootId } },
        body,
      }),
    );
    await this.loadRoots();
    await this.loadCalendar();
  }

  async deleteRoot(rootId: number): Promise<void> {
    await this.api.call(
      this.api.client.DELETE('/roots/{root_id}', {
        params: { path: { root_id: rootId } },
      }),
    );
    this.selectedId.set(null);
    await this.loadRoots();
  }

  async loadContracts(rootId: number): Promise<void> {
    this.contracts.set(
      await this.api.call(
        this.api.client.GET('/roots/{root_id}/contracts', {
          params: { path: { root_id: rootId } },
        }),
      ),
    );
  }

  async loadCalendar(): Promise<void> {
    const root = this.selected();
    if (!root) {
      return;
    }
    const params = { params: { path: { code: root.calendar_code } } };
    const [calendar, days] = await Promise.all([
      this.api.call(this.api.client.GET('/calendars/{code}', params)),
      this.api.call(this.api.client.GET('/calendars/{code}/days', params)),
    ]);
    this.calendar.set(calendar);
    this.calendarDays.set(days);
  }

  async addContract(rootId: number, body: ContractIn): Promise<void> {
    await this.api.call(
      this.api.client.POST('/roots/{root_id}/contracts', {
        params: { path: { root_id: rootId } },
        body,
      }),
    );
    await this.loadContracts(rootId);
  }

  async updateContract(contractId: number, body: ContractPatch): Promise<void> {
    await this.api.call(
      this.api.client.PATCH('/contracts/{contract_id}', {
        params: { path: { contract_id: contractId } },
        body,
      }),
    );
    await this.reloadContracts();
  }

  async deleteContract(contractId: number): Promise<void> {
    await this.api.call(
      this.api.client.DELETE('/contracts/{contract_id}', {
        params: { path: { contract_id: contractId } },
      }),
    );
    await this.reloadContracts();
  }

  private async reloadContracts(): Promise<void> {
    const id = this.selectedId();
    if (id !== null) {
      await this.loadContracts(id);
    }
  }

  // --- контракты из ISS -----------------------------------------------------

  resetIss(): void {
    this.issJob.set(null);
    this.issCandidates.set([]);
    this.issError.set(null);
  }

  /** Ставит запрос к ISS и ждёт результат по WebSocket; кандидаты — в `issCandidates`. */
  async requestIssContracts(fromYear: number): Promise<void> {
    const rootId = this.selectedId();
    if (rootId === null) {
      return;
    }
    this.resetIss();
    const job = await this.api.call(
      this.api.client.POST('/roots/{root_id}/iss-preview', {
        params: { path: { root_id: rootId } },
        body: { from_year: fromYear },
      }),
    );
    this.issJob.set(job);
    let last: Job = job;
    try {
      await lastValueFrom(
        this.jobs.watch(job.id),
        // Поток без значений (мгновенное закрытие) — берём то, что уже есть.
        { defaultValue: job },
      ).then((finished) => (last = finished));
    } catch (error) {
      this.issError.set((error as Error).message);
      return;
    }
    this.issJob.set(last);
    if (last.status !== 'succeeded') {
      this.issError.set(last.error ?? `Запрос завершился: ${last.status}`);
      return;
    }
    const known = new Set(this.contracts().map((c) => c.expiration_date));
    const found = (last.result as unknown as IssJobResult).contracts;
    this.issCandidates.set(
      found.map((item) => ({
        secid: item.secid,
        expiration_date: item.expiration_date,
        last_trade_date: item.last_trade_date,
        exists: known.has(item.expiration_date),
        selected: !known.has(item.expiration_date),
      })),
    );
  }

  toggleCandidate(secid: string, selected: boolean): void {
    this.issCandidates.update((list) =>
      list.map((c) => (c.secid === secid ? { ...c, selected } : c)),
    );
  }

  setAllCandidates(selected: boolean): void {
    this.issCandidates.update((list) => list.map((c) => ({ ...c, selected })));
  }

  /** Создаёт отмеченные контракты; `enqueueImports` — сразу поставить загрузку истории. */
  async confirmIssContracts(enqueueImports: boolean): Promise<number> {
    const rootId = this.selectedId();
    const chosen = this.issCandidates().filter((c) => c.selected);
    if (rootId === null || chosen.length === 0) {
      return 0;
    }
    const result = await this.api.call(
      this.api.client.POST('/roots/{root_id}/contracts/from-iss', {
        params: { path: { root_id: rootId } },
        body: {
          contracts: chosen.map((c) => ({
            secid: c.secid,
            expiration_date: c.expiration_date,
            last_trade_date: c.last_trade_date,
          })),
          enqueue_imports: enqueueImports,
        },
      }),
    );
    this.resetIss();
    await this.loadContracts(rootId);
    return result.created;
  }
}
