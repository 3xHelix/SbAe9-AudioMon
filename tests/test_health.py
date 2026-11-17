# SPDX-License-Identifier: GPL-2.0-only
"""Driver Health (driver 0.12.11+): parsing and the health line."""

import unittest

import support  # noqa: F401  (loads the app's modules the way the other tests do)
from audiomon import driverstate as ds


class Health(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(ds.parse_health("0, 0, 0, 0, 0, 96, 5, 86"), [0, 0, 0, 0, 0, 96, 5, 86])
        self.assertIsNone(ds.parse_health("0, 1"))
        self.assertIsNone(ds.parse_health("on"))
        self.assertIsNone(ds.parse_health(None))

    def test_text(self):
        self.assertEqual(ds.health_text(None), ("Driver health: n/a", False))
        text, problem = ds.health_text([0, 0, 0, 0, 0, 96, 5, -1])
        self.assertFalse(problem)
        self.assertEqual(text, "Driver health: OK · late replies 96 (max 5 µs) · "
                               "free router slots not measured yet")
        text, problem = ds.health_text([1, 0, 0, 0, 3, 96, 5, 86])
        self.assertTrue(problem)
        self.assertTrue(text.startswith("Driver health: 1 verb timeouts · 3 BDLs kept · "))

    def test_low_slots(self):
        self.assertEqual(ds.health_problems([0, 0, 0, 0, 0, 0, 0, 9]), [("free router slots", 9)])
        self.assertEqual(ds.health_problems([0, 0, 0, 0, 0, 0, 0, 10]), [])
        self.assertEqual(ds.health_problems([0, 0, 0, 0, 0, 0, 0, -1]), [])


if __name__ == "__main__":
    unittest.main()
