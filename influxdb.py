from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS, ASYNCHRONOUS, WriteApi
from influxdb_client.client.write_api import WriteOptions
import threading
import queue
import logging

URL = "http://localhost:8086"
TOKEN = "CDH18sO2Seasj8pPSW_WKslzOVEH9B90CgZHt41LGjsUH4jfJJDm8tERkivV1jX1wCbVRSlhweC8oIYwRSN-iw=="
ORG = "1cr.eu"
BUCKET = "EMGv3"

class InfluxWriter:
    def __init__(self, url: str, token: str, org: str, bucket: str,
                 batch_size: int = 5000, flush_interval_ms: int = 1000,
                 queue_max_points: int = 20000):
        self.bucket = bucket
        self.org = org
        self.client = InfluxDBClient(url=url, token=token, org=org, timeout=30_000)
        # Use ASYNCHRONOUS write to avoid blocking the caller
        self.write_api: WriteApi = self.client.write_api(
            write_options=WriteOptions(
                batch_size=batch_size,
                flush_interval=flush_interval_ms,
                jitter_interval=0,
                retry_interval=5_000,
                # Don't pile up retries forever — the outer queue will handle backpressure/drop
                max_retries=0
            )
        )

        # Bounded queue of points to write; we push lists (batches)
        self._q: "queue.Queue[list]" = queue.Queue(maxsize=max(1, queue_max_points // max(1, batch_size)))
        self._running = True
        self._thread = threading.Thread(target=self._worker, name="InfluxWriter", daemon=True)
        self._thread.start()

    def _worker(self):
        while self._running:
            try:
                batch = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if batch:
                    self.write_api.write(bucket=self.bucket, org=self.org, record=batch)
            except Exception as e:
                # Drop this batch on any error (non-blocking behavior)
                logging.warning(f"[Influx] write failed, dropping {len(batch)} points: {e}")
            finally:
                self._q.task_done()

    def write(self, points):
        # Non-blocking: if queue is full, drop silently (or with a very light log)
        if not points:
            return
        try:
            # Put_nowait to avoid blocking the data path
            self._q.put_nowait(points)
        except queue.Full:
            # Drop
            logging.debug("[Influx] queue full; dropping a batch")

    def close(self):
        try:
            self._running = False
            # Drain quickly without blocking the main shutdown
            try:
                while not self._q.empty():
                    self._q.get_nowait()
                    self._q.task_done()
            except Exception:
                pass
            # Flush internal client's buffers
            try:
                self.write_api.flush()
            finally:
                self.client.close()
        except Exception:
            # Ensure no exception bubbles during shutdown
            pass
