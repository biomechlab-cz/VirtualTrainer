"""
mqtt_replay.py
- Načte NDJSON záznam z mqtt_recorder.py
- Publikuje zprávy v původním pořadí s původními rozestupy (nebo zrychleně)
- Možnost přepsat QoS/retain, přidat prefix k topicu, změnit časování
"""
import argparse, json, time, base64, sys
import paho.mqtt.client as mqtt
from typing import Iterable

def _decode_payload(obj: dict) -> bytes:
    enc = obj.get("encoding")
    data = obj.get("data", "")
    if enc == "utf-8":
        return data.encode("utf-8")
    elif enc == "base64":
        return base64.b64decode(data.encode("ascii"))
    else:
        # fallback – zkusíme přímo string
        return str(data).encode("utf-8")

def _iter_records(paths: list[str]) -> Iterable[dict]:
    for p in paths:
        with (open(p, "rb")) as f:
            for line in f:
                if not line.strip():
                    continue
                yield json.loads(line.decode("utf-8"))

def main():
    ap = argparse.ArgumentParser(description="Replay MQTT messages from NDJSON.")
    ap.add_argument("files", nargs="+", help="NDJSON soubory v pořadí přehrání")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--client-id", default=None)
    ap.add_argument("--username", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--cafile", default=None)
    ap.add_argument("--certfile", default=None)
    ap.add_argument("--keyfile", default=None)
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--keepalive", type=int, default=60)
    ap.add_argument("--speed", type=float, default=1.0, help=">1.0 = zrychlit, 0 = bez čekání")
    ap.add_argument("--qos", type=int, choices=[0,1,2], default=None, help="Přepsat QoS (jinak původní)")
    ap.add_argument("--retain", type=str, choices=["orig","true","false"], default="false",
                    help="Retain vlajka: orig = dle záznamu, true/false = přepsat")
    ap.add_argument("--topic-prefix", default="", help="Přidat prefix k topicu (např. 'replay/') ")
    ap.add_argument("--start-offset", type=float, default=0.0, help="Přeskočit prvních N sekund")
    args = ap.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.client_id, protocol=mqtt.MQTTv5)
    if args.username:
        client.username_pw_set(args.username, args.password)

    if args.cafile:
        import ssl
        client.tls_set(ca_certs=args.cafile, certfile=args.certfile, keyfile=args.keyfile)
        if args.insecure:
            client.tls_insecure_set(True)
        if args.port == 1883:
            args.port = 8883

    client.connect(args.host, args.port, keepalive=args.keepalive)
    client.loop_start()

    # načti vše, seřaď podle ts (pro jistotu)
    recs = list(_iter_records(args.files))
    recs.sort(key=lambda r: r.get("ts", 0.0))
    if not recs:
        print("[WARN] Žádné zprávy k přehrání.")
        client.loop_stop(); client.disconnect()
        sys.exit(0)

    t0 = recs[0].get("ts", 0.0) + args.start_offset
    sent = 0
    start = time.time()

    for r in recs:
        ts = float(r.get("ts", 0.0))
        if ts < t0:
            continue
        # čekání dle původních rozestupů
        if args.speed > 0:
            wait = (ts - t0) / max(args.speed, 1e-9) - (time.time() - start)
            if wait > 0:
                time.sleep(wait)
        # připrav publish
        topic = args.topic_prefix + r.get("topic", "")
        payload = _decode_payload(r.get("payload", {}))
        qos = args.qos if args.qos is not None else int(r.get("qos", 0))
        if args.retain == "orig":
            retain = bool(r.get("retain", False))
        else:
            retain = (args.retain == "true")
        client.publish(topic, payload=payload, qos=qos, retain=retain)
        sent += 1
        if sent % 100 == 0:
            print(f"[INFO] Sent {sent} msgs…")

    client.loop_stop()
    client.disconnect()
    print(f"[DONE] Přehráno zpráv: {sent}")

if __name__ == "__main__":
    main()
