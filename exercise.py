import models.mautner.model as Mautner
from processing import process_mautner
from abc import ABC, abstractmethod
import numpy as np
from collections import deque
import math
from scipy.signal import find_peaks
from sensor import IMU_FS


class Exercise(ABC):
    """Abstract base class for exercises"""
    def __init__(self, name=None):
        self.name = name

    @abstractmethod
    def describe(self, data):
        """Return exercise description data"""
        pass


class ExerciseWideSquat(Exercise):
    """Specific implementation for Wide Squat exercise"""
    def __init__(self):
        super().__init__("wide_squat")
        # Initialize the model
        self.model = Mautner.ModelInterface()
        # Buffer for last 5 seconds of quaternion data
        buffer_size = 5 * IMU_FS  # 5 seconds * samples per second
        self.quat_buffer = deque(maxlen=buffer_size)

    def describe(self, data):
        """Return exercise description with form analysis"""
        phase = int(self.predict_phase(data)[0]) if data else 0
        
        # Update quaternion buffer with new data
        self.update_quaternion_buffer(data)
        
        # Calculate Euler angles once for all evaluations
        euler_angles = self.get_euler_angles()
        
        # Evaluate movement parameters using the euler angles
        technique_status, tempo_status, fluidity_status, squat_depth_status = self.evaluate_movement(euler_angles)
        
        return {
            "phase": phase,
            "feet": "good",  # TODO: Implement actual feet position analysis
            "squat_depth": squat_depth_status,
            "movement_fluidity": fluidity_status,
            "technique": technique_status,
            "tempo": tempo_status
        }

    def quaternion_to_euler(self, q):
        """Convert quaternion to Euler angles (roll, pitch, yaw) in degrees"""
        # Extract quaternion components
        if isinstance(q, np.ndarray) and q.ndim == 2:
            w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
        else:
            w, x, y, z = q

        # Roll (rotation around X)
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        roll = np.arctan2(sinr_cosp, cosr_cosp)

        # Pitch (rotation around Y)
        sinp = 2.0 * (w * y - z * x)
        pitch = np.arcsin(np.clip(sinp, -1.0, 1.0))

        # Yaw (rotation around Z)
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        yaw = np.arctan2(siny_cosp, cosy_cosp)

        # Convert to degrees and stack
        return np.rad2deg(np.column_stack([roll, pitch, yaw]) if isinstance(q, np.ndarray) and q.ndim == 2 else np.array([roll, pitch, yaw]))

    def update_quaternion_buffer(self, data):
        """Update the quaternion buffer with new sensor data"""
        if "Quadriceps" in data and data["Quadriceps"] is not None and "imu_quat" in data["Quadriceps"]:
            self.quat_buffer.append(data["Quadriceps"]["imu_quat"])

    def get_euler_angles(self):
        """Calculate Euler angles from the quaternion buffer"""
        # Check if we have enough data (at least 1 second)
        min_samples = IMU_FS
        if len(self.quat_buffer) < min_samples:
            return None

        # Convert buffer to numpy array for vectorized operations
        quat_array = np.array(list(self.quat_buffer))
        return self.quaternion_to_euler(quat_array)

    def find_movement_peaks(self, euler_angles):
        """Find peaks in yaw and roll angles"""
        peak_data = {}
        
        yaw = euler_angles[:, 2]  # Yaw angles
        roll = euler_angles[:, 0]  # Roll angles

        # Find positive peaks in both Yaw and Roll
        peak_data['yaw_peaks'], yaw_properties = find_peaks(yaw, prominence=40, distance=IMU_FS/2)
        peak_data['roll_peaks'], roll_properties = find_peaks(roll, prominence=40, distance=IMU_FS/2)
                
        # Store peak heights for positive peaks
        peak_data['yaw_heights'] = yaw_properties['peak_heights'] if 'peak_heights' in yaw_properties else []
        peak_data['roll_heights'] = roll_properties['peak_heights'] if 'peak_heights' in roll_properties else []
        
        # Find negative peaks in Yaw (for squat depth)
        neg_yaw_peaks, neg_yaw_properties = find_peaks(-yaw, prominence=40, distance=IMU_FS/2)
        peak_data['neg_yaw_peaks'] = neg_yaw_peaks
        peak_data['neg_yaw_heights'] = -neg_yaw_properties['peak_heights'] if 'peak_heights' in neg_yaw_properties else []

        return peak_data

    def evaluate_movement(self, euler_angles):
        """
        Evaluate technique, tempo, movement fluidity, and squat depth using the same data
        Returns (technique_status, tempo_status, fluidity_status, squat_depth_status)
        """
        if euler_angles is None:
            return "good", "good", "good", "good"  # Not enough data to evaluate

        peak_data = self.find_movement_peaks(euler_angles)

        # Initialize all statuses as good
        technique_status = tempo_status = fluidity_status = squat_depth_status = "good"

        # Evaluate technique using vectorized operations
        for heights in [peak_data['yaw_heights'], peak_data['roll_heights']]:
            if len(heights) >= 2 and np.any(np.abs(np.diff(heights)) > 10.0):
                technique_status = "bad"
                break

        # Evaluate tempo using vectorized operations
        for peaks in [peak_data['yaw_peaks'], peak_data['roll_peaks']]:
            if len(peaks) >= 4:
                periods = np.diff(peaks) / IMU_FS
                if np.any(np.abs(np.diff(periods)) > 0.3):
                    tempo_status = "bad"
                    break

        # Evaluate movement fluidity with optimized algorithm
        for angle_idx, peaks in [(2, peak_data['yaw_peaks']), (0, peak_data['roll_peaks'])]:
            if len(peaks) >= 2:
                # Get the angles during the movement period
                angle_values = euler_angles[peaks[0]:peaks[-1] + 1, angle_idx]
                diffs = np.abs(np.diff(angle_values))
                
                # Use strided array to check sequences of 10 values efficiently
                if len(diffs) >= 10:
                    # Create view of array with rolling windows of size 10
                    windows = np.lib.stride_tricks.sliding_window_view(diffs, 10)
                    if np.any(np.all(windows > 2.0, axis=1)):
                        fluidity_status = "bad"
                        break

        # Evaluate squat depth using vectorized operations
        if 'neg_yaw_heights' in peak_data and len(peak_data['neg_yaw_heights']) > 1:
            squat_depth_status = "bad" if np.any(peak_data['neg_yaw_heights'] > 0) else "good"

        return technique_status, tempo_status, fluidity_status, squat_depth_status

    def predict_phase(self, data):
        """Predict the phase of the wide squat exercise"""
        # Process input data according to model requirements
        processed_data = process_mautner(data)
        # Make prediction
        return self.model.predict(processed_data)


def create_exercise(name):
    """Factory function to create exercise instances"""
    if name == "wide_squat":
        return ExerciseWideSquat()
    else:
        raise ValueError(f"Unknown exercise type: {name}")
