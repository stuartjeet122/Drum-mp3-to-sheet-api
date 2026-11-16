# Backbeat Drum Studio

A responsive dark-mode workspace for the existing drum transcription API.
React/TypeScript, Vite, Tone.js, @tonejs/midi and PDF.js. Fonts and
the PDF worker are bundled locally; no CDN, analytics or third-party audio
uploads are used. The existing Python API and Docker stack are unchanged.

## Local Development

Use Node 22.12+ and npm. From this directory:

```sh
npm ci
npm run dev
```

Open http://127.0.0.1:5173/drum-studio/. The development server proxies
`/drum-sheet-api` to `https://stuserver.online`, avoiding browser CORS changes.
That hostname must resolve and be reachable from the machine running Vite.
An unreachable API shows an offline state; the built-in demo and interactive
drum pads still work. Use the recordings refresh button after reconnecting.

The production default also calls `/drum-sheet-api` on the site's own origin.
To use another API, set `VITE_API_BASE_URL` before building. It is the API prefix
without the final `/api`, for example `https://example.com/drum-sheet-api`.
This setting is public, not a place for API keys. Cross-origin deployment needs
the API's `CORS_ORIGINS` set to the frontend's exact origin. Same-origin
deployment is recommended, especially behind an authenticated reverse proxy.

## Workflow

- Upload and playback/score viewing are separate pages. The selected job is
  retained in the URL fragment so its score can be opened or refreshed directly.
- Pick or drop WAV, MP3, FLAC or AIFF audio up to 100 MiB, then create the score.
- Upload progress comes from the actual network transfer. The app separately
  starts inference, then polls committed job status without overlapping polls.
- Pipeline stages, failures and retry actions use the API's actual responses.
  A queue failure retries the existing uploaded job, not a duplicate upload.
- Recent server jobs can be reopened. The last selected job ID is retained in
  the URL fragment and browser storage; the audio itself is not stored there.
- Completed PDF scores render locally with page navigation and zoom. Current
  PDF, MIDI and MusicXML downloads are only enabled when the API reports them.
  A missing optional PDF does not hide the other formats.
- The exported MIDI drives a sample-based acoustic kit and a top-down
  illustration with per-hit impact ripples. Kick and snare use soft, medium,
  and hard layers; other drums use round-robin variations. Play/pause, restart,
  seek, loop, mute and speed controls are local;
  they do not submit processing jobs. The built-in groove is explicitly a demo.

The current API expects an **isolated drum stem**. This frontend does not add
source separation for mixed songs. The top-down kit is a simplified eight-piece
GM percussion visualization, not a promise of instrument-detection accuracy.
Playback uses a compact subset of DrumThrash's DT-Natural free drum recordings;
see `public/samples/drums/CREDITS.txt` for the source and included usage note.

## Deploy Beside the Existing API

The production site consists only of static files. No persistent Node service,
additional API container, Redis instance or Portainer stack is required.
The existing Portainer updater does **not** deploy this frontend automatically.
Publish these source files to Git before pulling on Ubuntu.

After pulling `/opt/drum-docker`, build with installed Node 22.12+ using
`npm ci` and `npm run build` in `frontend/`. Alternatively, use the existing
Docker installation for an isolated, temporary build:

```sh
sudo docker run --rm --memory=2g --cpus=1 \
  -v /opt/drum-docker/frontend:/work -w /work \
  node:22-bookworm-slim sh -c 'npm ci && npm run build'
```

Copy the contents of `frontend/dist/` into a dedicated static directory:

```sh
sudo install -d -m 0755 /var/www/backbeat
sudo cp -a /opt/drum-docker/frontend/dist/. /var/www/backbeat/
```

Add only these locations to the **existing** HTTPS Nginx server for
`stuserver.online`. Keep the site's other locations and its existing
`/drum-sheet-api/` reverse proxy unchanged:

```nginx
location = /drum-studio {
    return 308 /drum-studio/;
}

location ^~ /drum-studio/ {
    alias /var/www/backbeat/;
    index index.html;
    include /etc/nginx/mime.types;
    types { application/javascript mjs; }
    add_header X-Content-Type-Options nosniff always;
    add_header Cache-Control "no-cache" always;
}
```

Serve `.mjs` files as JavaScript: the bundled PDF worker uses this extension.
Validate with `sudo nginx -t`, then reload Nginx only after that succeeds.
Open https://stuserver.online/drum-studio/. If using a proxy management UI,
apply the equivalent static mapping there rather than overwriting generated
configuration. Protect the frontend **and** API consistently; the backend has
no built-in authentication or per-user recording isolation.

For subsequent frontend updates, rebuild and copy the new static files, keeping
older hashed assets until active browser sessions have finished. The commands
above do not delete older assets. The Vite development server is not a
production web server. The API updater is only needed when backend code changes.

## Verification

```sh
npm test
npm run build
npm run test:e2e
```

Browser tests use installed Google Chrome by default. For Edge set
`PLAYWRIGHT_CHANNEL=msedge`; for bundled Chromium install it with
`npx playwright install chromium` and set `PLAYWRIGHT_CHANNEL=chromium`.
Tests mock the management API and never submit real inference jobs. They cover
upload/start/poll/completion, queue retries, MIDI parsing and playback controls,
PDF rendering and mobile zoom, session restoration, export links, and responsive
top-down kit rendering at 320, 390, 768 and 1440 pixels. Screenshots are saved under
the ignored `test-results/` directory.

The live API is not reachable through DNS from the current development machine.
Production Nginx configuration and a fresh real-audio upload still need testing
on the server after deployment. Successful browser tests are not a transcription
accuracy or audio-fidelity benchmark.