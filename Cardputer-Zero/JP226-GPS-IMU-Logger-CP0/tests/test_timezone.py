"""Ports of test/test_timezone_fallback/test_main.cpp plus the POSIX rule
engine and the offline-zone confirmation logic."""

import unittest

from jp226cp0 import config
from jp226cp0.location_time import LocationTime, make_tz
from jp226cp0.posixtz import PosixTz, fixed_offset_rule
from jp226cp0.statestore import StateStore
from jp226cp0.tzfallback import distance_km, zone_for_position

from .helpers import utc_ms


class FallbackTableTests(unittest.TestCase):
    def expect(self, lat, lon, name):
        zone = zone_for_position(lat, lon)
        self.assertIsNotNone(zone, (lat, lon))
        self.assertEqual(zone.name, name, (lat, lon))

    def test_east_coast_and_inland(self):
        self.expect(-33.87, 151.21, "Australia/Sydney")
        self.expect(-37.81, 144.96, "Australia/Sydney")      # Melbourne
        self.expect(-42.88, 147.33, "Australia/Sydney")      # Hobart
        self.expect(-27.47, 153.03, "Australia/Brisbane")
        self.expect(-30.85, 143.09, "Australia/Sydney")      # Tibooburra

    def test_south_australia_and_broken_hill(self):
        self.expect(-34.93, 138.60, "Australia/Adelaide")
        self.expect(-31.64, 138.58, "Australia/Adelaide")    # Flinders Ranges
        self.expect(-29.01, 134.75, "Australia/Adelaide")    # Coober Pedy area
        self.expect(-31.96, 141.47, "Australia/Broken_Hill")

    def test_north_and_west(self):
        self.expect(-12.46, 130.84, "Australia/Darwin")
        self.expect(-23.70, 133.88, "Australia/Darwin")      # Alice Springs
        self.expect(-31.95, 115.86, "Australia/Perth")
        self.expect(-31.68, 128.89, "Australia/Eucla")
        self.expect(-20.0, 139.5, "Australia/Brisbane")      # Mount Isa side

    def test_outside_australia(self):
        self.assertIsNone(zone_for_position(51.5, -0.12))
        self.assertIsNone(zone_for_position(-41.29, 174.78))
        self.assertIsNone(zone_for_position(float("nan"), 140.0))

    def test_distance(self):
        km = distance_km(-33.87, 151.21, -37.81, 144.96)
        self.assertAlmostEqual(km, 714.0, delta=15.0)

    def test_every_fallback_rule_parses(self):
        from jp226cp0 import tzfallback
        for zone in (tzfallback.SYDNEY, tzfallback.BRISBANE, tzfallback.ADELAIDE,
                     tzfallback.BROKEN_HILL, tzfallback.DARWIN, tzfallback.PERTH,
                     tzfallback.EUCLA, tzfallback.LORD_HOWE):
            PosixTz(zone.rule)


class PosixRuleTests(unittest.TestCase):
    def test_fixed_zones(self):
        for rule, seconds in (("AEST-10", 10 * 3600), ("ACST-9:30", 34200),
                              ("AWST-8", 8 * 3600), ("ACWST-8:45", 31500)):
            tz = PosixTz(rule)
            self.assertEqual(tz.utcoffset(utc_ms(2026, 1, 1) // 1000), seconds)
            self.assertEqual(tz.utcoffset(utc_ms(2026, 7, 1) // 1000), seconds)
        self.assertEqual(PosixTz("AEST-10").abbreviation(0), "AEST")

    def test_sydney_dst(self):
        tz = PosixTz("AEST-10AEDT,M10.1.0,M4.1.0/3")
        self.assertEqual(tz.utcoffset(utc_ms(2026, 1, 15) // 1000), 11 * 3600)
        self.assertEqual(tz.utcoffset(utc_ms(2026, 7, 15) // 1000), 10 * 3600)
        self.assertEqual(tz.abbreviation(utc_ms(2026, 1, 15) // 1000), "AEDT")

    def test_sydney_transition_instants(self):
        tz = PosixTz("AEST-10AEDT,M10.1.0,M4.1.0/3")
        # DST starts Sunday 2026-10-04 at 02:00 AEST = 2026-10-03 16:00Z.
        start = utc_ms(2026, 10, 3, 16) // 1000
        self.assertEqual(tz.utcoffset(start - 1), 10 * 3600)
        self.assertEqual(tz.utcoffset(start), 11 * 3600)
        # DST ends Sunday 2026-04-05 at 03:00 AEDT = 2026-04-04 16:00Z.
        end = utc_ms(2026, 4, 4, 16) // 1000
        self.assertEqual(tz.utcoffset(end - 1), 11 * 3600)
        self.assertEqual(tz.utcoffset(end), 10 * 3600)

    def test_adelaide_dst(self):
        tz = PosixTz("ACST-9:30ACDT,M10.1.0,M4.1.0/3")
        self.assertEqual(tz.utcoffset(utc_ms(2026, 12, 25) // 1000), 37800)
        self.assertEqual(tz.utcoffset(utc_ms(2026, 6, 25) // 1000), 34200)

    def test_northern_hemisphere_rule(self):
        tz = PosixTz("CET-1CEST,M3.5.0,M10.5.0/3")
        self.assertEqual(tz.utcoffset(utc_ms(2026, 7, 1) // 1000), 7200)
        self.assertEqual(tz.utcoffset(utc_ms(2026, 1, 1) // 1000), 3600)

    def test_lord_howe_half_hour_dst(self):
        tz = PosixTz("LHST-10:30LHDT-11,M10.1.0,M4.1.0/2")
        self.assertEqual(tz.utcoffset(utc_ms(2026, 1, 15) // 1000), 11 * 3600)
        self.assertEqual(tz.utcoffset(utc_ms(2026, 7, 15) // 1000), 37800)

    def test_bad_rules(self):
        for bad in ("", "Sydney", "AEST-10AEDT", "AEST", "Australia/Sydney"):
            with self.assertRaises(ValueError, msg=bad):
                PosixTz(bad)

    def test_fixed_offset_rule_matches_firmware(self):
        self.assertEqual(fixed_offset_rule(36000), "LOC-10:00")
        self.assertEqual(fixed_offset_rule(-18000), "LOC+5:00")
        self.assertEqual(fixed_offset_rule(34200), "LOC-9:30")
        self.assertEqual(PosixTz(fixed_offset_rule(34200)).utcoffset(0), 34200)

    def test_make_tz_accepts_au_iana_names(self):
        self.assertEqual(make_tz("Australia/Adelaide").utcoffset(
            utc_ms(2026, 6, 1) // 1000), 34200)
        with self.assertRaises(ValueError):
            make_tz("Not/AZone")

    def test_matches_zoneinfo_when_available(self):
        try:
            import zoneinfo
            zi = zoneinfo.ZoneInfo("Australia/Adelaide")
        except Exception:
            self.skipTest("no system tz database")
        import datetime
        tz = PosixTz("ACST-9:30ACDT,M10.1.0,M4.1.0/3")
        for month in range(1, 13):
            t = datetime.datetime(2026, month, 15, 12, tzinfo=datetime.timezone.utc)
            self.assertEqual(tz.utcoffset(int(t.timestamp())),
                             int(t.astimezone(zi).utcoffset().total_seconds()))


class LocationTimeTests(unittest.TestCase):
    ALICE = (-23.70, 133.88)      # Darwin zone
    ADELAIDE = (-34.93, 138.60)

    def run_checks(self, lt, pos, times_ms):
        for t in times_ms:
            lt.update(pos[0], pos[1], True, 0.0, t)

    def test_defaults_to_sydney(self):
        lt = LocationTime()
        self.assertEqual((lt.zone_name, lt.zone_source), ("Australia/Sydney", "DEFAULT"))

    def test_three_checks_30s_apart_change_zone(self):
        store = StateStore(None)
        lt = LocationTime(store)
        self.run_checks(lt, self.ALICE, [1000, 31000])
        self.assertEqual(lt.zone_name, "Australia/Sydney")     # only 2 checks
        self.run_checks(lt, self.ALICE, [61000])
        self.assertEqual((lt.zone_name, lt.zone_source), ("Australia/Darwin", "GPS"))
        self.assertEqual(store.get("tz_rule"), "ACST-9:30")     # persisted

    def test_checks_closer_than_30s_do_not_count(self):
        lt = LocationTime()
        self.run_checks(lt, self.ALICE, [1000, 2000, 3000, 4000, 5000])
        self.assertEqual(lt.zone_name, "Australia/Sydney")

    def test_flapping_answers_never_confirm(self):
        lt = LocationTime()
        t = 1000
        for i in range(10):
            pos = self.ALICE if i % 2 == 0 else self.ADELAIDE
            lt.update(pos[0], pos[1], True, 0.0, t)
            t += config.OFFLINE_ZONE_CHECK_INTERVAL_MS
        self.assertEqual(lt.zone_name, "Australia/Sydney")

    def test_stale_position_is_ignored(self):
        lt = LocationTime()
        for t in (1000, 31000, 61000, 91000):
            lt.update(self.ALICE[0], self.ALICE[1], False, 0.0, t)
        self.assertEqual(lt.zone_name, "Australia/Sydney")

    def test_outside_australia_keeps_zone(self):
        lt = LocationTime()
        self.run_checks(lt, (51.5, -0.12), [1000, 31000, 61000, 91000])
        self.assertEqual(lt.zone_name, "Australia/Sydney")

    def test_timezone_auto_false_disables_following(self):
        lt = LocationTime(timezone_auto=False)
        self.run_checks(lt, self.ALICE, [1000, 31000, 61000, 91000])
        self.assertEqual(lt.zone_name, "Australia/Sydney")

    def test_saved_zone_and_config_override(self):
        store = StateStore(None)
        store.put(tz_rule="AWST-8", tz_name="Australia/Perth")
        lt = LocationTime(store)
        self.assertEqual((lt.zone_name, lt.zone_source), ("Australia/Perth", "SAVED"))
        lt = LocationTime(store, configured_timezone="ACST-9:30")
        self.assertEqual(lt.zone_source, "CONFIG")
        self.assertEqual(lt.offset_at(0), 34200)

    def test_bad_config_timezone_is_ignored(self):
        lt = LocationTime(configured_timezone="nonsense")
        self.assertEqual(lt.zone_source, "DEFAULT")
        self.assertTrue(lt.messages)

    def test_online_result_is_trusted_within_100km(self):
        lt = LocationTime()
        lt.note_online_lookup("Australia/Brisbane", 36000, *self.ALICE)
        self.assertEqual((lt.zone_name, lt.zone_source), ("Australia/Brisbane", "GPS+NET"))
        # 50 km away the table (Darwin) must NOT override the online answer...
        near = (self.ALICE[0] + 0.45, self.ALICE[1])
        self.run_checks(lt, near, [1000, 31000, 61000, 91000])
        self.assertEqual(lt.zone_name, "Australia/Brisbane")
        # ...but far away it takes over after three confirmations.
        self.run_checks(lt, self.ADELAIDE, [121000, 151000, 181000])
        self.assertEqual(lt.zone_name, "Australia/Adelaide")

    def test_unknown_online_zone_uses_fixed_offset(self):
        lt = LocationTime()
        lt.note_online_lookup("Pacific/Noumea", 39600, -22.3, 166.4)
        self.assertEqual(lt.offset_at(0), 39600)


if __name__ == "__main__":
    unittest.main()
