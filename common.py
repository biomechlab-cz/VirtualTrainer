import logging
import signal
import threading

EMG_FS = 1000
IMU_FS = 100

G0 = 9.80665  # m/s^2

# ADS1292: LSB → Volt = Vref / (Gain * 2^23).
ADS1292_VREF = 2.42   # V (internal ref ADS1292)
ADS1292_PGA  = 8     # amp (1,2,3,4,6,8,12)

# --- Robust envelope (gate) params ---
# outlier clip (percentiles) in a ~0.4 s window
CLIP_WIN_SAMPLES   = 400
CLIP_P_LOW         = 1.0
CLIP_P_HIGH        = 99.0
# step detection (for envelope blanking)
STEP_THR_uV        = 150.0  # |Δx| > thr → blanking
STEP_HOLD_MS       = 150
# envelope (EMA) + protections
ENV_ALPHA          = 0.02   # EMA speed (0.01–0.03)
ENV_DECAY          = 0.995  # envelope decay when gate is active
ENV_MAX_RISE_PCT   = 0.20   # max +20 % / sample
# IMU gate (motion vs. rest)
MOTION_ACC_TOL_G   = 0.08   # |a|-1g < 0.08g
MOTION_GYRO_THR_DPS= 30.0   # < 30 dps
MOTION_HOLD_MS     = 100    # minimum duration of motion/rest to switch state

CLEAN_HP_FC_HZ     = 20.0   # Clean channel (baseline-around-zero) high-pass cutoff [Hz]

stop_event = threading.Event()


def _signal_handler(seg, _):
    logging.warning("Shutting down...")
    stop_event.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _signal_handler)