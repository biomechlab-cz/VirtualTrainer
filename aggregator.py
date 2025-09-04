import time
import threading

from common import stop_event

from sensor import Sensor, current_millis

POSITIONS = {"Biceps": 0, "Triceps": 1, "Quadriceps": 2, "Gastrocnemius": 3}
POSITIONS_ALLOWED = POSITIONS.keys()

class Aggregator(threading.Thread):
    def __init__(self, server, rate_hz=20):
        super().__init__(daemon=True)
        self.server = server
        self.rate_hz = rate_hz
        self.running = True

        self.sensors = {
            "Biceps": None,
            "Triceps": None,
            "Quadriceps": None,
            "Gastrocnemius": None
        }

        self._exercise = None

        self.status_lock = threading.Lock()


    def run(self):
        period = 1.0 / self.rate_hz
        while self.running and not stop_event.is_set():
            start = time.time()

            self.check_new_sensors()

            # Collect data

            with self.status_lock:
                payload = {
                    "timestamp": current_millis(),
                    "sensors": {},
                    "exercise": self._exercise if self._exercise else "N/A",
                    "exercise_description": {}
                }

                for pos, sensor in self.sensors.items():
                    if sensor:
                        payload["sensors"][pos] = sensor.snapshot()
                    else:
                        payload["sensors"][pos] = {"status": "N/A"}

            # Publish
            self.server.mqtt.send_payload(payload)

            # Sleep to match max rate
            t = time.time() - start
            if t < period:
                time.sleep(period - t)

    def check_new_sensors(self):
        for pos in POSITIONS_ALLOWED:
            sensor = self.server.active_sensors.get(pos)
            if sensor:
                self.sensors[pos] = sensor

    def set_exercise(self, exercise):
        with self._exercise:
            if exercise:
                self._exercise = exercise
            else:
                self._exercise = None
