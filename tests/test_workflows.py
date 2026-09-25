"""Check release matrix boundaries and shell programs embedded in actions."""

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkflowContracts(unittest.TestCase):
    """Protect native platform coverage and the single-package release path."""

    def test_quality_image_bootstrap_has_no_release_dependency_cycle(self):
        source = (ROOT / ".github/workflows/containers.yml").read_text()
        quality = source.split("  build-quality:\n", 1)[1].split("  quality-manifest:", 1)[0]
        self.assertIn("target: quality", quality)
        self.assertNotIn("install-shared-dependencies", quality)
        self.assertNotIn("shared_packages=", quality)

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

    def test_quality_install_resolves_declared_runtime_dependencies(self):
        source = (ROOT / ".github/workflows/quality.yml").read_text()
        self.assertIn(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y ../usbradioplus_*.deb",
            source,
        )
        self.assertNotIn("dpkg -i ../usbradioplus_*.deb", source)

    def test_composite_action_shell_programs(self):
        for path in sorted((ROOT / "actions").glob("*/action.yml")):
            source = path.read_text()
            scripts = re.findall(r"(?m)^      run: \|\n((?:        .*\n|\n)+)", source)
            self.assertTrue(scripts, path)
            for index, script in enumerate(scripts):
                with self.subTest(action=path.parent.name, script=index):
                    subprocess.run(["shellcheck", "--shell=bash", "-"],
                                   input=textwrap.dedent(script), text=True, check=True)

    def test_shared_dependency_tools_install_missing_pkg_config(self):
        """An otherwise prepared release container still needs pkg-config."""
        source = (ROOT / "actions/install-shared-dependencies/action.yml").read_text()
        script = textwrap.dedent(
            re.search(r"(?m)^      run: \|\n((?:        .*\n|\n)+)", source).group(1)
        )
        for pkg_config_present in (False, True):
            with (
                self.subTest(pkg_config_present=pkg_config_present),
                tempfile.TemporaryDirectory() as work,
            ):
                directory = Path(work)
                for tool in ("gh", "jq", "curl"):
                    (directory / tool).symlink_to("/bin/true")
                if pkg_config_present:
                    (directory / "pkg-config").symlink_to("/bin/true")
                (directory / "env").symlink_to("/usr/bin/env")
                for name, body in (
                    ("id", "printf '0\\n'"),
                    ("apt-get", 'printf "%s\\n" "$*" >> "$APT_LOG"'),
                ):
                    command = directory / name
                    command.write_text(f"#!/bin/sh\n{body}\n")
                    command.chmod(0o755)
                log = directory / "apt.log"
                result = subprocess.run(
                    ["/bin/bash", "-c", script],
                    env=dict(os.environ, PATH=str(directory), APT_LOG=str(log)),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                if pkg_config_present:
                    self.assertFalse(log.exists())
                else:
                    self.assertTrue(
                        log.exists(), "missing pkg-config must trigger installation"
                    )
                    calls = log.read_text().splitlines()
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(calls[0], "update")
                    self.assertEqual(calls[1].split()[:2], ["install", "-y"])
                    self.assertIn("pkg-config", calls[1].split()[2:])

    def test_main_gate_uses_portable_pagination_and_latest_required_checks(self):
        source = (ROOT / "actions/verify-main-gate/action.yml").read_text()
        script = textwrap.dedent(re.search(
            r"(?m)^      run: \|\n((?:        .*\n|\n)+)", source
        ).group(1))
        self.assertNotIn("--slurp", script)
        commands = r'''
git() {
    case "$*" in
        'fetch origin main'|'merge-base --is-ancestor merged-sha origin/main') ;;
        'rev-parse HEAD') printf 'merged-sha\n' ;;
        *) return 90 ;;
    esac
}
gh() {
    test "$1" = api || return 91
    if test "$2" = --paginate; then
        # Model the Debian CLI: pagination works, but --slurp is unsupported.
        test "$#" -eq 3 || return 92
        test "$3" = 'repos/example/source/commits/tested-head/check-runs?per_page=100' || return 93
        printf '%s\n' "$GATE_PAGES"
    else
        test "$#" -eq 2 || return 94
        test "$2" = 'repos/example/source/commits/merged-sha/pulls' || return 95
        printf '%s\n' '[{"merged_at":"2026-09-13","base":{"ref":"main"},"merge_commit_sha":"merged-sha","head":{"sha":"tested-head"}}]'
    fi
}
'''
        quality = "quality / Required quality gate"
        container = "containers / Required container gate"

        def check(identifier, name, conclusion, status="completed"):
            return dict(id=identifier, name=name, conclusion=conclusion, status=status)

        older = [check(1, quality, "failure"), check(2, container, "failure")]
        scenarios = (
            ("latest success", older,
             [check(5, container, "success"), check(4, quality, "success")], True),
            ("latest quality failure", [check(1, quality, "success")],
             [check(5, container, "success"), check(4, quality, "failure")], False),
            ("latest container failure", [check(2, container, "success")],
             [check(5, container, "failure"), check(4, quality, "success")], False),
            ("incomplete quality", older,
             [check(5, container, "success"), check(4, quality, "success", "in_progress")], False),
            ("missing quality", [], [check(5, container, "success")], False),
            ("missing container", [], [check(4, quality, "success")], False),
            ("no checks", [], [], False),
        )
        for name, first, second, expected in scenarios:
            with self.subTest(name=name):
                pages = "\n".join(json.dumps(dict(check_runs=page)) for page in (first, second))
                result = subprocess.run(
                    ["bash", "-c", commands + script],
                    env=dict(os.environ, GITHUB_REPOSITORY="example/source", GATE_PAGES=pages),
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode == 0, expected, result.stderr)


    def test_rnnoise_package_install_retires_only_unowned_bootstrap_files(self):
        source = (ROOT / "actions/install-rnnoise-packages/action.yml").read_text()
        cleanup = textwrap.dedent(re.search(
            r"(?ms)^        for bootstrap in \\\n.*?^        done$", source
        ).group(0))
        expected = {
            "/usr/local/lib/librnnoise.so",
            "/usr/local/lib/librnnoise.so.0",
            "/usr/local/lib/librnnoise.so.0.4.1",
            "/usr/local/lib/librnnoise.a",
            "/usr/local/lib/librnnoise.la",
            "/usr/local/lib/pkgconfig/rnnoise.pc",
            "/usr/local/include/rnnoise.h",
        }
        self.assertEqual(set(re.findall(r"/usr/local/[\w./]+", cleanup)), expected)
        self.assertLess(source.index("dpkg -i"), source.index("for bootstrap in"))
        self.assertLess(source.index("${db:Status-Status}"), source.index("for bootstrap in"))
        self.assertIn('pkg-config --variable=libdir rnnoise', source)
        self.assertIn('pkg-config --variable=pcfiledir rnnoise', source)
        self.assertIn('dpkg-query -S "$package_libdir/librnnoise.so.0"', source)
        self.assertIn('ldconfig -p', source)
        self.assertIn('readlink -f "$resolved_library"', source)
        for package_owned in (False, True):
            with self.subTest(package_owned=package_owned), tempfile.TemporaryDirectory() as work:
                directory = Path(work)
                local = directory / "usr/local"
                files = [directory / name.lstrip("/") for name in expected]
                for path in files:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("bootstrap")
                broken = local / "lib/librnnoise.so"
                broken.unlink()
                broken.symlink_to("missing-bootstrap-target")
                unrelated = local / "lib/libunrelated.so"
                unrelated.write_text("preserve")
                commands = directory / "bin"
                commands.mkdir()
                query = commands / "dpkg-query"
                query.write_text('#!/bin/sh\ntest "$OWN_BOOTSTRAP" = 1\n')
                query.chmod(0o755)
                result = subprocess.run(
                    ["bash", "-c", cleanup.replace("/usr/local", str(local))],
                    env=dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
                             OWN_BOOTSTRAP=str(int(package_owned))),
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 1 if package_owned else 0, result.stderr)
                self.assertEqual(unrelated.read_text(), "preserve")
                for path in files:
                    self.assertEqual(path.exists() or path.is_symlink(), package_owned, path)


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
            "/incoming/librnnoise0_0.2-1_amd64.deb",
            "/incoming/librnnoise-dev_0.2-1_arm64.deb",
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

    def test_release_asset_selector_rejects_debian12_and_uses_current_abis(self):
        source = (ROOT / "actions/install-shared-dependencies/action.yml").read_text()
        selector = re.search(r"asset=\$\(jq.*?'(.*?)' <<<", source, re.S).group(1)
        self.assertNotIn("librate-adjusting-pcm-ring1", source)
        self.assertIn("default: v2.0.0-alpha.4", source)
        self.assertIn("default: v0.1.0-alpha.3", source)
        self.assertIn("librptadvradio4", source)
        self.assertIn("librptadv-portaudio-alsa-adapter2", source)
        self.assertIn("librptadv-rnnoise-adapter1", source)
        self.assertIn("librnnoise0 librnnoise-dev", source)
        for architecture in ("amd64", "arm64"):
            for package, version in (
                ("librate-adjusting-pcm-ring2", "2.0.0.alpha4-1"),
                ("librptadv-samplerate-adapter1", "0.1.0.alpha3-1"),
                ("librptadvradio4", "0.1.0.alpha5-1"),
                ("librptadv-portaudio-alsa-adapter2", "0.2.0.alpha3-1"),
                ("librptadv-rnnoise-adapter1", "0.1.0.alpha2-1"),
                ("librnnoise0", "0.2-1"),
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

    def test_installs_the_adapter_release_required_by_usbradioplus(self):
        source = (ROOT / "actions/install-shared-dependencies/action.yml").read_text()
        self.assertIn(
            "download_release rptadv-portaudio-alsa-adapter v0.2.0-alpha.3",
            source,
        )

    def test_package_workflow_passes_release_event_versions_to_dependency_downloads(self):
        source = (ROOT / ".github/workflows/packages.yml").read_text()
        self.assertIn("ring_version:", source)
        self.assertIn("samplerate_adapter_version:", source)
        self.assertIn("ring-version: ${{ inputs.ring_version }}", source)
        self.assertIn(
            "samplerate-adapter-version: ${{ inputs.samplerate_adapter_version }}",
            source,
        )

    def test_shared_package_dispatch_is_limited_and_includes_release_metadata(self):
        source = (ROOT / "actions/dispatch-package-index/action.yml").read_text()
        script = textwrap.dedent(re.search(
            r"(?m)^      run: \|\n((?:        .*\n|\n)+)", source
        ).group(1))
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            commands = directory / "bin"
            commands.mkdir()
            fake_gh = commands / "gh"
            fake_gh.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$GH_LOG\"\n"
                "cat > \"$GH_BODY\"\n"
            )
            fake_gh.chmod(0o755)
            args_log = directory / "args.log"
            body_log = directory / "body.json"
            environment = dict(
                os.environ,
                PATH=str(commands) + os.pathsep + os.environ["PATH"],
                GH_TOKEN="test-token",
                GH_LOG=str(args_log),
                GH_BODY=str(body_log),
                PACKAGE_REPOSITORY="cpeter1207/rate_adjusting_pcm_ring",
                PACKAGE_TAG="v2.0.0-alpha.4",
                SAMPLERATE_ADAPTER_TAG="v0.1.0-alpha.3",
            )
            result = subprocess.run(
                ["bash", "-c", script], env=environment, text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = args_log.read_text()
            payload = json.loads(body_log.read_text())
            self.assertIn("repos/cpeter1207/USBRadioPlus/dispatches", arguments)
            self.assertEqual(payload["event_type"], "shared-package-release")
            self.assertEqual(
                payload["client_payload"]["repository"],
                "cpeter1207/rate_adjusting_pcm_ring",
            )
            self.assertEqual(payload["client_payload"]["tag"], "v2.0.0-alpha.4")
            self.assertEqual(
                payload["client_payload"]["samplerate_adapter_tag"],
                "v0.1.0-alpha.3",
            )

            args_log.unlink()
            body_log.unlink()
            environment["PACKAGE_REPOSITORY"] = "untrusted/repository"
            result = subprocess.run(
                ["bash", "-c", script], env=environment, text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(args_log.exists())


if __name__ == "__main__":
    unittest.main()
