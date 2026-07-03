#!/usr/bin/env python3
"""
backfill.py — เติมประวัติย้อนหลัง 7 วันลง data/history.jsonl (รันครั้งเดียวผ่าน workflow_dispatch)

ใช้ฝนตกจริงรายชั่วโมงจาก Open-Meteo (model analysis, ฟรี ไม่ต้องมี key):
  a  = ฝนสะสม 24 ชม. ย้อนหลัง ณ เวลานั้น (ความหมายเดียวกับระบบจริง)
  f2 = ฝนจริงใน 2 ชม. "ถัดไป"  → พยากรณ์สมบูรณ์แบบ (perfect hindsight) ใช้ทดลองเกณฑ์
  fx = ฝนจริงรายชั่วโมงสูงสุดใน 2 ชม. ถัดไป
  r60/rp = null (radar nowcast ย้อนหลังไม่ได้)
ทุก record ติดธง "bf":1 (backfill) — record จริงจาก logger จะไม่ถูกทับ รันซ้ำได้ปลอดภัย
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PAST_DAYS = 8            # ดึงเผื่อ 1 วันไว้คำนวณฝนสะสม 24 ชม. ของวันแรก
ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "history.jsonl"

DISTRICTS = {
    "Kathu":         [[7.9172, 98.3137], [7.8960, 98.2960], [7.9180, 98.3330]],
    "Thalang":       [[8.0556, 98.3458], [8.0030, 98.2970], [8.0960, 98.3030]],
    "Mueang Phuket": [[7.8840, 98.3870], [7.8460, 98.3390], [7.7710, 98.3250]],
}


def fetch_point(lat, lon):
    """รายชั่วโมงฝน (มม.) พร้อมเวลา UTC — เฉพาะชั่วโมงที่ผ่านมาแล้ว"""
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "hourly": "precipitation",
        "past_days": PAST_DAYS, "forecast_days": 1,
        "timezone": "UTC",
    }, timeout=60)
    r.raise_for_status()
    j = r.json()["hourly"]
    now = datetime.now(timezone.utc)
    out = {}
    for t, p in zip(j["time"], j["precipitation"]):
        ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        if ts <= now and p is not None:
            out[ts] = float(p)
    return out


def main():
    # ฝนรายชั่วโมงต่ออำเภอ = ค่าสูงสุดจาก 3 จุด (สอดคล้องกับระบบจริง)
    district_hourly = {}
    for name, pts in DISTRICTS.items():
        series = [fetch_point(lat, lon) for lat, lon in pts]
        times = sorted(set.intersection(*[set(s.keys()) for s in series]))
        district_hourly[name] = {t: max(s[t] for s in series) for t in times}
        print(f"{name}: {len(times)} hours")

    times = sorted(set.intersection(*[set(v.keys()) for v in district_hourly.values()]))
    records = []
    for i, t in enumerate(times):
        if i < 24 or i + 2 >= len(times):
            continue    # ต้องมี 24 ชม.ก่อนหน้า (สำหรับ a) และ 2 ชม.ถัดไป (สำหรับ f2)
        rec = {"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "bf": 1, "d": {}}
        for name, h in district_hourly.items():
            past24 = sum(h[times[j]] for j in range(i - 23, i + 1))
            nxt = [h[times[i + 1]], h[times[i + 2]]]
            rec["d"][name] = {
                "a": round(past24, 1),
                "f2": round(sum(nxt), 2),
                "fx": round(max(nxt), 2),
                "r60": None, "rp": None,
            }
        records.append(rec)

    # merge กับไฟล์เดิม: record จริง (ไม่มีธง bf) ชนะเสมอ, กันซ้ำด้วย timestamp
    existing = {}
    if HISTORY.exists():
        for ln in HISTORY.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
                existing[r["t"]] = r
            except (ValueError, KeyError):
                continue
    added = 0
    for rec in records:
        if rec["t"] not in existing:
            existing[rec["t"]] = rec
            added += 1
    merged = [existing[k] for k in sorted(existing.keys())]
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n", encoding="utf-8")
    print(f"backfilled {added} new records, total {len(merged)}")


if __name__ == "__main__":
    main()
