# TitleTrack front end

React (Vite) app for the TitleTrack API. Needs **Node 18 or newer** (Node 20 LTS recommended).

```bash
npm install          # once
npm test             # 71 tests, no server needed
npm run build        # production build into dist/
npm run dev          # http://localhost:5173  (needs the Django server on port 8000)
```

- `npm run dev` proxies every `/api/...` call to Django on `127.0.0.1:8000`, so the browser sees one origin and the session and CSRF cookies just work. **Do not add `changeOrigin: true` to the proxy** in `vite.config.js`: Django's CSRF check would then reject every POST.
- `src/utils/permissions.matrix.json` is GENERATED from the backend. After changing `api/permissions.py` run `python manage.py export_permissions` in the Django project (a backend test fails if the file is stale).
- `src/test/fixtures/*.json` are real API responses captured from the demo data. The component tests run against them, so a change in the API's response shape shows up as a failing front-end test. Re-capture them when you deliberately change a response.
