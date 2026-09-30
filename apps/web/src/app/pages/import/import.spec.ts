import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { Import } from './import';

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve));
    fixture.detectChanges();
  }
}

describe('Import', () => {
  function setup() {
    const client = {
      GET: vi.fn((path: string) => {
        switch (path) {
          case '/roots':
            return ok([{ id: 1, code: 'NG', name: 'Gas' }]);
          case '/roots/{root_id}/contracts':
            return ok([
              {
                id: 10,
                root_id: 1,
                expiration_date: '2026-12-29',
                secid: 'NGZ6',
              },
            ]);
          case '/imports':
            return ok([
              {
                id: 55,
                contract_id: 10,
                provider: 'csv',
                kind: 'import',
                source_name: 'a.csv',
                status: 'completed',
                created_at: '2026-09-28T04:00:00Z',
                report: { inserted: 3, conflicts: 0 },
              },
            ]);
          default:
            return ok([]);
        }
      }),
      POST: vi.fn(),
    };
    TestBed.configureTestingModule({
      imports: [Import],
      providers: [
        provideEventPlugins(),
        provideTaiga(),
        { provide: API_CLIENT, useValue: client },
        { provide: JobsService, useValue: { watch: vi.fn() } },
      ],
    });
    return TestBed.createComponent(Import);
  }

  it('показывает инструмент, контракт и историю импортов в МСК', async () => {
    const fixture = setup();
    fixture.detectChanges();
    await settle(fixture);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';

    expect(text).toContain('NG — Gas');
    expect(text).toContain('NGZ6 (2026-12-29)');
    expect(text).toContain('28.09.2026, 07:00');
    expect(text).toContain('csv · a.csv');
    expect(text).toContain('Загрузить из ISS');
  });

  it('вкладка «Файл» показывает загрузку файла', async () => {
    const fixture = setup();
    fixture.detectChanges();
    await settle(fixture);
    const element = fixture.nativeElement as HTMLElement;

    const tab = Array.from(element.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Файл'),
    );
    tab?.click();
    await settle(fixture);

    expect(element.querySelector('input[type="file"]')).not.toBeNull();
  });
});
