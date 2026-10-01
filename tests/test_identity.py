import unittest
from datetime import date
from pathlib import Path

from relayrank.identity import Directory, VendorSpec, registrable_domain
from relayrank.io import load_config
from relayrank.models import Observation


def obs(source, name, domain, score=50.0):
    return Observation(source, name, domain=domain, score=score)


class IdentityTests(unittest.TestCase):
    def test_hosts_of_one_registrable_domain_join_across_sources(self):
        directory = Directory((VendorSpec("Packy Code", ("packyapi.com", "packyapi.ai")),))
        rows, domains = directory.resolve([
            obs("helpaio", "Packy Code", "www.packyapi.ai"),
            obs("relaypick", "PackyCode", "packyapi.com"),
            obs("veridrop", "api-slb.packyapi.com", "api-slb.packyapi.com"),
        ])
        self.assertEqual({o.vendor for o in rows}, {"Packy Code"})
        self.assertEqual(domains["Packy Code"], ("packyapi.ai", "packyapi.com"))

    def test_same_source_listing_two_domains_of_one_vendor_is_averaged_once(self):
        directory = Directory((VendorSpec("Packy Code", ("packyapi.com", "packyapi.ai")),))
        rows, _ = directory.resolve([obs("relaypick", "PackyCode", "packyapi.com", 60),
                                     obs("relaypick", "packyapi.ai", "packyapi.ai", 40)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].score, 50)
        self.assertIn("multiple_source_entries_averaged", rows[0].issues)

    def test_similar_name_on_another_domain_is_never_merged(self):
        directory = Directory((VendorSpec("Neko Code", ("nekocode.ai",)),))
        rows, _ = directory.resolve([obs("helpaio", "Neko Code", "nekocode.ai"),
                                     obs("relaypick", "Neko Code", "api.nekoapi.com")])
        names = sorted(o.vendor for o in rows)
        self.assertEqual(names, ["Neko Code", "Neko Code (nekoapi.com)"])
        stray = next(o for o in rows if o.vendor != "Neko Code")
        self.assertIn("name_matches_configured_vendor: Neko Code", stray.issues)

    def test_unconfigured_domains_join_each_other_but_shared_hosting_does_not(self):
        rows, _ = Directory().resolve([obs("a", "Foo", "foo.dev"), obs("b", "foo", "api.foo.dev"),
                                       obs("a", "One", "one.github.io"), obs("b", "Two", "two.github.io")])
        self.assertEqual(sorted(o.vendor for o in rows), ["Foo", "Foo", "One", "Two"])
        self.assertEqual(registrable_domain("api.example.com.cn"), "example.com.cn")

    def test_conflicting_domain_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "configured for both"):
            Directory((VendorSpec("A", ("shared.com",)), VendorSpec("B", ("api.shared.com",))))

    def test_shipped_config_keeps_known_lookalikes_apart(self):
        _, _, directory, _ = load_config(Path(__file__).parents[1] / "examples" / "config.toml", date(2026, 10, 1))
        rows, _ = directory.resolve([obs("a", "x", "duckcode.cn"), obs("a", "y", "www.duckcoding.ai"),
                                     obs("a", "z", "88api.ai"), obs("a", "w", "www.88code.org")])
        self.assertEqual(sorted(o.vendor for o in rows), ["88 Code", "Duck Code", "x", "z"])


if __name__ == "__main__":
    unittest.main()
