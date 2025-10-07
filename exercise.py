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
    def describe(self, data, timestamp):
        """Return exercise description data"""
        pass


class ExerciseWideSquat(Exercise):
    """Specific implementation for Wide Squat exercise"""
    def __init__(self):
        super().__init__("wide_squat")
        # Initialize the model
        self.model = Mautner.ModelInterface()
        # Buffer for last 5 seconds of quaternion data
        buffer_size = 10 * IMU_FS  # 5 seconds * samples per second
        self.quat_buffer = deque(maxlen=buffer_size)
        # Keep track of the last processed timestamp
        self.last_processed_timestamp = None
        # Keep track of the last processed quaternion to avoid duplicates
        self.last_processed_quat = None

    def describe(self, data, timestamp):
        """Return exercise description with form analysis"""
        phase = int(self.predict_phase(data)[0]) if data else 0
        
        # Update quaternion buffer with new data
        self.update_quaternion_buffer(data, timestamp)
        
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
        w, x, y, z = q

        # Roll (rotation around X)
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        # Pitch (rotation around Y)
        sinp = 2.0 * (w * y - z * x)
        pitch = math.asin(np.clip(sinp, -1.0, 1.0))

        # Yaw (rotation around Z)
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        # Convert to degrees
        return np.array([math.degrees(angle) for angle in [roll, pitch, yaw]])

    def update_quaternion_buffer(self, data, timestamp):
        """Update the quaternion buffer with new sensor data"""
        if "Quadriceps" not in data or data["Quadriceps"] is None or "imu_quat" not in data["Quadriceps"]:
            return

        # If this is older data, skip it
        if self.last_processed_timestamp is not None and timestamp < self.last_processed_timestamp:
            return

        # Get the quaternion data
        new_quats = data["Quadriceps"]["imu_quat"]
        
        # Find where to start appending new data
        start_idx = 0
        if self.last_processed_quat is not None:
            # Look for the last processed quaternion in the new data
            for i, quat in enumerate(new_quats):
                if np.array_equal(quat, self.last_processed_quat):
                    start_idx = i + 1
                    break

        # Append only the new quaternions (after the last processed one)
        for quat in new_quats[start_idx:]:
            self.quat_buffer.append(quat)
        
        # Update tracking variables
        if len(new_quats) > 0:
            self.last_processed_timestamp = timestamp
            self.last_processed_quat = new_quats[-1]

    def get_euler_angles(self):
        """Calculate Euler angles from the quaternion buffer"""
        # Check if we have enough data (at least 1 second)
        min_samples = IMU_FS
        if len(self.quat_buffer) < min_samples:
            return None

        # Convert quaternions to Euler angles
        euler_angles = [self.quaternion_to_euler(q) for q in list(self.quat_buffer)]
        return np.array(euler_angles)

    def find_movement_peaks(self, euler_angles):
        """Find peaks in yaw and roll angles"""
        peak_data = {}
        
        yaw = euler_angles[:, 2]  # Yaw angles
        roll = euler_angles[:, 0]  # Roll angles

        # Find positive peaks in both Yaw and Roll
        peak_data['yaw_peaks'], yaw_properties = find_peaks(yaw, prominence=40, distance=IMU_FS)
        peak_data['roll_peaks'], roll_properties = find_peaks(roll, prominence=40, distance=IMU_FS)
                
        # Store peak heights for positive peaks
        peak_data['yaw_heights'] = yaw_properties['peak_heights'] if 'peak_heights' in yaw_properties else []
        peak_data['roll_heights'] = roll_properties['peak_heights'] if 'peak_heights' in roll_properties else []
        
        # Find negative peaks in Yaw (for squat depth)
        neg_yaw_peaks, neg_yaw_properties = find_peaks(-yaw, prominence=40, distance=IMU_FS)
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
        
        # Evaluate technique (amplitude consistency)
        technique_status = "good"
        for heights in [peak_data['yaw_heights'], peak_data['roll_heights']]:
            if len(heights) >= 2:  # Need at least 2 peaks to compare
                # Compare consecutive peak amplitudes
                amplitude_diffs = np.abs(np.diff(heights))
                if np.any(amplitude_diffs > 10.0):  # More than 10° difference
                    technique_status = "bad"
                    break
        
        # Evaluate tempo (timing consistency)
        tempo_status = "good"
        for peaks in [peak_data['yaw_peaks'], peak_data['roll_peaks']]:
            if len(peaks) >= 4:  # Need at least 4 peaks to get 3 periods
                # Calculate time between peaks
                periods = np.diff(peaks) / IMU_FS  # Convert to seconds
                
                # Check if the difference between any periods is more than 0.3 seconds
                period_diffs = np.abs(np.diff(periods))
                if np.any(period_diffs > 0.4):
                    tempo_status = "bad"
                    break
        
        # Evaluate movement fluidity
        fluidity_status = "good"
        # Only evaluate during active movement (between first and last peak)
        for angle_name, peaks in [('yaw', peak_data['yaw_peaks']), ('roll', peak_data['roll_peaks'])]:
            if len(peaks) >= 2:  # Need at least two peaks to define movement period
                # Get the angles during the movement period
                start_idx = peaks[0]
                end_idx = peaks[-1]
                angles = euler_angles[start_idx:end_idx + 1]
                
                # Check yaw or roll depending on current iteration
                angle_idx = 2 if angle_name == 'yaw' else 0  # 2 for yaw, 0 for roll
                angle_values = angles[:, angle_idx]
                
                # Calculate consecutive differences
                diffs = np.abs(np.diff(angle_values))
                
                # Check for sequence of 5 consecutive differences > 2°
                for i in range(len(diffs) - 9):  # Need 5 consecutive values
                    if np.all(diffs[i:i+5] > 2.0):
                        fluidity_status = "bad"
                        break
                
                if fluidity_status == "bad":
                    break

        # Evaluate squat depth based on negative yaw peaks
        squat_depth_status = "good"
        if 'neg_yaw_heights' in peak_data and len(peak_data['neg_yaw_heights']) > 1:
            if np.any(peak_data['neg_yaw_heights'] > 0):  # Negative peaks should be below 0°
                squat_depth_status = "bad"

        return technique_status, tempo_status, fluidity_status, squat_depth_status

    def predict_phase(self, data):
        """Predict the phase of the wide squat exercise"""
        # Process input data according to model requirements
        processed_data = process_mautner(data)
        # Make prediction
        return self.model.predict(processed_data[0])


def create_exercise(name):
    """Factory function to create exercise instances"""
    if name == "wide_squat":
        return ExerciseWideSquat()
    else:
        raise ValueError(f"Unknown exercise type: {name}")
