import sys
import json
import threading
from typing import Dict, Any
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout,
                               QHBoxLayout, QWidget, QPushButton, QLabel)
from PySide6.QtCore import QTimer, Signal, QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtCharts import QChart, QChartView, QBarSeries, QBarSet, QBarCategoryAxis, QValueAxis
import paho.mqtt.client as mqtt


class MQTTWorker(QObject):
    """Worker class to handle MQTT communication in a separate thread"""
    data_received = Signal(dict)

    def __init__(self, broker_host="localhost", broker_port=1883):
        super().__init__()
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client = None
        self.is_running = False

    def setup_mqtt(self):
        """Setup MQTT client"""
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

    def on_connect(self, client, userdata, flags, rc, properties):
        """Callback for when MQTT client connects"""
        if rc == 0:
            print("Connected to MQTT broker")
            # Subscribe to MVCP data topic - adjust this topic as needed
            client.subscribe("virtualtrainer/mvcp")
            client.subscribe("virtualtrainer/data")
        else:
            print(f"Failed to connect to MQTT broker: {rc}")

    def on_message(self, client, userdata, msg):
        """Callback for when a message is received"""
        try:
            payload = msg.payload.decode('utf-8')
            data = json.loads(payload)
            self.data_received.emit(data)
        except json.JSONDecodeError:
            print(f"Invalid JSON received: {payload}")
        except Exception as e:
            print(f"Error processing message: {e}")

    def on_disconnect(self, client, userdata, flags, rc, properties):
        """Callback for when MQTT client disconnects"""
        print("Disconnected from MQTT broker")

    def start_mqtt(self):
        """Start MQTT client"""
        self.setup_mqtt()
        try:
            self.client.connect(self.broker_host, self.broker_port, 60)
            self.client.loop_start()
            self.is_running = True
            print(f"MQTT client started - connecting to {self.broker_host}:{self.broker_port}")
        except Exception as e:
            print(f"Failed to start MQTT client: {e}")

    def stop_mqtt(self):
        """Stop MQTT client"""
        if self.client and self.is_running:
            self.client.loop_stop()
            self.client.disconnect()
            self.is_running = False

    def send_command(self, command_data):
        """Send command to MQTT topic"""
        if self.client and self.is_running:
            try:
                command_json = json.dumps(command_data)
                self.client.publish("virtualtrainer/control", command_json)
                print(f"Sent command: {command_json}")
            except Exception as e:
                print(f"Failed to send command: {e}")


class MVCPVisualizer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MVCP Real-time Visualizer")
        self.setGeometry(100, 100, 900, 700)

        # Muscle groups from your MQTT data
        self.muscle_groups = ["Biceps", "Triceps", "Quadriceps", "Gastrocnemius"]

        # Current MVCP values
        self.mvcp_data: Dict[str, float] = {muscle: 0.0 for muscle in self.muscle_groups}
        # Previous MVCP values to track changes
        self.previous_mvcp_data: Dict[str, float] = {muscle: 0.0 for muscle in self.muscle_groups}
        # Track which specific muscles have changed
        self.changed_muscles = set()

        # Setup MQTT worker in separate thread
        self.mqtt_worker = MQTTWorker()
        self.mqtt_thread = threading.Thread(target=self.setup_mqtt_worker, daemon=True)
        self.mqtt_thread.start()

        # MVC capture toggle state
        self.is_mvc_setting = False
        # Wide squat toggle state
        self.wide_squat_active = False
        self.phase_key = 'Phase'
        self.phase_raw = None
        self.phase_value = 0.0

        self.setup_ui()

    def setup_mqtt_worker(self):
        """Setup MQTT worker in separate thread"""
        self.mqtt_worker.data_received.connect(self.update_data)
        self.mqtt_worker.start_mqtt()

    def setup_ui(self):
        """Setup the user interface"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main layout
        main_layout = QVBoxLayout(central_widget)

        # Title
        title_label = QLabel("Real-time MVCP Visualization")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        main_layout.addWidget(title_label)

        # Chart setup
        self.setup_chart()
        main_layout.addWidget(self.chart_view)

        # Control buttons
        button_layout = QHBoxLayout()

        self.reset_mvc_btn = QPushButton("Set MVC")
        self.reset_mvc_btn.clicked.connect(self.toggle_mvc)
        self.reset_mvc_btn.setMinimumHeight(40)
        self.reset_mvc_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff6b6b;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff5252;
            }
        """)

        button_layout.addWidget(self.reset_mvc_btn)

        # Wide squat toggle button (bottom-right)
        self.wide_squat_btn = QPushButton("Set wide squat")
        self.wide_squat_btn.clicked.connect(self.toggle_wide_squat)
        self.wide_squat_btn.setMinimumHeight(40)
        self.wide_squat_btn.setStyleSheet("""
            QPushButton {
                background-color: #5c7cfa;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4263eb;
            }
        """)

        button_layout.addWidget(self.wide_squat_btn)
        main_layout.addLayout(button_layout)

        # Status label
        self.status_label = QLabel("Status: Waiting for MQTT data...")
        self.status_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.status_label)

        # Timer for periodic updates
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.refresh_chart)
        self.update_timer.start(100)  # Update every 100ms

    def setup_chart(self):
        """Setup the bar chart using QtCharts"""
        # Create chart
        self.chart = QChart()
        self.chart.setTitle("MVCP by Muscle Group")
        self.chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)

        # Create bar series with single bar set
        self.bar_series = QBarSeries()

        # Create single bar set for all muscles
        self.bar_set = QBarSet("MVCP Values")

        # Initialize with test data to make bars visible
        initial_values = [0.0, 0.0, 0.0, 0.0]
        for i, value in enumerate(initial_values):
            self.mvcp_data[self.muscle_groups[i]] = value
            self.previous_mvcp_data[self.muscle_groups[i]] = value
            self.bar_set.append(value)

        # Force initial chart update for all muscles
        self.changed_muscles = set(self.muscle_groups)

        # Add bar set to series
        self.bar_series.append(self.bar_set)

        # Add series to chart
        self.chart.addSeries(self.bar_series)

        # Create axes
        self.axis_x = QBarCategoryAxis()
        self.axis_x.append(self.muscle_groups)
        self.chart.addAxis(self.axis_x, Qt.AlignBottom)
        self.bar_series.attachAxis(self.axis_x)

        self.axis_y = QValueAxis()
        self.axis_y.setRange(0, 100)
        self.axis_y.setTitleText("MVCP (%)")
        self.chart.addAxis(self.axis_y, Qt.AlignLeft)
        self.bar_series.attachAxis(self.axis_y)

        # Create chart view
        self.chart_view = QChartView(self.chart)

        # Initial chart update
        self.refresh_chart()

    def update_data(self, data):
        """Update MVCP data from MQTT message"""
        try:
            changed_muscles_this_update = set()

            if 'sensors' in data:
                for muscle_name, sensor_data in data['sensors'].items():
                    if muscle_name in self.mvcp_data and 'mvcp' in sensor_data:
                        new_value = self._to_float_or_none(sensor_data['mvcp'])
                        if new_value is not None and self.mvcp_data[muscle_name] != new_value:
                            self.mvcp_data[muscle_name] = new_value
                            changed_muscles_this_update.add(muscle_name)
            # Handle exercise phase for wide squat
            try:
                exdesc = data.get('exercise_description') if isinstance(data, dict) else None
                if isinstance(exdesc, dict) and 'phase' in exdesc:
                    p = float(exdesc['phase'])
                    # Map: 0 -> 100 (max), 50 -> 0, 100 -> 100
                    self.phase_value = max(0.0, min(100.0, 2.0 * abs(p - 50.0)))
                    if self.wide_squat_active:
                        # Ensure column exists and update value
                        if self.phase_key not in self.muscle_groups:
                            self._enable_phase_column()
                        self.mvcp_data[self.phase_key] = self.phase_value
                        changed_muscles_this_update.add(self.phase_key)
            except Exception as _e:
                # Phase handling is best-effort; ignore errors
                pass


            # Update the set of changed muscles
            self.changed_muscles.update(changed_muscles_this_update)

            # Update status based on changes
            if changed_muscles_this_update:
                changed_list = ", ".join(changed_muscles_this_update)
                if 'timestamp' in data:
                    timestamp = data['timestamp']
                    self.status_label.setText(f"Status: Updated {changed_list} - (Last: {timestamp})")
                else:
                    self.status_label.setText(f"Status: Updated {changed_list}")
            else:
                # Update status to show data received but unchanged
                if 'timestamp' in data:
                    timestamp = data['timestamp']
                    self.status_label.setText(f"Status: Data received (no changes) - (Last: {timestamp})")

        except Exception as e:
            print(f"Error updating data: {e}")
            self.status_label.setText(f"Status: Error processing data - {str(e)}")

    def refresh_chart(self):
        """Refresh only the bars for muscles that have changed"""
        try:
            # Only update if there are muscles that have changed
            if not self.changed_muscles:
                return

            # Process each changed muscle individually
            for muscle in list(self.changed_muscles):
                if muscle not in self.muscle_groups:
                    self.changed_muscles.discard(muscle)
                    continue

                muscle_index = self.muscle_groups.index(muscle)
                value = self.mvcp_data.get(muscle, 0.0)

                # Update the specific bar value
                if muscle_index < self.bar_set.count():
                    self.bar_set.replace(muscle_index, value)
                else:
                    # If the bar set is not long enough, extend it
                    while self.bar_set.count() <= muscle_index:
                        self.bar_set.append(0)
                    self.bar_set.replace(muscle_index, value)

            # Update previous values for the changed muscles and clear the changed set
            for muscle in self.changed_muscles:
                self.previous_mvcp_data[muscle] = self.mvcp_data[muscle]

            changed_list = ", ".join(self.changed_muscles)
#            print(f"Chart updated for muscles: {changed_list}")  # Debug message

            self.changed_muscles.clear()

        except Exception as e:
            print(f"Error refreshing chart: {e}")

            self.changed_muscles = {m for m in self.changed_muscles if m in self.muscle_groups}
            if not self.changed_muscles:
                return

            self.refresh_chart_full()

    def refresh_chart_full(self):
        """Fallback method: Full refresh of the entire chart"""
        try:
            print("Performing full chart refresh")

            # Clear existing data
            self.bar_set.remove(0, self.bar_set.count())

            # Add all muscle values to the single bar set
            for muscle in self.muscle_groups:
                value = self.mvcp_data[muscle]
                self.bar_set.append(value)

            # Update all previous values
            self.previous_mvcp_data = self.mvcp_data.copy()

        except Exception as e:
            print(f"Error in full chart refresh: {e}")

        except Exception as e:
            print(f"Error refreshing chart: {e}")
            # Fallback to full refresh if granular update fails
            self.refresh_chart_full()

    def toggle_mvc(self):
        """Toggle MVC set mode (publish mvc_start / mvc_stop)"""
        if not self.is_mvc_setting:
            # Start MVC capture
            command = {"cmd": "mvc_start"}
            self.mqtt_worker.send_command(command)
            self.is_mvc_setting = True
            self.reset_mvc_btn.setText("Stop MVC")
            self.status_label.setText("Status: MVC capture started")
        else:
            # Stop MVC capture
            command = {"cmd": "mvc_stop"}
            self.mqtt_worker.send_command(command)
            self.is_mvc_setting = False
            self.reset_mvc_btn.setText("Set MVC")
            self.status_label.setText("Status: MVC capture stopped")

    def get_timestamp(self):
        """Get current timestamp"""
        from datetime import datetime
        return datetime.now().isoformat()

    def _to_float_or_none(self, x):
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if s == "" or s.upper() in ("N/A", "NA", "NULL", "NONE"):
            return None
        try:
            return float(s)
        except Exception:
            return None

    def _update_categories_and_barset(self):
        """Ensure axis categories and bar set length match self.muscle_groups."""
        # Update X-axis categories
        try:
            self.axis_x.clear()
        except Exception:
            pass
        self.axis_x.append(self.muscle_groups)
        # Ensure bar_set count matches number of categories
        needed = len(self.muscle_groups)
        current = self.bar_set.count()
        if current < needed:
            for _ in range(needed - current):
                self.bar_set.append(0.0)
        elif current > needed:
            # Remove extra bars from the end
            try:
                self.bar_set.remove(needed, current - needed)
            except Exception:
                # Fallback: rebuild entire bar_set
                values = [0.0]*needed
                for idx, name in enumerate(self.muscle_groups):
                    if name in self.mvcp_data:
                        values[idx] = float(self.mvcp_data[name])
                self.bar_series.remove(self.bar_set)
                self.bar_set = QBarSet("MVCP Values")
                for v in values:
                    self.bar_set.append(v)
                self.bar_series.append(self.bar_set)

    def _enable_phase_column(self):
        if self.phase_key not in self.muscle_groups:
            self.muscle_groups.append(self.phase_key)
            self.mvcp_data[self.phase_key] = getattr(self, "phase_value", 0.0)
            self.previous_mvcp_data[self.phase_key] = self.mvcp_data[self.phase_key]
            self._update_categories_and_barset()
            self.changed_muscles.add(self.phase_key)

    def _disable_phase_column(self):
        if self.phase_key in self.muscle_groups:
            # Remove from categories and data
            try:
                idx = self.muscle_groups.index(self.phase_key)
            except ValueError:
                idx = -1
            if idx >= 0:
                del self.muscle_groups[idx]
            if self.phase_key in self.mvcp_data:
                del self.mvcp_data[self.phase_key]
            if self.phase_key in self.previous_mvcp_data:
                del self.previous_mvcp_data[self.phase_key]
            # Update axis and bar set lengths
            self._update_categories_and_barset()
            self.changed_muscles.discard(self.phase_key)
            # Full refresh to ensure removal is reflected
            self.refresh_chart_full()


    def toggle_wide_squat(self):
        """Toggle 'wide_squat' exercise set/unset and show/hide Phase column."""
        if not self.wide_squat_active:
            # Activate wide squat
            command = {"cmd": "set_exercise", "val": "wide_squat"}
            self.mqtt_worker.send_command(command)
            self.wide_squat_active = True
            self.wide_squat_btn.setText("Unset wide squat")
            # Ensure Phase column is visible
            self._enable_phase_column()
            self.status_label.setText("Status: Wide squat enabled")
        else:
            # Deactivate wide squat
            command = {"cmd": "set_exercise", "val": ""}
            self.mqtt_worker.send_command(command)
            self.wide_squat_active = False
            self.wide_squat_btn.setText("Set wide squat")
            # Hide Phase column
            self._disable_phase_column()
            self.status_label.setText("Status: Wide squat disabled")

    def closeEvent(self, event):
        """Handle application close"""
        self.mqtt_worker.stop_mqtt()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MVCPVisualizer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()