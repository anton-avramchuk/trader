import { TestBed } from '@angular/core/testing';
import { TuiAlertService } from '@taiga-ui/core';
import { of } from 'rxjs';
import { ApiError, ApiService, describeError } from './api';
import { AppErrorHandler } from './error-handler';

const response = (status: number) => ({ status });

describe('describeError', () => {
  it('берёт текст detail из ответа FastAPI', () => {
    expect(describeError({ detail: 'Root не найден' }, 404)).toBe(
      'Root не найден',
    );
  });

  it('собирает ошибки валидации по полям', () => {
    const error = {
      detail: [
        { loc: ['body', 'tick_size'], msg: 'должно быть больше 0' },
        { loc: ['body', 'code'], msg: 'слишком длинный' },
      ],
    };

    expect(describeError(error, 422)).toBe(
      'tick_size: должно быть больше 0; code: слишком длинный',
    );
  });

  it('для серверных ошибок без тела даёт общее сообщение', () => {
    expect(describeError(undefined, 503)).toContain('Ошибка сервера');
    expect(describeError(undefined, 418)).toContain('418');
  });
});

describe('ApiService.call', () => {
  const service = () => TestBed.inject(ApiService);

  it('возвращает данные', async () => {
    const data = await service().call(
      Promise.resolve({ data: { id: 1 }, response: response(200) }),
    );

    expect(data).toEqual({ id: 1 });
  });

  it('бросает ApiError с текстом из detail', async () => {
    const request = service().call(
      Promise.resolve({
        error: { detail: 'Контракт не найден' },
        response: response(404),
      }),
    );

    await expect(request).rejects.toMatchObject({
      status: 404,
      message: 'Контракт не найден',
    });
  });

  it('обрыв связи превращается в сетевую ошибку', async () => {
    const error = await service()
      .call(Promise.reject(new TypeError('Failed to fetch')))
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).isNetwork).toBe(true);
  });

  it('ответ 204 без тела — не ошибка', async () => {
    const data = await service().call(
      Promise.resolve({ response: response(204) }),
    );

    expect(data).toBeUndefined();
  });
});

describe('AppErrorHandler', () => {
  function setup() {
    const open = vi.fn(() => of(undefined));
    TestBed.configureTestingModule({
      providers: [
        AppErrorHandler,
        { provide: TuiAlertService, useValue: { open } },
      ],
    });
    return { open, handler: TestBed.inject(AppErrorHandler) };
  }

  it('показывает ошибку API уведомлением', () => {
    const { open, handler } = setup();

    handler.handleError(new ApiError(409, 'Root уже существует'));

    expect(open).toHaveBeenCalledWith(
      'Root уже существует',
      expect.objectContaining({ label: 'Ошибка запроса' }),
    );
  });

  it('различает потерю связи', () => {
    const { open, handler } = setup();

    handler.handleError(new ApiError(0, 'Нет связи с сервером'));

    expect(open).toHaveBeenCalledWith(
      'Нет связи с сервером',
      expect.objectContaining({ label: 'Нет связи' }),
    );
  });

  it('неизвестную ошибку логирует и показывает общее сообщение', () => {
    const { open, handler } = setup();
    const log = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    handler.handleError(new Error('boom'));

    expect(log).toHaveBeenCalled();
    expect(open).toHaveBeenCalledWith(
      expect.stringContaining('Что-то пошло не так'),
      expect.anything(),
    );
    log.mockRestore();
  });
});
