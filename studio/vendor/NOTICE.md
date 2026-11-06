# Vendored third-party code

## AG Grid Community 34.3.1 (MIT)

- Source: npm `ag-grid-community@34.3.1` (https://www.ag-grid.com), tarball SHA-256
  `75c1c238511103d3ca982825d0142c756450ad170e4573c1591f263c9a74581d`
- Served from `studio/ui/static/vendor/` (CSP stays `script-src 'self'`); license in
  `AG-GRID-LICENSE.txt`. Used by the `/money` page (`studio/ui/static/money.js`).

| file | from the package | SHA-256 |
| --- | --- | --- |
| `ag-grid-community.min.noStyle.js` | `dist/ag-grid-community.min.noStyle.js` | `3709c83baf68d30a67612071d8722a61c204808fa1c846e45f7de488dfd3b166` |
| `ag-grid.min.css` | `styles/ag-grid.min.css` | `ad354d34bbc55949d7c8f92cf38c3d62b35f8e9705c2959aec47869373b4ffcd` |
| `ag-theme-quartz.min.css` | `styles/ag-theme-quartz-no-font.min.css` (renamed; no web font) | `9d50348aa99b9634c216cb14fd6cd1c9670ff705a47b039f69cc5c6e4675b058` |
| `AG-GRID-LICENSE.txt` | `LICENSE.txt` | `1cb5ddc0b89c5bf0c5ccdf3a387edc4a1408c922968d633c719265c8ccdd354b` |

No local edits. The `noStyle` build is used because the standard build injects ~14 legacy-theme
`<style>` elements at load, which the CSP blocks. The grid runs with `theme: "legacy"` and these static CSS files, because the
page's `style-src 'self'` forbids the `<style>` elements the Theming API would inject.
`studio/ui/static/money.css` maps the studio's light/dark tokens onto the grid.

The PayPal Agent Toolkit is vendored in `paypal_agent_toolkit/` (see its `NOTICE.md`).
