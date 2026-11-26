# Retired cloud data workflows

The VPS Worker and 1Panel replace these nine GitHub Actions data workflows. Files in this directory are exact archived originals, not active GitHub Actions definitions. Do not copy them into .github/workflows or invoke them while VPS data jobs are active. In particular, the archived definitions include scheduled, workflow_run and one-shot push triggers; they are unsafe to restore unchanged.

Rollback requires explicit approval, stopping and verifying VPS writers are idle, checking cloud data freshness and credentials, and restoring only reviewed manual triggers through a dedicated PR. One-shot markers are retired. Do not reactivate simultaneous data writers. The original files are also preserved in Git history at 96c5fb1f5f2f564139c730c4cd8198e6efb509aa.

Retain production and rollback image digests, VPS database/archive backups, CI and security tests. This retirement does not delete Neon, Vercel, VPS data, credentials, registry packages or deployment settings. History/ST collection modes still require separate live-provider validation.
