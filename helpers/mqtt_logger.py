"""
mqtt_recorder.py
- Připojí se k MQTT brokeru, odbírá zadané topicy (default '#')
- Každou přijatou zprávu uloží jako NDJSON řádek:
  {"ts": 1699999999.123, "topic":"a/b", "qos":1, "retain":false,
   "payload":{"encoding":"utf-8","data":"..."}}
- Pokud payload není platné UTF-8, uloží se jako base64: {"encoding":"base64",...}
- Podporuje: TLS, user/pass, gzip výstup, více --topic, filtrování $SYS, limit času/počtu zpráv
"""
import argparse, gzip, json, signal, sys, time, base64, os
from datetime import datetime, timezone
from typing import Optional, IO
import paho.mqtt.client as mqtt

def _open_out(path: str) -> IO[bytes]:
    if path.endswith(".gz"):
        return gzip.open(path, "ab")
    return open(path, "ab")

def _now() -> float:
    return time.time()

def _encode_payload(b: bytes) -> dict:
    try:
        s = b.decode("utf-8")
        return {"encoding": "utf-8", "data": s}
    except UnicodeDecodeError:
        return {"encoding": "base64", "data": base64.b64encode(b).decode("ascii")}

def main():
    ap = argparse.ArgumentParser(description="Record MQTT messages to NDJSON.")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--client-id", default=None)
    ap.add_argument("--username", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--cafile", default=None, help="Path to CA certificate (enables TLS)")
    ap.add_argument("--certfile", default=None)
    ap.add_argument("--keyfile", default=None)
    ap.add_argument("--insecure", action="store_true", help="Disable TLS cert verification")
    ap.add_argument("--keepalive", type=int, default=60)
    ap.add_argument("--topic", action="append", default=["virtualtrainer/data", "virtualtrainer/control"], help="Topic filter to subscribe (can repeat)")
    ap.add_argument("--no-sys", action="store_true", help="Skip topics starting with $SYS")
    ap.add_argument("--outfile", default="mqtt_record.ndjson", help="Output file (.gz supported)")
    ap.add_argument("--max-messages", type=int, default=0, help="Stop after N messages (0 = unlimited)")
    ap.add_argument("--duration", type=float, default=0, help="Stop after seconds (0 = unlimited)")
    ap.add_argument("--qos", type=int, default=0, choices=[0,1,2], help="Subscribe QoS")
    args = ap.parse_args()

    stop_flag = {"stop": False}
    start_ts = _now()

    def request_stop(*_):
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    out = _open_out(args.outfile)

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc != 0:
            print(f"[ERR] MQTT connect failed rc={rc}", file=sys.stderr)
            stop_flag["stop"] = True
            return
        for t in args.topic:
            client.subscribe(t, qos=args.qos)
        print(f"[OK] Connected. Subscribed: {args.topic}. Recording to {args.outfile}")

    msg_count = 0

    def on_message(client, userdata, msg: mqtt.MQTTMessage):
        nonlocal msg_count
        if args.no_sys and msg.topic.startswith("$SYS"):
            return
        rec = {
            "ts": _now(),  # epoch seconds float (receive time)
            "topic": msg.topic,
            "qos": int(msg.qos),
            "retain": bool(msg.retain),
            "payload": _encode_payload(msg.payload or b""),
        }
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        out.write(line)
        # pro .gz je lepší občas flushnout
        if msg_count % 100 == 0:
            out.flush()
        msg_count += 1
        if args.max_messages and msg_count >= args.max_messages:
            stop_flag["stop"] = True

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.client_id, protocol=mqtt.MQTTv5)
    if args.username:
        client.username_pw_set(args.username, args.password)

    # TLS
    if args.cafile:
        import ssl
        client.tls_set(ca_certs=args.cafile, certfile=args.certfile, keyfile=args.keyfile)
        if args.insecure:
            client.tls_insecure_set(True)
        if args.port == 1883:
            args.port = 8883

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(args.host, args.port, keepalive=args.keepalive)
    client.loop_start()

    try:
        while not stop_flag["stop"]:
            if args.duration and (_now() - start_ts) >= args.duration:
                break
            time.sleep(0.05)
    finally:
        client.loop_stop()
        client.disconnect()
        out.flush()
        out.close()
        print(f"[DONE] Messages recorded: {msg_count}")

if __name__ == "__main__":
    main()
