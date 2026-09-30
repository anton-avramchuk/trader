import { Component, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { TuiRoot } from '@taiga-ui/core';
import { TuiNavigation } from '@taiga-ui/layout';
import { navEntries } from './nav';

@Component({
  imports: [RouterLink, RouterLinkActive, RouterOutlet, TuiNavigation, TuiRoot],
  selector: 'app-root',
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly entries = navEntries();
  protected readonly expanded = signal(true);
}
