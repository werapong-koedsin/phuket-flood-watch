#!/usr/bin/env python3
"""
ดึงระดับน้ำรายชั่วโมง สถานี X.191 คลองบางใหญ่ (ตอนล่าง) จ.ภูเก็ต จาก RID hyd-app
→ เขียน data/waterlevel.json (schema เดียวกับที่ dashboard/GAS ใช้)
รันโดย GitHub Actions ทุก 15 นาที
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

URL = "https://hyd-app.rid.go.th/webservice/getGroupHourlyWaterLevelReportHL.ashx"
GROUP_ID = "1178"          # คลองบางใหญ่ (ตอนล่าง) จ.ภูเก็ต
BANK_M = 4.60              # ระดับตลิ่ง
TZ = timezone(timedelta(hours=7))
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "waterlevel.json"


def fetch_rows():
    now = datetime.now(TZ)
    thai_date = now.strftime("%d/%m/") + str(now.year + 543)
    payload = {
        "DW[StationGroupID]": GROUP_ID,
        "DW[TimeCurrent]": thai_date,
        "DW[Frozen]": "false",
        "_search": "false",
        "nd": str(int(now.timestamp() * 1000)),
        "rows": "100", "page": "1", "sidx": "indexhourly", "sord": "asc",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Referer": "https://hyd-app.rid.go.th/hydro8h.html",
        "X-Requested-With": "XMLHttpRequest",
    }
    r = requests.post(URL, data=payload, headers=headers, timeout=45)
    r.raise_for_status()
    return r.json()["rows"], now


def parse(rows, now):
    """คอลัมน์: hourlytime (1.00–24.00), wlvaluesN/qvaluesN โดย N สุดท้าย = วันที่ขอ (วันนี้)"""
    day_ids = sorted({int(m.group(1)) for k in rows[0] for m in [re.match(r"wlvalues(\d+)$", k)] if m})
    if not day_ids:
        raise SystemExit("no wlvaluesN columns; keys=" + ",".join(rows[0].keys()))
    n = len(day_ids)
    series = []
    for i, di in enumerate(day_ids):
        day = (now + timedelta(days=i - (n - 1))).date()
        for r in rows:
            try:
                hr = int(float(r["hourlytime"]))
                wl = float(r[f"wlvalues{di}"])
            except (TypeError, ValueError, KeyError):
                continue
            t = datetime(day.year, day.month, day.day, tzinfo=TZ)
            t += timedelta(hours=hr)          # ชั่วโมง 24.00 = เที่ยงคืนขึ้นวันใหม่ (บวกข้ามวันอัตโนมัติ)
            try:
                q = float(str(r.get(f"qvalues{di}", "")).replace(",", ""))
            except (TypeError, ValueError):
                q = None
            series.append({"t": t.isoformat(), "wl": round(wl, 3), "q": q})
    series.sort(key=lambda x: x["t"])
    if not series:
        raise SystemExit("parsed 0 points")
    return series


def main():
    rows, now = fetch_rows()
    series = parse(rows, now)
    out = {
        "station": "X.191 คลองบางใหญ่ (ตอนล่าง)",
        "bank": BANK_M,
        "latest": series[-1],
        "series": series,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "RID hyd-app (ศูนย์อุทกวิทยาชลประทานภาคใต้)",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT}: {len(series)} points, latest {series[-1]['t']} wl={series[-1]['wl']} m "
          f"({series[-1]['wl'] / BANK_M * 100:.0f}% of bank)")


if __name__ == "__main__":
    main()
