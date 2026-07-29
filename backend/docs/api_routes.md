# Backend API Routes

Base URL for local development:

```text
http://127.0.0.1:8000
```

Interactive API documentation is available at `/docs` and `/redoc`.
Authentication is not implemented in the current phase.

## Health

### Application health

- Method: `GET`
- Path: `/health`
- Purpose: Confirms that the FastAPI application is running.
- Request body: None
- Storage used: None

Response:

```json
{
  "status": "ok"
}
```

### PostgreSQL health

- Method: `GET`
- Path: `/health/db`
- Purpose: Runs `SELECT 1` against PostgreSQL.
- Request body: None
- Storage used: PostgreSQL

Response:

```json
{
  "database": "connected"
}
```

Returns HTTP `503` if PostgreSQL cannot be reached.

### MinIO health

- Method: `GET`
- Path: `/health/storage`
- Purpose: Confirms that MinIO is reachable and ensures the configured bucket exists.
- Request body: None
- Storage used: MinIO

Response:

```json
{
  "storage": "connected",
  "bucket": "safety-monitoring-evidence"
}
```

Returns HTTP `503` if MinIO is not configured or cannot be reached.

### SMTP health

- Method: `GET`
- Path: `/health/smtp`
- Purpose: Connects to the configured SMTP server (STARTTLS/SSL + login as
  configured), issues a `NOOP`, then disconnects.
- Request body: None
- Storage used: None

Response:

```json
{
  "smtp": "connected",
  "host": "smtp.gmail.com",
  "port": "587"
}
```

Returns HTTP `503` if `REPORT_EMAIL_ENABLED` is `false`, SMTP is not
configured, or the SMTP server cannot be reached.

## Prediction

### Predict PPE from an image

- Method: `POST`
- Path: `/predict`
- Purpose: Runs PPE detection against one image.
- Request: `multipart/form-data`
- Storage used: None. This endpoint returns detections but does not persist them.

Form field:

| Field | Type | Description |
|---|---|---|
| `file` | File | JPEG, PNG, WebP, or BMP image |

Example response:

```json
{
  "detections": [
    {
      "id": 1,
      "label": "Person",
      "category": "compliant",
      "confidence": 0.96,
      "bbox": {
        "x1": 100.0,
        "y1": 80.0,
        "x2": 260.0,
        "y2": 460.0
      },
      "color": "#22c55e"
    }
  ],
  "persons": [
    {
      "person_id": 1,
      "track_id": null,
      "bbox": {
        "x1": 100.0,
        "y1": 80.0,
        "x2": 260.0,
        "y2": 460.0
      },
      "confidence": 0.96,
      "equipment": [],
      "compliant": true
    }
  ],
  "summary": {
    "total_persons": 1,
    "compliant": 1,
    "violations": 0,
    "inference_ms": 42.5
  }
}
```

### Process a video

- Method: `POST`
- Path: `/predict-video`
- Purpose: Processes a video for PPE violations and configured zone breaches.
- Request: `multipart/form-data`
- Storage used:
  - PostgreSQL for PPE and zone violation metadata
  - MinIO for violation snapshots
  - `storage/snapshots` as a local snapshot workspace

Form field:

| Field | Type | Description |
|---|---|---|
| `file` | File | MP4, MPEG, MOV, AVI, MKV, or WebM video |

Example response:

```json
{
  "summary": {
    "video_name": "factory.mp4",
    "total_frames": 500,
    "processed_frames": 500,
    "fps": 25.0,
    "duration_seconds": 20.0,
    "unique_violations": 2,
    "candidate_violations": 3,
    "inference_ms": 8412.7
  },
  "reports": [
    {
      "id": 41,
      "timestamp": "2026-06-06T08:30:00+00:00",
      "violation_type": "missing_helmet",
      "details": "track 7 missing Helmet at frame 120",
      "snapshot_url": "http://localhost:9000/bucket/object?signature=...",
      "video_name": "factory.mp4",
      "frame_index": 120,
      "track_id": 7
    }
  ],
  "zone_violations": [
    {
      "id": 18,
      "zone_id": 3,
      "camera_id": 2,
      "physical_zone_id": 5,
      "camera_zone_view_id": 3,
      "zone_name": "Restricted Area",
      "zone_type": "RESTRICTED",
      "track_id": 7,
      "timestamp": "2026-06-06T08:30:02+00:00",
      "video_name": "factory.mp4",
      "frame_index": 170,
      "snapshot_path": "http://localhost:9000/bucket/object?signature=...",
      "status": "OPEN",
      "severity": null
    }
  ]
}
```

The returned evidence links are normally temporary MinIO presigned URLs.
`zone_id` is kept as a backward-compatible API alias for
`camera_zone_view_id`.

## PPE Violations

### List PPE violations

- Method: `GET`
- Path: `/violations`
- Purpose: Returns recent PPE violations in reverse chronological order.
- Query parameter: `limit`, default `100`, minimum `1`, maximum `500`
- Storage used: PostgreSQL and MinIO

Example response:

```json
[
  {
    "id": 41,
    "timestamp": "2026-06-06T08:30:00+00:00",
    "violation_type": "missing_helmet",
    "details": "track 7 missing Helmet at frame 120",
    "snapshot_url": "http://localhost:9000/bucket/object?signature=...",
    "video_name": "factory.mp4",
    "frame_index": 120,
    "track_id": 7
  }
]
```

### Delete all violation records

- Method: `DELETE`
- Path: `/violations`
- Purpose: Deletes all PPE and zone violation database records.
- Request body: None
- Storage used: PostgreSQL

Response:

```json
{
  "ppe_violations_deleted": 4,
  "zone_violations_deleted": 2,
  "total_deleted": 6
}
```

This operation does not currently delete MinIO objects.

### Delete one PPE violation

- Method: `DELETE`
- Path: `/violations/{violation_id}`
- Purpose: Deletes one PPE violation and its subjects.
- Request body: None
- Storage used: PostgreSQL

Response:

```json
{
  "success": true
}
```

Returns HTTP `404` when the record does not exist. The MinIO object is not
currently deleted.

## Zones

Zone API field names remain compatible with the frontend. Internally,
`video_name` maps to `cameras.source_key`. One submitted zone is stored as a
`physical_zones` row for the real factory area and a `camera_zone_views` row
for the camera-specific polygon used by detection. JSON strings from the API
are stored as JSONB.

The API `id` for a zone is the `camera_zone_views.id` value.

Zone types are `RESTRICTED` and `WALKWAY`.

### Create a zone

- Method: `POST`
- Path: `/zones`
- Purpose: Creates a zone. A camera is created automatically when the
  `video_name` source does not exist.
- Storage used: PostgreSQL

Request:

```json
{
  "video_name": "factory.mp4",
  "zone_name": "Restricted Area",
  "zone_type": "RESTRICTED",
  "dwell_threshold_seconds": 2,
  "is_active": true,
  "ui_shape_data": "{\"type\":\"polygon\"}",
  "flattened_coordinates": "[{\"x\":0.1,\"y\":0.1},{\"x\":0.9,\"y\":0.1},{\"x\":0.9,\"y\":0.9}]"
}
```

Response:

```json
{
  "id": 3,
  "video_name": "factory.mp4",
  "zone_name": "Restricted Area",
  "zone_type": "RESTRICTED",
  "dwell_threshold_seconds": 2,
  "is_active": true,
  "ui_shape_data": "{\"type\":\"polygon\"}",
  "flattened_coordinates": "[{\"x\":0.1,\"y\":0.1},{\"x\":0.9,\"y\":0.1},{\"x\":0.9,\"y\":0.9}]"
}
```

### List zones for a video

- Method: `GET`
- Path: `/zones/{video_name}`
- Purpose: Lists zones assigned to the source.
- Request body: None
- Storage used: PostgreSQL

Response:

```json
[
  {
    "id": 3,
    "video_name": "factory.mp4",
    "zone_name": "Restricted Area",
    "zone_type": "RESTRICTED",
    "dwell_threshold_seconds": 2,
    "is_active": true,
    "ui_shape_data": "{\"type\":\"polygon\"}",
    "flattened_coordinates": "[{\"x\":0.1,\"y\":0.1},{\"x\":0.9,\"y\":0.1},{\"x\":0.9,\"y\":0.9}]"
  }
]
```

Returns `[]` when the camera or zones do not exist.

### Update a zone

- Method: `PUT`
- Path: `/zones/{zone_id}`
- Purpose: Updates an existing zone. The path ID is authoritative.
- Storage used: PostgreSQL
- Request body: Same fields as `POST /zones`; `id` is optional and ignored in
  favor of the path ID.
- Response: Updated zone using the same format as `POST /zones`

### Delete one zone

- Method: `DELETE`
- Path: `/zones/{zone_id}`
- Purpose: Deletes one zone.
- Request body: None
- Storage used: PostgreSQL

Response:

```json
{
  "status": "success"
}
```

Deleting a zone removes its camera-zone-view configuration and physical-zone
configuration. Historical zone violations keep copied `zone_name`,
`zone_type`, and `source_key`; nullable references such as
`physical_zone_id`, `camera_zone_view_id`, and the API alias `zone_id` may be
`null` after referenced configuration is deleted.

### Delete zones for a video

- Method: `DELETE`
- Path: `/zones/video/{video_name}`
- Purpose: Deletes all zones assigned to the source.
- Request body: None
- Storage used: PostgreSQL

Response:

```json
{
  "status": "success",
  "deleted": 2
}
```

## Zone Violations

### List zone violations

- Method: `GET`
- Path: `/zone-violations`
- Purpose: Returns recent zone violations in reverse chronological order.
- Query parameter: `limit`, default `100`; the service accepts `1` to `500`
- Storage used: PostgreSQL and MinIO

Response:

```json
[
  {
    "id": 18,
    "zone_id": 3,
    "camera_id": 2,
    "physical_zone_id": 5,
    "camera_zone_view_id": 3,
    "zone_name": "Restricted Area",
    "zone_type": "RESTRICTED",
    "track_id": 7,
    "timestamp": "2026-06-06T08:30:02+00:00",
    "video_name": "factory.mp4",
    "frame_index": 170,
    "snapshot_path": "http://localhost:9000/bucket/object?signature=...",
    "status": "OPEN",
    "severity": null
  }
]
```

Zone incidents persist `physical_zone_id`, `camera_zone_view_id`, copied
`zone_name`, copied `zone_type`, and copied `source_key`. `zone_id` is kept as
a backward-compatible API alias for `camera_zone_view_id`.

`physical_zone_id`, `camera_zone_view_id`, and `zone_id` can be `null` after
referenced configuration is deleted. Copied `zone_name`, `zone_type`, and
`video_name` remain available for historical reporting.

### Delete one zone violation

- Method: `DELETE`
- Path: `/zone-violations/{zone_violation_id}`
- Purpose: Deletes one zone violation record.
- Request body: None
- Storage used: PostgreSQL

Response:

```json
{
  "success": true
}
```

Returns HTTP `404` when the record does not exist. The MinIO object is not
currently deleted.

## Incident Reports

PDF export + SMTP email delivery for the Incident Analytics dashboard. See
`docs/features/PDF_REPORT_EMAIL.md` for the locked decisions and known
limitations behind this feature.

### Preview a report

- Method: `GET`
- Path: `/reports/incidents/preview`
- Purpose: Returns the headline numbers and key insights for a report without
  generating the PDF — lets the frontend show a preview before download/send.
- Query parameters: `range` (`24H` \| `7D` \| `30D`, default `7D`), `zone_id`
  (optional)
- Storage used: PostgreSQL (read-only, via `AnalyticsService` /
  `UnifiedIncidentService`)

Example response:

```json
{
  "range": "7D",
  "zone_scope_label": "All zones",
  "generated_at_local": "29 Jul 2026, 14:32",
  "grand_total": 128,
  "severity_counts": {"Critical": 7, "High": 19, "Medium": 40, "Low": 62},
  "insights": ["**Incidents are up 34.2%** vs the previous 7 days (128 vs 95)."],
  "data_caveats": []
}
```

### Download a report PDF

- Method: `GET`
- Path: `/reports/incidents.pdf`
- Purpose: Renders and returns the Incident Analytics PDF report inline.
- Query parameters: `range` (default `7D`), `zone_id` (optional),
  `include_snapshots` (default `true`)
- Storage used: PostgreSQL (read-only); MinIO if evidence snapshots are
  fetched for the appendix
- Response: `application/pdf` body with `Content-Disposition: attachment`

Returns HTTP `422` for an invalid `range`.

### Email a report

- Method: `POST`
- Path: `/reports/incidents/email`
- Purpose: Generates the PDF and emails it via SMTP. Synchronous — the
  response reflects the actual send result, not a fire-and-forget queue.
- Storage used: PostgreSQL; MinIO if `REPORT_ARCHIVE_TO_MINIO` is enabled
  (archive failure does not block delivery); a `report_deliveries` audit row
  is written for every attempt.

Request:

```json
{
  "recipients": ["safety@deheus.com"],
  "range": "7D",
  "zone_id": null,
  "subject": null,
  "message": "FYI — see the Critical items on page 2.",
  "include_snapshots": true
}
```

Response:

```json
{
  "status": "sent",
  "recipients": ["safety@deheus.com"],
  "filename": "safety-report_default-factory_7D_20260729-1432.pdf",
  "size_bytes": 48430,
  "object_key": "reports/2026/07/9f2c1e0b6b1a4d0c9a2e6f3b1c7d8e90.pdf",
  "sent_at": "2026-07-29T14:32:10.512000+00:00"
}
```

| Condition | Status |
|---|---|
| `REPORT_EMAIL_ENABLED=false` | `503` |
| Recipient rejected by policy (cap, allowlist, malformed, header injection) | `422` |
| Rate limit exceeded (`REPORT_EMAIL_RATE_LIMIT_PER_HOUR`) | `429` |
| SMTP server timed out | `504` |
| SMTP transport/config failure | `502` |
| MinIO archive failure | Does not fail the request — `object_key` is `null` |

This endpoint has no authentication. It must not be exposed to the public
internet — see `docs/internal_deployment.md`.

## Compatibility Static Route

- Method: `GET`
- Path: `/snapshots/{filename}`
- Purpose: Serves files from `storage/snapshots` for compatibility with older
  local snapshot paths.
- Storage used: Local filesystem

New persisted evidence should normally be returned through MinIO URLs.
