"""Artifact storage backends.

``get_storage()`` selects the backend from the ``DROP_STORAGE`` env var
(``local`` default, ``s3`` for R2-style S3-compatible storage).

Interface every backend implements:
    save(token, fileobj, mime_type, metadata=None) -> None
        (``metadata`` is a small str->str dict stored with the object where
        the backend supports it)
    save_may_have_stored(exc) -> bool: whether a save() that raised ``exc``
        may still have stored the object (then the caller deletes it)
    open(token) -> binary file-like (caller closes); FileNotFoundError when
        the object does not exist, any other exception is a backend failure
    delete(token) -> None (missing file is not an error)
    delete_many(tokens) -> {token: error message} for the deletes that
        failed (missing files are not failures)
    exists(token) -> bool
    list_objects() -> iterable of (token, last_modified aware datetime)
"""

import datetime
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)


class LocalStorage:
    """Filesystem backend, rooted at DROP_LOCAL_DIR (dev default)."""

    def __init__(self, root=None):
        env_dir = os.environ.get("DROP_LOCAL_DIR")
        self.root = Path(root or env_dir or settings.BASE_DIR / "media" / "artifacts")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, token):
        # Tokens are unguessable and generated server-side; the basename
        # call is defense-in-depth against path traversal.
        return self.root / os.path.basename(token)

    def save(self, token, fileobj, mime_type, metadata=None):
        path = self._path(token)
        try:
            with open(path, "wb") as fh:
                for chunk in iter(lambda: fileobj.read(1024 * 1024), b""):
                    fh.write(chunk)
        except BaseException:
            # The caller deletes the row on failure; never leave a partial
            # file behind with nothing pointing at it.
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            raise

    def save_may_have_stored(self, exc):
        # save() removes its partial file before re-raising.
        return False

    def open(self, token):
        return open(self._path(token), "rb")

    def delete(self, token):
        try:
            os.remove(self._path(token))
        except FileNotFoundError:
            pass

    def delete_many(self, tokens):
        failures = {}
        for token in tokens:
            try:
                self.delete(token)
            except Exception as exc:
                failures[token] = f"{type(exc).__name__}: {exc}"
        return failures

    def exists(self, token):
        return self._path(token).is_file()

    def list_objects(self):
        for entry in os.scandir(self.root):
            if entry.is_file():
                modified = datetime.datetime.fromtimestamp(
                    entry.stat().st_mtime, tz=datetime.timezone.utc
                )
                yield entry.name, modified


# Error codes S3/R2 use for a key that does not exist (GetObject answers
# NoSuchKey; HeadObject has no body, so botocore reports the bare status).
_MISSING_CODES = {"NoSuchKey", "NotFound", "404"}


def _is_missing(exc):
    return exc.response.get("Error", {}).get("Code") in _MISSING_CODES


# DeleteObjects takes at most 1000 keys per call (R2 included).
_DELETE_BATCH = 1000


class S3Storage:
    """S3-compatible backend (Cloudflare R2 in prod). Requires
    AWS_S3_ENDPOINT_URL, AWS_S3_BUCKET, AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY."""

    def __init__(self):
        import boto3
        from botocore.config import Config

        endpoint = os.environ.get("AWS_S3_ENDPOINT_URL")
        if not endpoint:
            raise RuntimeError("AWS_S3_ENDPOINT_URL is required for DROP_STORAGE=s3")
        self.bucket = os.environ.get("AWS_S3_BUCKET")
        if not self.bucket:
            raise RuntimeError("AWS_S3_BUCKET is required for DROP_STORAGE=s3")
        # Bucket name and endpoint host are not secrets; log them at startup
        # so R2 misconfiguration is diagnosable from Render logs.
        logger.info(
            "S3 storage configured: endpoint=%s bucket=%r",
            urlparse(endpoint).netloc or endpoint,
            self.bucket,
        )
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            # R2 is S3-compatible but has no AWS regions; "auto" is the
            # documented region name or boto3 raises NoRegionError.
            region_name="auto",
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            # botocore's defaults (60 s connect and read, legacy retries)
            # let one hung R2 call outlast gunicorn's 30 s worker timeout.
            # Two attempts in total (botocore's max_attempts would count
            # retries only) of at most 3 s connect / 10 s read stay under it.
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"total_max_attempts": 2, "mode": "standard"},
            ),
        )

    def save(self, token, fileobj, mime_type, metadata=None):
        # PutObject stores the whole object or nothing, but the client can
        # give up (read timeout, dropped connection) after R2 has stored
        # it; see save_may_have_stored.
        params = {
            "Bucket": self.bucket,
            "Key": token,
            "Body": fileobj.read(),
            "ContentType": mime_type or "application/octet-stream",
        }
        if metadata:
            # Lets an operator attribute and expire an object even if its
            # DB row is lost (restore from backup, rollback).
            params["Metadata"] = metadata
        self.client.put_object(**params)

    def save_may_have_stored(self, exc):
        from botocore.exceptions import ClientError

        # An error response means R2 refused the object. Anything else (a
        # timeout, a reset connection) may have come after it was stored.
        return not isinstance(exc, ClientError)

    def open(self, token):
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=token)["Body"]
        except ClientError as exc:
            if _is_missing(exc):
                # Same contract as LocalStorage. The message omits the key:
                # it is a live bearer token.
                raise FileNotFoundError("artifact object not found") from None
            raise

    def delete(self, token):
        # DeleteObject on a missing key succeeds (204), so this is
        # idempotent like LocalStorage.delete.
        self.client.delete_object(Bucket=self.bucket, Key=token)

    def delete_many(self, tokens):
        """Delete up to 1000 keys per DeleteObjects call. Stops at the first
        call that fails outright: in an outage every later call would fail
        too, and each one can take seconds. Those keys count as failed."""
        tokens = list(tokens)
        failures = {}
        for start in range(0, len(tokens), _DELETE_BATCH):
            chunk = tokens[start : start + _DELETE_BATCH]
            try:
                if len(chunk) == 1:
                    self.delete(chunk[0])
                    continue
                response = self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": key} for key in chunk], "Quiet": True},
                )
            except Exception as exc:
                failures.update(
                    dict.fromkeys(tokens[start:], f"{type(exc).__name__}: {exc}")
                )
                break
            # Quiet mode lists only the keys that failed.
            for error in response.get("Errors", []):
                if error.get("Code") not in _MISSING_CODES:
                    failures[error.get("Key")] = (
                        f"{error.get('Code')}: {error.get('Message')}"
                    )
        return failures

    def exists(self, token):
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=token)
        except ClientError as exc:
            if _is_missing(exc):
                return False
            # Auth/outage errors must not read as "object is gone".
            raise
        return True

    def list_objects(self):
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket):
            for obj in page.get("Contents", []):
                yield obj["Key"], obj["LastModified"]


# One S3Storage (and boto3 client, which is thread-safe) per configuration
# per process: building a client per call is slow and logs the config line
# on every request.
_s3_storages = {}


def _s3_storage():
    config = tuple(
        os.environ.get(name)
        for name in (
            "AWS_S3_ENDPOINT_URL",
            "AWS_S3_BUCKET",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        )
    )
    storage = _s3_storages.get(config)
    if storage is None:
        storage = _s3_storages[config] = S3Storage()
    return storage


def get_storage():
    backend = os.environ.get("DROP_STORAGE", "local").lower()
    if backend == "s3":
        return _s3_storage()
    if backend == "local":
        return LocalStorage()
    raise RuntimeError(f"Unknown DROP_STORAGE backend: {backend!r}")
