"""Check release matrix boundaries and shell programs embedded in actions."""

import json
import re
import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkflowContracts(unittest.TestCase):
    """Protect native platform coverage and the single-package release path."""

    def test_package_matrix_builds_once_per_architecture(self):
        source = (ROOT / ".github/workflows/packages.yml").read_text()
        builds = [json.loads(value)["include"] for value in re.findall(
            r"build_matrix='([^']+)'", source
        )]
        self.assertEqual(len(builds), 2)
        self.assertEqual(len(builds[0]), 2)
        self.assertEqual({entry["debian"] for entry in builds[0]}, {"13"})
        self.assertEqual({entry["runner"] for entry in builds[0]}, {
            "ubuntu-24.04", "ubuntu-24.04-arm"
        })
        self.assertEqual(len(builds[1]), 4)
        self.assertNotIn("requested_api", source)
        self.assertNotIn("ASL_RADIO_API", source)

    def test_published_package_is_verified_under_both_runtimes(self):
        source = (ROOT / ".github/workflows/packages.yml").read_text()
        verification = json.loads(re.search(
            r"verify_matrix='([^']+)'", source
        ).group(1))["include"]
        self.assertEqual({(entry["debian"], entry["runner"], entry["asl"])
                          for entry in verification}, {
            ("13", runner, runtime)
            for runner in ("ubuntu-24.04", "ubuntu-24.04-arm")
            for runtime in ("3.9.3", "3.10.5")
        })

    def test_required_gate_loads_identical_module_before_and_after_runtime_change(self):
        source = (ROOT / ".github/workflows/quality.yml").read_text()
        self.assertEqual(source.count("sh tests/container-smoke-test.sh"), 2)
        self.assertLess(source.index("sha256sum \"$module\""),
                        source.index("Install second supported ASL3 runtime"))
        self.assertGreater(source.index("sha256sum -c"),
                           source.index("Install second supported ASL3 runtime"))
        self.assertLess(source.index("Upload Debian 13 amd64 coverage"),
                        source.index("dpkg-buildpackage"))
        self.assertNotIn("res_usbradio", source)
        self.assertNotIn("ASL_LEGACY_INCLUDEDIR", source)

    def test_composite_action_shell_programs(self):
        for path in sorted((ROOT / "actions").glob("*/action.yml")):
            source = path.read_text()
            scripts = re.findall(r"(?m)^      run: \|\n((?:        .*\n|\n)+)", source)
            self.assertTrue(scripts, path)
            for index, script in enumerate(scripts):
                with self.subTest(action=path.parent.name, script=index):
                    subprocess.run(["shellcheck", "--shell=bash", "-"],
                                   input=textwrap.dedent(script), text=True, check=True)


if __name__ == "__main__":
    unittest.main()
