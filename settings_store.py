import boto3
import json
import os
from botocore.config import Config

_SETTINGS_KEY = "team-accountability/settings.json"


def _client():
    return boto3.client(
        service_name="s3",
        endpoint_url=f"https://{os.getenv('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(s3={"addressing_style": "path"}),
    )


def load_settings() -> dict:
    try:
        resp = _client().get_object(Bucket=os.getenv("BUCKET_NAME"), Key=_SETTINGS_KEY)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception:
        return {}


def save_settings(settings: dict):
    _client().put_object(
        Bucket=os.getenv("BUCKET_NAME"),
        Key=_SETTINGS_KEY,
        Body=json.dumps(settings, indent=2),
        ContentType="application/json",
    )
