import { Component, inject } from '@angular/core';
import { MskPipe } from '../../core/time/msk';
import { StructureStore } from './structure.store';
import {
  describeTrend,
  TREND_ARROWS,
  TREND_TITLES,
  type TrendInfo,
} from './trend';

/** Тренд на текущем и старших таймфреймах: направление, сила, с какого бара. */
@Component({
  selector: 'app-trend-block',
  imports: [MskPipe],
  template: `
    <h2 class="section-title">Тренд</h2>
    @if (store.trends().length) {
      <table aria-label="Тренд по таймфреймам">
        <tbody>
          @for (row of store.trends(); track row.timeframe) {
            <tr>
              <th scope="row">{{ row.timeframe }}</th>
              @if (row.info; as info) {
                <td [class]="info.state" [title]="hint(info)">
                  {{ arrows[info.state] }} {{ describe(info) }}
                </td>
                <td class="muted">с {{ info.since | msk: 'date' }}</td>
              } @else {
                <td class="muted" colspan="2">нет данных</td>
              }
            </tr>
          }
        </tbody>
      </table>
    } @else {
      <p class="muted">Считаем…</p>
    }
  `,
  styles: `
    table {
      border-collapse: collapse;
      font-size: 0.85rem;
    }
    th,
    td {
      padding: 0.15rem 0.6rem 0.15rem 0;
      text-align: start;
    }
    .uptrend {
      color: #26a69a;
    }
    .downtrend {
      color: #ef5350;
    }
  `,
})
export class TrendBlock {
  protected readonly store = inject(StructureStore);
  protected readonly arrows = TREND_ARROWS;

  protected describe(info: TrendInfo): string {
    return describeTrend(info);
  }

  protected hint(info: TrendInfo): string {
    return `${TREND_TITLES[info.state]}: сила ${Math.round(info.strength)} из 100 (эффективность движения ${info.efficiency})`;
  }
}
