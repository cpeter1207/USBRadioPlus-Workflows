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

    def test_repository_omits_both_obsolete_asl3105_packages(self):
        source = (ROOT / ".github/workflows/packages.yml").read_text()
        classifier = re.search(
            r'(?ms)^            case "\$package" in\n.*?^            esac$', source
        ).group(0)
        retained = [
            "/incoming/usbradioplus_0.1.0~alpha18-1.deb13_amd64.deb",
            "/incoming/usbradioplus-dbgsym_0.1.0~alpha18-1.deb13_amd64.deb",
            "/incoming/librate-adjusting-pcm-ring1_1.0.1-1.deb13_amd64.deb",
            "/incoming/librate-adjusting-pcm-ring2_2.0.0.alpha1-1_amd64.deb",
            "/incoming/librptadv-portaudio-alsa-adapter1_0.1.0.alpha2-1_amd64.deb",
        ]
        obsolete = [
            "/incoming/usbradioplus-asl3105_0.1.0~alpha17-1.deb13+modern.asl3105_arm64.deb",
            "/incoming/usbradioplus-asl3105-dbgsym_0.1.0~alpha17-1.deb13+modern.asl3105_arm64.deb",
        ]
        script = ('set -eu\nINCLUDE_DEBIAN12=false\nfor package do\n'
                  + textwrap.dedent(classifier)
                  + '\nprintf "%s:%s\\n" "$package" "$suite"\ndone\n')
        result = subprocess.run(
            ["bash", "-c", script, "repository-classifier", *retained, *obsolete],
            text=True, check=True, capture_output=True,
        )
        self.assertEqual(result.stdout.splitlines(), [f"{path}:trixie" for path in retained])

    def test_release_asset_selector_rejects_debian12_and_retains_both_ring_abis(self):
        source = (ROOT / "actions/install-shared-dependencies/action.yml").read_text()
        selector = re.search(r"asset=\$\(jq.*?'(.*?)' <<<", source, re.S).group(1)
        self.assertIn("rate_adjusting_pcm_ring v1.0.1", source)
        self.assertIn("rate_adjusting_pcm_ring v2.0.0-alpha.1", source)
        for architecture in ("amd64", "arm64"):
            for package, version in (
                ("librate-adjusting-pcm-ring1", "1.0.1-1_debian13"),
                ("librate-adjusting-pcm-ring2", "2.0.0.alpha1-1"),
            ):
                expected = f"{package}_{version}_{architecture}.deb"
                metadata = {"assets": [
                    {"name": expected},
                    {"name": f"{package}_1.0.1-1_debian12_{architecture}.deb"},
                    {"name": f"{package}_{version}_wrongarch.deb"},
                    {"name": f"unrelated_{version}_{architecture}.deb"},
                ]}
                with self.subTest(package=package, architecture=architecture):
                    result = subprocess.run(
                        ["jq", "-er", "--arg", "package", package,
                         "--arg", "architecture", architecture, selector],
                        input=json.dumps(metadata), text=True, check=True,
                        capture_output=True,
                    )
                    self.assertEqual(json.loads(result.stdout)["name"], expected)


if __name__ == "__main__":
    unittest.main()
