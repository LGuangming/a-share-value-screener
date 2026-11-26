# Retired GHCR workflows (historical only)

These six exact original workflow definitions were moved out of .github/workflows after production VPS cutover. They are not executable Actions workflows. Do not restore them unchanged: they target historical source revisions and registry recovery assumptions. Original revision: c6064b1d5c0246bdeae3aea5a283c2c4450b67c0.

The active CI publishes only with explicit main-push markers [publish-test-web] or [publish-worker], after current-revision tests and Docker acceptance. The archived bootstrap/readiness/publisher offline safety tests still run in ci.yml with updated file paths; historical API fixture paths remain unchanged. Existing images, package access, registry credentials, deployment settings and VPS resources are unaffected.

## Completed original Worker recovery
The one-time recovery job is preserved as worker-recovery-job.yml.txt (not an executable workflow). The actual acceptance result was observed in run 45763923520, job 139720527008, on 2026-11-26 05:49:29 UTC:
- Source revision: aca1c37bf348d396ef4c4305b95d71e4e5fbf902
- Immutable reference: ghcr.io/lguangming/a-share-value-screener-worker-test-pr28@sha256:0eff99f41d06b444a623f0a6bad776cb71ee141e022b98708c4189c803380139
- Daemon image ID: sha256:0eff99f41d06b444a623f0a6bad776cb71ee141e022b98708c4189c803380139
- Config SHA256: sha256:b6e48efa642c36d74ada3d3107cb212ada9c5c16694512d020e855bd20bf8952
- Verified layers: 28; release_approved: true
- Original receipt artifact: 15090274584, worker-recovery-acceptance, ZIP SHA256 bc97ae1bf4066ce278ef8fcd7667d1e083c15bf92c51b5cad375efb2598dfd83 (expires 2026-12-26 05:49:29 UTC).
- Logs: https://github.com/LGuangming/a-share-value-screener/actions/runs/45763923520/job/139720527008

This summary records the observed result, not a replacement for the original receipt. Retain/download actual acceptance artifacts separately. Generic image identity verification remains in tests/approve_worker_identity.py and is used in current Worker publication.

Historical GHCR documents in docs/ describe prior migration stages, not current operating instructions. For current local maintenance see ../LOCAL_MAINTENANCE_1PANEL.md.
