# main.py
import argparse

import common
import logging

from common import stop_event, threading
from server import Server
from mqtt import Mqtt

from influxdb import InfluxWriter, URL as IFX_URL, TOKEN as IFX_TOKEN, ORG as IFX_ORG, BUCKET as IFX_BUCKET

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0", help="TCP bind host")
    parser.add_argument("--port", type=int, default=8888, help="TCP bind port")
    parser.add_argument("--mqtt-host", required=True, help="MQTT broker host")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-data-topic", default="virtualtrainer/data", help="MQTT topic for data")
    parser.add_argument("--mqtt-control-topic", default="virtualtrainer/control", help="MQTT topic for control")

    parser.add_argument("--influx-enable", action="store_true", help="Enable writing data to InfluxDB")
    parser.add_argument("--influx-url", default=IFX_URL)
    parser.add_argument("--influx-token", default=IFX_TOKEN)
    parser.add_argument("--influx-org", default=IFX_ORG)
    parser.add_argument("--influx-bucket", default=IFX_BUCKET)
    parser.add_argument("--influx-batch-size", type=int, default=5000)
    parser.add_argument("--influx-flush-ms", type=int, default=1000)

    parser.add_argument("--ads-vref", type=float, default=common.ADS1292_VREF,
                        help="ADS1292 reference voltage [V]")
    parser.add_argument("--ads-gain", type=int, default=common.ADS1292_PGA,
                        help="ADS1292 PGA gain (1,2,3,4,6,8,12)")


    args = parser.parse_args()

    common.ADS1292_VREF = float(args.ads_vref)
    common.ADS1292_PGA = int(args.ads_gain)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    )

    mqtt = Mqtt(args.mqtt_host, args.mqtt_port, args.mqtt_data_topic, args.mqtt_control_topic)

    influx = None
    if args.influx_enable:
        influx = InfluxWriter(
            url=args.influx_url, token=args.influx_token, org=args.influx_org, bucket=args.influx_bucket,
            batch_size=args.influx_batch_size, flush_interval_ms=args.influx_flush_ms
        )

    server = Server(args.host, args.port, mqtt, influx)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down...")

        stop_event.set()
        mqtt.close()
        if influx:
            influx.close()

        for t in threading.enumerate():
            if t is not threading.current_thread():
                t.join(timeout=2)

if __name__ == "__main__":
    main()
