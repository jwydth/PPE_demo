# Storage Architecture

The backend separates structured records from evidence files.

## PostgreSQL

PostgreSQL is the source of truth for structured metadata:

- Factories, cameras, and source identifiers
- Physical factory zones and optional floor-plan polygons
- Per-camera views of physical zones and their normalized detection polygons
- PPE violation records
- People associated with PPE violations
- Zone violation records
- MinIO object keys for evidence snapshots

PostgreSQL is accessed through SQLModel repositories and services. SQLite is
not used by the current application.

The database design is documented in `database_schema.dbml`. It separates a
real factory area (`physical_zones`) from the polygon used to detect that area
in one camera (`camera_zone_views`). A physical zone can be monitored by many
cameras, and a camera can monitor many physical zones. Detection uses
`camera_zone_views.normalized_coordinates`; reporting and history group
incidents by `physical_zones`.

The current backend implements the DBML persistence model for factories,
cameras, physical zones, camera-zone views, PPE violations, PPE violation
subjects, and zone violations. The DBML `users` table is approved for a later
auth/report-recipient phase; auth is not implemented in the current backend.
Behavior violations are deferred until danger behavior detection exists.

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
MinIO. This directory is temporary upload workspace, not the authoritative
evidence store.

Current behavior:

- PPE and zone snapshots are generated locally before upload.
- Evidence is persisted to MinIO.
- Local cleanup is best effort after successful persistence.

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
  -> zone violation row in PostgreSQL with physical_zone_id,
     camera_zone_view_id, copied zone_name, copied zone_type, and copied
     source_key
  -> local temporary snapshot removal
  -> frontend-compatible API response
```

Zone configuration:

```text
Factory floor-plan polygon
  -> physical_zones.floor_plan_polygon

Frontend polygon for a specific camera and physical zone
  -> service validation and JSON parsing
  -> camera_zone_views.ui_shape_data
  -> camera_zone_views.normalized_coordinates used by detection
```

## Deletion Behavior

Deleting a violation currently removes its PostgreSQL row. It does not delete
the corresponding MinIO object.

Deleting a camera, physical zone, or camera-zone view sets the corresponding
historical zone-violation reference to `NULL`. `zone_name`, `zone_type`,
source, timestamp, frame, and snapshot object key remain available. Deleting a
camera or physical zone cascades its associated camera-zone-view
configuration, but does not delete historical incidents.

The zone violation API still exposes `zone_id` for frontend compatibility. It
is an alias for `camera_zone_view_id`, not a reference to a legacy `zones`
table.
