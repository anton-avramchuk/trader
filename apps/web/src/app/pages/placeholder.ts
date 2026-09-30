import { Component, inject } from '@angular/core';
import { ActivatedRoute } from '@angular/router';

/** Заготовка раздела: заголовок и пояснение берутся из `data` маршрута. */
@Component({
  selector: 'app-placeholder',
  template: `
    <h1>{{ title }}</h1>
    <p>{{ note }}</p>
  `,
})
export class Placeholder {
  private readonly data = inject(ActivatedRoute).snapshot.data;
  protected readonly title: string = this.data['title'] ?? '';
  protected readonly note: string = this.data['note'] ?? 'Раздел в разработке.';
}
