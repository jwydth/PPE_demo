# Storage Architecture

The backend separates structured records from evidence files.

## PostgreSQL

PostgreSQL is the source of truth for structured metadata:

- Cameras and source identifiers
- Camera calibration
- Zone definitions and normalized coordinates
- PPE violation records
- People associated with PPE violations
- Zone violation records
- MinIO object keys for evidence snapshots

PostgreSQL is accessed through SQLModel repositories and services. SQLite is
not used by the current application.

## MinIO

MinIO stores binary evidence:

- PPE violation snapshots
- Zone violation snapshots
- Future generated reports

The configured bucket is created automatically when storage is first checked
or used. The default example bucket name is:

```text
safety-monitoring-evidence
```

### Object key conventions

PPE snapshots:

```text
ppe-violations/YYYY/MM/DD/<uuid>.jpg
```

Example:

```text
ppe-violations/2026/06/06/95fa6b85d8514ad597ad33f140f89c63.jpg
```

Zone snapshots:

```text
zone-violations/YYYY/MM/DD/<uuid>.jpg
```

Example:

```text
zone-violations/2026/06/06/8c49ca66a7d24619aca28fbf83dc577a.jpg
```

Reports:

```text
reports/YYYY/MM/<uuid>.pdf
```

Example:

```text
reports/2026/06/6aa91f34e82c43c6a9fe57d19132dfe6.pdf
```

The database stores the object key, not the temporary presigned URL. API
services generate a fresh MinIO URL when records are read.

## Local Snapshot Workspace

The directory configured by `SNAPSHOT_DIR` defaults to:

```text
backend/storage/snapshots
```

Video processing creates annotated snapshots locally before uploading them to
MinIO. This directory is a workspace and compatibility location, not the
authoritative evidence store.

Current behavior:

- Zone snapshots are removed locally after successful MinIO and PostgreSQL
  persistence.
- PPE snapshots are uploaded to MinIO, but local cleanup is not currently
  guaranteed.
- Existing local files are retained.
- `/snapshots/{filename}` remains mounted for compatibility with older paths.

New application records use PostgreSQL and MinIO. Team members should not use
the local directory as a database or permanent evidence archive.

## Why Images Are Not Stored in PostgreSQL

Keeping images in MinIO provides several benefits:

- PostgreSQL remains focused on searchable structured data.
- Database backups remain smaller and faster.
- Large files do not increase normal query and replication load.
- MinIO is designed for object storage, streaming, retention, and large-file
  access.
- Evidence can be organized with stable object keys.
- API access can use temporary presigned URLs without exposing MinIO
  credentials.

PostgreSQL stores enough metadata to find each object:

```text
zone_violations.snapshot_path
    -> zone-violations/2026/06/06/<uuid>.jpg
```

## Persistence Flows

PPE violation:

```text
PPE detection
  -> annotated local snapshot
  -> MinIO upload
  -> PPE violation and subject rows in PostgreSQL
  -> frontend-compatible API response
```

Zone violation:

```text
Zone breach
  -> annotated local snapshot
  -> MinIO upload
  -> zone violation row in PostgreSQL
  -> local temporary snapshot removal
  -> frontend-compatible API response
```

Zone configuration:

```text
Frontend zone JSON strings
  -> service validation and JSON parsing
  -> cameras and zones tables in PostgreSQL
```

## Deletion Behavior

Deleting a violation currently removes its PostgreSQL row. It does not delete
the corresponding MinIO object.

Deleting a zone does not delete historical zone violations. PostgreSQL sets
their `zone_id` to `NULL`, while `zone_name`, source, timestamp, frame, and
snapshot object key remain available.
