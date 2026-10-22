# Backup and recovery

No managed services have been provisioned in this project environment, so backups and restoration have not been configured or tested.

## Owner checklist before production

- Enable managed PostgreSQL automated backups and point-in-time recovery; choose retention and recovery objectives appropriate to the research team.
- Enable object versioning or an equivalent immutable backup policy for source documents and selected intermediate artifacts.
- Treat Qdrant as a rebuildable index, but retain snapshots if rebuild time/cost requires it. Preserve collection/model/version configuration.
- Encrypt backups at rest and in transit; restrict restore access; store backup credentials outside the application.
- Document who can authorize a restore and who verifies restored data.
- Perform a restore drill into a separate environment before ingesting the full corpus and record duration, row/object counts, and reconciliation results.

## Recovery principles

PostgreSQL, object storage, and Qdrant do not share a distributed atomic transaction. Restore PostgreSQL and source objects first, then rebuild or reconcile Qdrant using stable vector IDs and recorded embedding model/version metadata. Never assume a successful restore merely because a snapshot exists.

If a document job failed, later worker tooling should resume from a durable completed checkpoint rather than repeat extraction or question generation unnecessarily. If a Qdrant write succeeded but a status update failed, retry the idempotent upsert with the same vector ID. If object metadata and the actual object disagree, quarantine and reconcile; do not silently delete approved records.

The initial schema has checkpoints and restrictive foreign keys but does not yet implement restore automation, reprocessing, or service-to-service reconciliation.
