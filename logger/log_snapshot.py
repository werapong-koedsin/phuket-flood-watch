#!/usr/bin/env python3
"""
log_snapshot.py — เก็บประวัติตัวแปรเตือนภัยทุกตัว ต่อท้าย data/history.jsonl (1 บรรทัด/รอบ)
รันโดย GitHub Actions ทุก 15 นาที — ใช้เป็นข้อมูลสำหรับหน้า history.html และงาน calibrate เกณฑ์

แหล่งข้อมูล (ผ่านช่องทางที่มีอยู่แล้วทั้งหมด ไม่เขียน parser ซ้ำ):
  - ฝนสะสม 24 ชม. รายอำเภอ: GAS proxy ?src=rain (HII)
  - ฝนพยากรณ์ 2 ชม. (NWP): GAS proxy ?lat&lon (TMD ผ่าน cache เดิม) จุดตัวแทน 3 จุด/อำเภอ
  - เรดาร์ nowcast: อ่าน data/nowcast.json ในเครื่อง (repo เดียวกัน)

★ แก้บรรทัดเดียวก่อนใช้: PROXY_URL ให้เป็น URL /exec ของคุณ
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

PROXY_URL = "PASTE_YOUR_GAS_EXEC_URL_HERE"   # ★ URL GAS Web App (ลงท้าย /exec)
KEEP_DAYS = 30                                # เก็บประวัติย้อนหลังกี่วัน (ที่ 15 นาที/จุด ≈ 96 จุด/วัน)

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "history.jsonl"
NOWCAST = ROOT / "data" / "nowcast.json"

DISTRICTS = {
    "Kathu":         [[7.9172, 98.3137], [7.8960, 98.2960], [7.9180, 98.3330]],
    "Thalang":       [[8.0556, 98.3458], [8.0030, 98.2970], [8.0960, 98.3030]],
    "Mueang Phuket": [[7.8840, 98.3870], [7.8460, 98.3390], [7.7710, 98.3250]],
}


def get_json(url, timeout=60):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_rain24():
    """ฝนรายอำเภอจาก GAS ?src=rain → ({district: rain24_max}, {district: rain1_max})"""
    try:
        j = get_json(f"{PROXY_URL}?src=rain&t={int(datetime.now().timestamp())}")
        d = j.get("districts", {})
        return ({k: v.get("rain24_max") for k, v in d.items()},
                {k: v.get("rain1_max") for k, v in d.items()})
    except Exception as e:
        print(f"rain24 skip: {e}", file=sys.stderr)
        return {}, {}


def fetch_nwp():
    """NWP หลายแบบจำลองผ่าน proxy ?src=nwp (TMD หลัก / Open-Meteo สำรอง)
    → {district: (rain2h, max_hr, source)} แบบ max จาก 3 จุด"""
    out = {}
    for name, pts in DISTRICTS.items():
        series, srcs = [], set()
        for lat, lon in pts:
            try:
                j = get_json(f"{PROXY_URL}?src=nwp&lat={lat}&lon={lon}")
                if j.get("error") or not j.get("series"):
                    print(f"nwp {name} {lat},{lon}: {j.get('error', 'empty')}", file=sys.stderr)
                    continue
                vals = [float(x.get("rain") or 0) for x in j["series"][:2]]
                if vals:
                    series.append(vals)
                    srcs.add(j.get("source", "?"))
            except Exception as e:
                print(f"nwp {name} {lat},{lon} skip: {e}", file=sys.stderr)
        if series:
            n = min(len(s) for s in series)
            hourly_max = [max(s[i] for s in series) for i in range(n)]
            if hourly_max:
                src = "om" if "openmeteo" in srcs else "tmd"
                out[name] = (round(sum(hourly_max), 2), round(max(hourly_max), 2), src)
    return out


def read_radar():
    """เรดาร์ nowcast จากไฟล์ใน repo → {district: (rain60_p95, peak)} — ข้ามถ้าข้อมูลเก่า"""
    try:
        j = json.loads(NOWCAST.read_text(encoding="utf-8"))
        age_min = (datetime.now(timezone.utc).timestamp() - j["latest_frame_unix"]) / 60
        if j.get("synthetic") or age_min > 45:
            return {}
        return {k: (v.get("rain60_p95"), max(v.get("series_p95") or [0]))
                for k, v in j.get("districts", {}).items()}
    except Exception as e:
        print(f"radar skip: {e}", file=sys.stderr)
        return {}


def main():
    if PROXY_URL.startswith("PASTE_"):
        raise SystemExit("ยังไม่ได้ตั้งค่า PROXY_URL ใน logger/log_snapshot.py")

    rain24, rain1 = fetch_rain24()
    nwp = fetch_nwp()
    radar = read_radar()

    rec = {"t": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "d": {}}
    for name in DISTRICTS:
        f2, fx, fs = nwp.get(name, (None, None, None))
        r60, rp = radar.get(name, (None, None))
        rec["d"][name] = {
            "a": rain24.get(name),   # ฝนสะสม 24 ชม. (HII)
            "r1": rain1.get(name),   # ฝนจริงชั่วโมงล่าสุด (HII)
            "f2": f2,                # NWP ฝนรวม 2 ชม.ข้างหน้า
            "fx": fx,                # NWP สูงสุดรายชั่วโมง (ใน 2 ชม.)
            "fs": fs,                # แหล่ง NWP: tmd | om (Open-Meteo สำรอง)
            "r60": r60,              # เรดาร์ ฝนสะสมคาด 60 นาที (p95)
            "rp": rp,                # เรดาร์ อัตราฝนพีค (p95, มม./ชม.)
        }

    # append + ตัดให้เหลือ KEEP_DAYS
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    lines = HISTORY.read_text(encoding="utf-8").splitlines() if HISTORY.exists() else []
    lines.append(json.dumps(rec, ensure_ascii=False))
    if len(lines) > KEEP_DAYS * 96 + 50:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        kept = []
        for ln in lines:
            try:
                if json.loads(ln)["t"] >= cutoff:
                    kept.append(ln)
            except (ValueError, KeyError):
                continue
        lines = kept
    HISTORY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"logged {rec['t']}: {len(lines)} records total")
    print(json.dumps(rec, ensure_ascii=False))


if __name__ == "__main__":
    main()
