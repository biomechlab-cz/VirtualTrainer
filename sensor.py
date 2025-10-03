import logging
import socket
import time
from itertools import islice

import numpy as np
from collections import deque

import neurokit2 as nk

import aggregator
from orientation import OrientationEstimator

import common

from common import threading, stop_event

from influxdb_client import Point, WritePrecision

from shared.c_struct_manager import SocketDataStructure


CHUNK_PRIMARY = 100                     # EMG samples in packet
CHUNK_SECONDARY = CHUNK_PRIMARY // 10   # IMU samples in packet
EMG_FS = 1000  # 1 kHz
IMU_FS = 100  # 100 Hz
ENVELOPE_WIN = 100


class Sensor(threading.Thread):
    """Handles communication with sensor."""

    BUFFER_SECONDS = 10

    def __init__(self, server, sock: socket.socket, addr):
        super().__init__(daemon=True)
        self.server = server
        self.sock = sock
        self.addr = addr
        self.position = None         # Biceps / Triceps / …
        self.participant = None
        self.device_id = None
        self.device_name = None

        self.orientation = OrientationEstimator()

        self.mvc = 1

        self.mvc_capture = 0

        # Circular buffers: deque with maxlen = fs * 10 s
        self.emg_raw = deque(maxlen=EMG_FS * self.BUFFER_SECONDS)
        self.emg_clean = deque(maxlen=EMG_FS * self.BUFFER_SECONDS)
        self.emg_env = deque(maxlen=EMG_FS * self.BUFFER_SECONDS)
        self.imu_quat = deque(maxlen=IMU_FS * self.BUFFER_SECONDS)
        self.time_emu = deque(maxlen=EMG_FS * self.BUFFER_SECONDS)
        self.time_imu = deque(maxlen=IMU_FS * self.BUFFER_SECONDS)


        # Motion gating state (bridges 100 Hz IMU to 1 kHz EMG)
        self._motion_active = False
        self._motion_left = 0  # samples remaining (1 kHz)

        self.status_lock = threading.Lock()
        self.connected = True

        self._time_corr = None

        logging.info("Sensor thread init from %s", addr)

    def mvc_start(self):
        self.mvc_capture = 1
        self.mvc = 1

    def mvc_stop(self):
        self.mvc_capture = 0

    # ------------------------------------------------------------------
    # Low-level recv loop
    # ------------------------------------------------------------------
    def run(self):
        try:
            struct_size = SocketDataStructure.size
            while self.connected and not stop_event.is_set():
                try:
                    raw = self._recvall(struct_size)
                    if not raw:
                        logging.info("Socket closed by %s", self.addr)
                        break

                    packet = SocketDataStructure(raw)

                    if packet.isInitialData > 0:
                        # Reading init package
                        init = packet.unionData.initialData
                        self.participant = (
                            init.participant.decode("utf-8", "ignore").strip("\x00")
                        )
                        self.position = (
                            init.position.decode("utf-8", "ignore").strip("\x00")
                        )
                        if self.position not in aggregator.POSITIONS_ALLOWED:
                            logging.warning(
                                "Rejected unknown position %s from %s", self.position, self.addr
                            )
                            self.close()
                            break

                        self.device_id = init.device_id
                        self.device_name = f"device{self.device_id}"
                        logging.info(
                            "Connected %s – participant=%s position=%s",
                            self.device_name,
                            self.participant,
                            self.position,
                        )

                        self.server.active_sensors[self.position] = self
                    else:
                        if self.position not in aggregator.POSITIONS_ALLOWED:
                            self.close()
                            break

                        data = packet.unionData.data
                        self._handle_data(data, packet.batteryLevel)

                except TimeoutError:
                    pass

        except Exception as ex:
            logging.exception("Sensor thread error: %s", ex)
        finally:
            self.close()

    def _recvall(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size and self.connected and not stop_event.is_set():
            try:
                chunk = self.sock.recv(size - len(data))
                if not chunk:
                    return b""
                data.extend(chunk)
            except socket.timeout:
                continue
        return bytes(data)

    # ------------------------------------------------------------------
    # Parsing + local processing
    # ------------------------------------------------------------------
    def _handle_data(self, d, battery):
        # --- Time correction ---
        raw_t = np.array(d.time, dtype=np.int64)
        if self._time_corr is None:
            if raw_t[0] < 1577836800000:
                self._time_corr = current_millis() - int(raw_t[0])
            else:
                self._time_corr = 0
        corrected_time = raw_t + self._time_corr

        # Time for EMG: interpolate 10 timestamps for 100 samples
        emg_time = np.interp(
            np.linspace(0, len(corrected_time) - 1, CHUNK_PRIMARY),
            np.arange(len(corrected_time)),
            corrected_time
        ).astype(np.int64)

        # --- IMU (10 samples → scaled SI/phys units) ---
        acc_x_int = np.asarray(d.imu_acc_x, dtype=np.int16)
        acc_y_int = np.asarray(d.imu_acc_y, dtype=np.int16)
        acc_z_int = np.asarray(d.imu_acc_z, dtype=np.int16)

        gyro_x_int = np.asarray(d.imu_gyro_x, dtype=np.int16)
        gyro_y_int = np.asarray(d.imu_gyro_y, dtype=np.int16)
        gyro_z_int = np.asarray(d.imu_gyro_z, dtype=np.int16)

        mag_x_int = np.asarray(d.compass_x, dtype=np.int16)
        mag_y_int = np.asarray(d.compass_y, dtype=np.int16)
        mag_z_int = np.asarray(d.compass_z, dtype=np.int16)

        imu_acc = np.column_stack([
            scale_acc_int16_to_ms2(acc_x_int),
            scale_acc_int16_to_ms2(acc_y_int),
            scale_acc_int16_to_ms2(acc_z_int),
        ]).astype(np.float32)

        imu_gyro = np.column_stack([
            scale_gyro_int16_to_dps(gyro_x_int),
            scale_gyro_int16_to_dps(gyro_y_int),
            scale_gyro_int16_to_dps(gyro_z_int),
        ]).astype(np.float32)

        imu_mag = np.column_stack([
            scale_mag_int16_to_uT(mag_x_int),
            scale_mag_int16_to_uT(mag_y_int),
            scale_mag_int16_to_uT(mag_z_int),
        ]).astype(np.float32)

        # --- EMG (counts → µV) ---
        emg_counts = np.asarray(d.emg_data_arr, dtype=np.int32)
        emg_uv = scale_ads1292_counts_to_uV(emg_counts)

        clean_emg_uv = nk.emg_clean(emg_uv, sampling_rate=EMG_FS)

        env = self.emg_envelope(clean_emg_uv)

        max_env = np.min(env)

        dt = 1.0 / IMU_FS
        quats = np.empty((CHUNK_SECONDARY, 4), dtype=np.float32)

        # --- updating buffer for runtime/MQTT ---
        with self.status_lock:
            for i in range(CHUNK_SECONDARY):
                quats[i] = self.orientation.update(imu_gyro[i], imu_acc[i], imu_mag[i], dt)

            self.emg_raw.extend(emg_uv)          # µV
            self.emg_clean.extend(clean_emg_uv)  # µV
            self.emg_env.extend(env)
            self.time_emu.extend(emg_time) # TODO: Ověřit extrapolaci času - v influxu nejsou časy vzorků rovnoměrně

            if self.mvc_capture and max_env > self.mvc:
                self.mvc = max_env

            self.imu_quat.extend(quats)
            self.time_imu.extend(corrected_time)

        # --- influx ---
        if getattr(self.server, "influx", None):
            tags = {
                "device": self.device_name or "",
                "position": self.position or "",
                "participant": self.participant or "",
            }
            points = []

            # IMU 10×
            for i in range(CHUNK_SECONDARY):
                t = int(corrected_time[i])
                points.append(
                    Point("imu")
                    .tag("device", tags["device"]).tag("position", tags["position"]).tag("participant",
                                                                                         tags["participant"])
                    .field("acc_x", float(imu_acc[i, 0])).field("acc_y", float(imu_acc[i, 1])).field("acc_z", float(
                        imu_acc[i, 2]))
                    .field("gyro_x", float(imu_gyro[i, 0])).field("gyro_y", float(imu_gyro[i, 1])).field("gyro_z",
                                                                                                         float(imu_gyro[
                                                                                                                   i, 2]))
                    .field("mag_x", float(imu_mag[i, 0])).field("mag_y", float(imu_mag[i, 1])).field("mag_z", float(
                        imu_mag[i, 2]))
                    .field("quat_w", float(quats[i, 0])).field("quat_x", float(quats[i, 1]))
                    .field("quat_y", float(quats[i, 2])).field("quat_z", float(quats[i, 3]))
                    .time(t, write_precision=WritePrecision.MS)
                )

            # EMG 100× (raw + clean + envelope)
            emg_throttle = 1 if getattr(self.server, 'influx_emg_fs', 1000) >= 1000 else 5
            for i in range(0, CHUNK_PRIMARY, emg_throttle):
                t = int(emg_time[i])
                # raw (µV)
                points.append(
                    Point("emg_raw")
                    .tag("device", tags["device"]).tag("position", tags["position"]).tag("participant", tags["participant"])
                    .field("value_uV", float(emg_uv[i]))
                    .time(t, write_precision=WritePrecision.MS)
                )
                # clean (µV)
                points.append(
                    Point("emg_clean")
                    .tag("device", tags["device"]).tag("position", tags["position"]).tag("participant", tags["participant"])
                    .field("value_uV", float(clean_emg_uv[i]))
                    .time(t, write_precision=WritePrecision.MS)
                )
                # envelope (µV)
                points.append(
                    Point("emg_envelope")
                    .tag("device", tags["device"]).tag("position", tags["position"]).tag("participant", tags["participant"])
                    .field("value_uV", float(env[i]))
                    .time(t, write_precision=WritePrecision.MS)
                )

            try:
                bv = int(battery)
                points.append(
                    Point("device_status")
                    .tag("device", tags["device"]).tag("position", tags["position"]).tag("participant",
                                                                                         tags["participant"])
                    .field("battery", bv)
                    .time(int(corrected_time[-1]), write_precision=WritePrecision.MS)
                )
            except Exception:
                pass

            # batch write
            self.server.influx.write(points)

    # TODO: Ověřit obálku - do influxu klidně posílat Hilbertovu obálku, do modelu 200 Hz 1 sekundu z Neurokitu
    def emg_envelope(self, _clean_emg: np.ndarray, history_seconds: float = 2.0, min_history_samples: int = 32) -> np.ndarray:
        x = np.asarray(_clean_emg, dtype=np.float32).reshape(-1)
        n = x.size
        hist_len = max(int(history_seconds * EMG_FS), int(min_history_samples))

        # Pull last `hist_len` samples from the deque (efficiently).
        hsize = len(self.emg_clean)
        if hsize > 0 and hist_len > 0:
            start = max(0, hsize - hist_len)
            # islice avoids copying the entire deque
            h = np.fromiter(islice(self.emg_clean, start, hsize), dtype=np.float32, count=hsize - start)
            full = np.concatenate([h, x]) if h.size > 0 else x
        else:
            full = x

        amp_full = nk.emg_amplitude(full)
        amp_full = np.asarray(amp_full, dtype=np.float32)

        # Return only the tail corresponding to the current block
        return amp_full[-n:]


    def reset_orientation(self):
        with self.status_lock:
            self.orientation.reset()

        logging.info(f"Sensor id {self.device_id} (position {self.position}) has reset its orientation")

    def reset_mvc(self):
        with self.status_lock:
            self.mvc = 1

        logging.info(f"Sensor id {self.device_id} (position {self.position}) has reset its MVC")

    # ------------------------------------------------------------------
    def snapshot(self): # TODO: Doplnit určené parametry podle https://docs.google.com/document/d/19UeT0tX7oSPupByxA30gNaGhILO0bvwa/edit?usp=sharing&ouid=107420772597417870596&rtpof=true&sd=true
        """Returns last values without copying the whole buffer."""
        with self.status_lock:
            return {
                "status": "Active" if self.connected else "N/A",
                "emg_envelope": float(self.emg_env[-1]) if len(self.emg_env) else "N/A",
                "mvcp": round(self.emg_env[-1] / self.mvc * 100) if (len(self.emg_env) and self.mvc > 0) else "N/A",
                "quat_wxyz": self.imu_quat[-1].tolist() if len(self.imu_quat) else "N/A"
            }

    def get_model_data(self):
        with self.status_lock:
            if len(self.emg_raw) > 1000 and len(self.imu_quat) > 100:
                return {
                    "emg_raw": np.array(list(self.emg_raw)[-1000:], dtype=np.float32),
                    "imu_quat": np.array(list(self.imu_quat)[-100:], dtype=np.float32)
                }
            else:
                return None

    def close(self):
        with self.status_lock:
            self.connected = False
        try:
            self.sock.close()
        except OSError:
            pass


def current_millis() -> int:
    return int(time.time() * 1000)


ACC_FS_G = 2       # ±2g
GYRO_FS_DPS = 250  # ±250 dps

_ACC_mg_per_LSB = {2: 0.061, 4: 0.122, 8: 0.244, 16: 0.488}           # mg/LSB
_GYRO_mdps_per_LSB = {250: 8.75, 500: 17.50, 1000: 35.0, 2000: 70.0} # mdps/LSB

MAG_LSB_PER_uT = 120.0

def scale_acc_int16_to_ms2(raw: "np.ndarray[int16]") -> "np.ndarray[float32]":
    """int16 → m/s²"""
    import numpy as np
    mg_per_lsb = _ACC_mg_per_LSB[ACC_FS_G]                  # mg / LSB
    ms2_per_lsb = (mg_per_lsb * 1e-3) * common.G0           # (g * 9.81) / LSB
    return (raw.astype(np.float32) * ms2_per_lsb).astype(np.float32)

def scale_gyro_int16_to_dps(raw: "np.ndarray[int16]") -> "np.ndarray[float32]":
    """int16 → deg/s"""
    import numpy as np
    dps_per_lsb = _GYRO_mdps_per_LSB[GYRO_FS_DPS] * 1e-3  # mdps→dps
    return (raw.astype(np.float32) * dps_per_lsb).astype(np.float32)

def scale_mag_int16_to_uT(raw: "np.ndarray[int16]") -> "np.ndarray[float32]":
    """int16 → µT"""
    import numpy as np
    return (raw.astype(np.float32) / float(MAG_LSB_PER_uT)).astype(np.float32)

def scale_ads1292_counts_to_uV(raw_counts: "np.ndarray[int32]") -> "np.ndarray[float32]":
    """
    ADS1292 24-bit two's complement. LSB[V] = Vref / (Gain * 2^23).
    Returns µV (float32).
    """
    import numpy as np
    lsb_V = common.ADS1292_VREF / (common.ADS1292_PGA * (2**23))
    return (raw_counts.astype(np.float32) * (lsb_V * 1e6)).astype(np.float32)

