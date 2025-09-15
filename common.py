import logging
import signal
import threading

EMG_FS = 1000
IMU_FS = 100

G0 = 9.80665  # m/s^2

# ADS1292: LSB → Volt = Vref / (Gain * 2^23).
ADS1292_VREF = 2.42   # V (internal ref ADS1292)
ADS1292_PGA  = 4     # amp (1,2,3,4,6,8,12)


stop_event = threading.Event()


def _signal_handler(seg, _):
    logging.warning("Shutting down...")
    stop_event.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _signal_handler)