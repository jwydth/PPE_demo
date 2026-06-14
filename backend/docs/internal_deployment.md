# Internal Deployment

## Data Boundaries

The internal deployment has no external database dependency:

- Structured records stay in internal PostgreSQL.
- PPE and zone evidence stay in internal MinIO.
- The backend stores MinIO object keys in PostgreSQL.
- Local snapshot files are temporary staging files, not the evidence archive.

The application architecture remains unchanged. Repositories and services use
the configured PostgreSQL and MinIO endpoints.

## Single-Host Development

When PostgreSQL, MinIO, and the backend run on the same workstation with the
backend outside Docker:

```dotenv
DATABASE_URL=postgresql+psycopg://safety_user:<password>@localhost:5432/safety_monitoring
MINIO_ENDPOINT=localhost:9000
MINIO_SECURE=false
```

Start infrastructure from the project root:

```powershell
docker compose up -d
```

## Company LAN Deployment

When PostgreSQL and MinIO run on an internal server, replace `localhost` with
the private DNS name or LAN address:

```dotenv
DATABASE_URL=postgresql+psycopg://safety_user:<password>@safety-infra.internal:5432/safety_monitoring
MINIO_ENDPOINT=safety-infra.internal:9000
```

Restrict ports `5432`, `9000`, and `9001` with host firewalls and network
policy. The MinIO console should be available only to administrators.

Use:

- Strong unique credentials
- Internal DNS
- TLS where traffic crosses hosts
- Persistent, backed-up storage
- A container restart policy
- Company secret management rather than committed `.env` files

## Future Dockerized Backend

If the backend is added to the same Compose network, container-to-container
connections must use service names rather than `localhost`:

```dotenv
DATABASE_URL=postgresql+psycopg://safety_user:<password>@postgres:5432/safety_monitoring
MINIO_ENDPOINT=minio:9000
```

Inside a backend container, `localhost` refers to that backend container, not
the PostgreSQL or MinIO containers.

## Initialization and Health

Initialize a new database from the backend environment:

```powershell
python -m app.db.init_db
```

Verify:

```powershell
Invoke-RestMethod http://localhost:8000/health/db
Invoke-RestMethod http://localhost:8000/health/storage
```

## Operational Notes

- `docker compose down` preserves named-volume data.
- `docker compose down -v` permanently deletes local database and object data.
- PostgreSQL backups and MinIO volume/object backups are separate concerns.
- Database row deletion does not currently remove the corresponding MinIO
  object.
