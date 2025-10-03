import time
import threading
import logging

from exercise import create_exercise
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

        # Initialize standard sensors for exercise tracking
        self.sensors = {
            "Biceps": None,
            "Triceps": None,
            "Quadriceps": None,
            "Gastrocnemius": None
        }
        # Dictionary for additional sensors (not used in exercise tracking)
        self.additional_sensors = {}
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

                # Process standard exercise sensors
                for pos, sensor in self.sensors.items():
                    if sensor:
                        payload["sensors"][pos] = sensor.snapshot()
                        model_data[pos] = sensor.get_model_data()
                    else:
                        payload["sensors"][pos] = {"status": "N/A"}
                        model_data[pos] = None
                
                # Process additional sensors
                for pos, sensor in self.additional_sensors.items():
                    if sensor:
                        payload["sensors"][pos] = sensor.snapshot()

                # Check if all sensors are "Active"
                self.check_all_sensors_active(payload["sensors"])

                # Get exercise description if all sensors are active
                if self.all_active and self._exercise and all(v is not None for v in model_data.values()):
                    payload["exercise_description"] = self._exercise.describe(model_data)

            # Publish data via MQTT if enabled
            if self.server.mqtt:
                self.server.mqtt.send_payload(payload)

            # Sleep to match max rate
            t = time.time() - start
            if t < period:
                time.sleep(period - t)

    def check_new_sensors(self):
        # Process all active sensors
        for pos, sensor in self.server.active_sensors.items():
            if pos in POSITIONS_ALLOWED:
                # Standard exercise sensors
                self.sensors[pos] = sensor
            else:
                # Additional sensors (only for data collection)
                self.additional_sensors[pos] = sensor

    def check_all_sensors_active(self, sensors):
        self.all_active = all(
            sensor is not None and isinstance(sensor, dict) and sensor.get("status") == "Active"
            for sensor in sensors.values()
        )

    def set_exercise(self, exercise_name):
        if exercise_name:
            try:
                self._exercise = create_exercise(exercise_name)
            except ValueError as e:
                logging.warning(f"Failed to create exercise: {e}")
                self._exercise = None
        else:
            self._exercise = None
