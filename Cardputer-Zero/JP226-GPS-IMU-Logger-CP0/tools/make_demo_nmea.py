#!/usr/bin/env python3
"""Generate sample_data/demo_drive.nmea: a short SYNTHETIC drive (no real GPS).

Port Augusta, SA, heading north on 2026-10-20 (UTC 01:30): 20 s parked,
accelerate to 90 km/h, cruise (with one 400 m position glitch that the
plausibility guard must reject), brake, park again. It also contains a GSV
sentence and one sentence with a bad checksum so the parser's rejection path is
exercised. Standard library only:

    python -I tools/make_demo_nmea.py [output_path]
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from jp226cp0.nmea import format_latlon, make_sentence  # noqa: E402

START = datetime.datetime(2026, 10, 20, 1, 30, 0)
START_LAT, START_LON = -32.4936, 137.7716
DURATION_S = 190
M_PER_DEG_LAT = 111195.0


def speed_at(t: int) -> float:
    if t < 20:
        return 0.0
    if t < 50:
        return 3.0 * (t - 20)
    if t < 110:
        return 90.0
    if t < 140:
        return max(0.0, 90.0 - 3.0 * (t - 110))
    return 0.0


def generate() -> list:
    lines = []
    lat = START_LAT
    for t in range(DURATION_S + 1):
        now = START + datetime.timedelta(seconds=t)
        kmh = speed_at(t)
        if t > 0:
            lat += (kmh / 3.6) / M_PER_DEG_LAT
        shown_lat = lat + (0.0036 if t == 70 else 0.0)   # one-second glitch
        lat_s, ns = format_latlon(shown_lat, True)
        lon_s, ew = format_latlon(START_LON, False)
        knots = kmh / 1.852
        course = "0.00" if kmh > 1 else ""
        hhmmss = now.strftime("%H%M%S.00")
        ddmmyy = now.strftime("%d%m%y")
        lines.append(make_sentence("GNRMC,{},A,{},{},{},{},{:.2f},{},{},,,A".format(
            hhmmss, lat_s, ns, lon_s, ew, knots, course, ddmmyy)))
        lines.append(make_sentence("GNVTG,{},T,,M,{:.2f},N,{:.2f},K,A".format(
            course, knots, kmh)))
        lines.append(make_sentence("GNGGA,{},{},{},{},{},1,09,0.9,{:.1f},M,12.0,M,,".format(
            hhmmss, lat_s, ns, lon_s, ew, 14.0)))
        lines.append(make_sentence(
            "GNGSA,A,3,01,03,06,09,11,14,17,19,22,,,,1.5,0.9,1.2,1"))
        if t % 5 == 0:
            lines.append(make_sentence(
                "GPGSV,3,1,11,01,40,083,46,02,17,308,41,03,07,344,39,06,22,228,45"))
        if t == 30:
            lines.append("$GNRMC,013030.00,A,3229.6160,S,13746.3000,E,25.0,0.00,201026,,,A*00")
    return lines


def main(argv) -> int:
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                           "sample_data", "demo_drive.nmea")
    path = argv[1] if len(argv) > 1 else default
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="ascii", newline="\n") as handle:
        handle.write("\n".join(generate()) + "\n")
    print("wrote", os.path.abspath(path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
