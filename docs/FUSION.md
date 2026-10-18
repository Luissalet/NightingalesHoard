# Nightingale and Laplace

Nightingale now owns the complete native workbench and calculation modules in one ASGI
server. `python -m nightingale` starts `fusion.app:create_app`; the existing
`nightingale.main:create_app` remains the native workbench factory for internal use.
The shell imports neither engine while discovering tools. Data and Calculation are
independent switches, both OFF on the first installation or migration.

## Lifetimes and data

`data/modules.json` records `enabled` and `data_paths`. Each data path may point to its
existing directory; adopting a directory never copies, moves or converts its contents.
The default Data path is the existing Nightingale data directory. Calculation defaults
to its own subdirectory until an explicit mapping adopts the existing Laplace directory.
QA uses synthetic directories only. No watcher or model starts from discovery.

Switching OFF is persisted before shutdown, waits for in-flight requests, cancels live
streams, drains the workbench executor, stops the calculation worker and closes databases.
Calculation opens its DuckDB catalog only on the first dataset operation; arithmetic,
units, symbolic math and dates do not load the workbench or DuckDB.

The former Laplace `target="nightingale"` operations dispatch directly inside this
ASGI process and require Data to be enabled. They do not call another server or start Data.

## Tools and UI

`fusion/catalog.json` is the immutable discovery catalog. Run
`python -m fusion.build_catalog` after changing native tool definitions. Existing
Nightingale names retain their names. Laplace collisions gain the `laplace_` prefix;
the `native_name` field records the dispatch name. All schemas remain those of the
native implementations. The shell adds `nightingale_modules` and `nightingale_set_module`.
The stdio bridge reads only the catalog and never starts a server.

Data is available under `/data/`, Calculation under `/calculation/`. Both full native
interfaces retain their screens, operations and settings. The family shell adds the
common Faustus control, module navigation and existing Nightingale icon.

Build Data with `npm ci && npm run build` at the root. Build Calculation with
`npm ci && npm run build` in `fusion/calculation-ui`. Its build output is the native
`fusion/calculation-static` directory. Module-relative assets/API/downloads/service workers
use explicit Vite bases and source-level API/download prefixes. JavaScript and CSS
are served byte-for-byte; the ASGI response adapter decorates HTML and scopes the
manifest/service worker. Neither UI needs the absorbed repository.

## Provenance

- Nightingale baseline: `76193ba`.
- Laplace native API, engines, UI source and documentation: `b55bbc41a04d4df6f3c43b4771b126a205a0179a`.
  Its license is retained in `fusion/LAPLACE-LICENSE` and documentation in `fusion/laplace-docs`.
- Lifecycle shell and streaming adapter: Talos `eb31ce75699e356d7fa2aad64a0fe87be3e76274`,
  adapted to two modules with Talos-specific branches removed.
- Laplace's family SDK loads the same bundled Nightingale SDK source into a separate
  module namespace, preserving its per-app token/identity globals without a second
  SDK copy or external repository.

Original collected records, citations, calculation logs, datasets, recipes, charts,
dashboards and models stay in their adopted native directories and formats.
