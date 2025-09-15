from scipy.signal import butter, filtfilt
import neurokit2 as nk
import numpy as np


# https://github.com/MautnerVo/Hydronaut/blob/main/Segmentace/emg_envelope.py
def custom_highpass(signal, cutoff=10, fs=200, order=4):
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    b, a = butter(order, normal_cutoff, btype='high', analog=False)
    filtered = filtfilt(b, a, signal)
    return filtered


def process_mautner(data):
    muscles_order = ["Biceps", "Triceps", "Gastrocnemius", "Quadriceps"]
    all_signals = []
    epsilon = 1

    for muscle in muscles_order:
        # Resample EMG
        emg_raw = np.asarray(data[muscle]["emg_raw"], dtype=float)
        emg_resampled = nk.signal_resample(emg_raw, sampling_rate=1000, desired_length=200)

        # Process and envelope EMG
        # https://github.com/MautnerVo/Hydronaut/blob/main/Segmentace/emg_envelope.py
        emg_clean = custom_highpass(emg_resampled)
        emg_signals, info = nk.emg_process(emg_clean, sampling_rate=200, lowcut=20, highcut=80, method_cleaning="none")
        # emg_log = np.log(np.abs(emg_signals.iloc[:, 2].fillna(0)) + epsilon)
        all_signals.append(emg_signals.iloc[:, 2].fillna(0))

        # Resample IMU quaternions
        imu_quat = np.stack(data[muscle]["imu_quat"], dtype=float)
        for col in range(4):
            quat_resampled = nk.signal_resample(imu_quat[:, col], sampling_rate=100, desired_length=200)
            all_signals.append(quat_resampled)

    # Stack into final array (20, 200) then reshape to (1, 20, 200)
    data = np.stack(all_signals)
    return data[np.newaxis, :, :]
