from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS, ASYNCHRONOUS, WriteApi
from influxdb_client.client.write_api import WriteOptions

URL = "http://localhost:8086"
TOKEN = "CDH18sO2Seasj8pPSW_WKslzOVEH9B90CgZHt41LGjsUH4jfJJDm8tERkivV1jX1wCbVRSlhweC8oIYwRSN-iw=="
ORG = "1cr.eu"
BUCKET = "EMGv3"

class InfluxWriter:
    def __init__(self, url: str, token: str, org: str, bucket: str,
                 batch_size: int = 5000, flush_interval_ms: int = 1000):
        self.bucket = bucket
        self.org = org
        self.client = InfluxDBClient(url=url, token=token, org=org, timeout=30_000)
        self.write_api: WriteApi = self.client.write_api(
            write_options=WriteOptions(
                batch_size=batch_size,
                flush_interval=flush_interval_ms,
                jitter_interval=0,
                retry_interval=5_000,
            )
        )

    def write(self, points):
        if points:
            self.write_api.write(bucket=self.bucket, org=self.org, record=points)

    def close(self):
        try:
            self.write_api.flush()
        finally:
            self.client.close()