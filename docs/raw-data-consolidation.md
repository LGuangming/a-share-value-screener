# Unified raw archive

The existing `a-share-backtest/v1/` namespace remains the live archive and is not deleted or modified. The consolidation copies verified source objects to `a-share-backtest/01_raw_data/` with nine dataset folders. Binary payloads are byte-identical; JSON manifests change only archive path references. Original manifests remain in the legacy namespace. Each group has a receipt under `_inventory/` with source and destination SHA-256 and size. The final `dataset_catalog.json` is published only after all groups and the complete inventory pass verification.

The job does not call market data providers, convert formats, build stock-level files, alter PostgreSQL, or change website readers. Restart the workflow to reuse byte-identical destination objects. A mismatched existing destination fails closed. Future writer/reader cutover and source cleanup require separate validation. The consolidated snapshot is not automatically kept in sync until that cutover.

## Reader and writer cutover

Active historical readers and collectors now use `a-share-backtest/01_raw_data/`. Daily market refresh continues to use PostgreSQL as before; historical completed-year exports use the new object namespace. `raw-data-cutover-validation.yml` re-verifies the migrated catalog and performs a 2025 engineering backtest without legacy reads. The old consolidation workflow is migration-only and must not be rerun after cutover. Keep the old archive until production deployment and API verification, daily refresh, full destination validation, and dependency audit succeed. Do not remove the source based only on a passing build.

## Legacy retirement

The one-time migration workflow has been retired. The retirement workflow checks active main source and workflow paths, checks live production API responses, validates the exact migration inventory and both source and destination bytes, preserves byte-exact changed original JSON manifests in a compressed private backup, and only then removes the exact legacy objects. The immutable original catalog is a historical migration receipt; `_inventory/legacy_retirement.json` records the current retirement status. Pre-cutover deployments or branches must not be used for rollback or old workflow reruns after retirement; use a post-cutover commit. The new namespace is never deleted by retirement.
