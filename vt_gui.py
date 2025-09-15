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

        self.reset_mvc_btn = QPushButton("Reset MVC")
        self.reset_mvc_btn.clicked.connect(self.reset_mvc)
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

        self.reset_orientation_btn = QPushButton("Reset Orientation")
        self.reset_orientation_btn.clicked.connect(self.reset_orientation)
        self.reset_orientation_btn.setMinimumHeight(40)
        self.reset_orientation_btn.setStyleSheet("""
            QPushButton {
                background-color: #4ecdc4;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45b7aa;
            }
        """)

        button_layout.addWidget(self.reset_mvc_btn)
        button_layout.addWidget(self.reset_orientation_btn)
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
        self.chart.setAnimationOptions(QChart.SeriesAnimations)

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
                        new_value = float(sensor_data['mvcp'])
                        if self.mvcp_data[muscle_name] != new_value:
                            self.mvcp_data[muscle_name] = new_value
                            changed_muscles_this_update.add(muscle_name)

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
            for muscle in self.changed_muscles:
                muscle_index = self.muscle_groups.index(muscle)
                value = self.mvcp_data[muscle]

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
            print(f"Chart updated for muscles: {changed_list}")  # Debug message

            self.changed_muscles.clear()

        except Exception as e:
            print(f"Error refreshing chart: {e}")
            # Fallback to full refresh if granular update fails
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

    def reset_mvc(self):
        """Send reset MVC command via MQTT"""
        command = {
            "cmd": "reset_mvc",
        }
        self.mqtt_worker.send_command(command)
        self.status_label.setText("Status: Reset MVC command sent")

    def reset_orientation(self):
        """Send reset orientation command via MQTT"""
        command = {
            "cmd": "reset_orientation",
        }
        self.mqtt_worker.send_command(command)
        self.status_label.setText("Status: Reset Orientation command sent")

    def get_timestamp(self):
        """Get current timestamp"""
        from datetime import datetime
        return datetime.now().isoformat()

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