import json
import logging
import paho.mqtt.client as mqtt


# TODO Vytvořit skript s GUI, který bude pro jednotlivé svalové skupiny ukazovat MVC% (třeba v Bar chartu), umožní resetovat MVC a orientaci (přes MQTT).
class Mqtt:
    def __init__(self, host, port, data_topic, control_topic):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata={"data_topic": data_topic,
                                                                              "control_topic": control_topic})

        self.data_topic = data_topic
        self.control_topic = control_topic

        self._control_handler = None

        self.client.on_connect = self._on_connect
        self.client.on_subscribe = self._on_subscribe
        self.client.on_message = self._on_message

        self.client.connect(host, port)
        self.client.loop_start()

    def set_control_handler(self, handler):
        self._control_handler = handler

    def send_payload(self, payload):
        self.client.publish(self.data_topic, json.dumps(payload), qos=0)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logging.error("Unable to connect to mqtt: {reason_code}. loop_forever() will retry connection")
            exit(1)

        else:
            # we should always subscribe from on_connect callback to be sure
            # our subscribed is persisted across reconnections.
            client.subscribe(self.control_topic, qos=1)

    def _on_subscribe(self, client, userdata, mid, reason_code_list, properties):
        if reason_code_list[0].is_failure:
            print(f"Broker rejected you subscription: {reason_code_list[0]}")

    def _on_message(self, client, userdata, message):
        if message.topic == self.control_topic and self._control_handler is not None:
            try:
                payload = json.loads(message.payload.decode("utf-8"))

            except json.decoder.JSONDecodeError:
                logging.error("Unable to decode payload: {message}")

                return

            try:
                self._control_handler(payload)

                return
            except Exception as e:
                logging.exception(f"Control handler error: {e}")

        logging.info("Unknown message. Topic: " + str(message.topic) + ", message: " + str(message.payload))

