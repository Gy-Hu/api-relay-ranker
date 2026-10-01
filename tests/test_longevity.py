import unittest
from datetime import date, timedelta

from relayrank.engine import aggregate
from relayrank.longevity import Budget, record_observations, refresh, vendor_age
from relayrank.models import Config, Observation, Source

DAY = date(2026, 10, 1)


def cache(**domains):
    return {"schema_version": 1, "domains": domains}


def rdap(registered=None, expires=None, checked=DAY):
    return {"checked": checked.isoformat(), "registered": registered and registered.isoformat(),
            "expires": expires and expires.isoformat(), "status": []}


class FakeLookups:
    def __init__(self, registrations=None, certs=None):
        self.registrations, self.certs, self.calls = registrations or {}, certs or {}, []

    def rdap(self, domain):
        self.calls.append(("rdap", domain))
        registered, expires = self.registrations[domain]
        return {"registered": registered.isoformat(), "expires": expires.isoformat(), "status": []}

    def certificates(self, domain):
        self.calls.append(("cert", domain))
        return self.certs.get(domain, [])

    def wayback(self, domain, since):
        self.calls.append(("wayback", domain))
        return None


class VendorAgeTests(unittest.TestCase):
    def test_registration_alone_is_not_operating_evidence(self):
        age = vendor_age(["new.ai"], cache(**{"new.ai": {"first_seen": DAY.isoformat(),
                                                         "rdap": rdap(date(2020, 1, 1), DAY + timedelta(days=300))}}), DAY)
        self.assertEqual(age.operating_days, 0)
        self.assertEqual(age.basis, "first_seen")

    def test_domain_derived_evidence_before_registration_belongs_to_previous_owner(self):
        entry = {"first_seen": DAY.isoformat(), "rdap": rdap(date(2026, 3, 1), date(2027, 3, 1)),
                 "cert": {"checked": DAY.isoformat(), "basis": "2026-03-01", "first": "2026-03-05", "first_any": "2026-03-05"},
                 "relaypick_online": "2014-01-01"}
        age = vendor_age(["new.ai"], cache(**{"new.ai": entry}), DAY)
        self.assertEqual((age.service_start, age.basis), (date(2026, 3, 5), "first_cert"))
        dropped = [(e["kind"], e["reason"]) for e in age.evidence if not e["used"]]
        self.assertEqual(dropped, [("relaypick_online", "before_registration")])

    def test_second_hand_domain_history_is_not_attributed_to_the_current_operator(self):
        # hao.ai: registered 2017, captured since 2015, a relay only since 2026. A transfer keeps the
        # old registration date, so captures after registration may still belong to someone else.
        entry = {"first_seen": DAY.isoformat(), "rdap": rdap(date(2017, 12, 16), date(2028, 12, 4)),
                 "wayback": {"checked": DAY.isoformat(), "basis": "2017-12-16", "first": "2018-01-02", "first_any": "2015-03-29"},
                 "relaypick_online": "2026-03-01"}
        age = vendor_age(["hao.ai"], cache(**{"hao.ai": entry}), DAY)
        self.assertEqual(age.reused_domains, ("hao.ai",))
        self.assertEqual((age.service_start, age.basis), (date(2026, 3, 1), "relaypick_online"))
        self.assertEqual([e["reason"] for e in age.evidence if e["kind"] == "wayback"], ["domain_reused"])

    def test_third_party_observation_survives_a_domain_move(self):
        # HelpAIO tracked the vendor before its current domain existed.
        entry = {"first_seen": DAY.isoformat(), "rdap": rdap(date(2026, 5, 7), date(2028, 5, 7)),
                 "helpaio_listed": "2025-11-22"}
        age = vendor_age(["moved.ai"], cache(**{"moved.ai": entry}), DAY)
        self.assertEqual((age.service_start, age.basis), (date(2025, 11, 22), "helpaio_listed"))
        self.assertTrue(age.mature(90))

    def test_earliest_evidence_across_a_vendors_domains_wins(self):
        c = cache(**{"a.com": {"first_seen": DAY.isoformat(), "veridrop_report": "2026-06-01"},
                     "a.ai": {"first_seen": DAY.isoformat(), "veridrop_report": "2026-05-10"}})
        self.assertEqual(vendor_age(["a.com", "a.ai"], c, DAY).basis_domain, "a.ai")

    def test_expiry_excludes_only_after_a_post_expiry_check_and_only_if_every_domain_lapsed(self):
        lapsed = date(2026, 8, 15)
        stale = cache(**{"dead.com": {"first_seen": "2026-01-01", "rdap": rdap(date(2025, 8, 15), lapsed, checked=date(2026, 8, 1))}})
        self.assertIsNone(vendor_age(["dead.com"], stale, DAY).excluded)  # last check predates expiry: may have renewed
        confirmed = cache(**{"dead.com": {"first_seen": "2026-01-01", "rdap": rdap(date(2025, 8, 15), lapsed)}})
        self.assertIn("dead.com", vendor_age(["dead.com"], confirmed, DAY).excluded)
        confirmed["domains"]["alive.ai"] = {"first_seen": "2026-01-01", "rdap": rdap(date(2026, 1, 1), date(2027, 1, 1))}
        age = vendor_age(["dead.com", "alive.ai"], confirmed, DAY)
        self.assertIsNone(age.excluded)
        self.assertEqual(age.expired_domains, ("dead.com",))


class RefreshTests(unittest.TestCase):
    def test_new_domains_are_looked_up_and_settled_ones_are_not_requeried(self):
        lookups = FakeLookups({"x.com": (date(2025, 1, 1), date(2027, 1, 1))}, {"x.com": [date(2024, 1, 1), date(2025, 2, 1)]})
        c = cache()
        refresh(c, ["x.com"], DAY, lookups)
        self.assertEqual(c["domains"]["x.com"]["cert"]["first"], "2025-02-01")
        calls = len(lookups.calls)
        refresh(c, ["x.com"], DAY + timedelta(days=1), lookups)
        self.assertEqual(len(lookups.calls), calls)

    def test_domain_near_expiry_is_rechecked_daily_so_a_renewal_clears_exclusion(self):
        lookups = FakeLookups({"r.com": (date(2025, 1, 1), DAY + timedelta(days=10))})
        c = cache()
        refresh(c, ["r.com"], DAY, lookups)
        lookups.registrations["r.com"] = (date(2025, 1, 1), date(2027, 10, 11))
        later = DAY + timedelta(days=12)
        refresh(c, ["r.com"], later, lookups)
        self.assertIsNone(vendor_age(["r.com"], c, later).excluded)
        self.assertEqual(c["domains"]["r.com"]["rdap"]["expires"], "2027-10-11")

    def test_budget_caps_lookups_and_failures_are_cached_for_retry(self):
        class Failing(FakeLookups):
            def rdap(self, domain):
                self.calls.append(("rdap", domain))
                raise TimeoutError("slow")
        lookups = Failing()
        c = cache()
        refresh(c, ["a.com", "b.com", "c.com"], DAY, lookups, Budget(rdap=2, cert=0, wayback=0))
        self.assertEqual([call for call in lookups.calls if call[0] == "rdap"], [("rdap", "a.com"), ("rdap", "b.com")])
        self.assertEqual(c["domains"]["a.com"]["rdap"]["error"], "slow")
        self.assertNotIn("rdap", c["domains"]["c.com"])

    def test_source_evidence_is_recorded_per_registrable_domain_and_only_moves_earlier(self):
        c = cache()
        record_observations(c, [Observation("helpaio", "V", domain="www.v.com", score=50, observed_at=DAY,
                                            raw_evidence={"listed_days": 100}),
                                Observation("veridrop", "v.com", domain="v.com", score=90,
                                            raw_evidence={"earliest_report_seen": "2026-09-01"})], DAY)
        record_observations(c, [Observation("veridrop", "v.com", domain="v.com", score=90,
                                            raw_evidence={"earliest_report_seen": "2026-09-20"})], DAY)
        entry = c["domains"]["v.com"]
        self.assertEqual((entry["helpaio_listed"], entry["veridrop_report"]), ("2026-06-23", "2026-09-01"))


class ExclusionTests(unittest.TestCase):
    def test_excluded_vendor_leaves_the_cohort_without_moving_anyone_else(self):
        sources = {s: Source(s) for s in "ab"}
        rows = [Observation(s, v, score=sc, observed_at=DAY) for s in "ab" for v, sc in zip("vwxyz", [90, 70, 50, 30, 10])]
        config = Config(DAY, prior_strength=0, minimum_peers=1)
        before = {r.vendor: r.score for r in aggregate(rows, sources, config).results}
        after = aggregate(rows, sources, config, excluded={"v": "domain_expired: v.com"})
        self.assertNotIn("v", [r.vendor for r in after.results])
        self.assertEqual({r.vendor: r.score for r in after.results}, {k: s for k, s in before.items() if k != "v"})
        self.assertEqual(after.evaluations[("a", "v")]["vendor_status"], "excluded")


if __name__ == "__main__":
    unittest.main()
