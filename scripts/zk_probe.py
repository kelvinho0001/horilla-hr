#!/usr/bin/env python3
"""
READ-ONLY on-site test: can this machine talk to the ZKTeco device directly (e.g. uFace 202)?

    pip install pyzk
    python zk_probe.py 192.168.1.201            # port 4370, comm key 0
    python zk_probe.py 192.168.1.201 --port 4370 --password 1234

It only connects, reads firmware/serial/user count/attendance count and prints the last 5 punches.
It never clears logs, writes users, or changes device settings. Report the output (or the error) to the developer.
"""

import argparse
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ip")
    p.add_argument("--port", type=int, default=4370)
    p.add_argument("--password", type=int, default=0, help="device communication key")
    p.add_argument("--timeout", type=int, default=15)
    args = p.parse_args()
    try:
        from zk import ZK
    except ImportError:
        sys.exit("Install pyzk first: pip install pyzk")
    conn = None
    try:
        conn = ZK(args.ip, port=args.port, timeout=args.timeout, password=args.password).connect()
        print("CONNECTED")
        for label, call in (
            ("firmware", "get_firmware_version"), ("serial", "get_serialnumber"),
            ("device name", "get_device_name"), ("platform", "get_platform"),
        ):
            try:
                print(f"{label}: {getattr(conn, call)()}")
            except Exception as exc:  # some firmware refuses some queries
                print(f"{label}: unavailable ({exc})")
        users = conn.get_users() or []
        logs = conn.get_attendance() or []
        print(f"users: {len(users)}   attendance records: {len(logs)}")
        for rec in logs[-5:]:
            print(f"  user_id={rec.user_id} time={rec.timestamp} status={getattr(rec, 'status', '')}")
        print("RESULT: direct ZK connection works")
    except Exception as exc:
        print(f"RESULT: FAILED - {exc}")
        sys.exit(1)
    finally:
        if conn:
            conn.disconnect()


if __name__ == "__main__":
    main()
