# TCG Pulse

Tracks the Cardmarket price of every single **Magic: The Gathering** card over time and shows it in
Grafana. Magic is the only game: there is no game column or selector anywhere.

- **Daily sync** (`python -m tcg_pulse sync`): downloads the public price guide and singles list
  (trend, low, averages, normal and foil) into a PostgreSQL +
  TimescaleDB hypertable, one row per card per day.
- **Backfill** (`python -m tcg_pulse backfill`, once): Cardmarket only publishes today's prices, so the
  last ~90 days come from [MTGJSON](https://mtgjson.com), which keeps Cardmarket's daily trend (normal
  and foil). Only trend and trend foil are filled on those days.
- **Rising cards**: after the import, every card priced ≥ 1 € is measured over 14, 28 and 56 days
  (gain, steadiness R², days with a price, rising quarters, largest single-day jump). A window must
  also start at ≥ 1 €: a new card's first prices of a few cents are not a rise. The Rising
  dashboard applies the thresholds (defaults: ≥ 5 €, +15 %, no single-day spikes).
- **Expansion names** are derived from the names of Cardmarket's sealed products ("Bloomburrow Play
  Booster Box" → "Bloomburrow"). Wrong or missing names can be fixed by hand in
  [`tcg_pulse/expansion_overrides.json`](tcg_pulse/expansion_overrides.json) (`{"6381": "Name"}`);
  an expansion with no name shows its id, e.g. `#5229`.
- **Grafana** with two dashboards, provisioned from [`grafana/`](grafana/): **Rising** (pick the
  window, normal/foil, thresholds) and **Card detail** (search a card, its image from
  [Scryfall](https://scryfall.com), price history). Login only.

## Running locally

```sh
cp .env.example .env          # set the passwords
docker compose up -d --build  # db, Grafana on http://localhost:3000, and one sync
docker compose run --rm sync python -m tcg_pulse backfill  # once, after the first sync: ~90 days of history
docker compose run --rm sync  # sync again
```

Grafana reads the database as `grafana_reader`, a role that can only read. The sync's migrations create
it with `TCG_PULSE_DB_READER_PASSWORD` when it is missing — also on a database volume that already
exists — and let it read every table. Changing that password later is done by hand
(`ALTER ROLE grafana_reader PASSWORD '...'`). `GRAFANA_ADMIN_PASSWORD` only applies when the Grafana
volume is first created.

## Backfill

`python -m tcg_pulse backfill` loads the history Cardmarket no longer publishes. Run it **once**, after
the first sync (it stores prices only for the cards that sync listed). It downloads MTGJSON's
`AllPrices.json.xz` (~50 MB, 1.2 GB once decompressed, read as a stream) and its identifiers files, and
takes a few minutes.

- MTGJSON's Cardmarket price of a day is that day's price guide trend: `normal` → `trend`, `foil` and
  `etched` → `trend_foil` (foil first). Checked on 2026-11-01 against Cardmarket's own guide: the same
  value for 100 % of normal and 99.9 % of foil prices.
- Cards are matched through MTGJSON's `mcmId`, Cardmarket's `idProduct`. MTGJSON leaves out about 30 %
  of the singles priced ≥ 1 € — third-party tokens, alters, foreign-only and unofficial printings —
  whose history starts from the first sync.
- Only days with no prices at all, before today, are filled: days imported from Cardmarket are never
  touched, and today is left to the daily sync. Rerunning it fills just the gaps (a day the sync
  missed, within MTGJSON's 90 days). Rising is recomputed at the end.
- Low and the 1/7/30-day averages stay empty on those days, so on Card detail those lines start from
  the first sync.

## Database

The tables are SQLAlchemy models in [`tcg_pulse/models.py`](tcg_pulse/models.py); the schema is managed with
Alembic migrations in [`tcg_pulse/migrations`](tcg_pulse/migrations), which the sync applies on every run. After
changing a model, generate a migration and review it (TimescaleDB specifics, such as the `price`
hypertable, are written by hand in the migrations):

```sh
DATABASE_URL=postgresql://tcg_pulse:tcg_pulse@localhost:5432/tcg_pulse uv run alembic revision --autogenerate -m "Describe the change"
```

A test fails when the models and the migrations disagree.

Tests need a TimescaleDB database; each test works in its own throwaway schema, migrated with Alembic:

```sh
docker compose up -d db
uv run pytest               # TEST_DATABASE_URL defaults to the compose database
```

## Daily sync

`python -m tcg_pulse sync` is a one-shot command meant to run **once a day** from an external scheduler at
about **04:30 Europe/Rome**, after Cardmarket publishes the guides (~02:45).

- A failed import — network error, or a guide still dated yesterday — is retried up to **4 attempts,
  15 minutes apart** (about 45 minutes at most).
- The command exits non-zero if the import still failed, so the scheduler can report it.
- Idempotent: a day fully imported (prices, rising, names) is skipped, so a manual rerun only
  does what is missing.
- Every attempt is logged in the `import_run` table (shown on the Rising dashboard as "Last import").
- Every downloaded file is kept gzipped under `DATA_DIR/<date>/` (a few MB a day). To import an
  archived day again, replacing that day's prices (e.g. after fixing an import bug):
  `python -m tcg_pulse reimport --date 2026-11-01`.

## Production

- A new, empty PostgreSQL database with the TimescaleDB extension. The sync applies the Alembic
  migrations on every run.
- The migrations also create the read-only `grafana_reader` role, with `TCG_PULSE_DB_READER_PASSWORD`.
  Only if the sync's database user may not create roles (a managed database, or a deliberately
  restricted user), create it once by hand — `CREATE ROLE grafana_reader LOGIN PASSWORD '...';` — and
  the migrations just grant it read access.
- Run the image (`ghcr.io/<owner>/tcg-pulse`, default command `python -m tcg_pulse sync`) once a day with
  `DATABASE_URL` (and `TCG_PULSE_DB_READER_PASSWORD`, see above) set and a persistent volume on `/app/data`.
  After the first sync, run the same image once with `python -m tcg_pulse backfill`.
- Run Grafana with the files in `grafana/provisioning` mounted on `/etc/grafana/provisioning`, the
  dashboards in `grafana/dashboards` on `/var/lib/grafana/dashboards`, and the variables below.
  Disable anonymous access and sign-up as `docker-compose.yml` does; HTTPS is terminated by the reverse
  proxy in front of it.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `DATABASE_URL` | sync | PostgreSQL URL; the database needs the TimescaleDB extension |
| `DATA_DIR` | sync | where downloaded files are archived (`/app/data` in the image) |
| `POSTGRES_PASSWORD` | compose | password of the `tcg_pulse` database user |
| `TCG_PULSE_DB_HOST`, `TCG_PULSE_DB_NAME` | Grafana | database host and name for the datasource |
| `TCG_PULSE_DB_SSLMODE` | Grafana | SSL mode of the datasource connection (default `disable`) |
| `TCG_PULSE_DB_READER_PASSWORD` | sync, Grafana | password of the `grafana_reader` role; the sync creates the role with it when missing |
| `GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD` | compose | the Grafana login (`GF_SECURITY_ADMIN_*`) |
