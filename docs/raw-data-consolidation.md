# Unified raw archive

The existing `a-share-backtest/v1/` namespace remains the live archive and is not deleted or modified. The consolidation copies verified source objects to `a-share-backtest/01_raw_data/` with nine dataset folders. Binary payloads are byte-identical; JSON manifests change only archive path references. Original manifests remain in the legacy namespace. Each group has a receipt under `_inventory/` with source and destination SHA-256 and size. The final `dataset_catalog.json` is published only after all groups and the complete inventory pass verification.

The job does not call market data providers, convert formats, build stock-level files, alter PostgreSQL, or change website readers. Restart the workflow to reuse byte-identical destination objects. A mismatched existing destination fails closed. Future writer/reader cutover and source cleanup require separate validation. The consolidated snapshot is not automatically kept in sync until that cutover.
