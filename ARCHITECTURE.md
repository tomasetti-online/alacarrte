# ALACarrte architecture

What lives where, who owns each piece of state, and how data flows. A later
pass should build with this structure, not around it.

## Modules

| Module      | Owns                                                    | Knows about        |
|-------------|---------------------------------------------------------|--------------------|
| `config.py` | Every tunable: env-config reading, defaults, `DATA_DIR`, caps, intervals, secrets | nothing            |
| `state.py`  | Process-wide mutable state: `tasks`, `LOCK`, `GLOBAL_SLOTS`, `rate_map`, VPN/MB flags | `config`           |
| `library.py`| Persisted history (`library.json`), dedup match policy, batch summary. **Single owner of "does a track match something already downloaded?"** | `config`           |
| `engine.py` | Download core: `run_ytdl`, `search_musicbrainz`, `do_download`, `process_track`, per-track retry, retry-failed worker, `bg_cleanup`, text utils | `config`, `state`, `library` |
| `app.py`    | Thin Flask layer: routes, session, request middleware, rate limiting. Only marshals HTTP <-> the modules above | `config`, `state`, `library`, `engine` |

## State ownership

- **`state.tasks`** — the live task registry; mutated under `state.LOCK`. Only
  `engine` writes task fields; routes read.
- **`state.GLOBAL_SLOTS`** — concurrency semaphore; only `engine` acquires.
- **`state.rate_map`** — per-IP request timestamps; only `app` (rate limiting) touches.
- **`library.json`** — persisted history; only `library` reads/writes it.
- **The dedup match rule** lives in ONE place: `library._matches` (recording-id
  preferred, else normalized artist+title). `find_library_duplicates` and the
  replace-prune in `_record_library` both call it. **Do not reimplement this
  rule anywhere else.**

## Data flow

1. `app.api_info` / `app.api_channel` / `app.api_download` call
   `engine.run_ytdl` for metadata and build a task in `state.tasks`.
2. `api_download` consults `library.find_library_duplicates` before starting
   (Skip → 409, Replace → proceed, task flagged `_replace`).
3. `engine.do_download` spawns `process_track` threads; each thread finishes by
   setting `done` **or** `error` — both are terminal, so a batch always completes.
4. On batch completion `engine` calls `library._record_library` (persists entry,
   caches cover, prunes replaced matches).
5. `app.api_status` calls `library._batch_summary` for the end-of-batch summary.
6. `engine.bg_cleanup` (started once from `app`) spares any tid in
   `library.library_tids()`.

## Conventions

- `engine` and `library` must stay Flask-free; only `app` imports Flask.
- `state` holds singletons, not logic; `config` holds tunables, not logic.
- Keep the three helpers (`slugify`, `clean_title`, `_norm_key`) where they
  currently live; don't move them into `app`.
- `tests/conftest.py` owns the shared mocks/fixtures — copy nothing new into a
  test file.
