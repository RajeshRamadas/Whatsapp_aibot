"""Tenant-isolated document storage, with local and S3-compatible backends."""
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import quote

from ..config import settings


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def presign_put(self, key: str, content_type: str) -> str: ...

    @abstractmethod
    def presign_get(self, key: str) -> str: ...


class LocalStorage(Storage):
    def __init__(self, root: Path | None = None):
        self.root = Path(root or settings.storage_local_path).resolve()

    def _path(self, key: str) -> Path:
        if not key or any(part in ('', '.', '..') for part in key.split('/')) or '\\' in key:
            raise ValueError('Invalid storage key')
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Invalid storage key')
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def presign_put(self, key: str, content_type: str) -> str:
        # Local development uses the authenticated API upload endpoint instead of a public URL.
        return f'/api/v1/sources/uploads/local/{quote(key, safe="/")}'

    def presign_get(self, key: str) -> str:
        return f'/api/v1/sources/uploads/local/{quote(key, safe="/")}'


class S3Storage(Storage):
    def __init__(self):
        import boto3
        self.bucket = settings.storage_bucket
        self.client = boto3.client('s3', endpoint_url=settings.storage_endpoint_url,
                                   region_name=settings.storage_region)

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)['Body'].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def presign_put(self, key: str, content_type: str) -> str:
        return self.client.generate_presigned_url('put_object', Params={
            'Bucket': self.bucket, 'Key': key, 'ContentType': content_type}, ExpiresIn=900)

    def presign_get(self, key: str) -> str:
        return self.client.generate_presigned_url('get_object', Params={
            'Bucket': self.bucket, 'Key': key}, ExpiresIn=900)


def storage() -> Storage:
    return S3Storage() if settings.storage_backend == 's3' else LocalStorage()
