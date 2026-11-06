# SPDX-License-Identifier: GPL-2.0-only
"""The shared kit (sbae9kit) is exactly the copy SbAe9-Kit shipped, and
AudioMon stays listen-only: it never imports the kit's mixer (ALSA writes).

Change the kit in ~/Projects/SbAe9-Kit and copy it here with its
tools/sync_kit.py, never by editing sbae9kit/ in this repo.
"""

import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

support.setup()

import sbae9kit  # noqa: E402
from sbae9kit import vendor  # noqa: E402


class KitTest(unittest.TestCase):
    def test_vendored_kit_is_unchanged(self):
        self.assertEqual(vendor.verify(), [])

    def test_manifest_matches_the_kit_version(self):
        self.assertEqual(vendor.read_manifest()["kit"], sbae9kit.KIT_VERSION)


class ListenOnly(unittest.TestCase):
    def test_the_app_never_imports_the_mixer(self):
        import audiomon.__main__  # noqa: F401  (pulls in every module of the app)
        from audiomon import window  # noqa: F401
        loaded = [m for m in sys.modules if m.startswith("sbae9kit.") and "mixer" in m]
        self.assertEqual(loaded, [], "AudioMon is listen-only: it must not load the ALSA mixer")

    def test_no_module_mentions_it(self):
        for path in glob.glob(os.path.join(support.ROOT, "audiomon", "*.py")):
            with open(path) as f:
                src = f.read()
            self.assertIsNone(re.search(r"import\s+.*\bmixer\b|from\s+\S*mixer\b", src), path)


if __name__ == "__main__":
    unittest.main()
