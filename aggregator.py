import time
import threading

from exercise import Exercise
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
        self.all_active = False

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
                    "exercise": self._exercise.name if self._exercise else "N/A",
                    "exercise_description": {}
                }

                model_data = {}

                for pos, sensor in self.sensors.items():
                    if sensor:
                        payload["sensors"][pos] = sensor.snapshot()
                        model_data[pos] = sensor.get_model_data()
                    else:
                        payload["sensors"][pos] = {"status": "N/A"}
                        model_data[pos] = None

                # Check if all sensors are "Active"
                self.check_all_sensors_active(payload["sensors"])

                # Make predictions only if all sensors are active
                if self.all_active and self._exercise and all(v is not None for v in model_data.values()):
                    phase = self._exercise.predict_wide_squat_phase(data=model_data)
                    payload["exercise_description"]["phase"] = phase

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

    def check_all_sensors_active(self, sensors):
        self.all_active = all(
            sensor is not None and isinstance(sensor, dict) and sensor.get("status") == "Active"
            for sensor in sensors.values()
        )

    def set_exercise(self, exercise):
        if exercise:
            self._exercise = Exercise(name=exercise)
        else:
            self._exercise = None
