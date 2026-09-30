import { Component } from '@angular/core';
import { shortName, PATTERN_TITLES } from './pattern-layer';
import { PATTERN_EXAMPLES, type PatternExample } from './pattern-examples';

const HEIGHT = 60;

/** Галерея схем паттернов: как выглядит каждая фигура и что считается подтверждением. */
@Component({
  selector: 'app-pattern-gallery',
  template: `
    <details class="gallery">
      <summary>Примеры паттернов</summary>
      <div class="grid">
        @for (example of examples; track example.pattern) {
          <figure class="card" [attr.data-pattern]="example.pattern">
            <svg
              viewBox="0 0 90 60"
              role="img"
              [attr.aria-label]="title(example)"
            >
              @for (line of example.lines; track $index) {
                <line
                  class="line"
                  [attr.x1]="line[0]"
                  [attr.y1]="y(line[1])"
                  [attr.x2]="line[2]"
                  [attr.y2]="y(line[3])"
                />
              }
              <polyline
                class="path"
                [class.up]="example.direction === 'bullish'"
                [class.down]="example.direction === 'bearish'"
                [attr.points]="points(example)"
              />
              <circle
                class="break"
                [attr.cx]="example.breakout[0]"
                [attr.cy]="y(example.breakout[1])"
                r="2.5"
              />
            </svg>
            <figcaption>
              <strong>{{ title(example) }}</strong>
              <span class="short">{{ short(example) }}</span>
              <span class="note">{{ example.note }}</span>
            </figcaption>
          </figure>
        }
      </div>
    </details>
  `,
  styles: `
    .gallery {
      margin-bottom: 0.5rem;
      font-size: 0.85rem;
    }
    summary {
      cursor: pointer;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
      gap: 0.6rem;
      margin-top: 0.5rem;
    }
    .card {
      margin: 0;
      border: 1px solid var(--tui-border-normal);
      border-radius: 0.5rem;
      padding: 0.4rem;
    }
    svg {
      width: 100%;
      height: auto;
      background: var(--tui-background-neutral-1);
      border-radius: 0.3rem;
    }
    .path {
      fill: none;
      stroke-width: 1.6;
    }
    .path.up {
      stroke: #26a69a;
    }
    .path.down {
      stroke: #ef5350;
    }
    .line {
      stroke: #f9a825;
      stroke-width: 1.2;
    }
    .break {
      fill: #f9a825;
    }
    figcaption {
      display: flex;
      flex-direction: column;
      gap: 0.1rem;
    }
    .short {
      opacity: 0.6;
      font-family: monospace;
    }
    .note {
      opacity: 0.75;
      font-size: 0.78rem;
    }
  `,
})
export class PatternGallery {
  protected readonly examples = PATTERN_EXAMPLES;

  protected y(value: number): number {
    return HEIGHT - value;
  }

  protected points(example: PatternExample): string {
    return example.path.map(([x, y]) => `${x},${HEIGHT - y}`).join(' ');
  }

  protected title(example: PatternExample): string {
    return PATTERN_TITLES[example.pattern] ?? example.pattern;
  }

  protected short(example: PatternExample): string {
    return shortName(example.pattern);
  }
}
