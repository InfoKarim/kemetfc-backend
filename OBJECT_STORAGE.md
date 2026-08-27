# Private video object storage

TrainingBuddy keeps local video storage for development and tests. Production
requires a private S3-compatible bucket. Database video paths remain stable as
`/uploads/videos/<filename>` so switching storage backends does not break the
UI or existing records.

## Production configuration

Set these environment variables in the application and analysis worker:

```text
VIDEO_STORAGE_BACKEND=s3
S3_VIDEO_BUCKET=trainingbuddy-private-videos
S3_VIDEO_PREFIX=videos
S3_REGION=us-east-1
S3_PRESIGNED_URL_EXPIRY_SECONDS=300
```

For an S3-compatible provider, also set its HTTPS API endpoint:

```text
S3_ENDPOINT_URL=https://your-account.example-s3-provider.com
```

Provide credentials through the provider's standard AWS environment variables
or workload identity. Do not store access keys in Git:

```text
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

The bucket must block public access. An authenticated TrainingBuddy request
creates a short-lived signed download URL. The default lifetime is five
minutes.

## Recommended bucket policy

- Block all public access.
- Allow the application identity to get, put, and delete objects only under
  `S3_VIDEO_PREFIX`.
- Enable server-side encryption at the bucket level.
- Enable object versioning when the provider supports it.
- Add a lifecycle rule for abandoned multipart uploads.
- Define retention according to the academy's parental consent and privacy
  policy. Do not automatically delete source videos until that policy exists.

## Migrate existing local videos

First perform a dry run:

```bash
export VIDEO_STORAGE_BACKEND=s3
export S3_VIDEO_BUCKET=trainingbuddy-private-videos
python scripts/migrate_local_videos_to_s3.py
```

Upload after reviewing the list:

```bash
python scripts/migrate_local_videos_to_s3.py --apply
```

The migration verifies each local file SHA-256 checksum against the database.
It does not delete local files. After application and worker verification,
archive or remove them according to the approved retention policy.

