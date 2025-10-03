import logging
import socket
from contextlib import closing

from common import stop_event
from aggregator import Aggregator
from sensor import Sensor


class Server:
    def __init__(self, host, port, mqtt=None, influx=None, influx_emg_fs: int = 1000):
        self.host = host
        self.port = port
        self.mqtt = mqtt
        if self.mqtt:
            self.mqtt.set_control_handler(self._handle_control)
        self.influx = influx
        self.all_sensors = {}  # position->Sensor
        self.active_sensors = {}  # position->Sensor
        self.aggregator = Aggregator(self)
        self.aggregator.start()
        self.influx_emg_fs = int(influx_emg_fs)


    def serve_forever(self):
        local_hostname = socket.gethostname()
        ip_addresses = socket.gethostbyname_ex(local_hostname)[2]
        filtered_ips = [ip for ip in ip_addresses if not ip.startswith("127.")]
        logging.info("Local addresses: %s", filtered_ips)

        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen()
            logging.info("Listening on %s:%d ...", self.host, self.port)

            while not stop_event.is_set():
                try:
                    client_sock, addr = s.accept()
                    client_sock.settimeout(1)
                    sensor = Sensor(self, client_sock, addr)
                    sensor.start()

                    # Registration after the init packet, Aggregator is able
                    # to detect sensor according sensor.detected
                    self.all_sensors[sensor.position or f"unknown_{addr}"] = sensor

                except TimeoutError:
                    pass

    def _handle_control(self, payload):
        cmd = payload.get("cmd")

        if cmd == "reset_orientation":
            for pos, s in list(self.all_sensors.items()):
                if s and s.connected:
                    s.reset_orientation()

        if cmd == "mvc_start":
            for pos, s in list(self.all_sensors.items()):
                if s and s.connected:
                    s.mvc_start()

        if cmd == "mvc_stop":
            for pos, s in list(self.all_sensors.items()):
                if s and s.connected:
                    s.mvc_stop()

        if cmd == "set_exercise":
            self.aggregator.set_exercise(payload.get("val"))