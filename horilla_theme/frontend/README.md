# horilla_theme frontend build

Builds the theme's UX polish layer. The output is committed, like every other
static asset in this repo, so running the app needs no Node toolchain:

| Source              | Built file (committed)                          | Loaded by                                  |
| ------------------- | ----------------------------------------------- | ------------------------------------------ |
| `src/ux.css`        | `static/horilla_theme/assets/css/ux.css`        | `index.html`, `auth_layout.html`           |
| `src/ux-motion.js`  | `static/horilla_theme/assets/js/ux-motion.js`   | `horilla_theme/components/footer_scripts.html` |

- `ux.css` - Tailwind CSS v4 (CLI). Design tokens only (namespaced `--hx-*`),
  no preflight and no generated utility classes, so it cannot clash with the
  existing `tailwind.css` / Tailwind v3 runtime. See the header of
  `src/ux.css` for the rules it follows.
- `ux-motion.js` - Motion (motion.dev) `motion/mini`, bundled by esbuild into
  one IIFE. Dialog and dropdown entrances and the htmx loading bar, all off
  under `prefers-reduced-motion`.

## Rebuild

```sh
cd horilla_theme/frontend
npm ci
npm run build        # or build:css / build:js; watch:css while editing
```

Then bump the `?v=` query on the matching `<link>` / `<script>` tag so
browsers drop their cached copy (WhiteNoise serves unhashed filenames).
