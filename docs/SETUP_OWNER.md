# Owner setup

This guide describes prerequisites only. The repository does not create cloud projects, provision infrastructure, or issue a database username/password.

## Create shared services

1. Choose a managed PostgreSQL provider that supports concurrent clients, TLS, automated backups, and your organization's data requirements.
2. Create a Qdrant deployment and an S3-compatible storage bucket in appropriate regions.
3. Enable encrypted connections. Use HTTPS endpoints for remote Qdrant and TLS for remote PostgreSQL and object storage.
4. Create named service credentials with minimum required permissions. Keep administrative credentials with the owner; do not give them to the researcher group.
5. Distribute researcher credentials with an approved secrets manager or individual provider identities. Never put credentials in Git, chat, or the project files.
6. Before adding production data, configure a migration-capable database role, backup configuration, and recovery checks.

## Where to obtain the connection details

The following are provider examples, not accounts already created by this project:

- **PostgreSQL (recommended starting point: Neon):** create a project at [Neon Console](https://console.neon.tech/). Select **Connect** in the project console, choose the branch, database, and role, and copy the PostgreSQL connection string. The migration role needs schema/table creation rights. Do not share an owner/admin URL with all researchers; create least-privilege application/researcher roles after the migrations and role policy are finalized. See [Neon's connection-string guide](https://neon.com/docs/connect/connect-from-any-app).
- **Object storage (example: Cloudflare R2):** create a bucket in [Cloudflare Dashboard](https://dash.cloudflare.com/), then open **R2 Object Storage → Manage R2 API Tokens**. Create a token with **Object Read & Write** scoped to the one bucket. Copy its Access Key ID and Secret Access Key at creation; the secret may not be shown again. The endpoint is `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`; bucket name and account ID are not secret. See [Cloudflare R2 S3 authentication](https://developers.cloudflare.com/r2/api/tokens/). AWS S3 or another S3-compatible provider can be used instead.
- **Vector service (Qdrant Cloud):** create a cluster in [Qdrant Cloud](https://cloud.qdrant.io/), then copy the cluster URL and create an API key with only the necessary collection permissions. See [Qdrant Managed Cloud](https://qdrant.tech/documentation/cloud/) and [Qdrant authentication](https://qdrant.tech/documentation/cloud/authentication/). Qdrant is not yet used by the current ingestion command.

Credential placement: open this project locally, copy `.env.example` to `.env`, and fill the relevant values from each provider dashboard. `.env` is gitignored. Do **not** paste the connection string, passwords, API keys, or secret access keys into chat, screenshots, tickets, or Git. Only non-secret provider choice/status should be shared here. If a secret was exposed, revoke and rotate it in the provider dashboard.

## Configure the application

Copy `.env.example` to a local `.env` and supply the provider values privately. Set `DATABASE_URL`, `QDRANT_URL`, and the S3 bucket and credentials. A remote Qdrant or S3-compatible endpoint must use HTTPS. The database probe requires TLS for remote connections and applies `sslmode=require` unless a stronger verification mode is configured.

Run the probes separately:

```sh
PYTHONPATH=src python -m cis_fin.cli check-db
PYTHONPATH=src python -m cis_fin.cli check-qdrant
PYTHONPATH=src python -m cis_fin.cli check-storage
```

These are read-only tests; they do not validate schema grants or run migrations. After confirming the target database, inspect and apply the schema separately:

```sh
PYTHONPATH=src python -m cis_fin.cli migrate --dry-run
PYTHONPATH=src python -m cis_fin.cli migrate --apply
```

`--apply` creates the initial schema inside a PostgreSQL transaction. It requires schema-creation rights. Run it only against the intended database, using the owner-controlled migration credential. Researcher-level roles and the app's enforcement of those roles are not yet implemented.

If `migrate --dry-run` reports that an applied migration checksum differs, stop. The runner intentionally refuses to change the database. Restore the exact applied SQL from its original deployment copy or have a PostgreSQL administrator compare the live schema and plan a reviewed forward-only reconciliation migration. Do not modify/delete `schema_migrations` records or disable checksum checking.

For the deployment whose `0001` record is exactly `89ae1aa9d925a923ac02dec72ff147011964cbf71a7532d7f7d6097dc33f660f`, the runner includes a narrow compatibility entry and verifies the expected core columns before considering later migrations. This is not a general bypass; any other historical checksum mismatch still fails closed.

## Accounts and team size

No credentials can be generated from this repository. A human owner must create the central database and accounts. The initial rollout target is five researchers; the schema permits future growth and does not hard-code individual names. Authentication and role enforcement remain to be implemented. Do not use one shared administrator login for all researchers.

The next live validation requires the owner to provision the database and bucket, place their connection details in local `.env`, and run the tests below. Qdrant credentials are not required until the vector-index phase.

No company list is automatically seeded. Use the local Streamlit **Add a company from verified constituent data** form after opening the app, or `cis-fin add-company` from the CLI. Provide the official list source/version and effective date, plus approved sector and industry values. The insertion is audited and existing symbols are never overwritten. This local UI has no role authentication; only use it on the owner's machine.
