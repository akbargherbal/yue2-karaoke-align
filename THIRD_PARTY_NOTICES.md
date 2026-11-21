# Third-party notices

This repository is MIT-licensed (see [LICENSE](LICENSE)). The following files
are adapted from another MIT project.

## score-lab

- Upstream: https://github.com/akbargherbal/score-lab
- License: MIT, Copyright (c) 2026 Akbar Gherbal
- Adapted here:
  - `scripts/score_viewer.py`
  - `scripts/view_run.py`
  - `scripts/score_player-template.html`

The adaptation keeps score-lab's batch discovery (expanded dirs, `.tar.gz`/
`.zip` archives, `gs://` audio fetch), its index gallery, and its musical lanes
(waveform, bars, chords, sections, piano roll, score-page sync). It adds the
karaoke lane and the A1 `<song>.words.json` input, and makes score pages
optional (`--no-pages`).

The upstream MIT text:

```text
MIT License

Copyright (c) 2026 Akbar Gherbal

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
