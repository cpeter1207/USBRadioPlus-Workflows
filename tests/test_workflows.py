#!/usr/bin/env python3
"""Check release matrix boundaries and shell programs embedded in actions."""

import hashlib
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
    """Protect native platform coverage and the split-package release path."""

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
        self.assertIn("../libusbradioplus-product1_*.deb", source)
        self.assertIn("../libusbradioplus-product-dev_*.deb", source)
        self.assertNotIn("dpkg -i ../usbradioplus_*.deb", source)

    def test_release_stages_and_checks_split_product_packages(self):
        source = (ROOT / ".github/workflows/packages.yml").read_text()
        build = source.split("      - name: Build USBRadioPlus", 1)[1].split(
            "      - uses: actions/upload-artifact", 1
        )[0]
        self.assertIn("../libusbradioplus-product1_*.deb", build)
        self.assertIn("../libusbradioplus-product-dev_*.deb", build)
        self.assertIn("libusbradioplus-product1-dbgsym_*.deb", build)
        self.assertIn(
            'dpkg-deb -f ../libusbradioplus-product1_*.deb Depends | grep -F "$library"',
            build,
        )
        self.assertNotIn(
            'dpkg-deb -f ../usbradioplus_*.deb Depends | grep -F "$library"',
            build,
        )
        self.assertIn('libusbradioplus-product1 (= $product_version)', build)

    def test_shared_release_refresh_does_not_build_consumer_packages(self):
        source = (ROOT / ".github/workflows/packages.yml").read_text()
        matrix = source.split("  select_package_matrix:\n", 1)[1].split("  build:\n", 1)[0]
        self.assertIn("INDEX_ONLY: ${{ inputs.released_repository != '' }}", matrix)
        self.assertIn('if [[ "$INCLUDE_DEBIAN12" == "true" && "$INDEX_ONLY" != "true" ]]', matrix)
        matrix_script = textwrap.dedent(matrix.split("        run: |\n", 1)[1])
        subprocess.run(["shellcheck", "--shell=bash", "-"], input=matrix_script,
                       text=True, check=True)
        with tempfile.TemporaryDirectory() as work:
            output = Path(work) / "output"
            result = subprocess.run(
                ["bash", "-c", matrix_script],
                env=dict(os.environ, INCLUDE_DEBIAN12="true", INDEX_ONLY="true",
                         GITHUB_OUTPUT=str(output)),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            selected = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual({entry["suite"] for entry in json.loads(selected["build_matrix"])["include"]},
                             {"trixie"})
        build = source.split("  build:\n", 1)[1].split("  publish:\n", 1)[0]
        for step in (
            "actions/checkout@v7", "Trust checked-out workspace",
            "Require the previously passed pull-request gate",
            "Install ASL3 repository and package tools",
            "Install manually published Debian 12 dependencies",
            "Install RNNoise companion packages",
            "Build USBRadioPlus integration and product packages",
        ):
            with self.subTest(step=step):
                match = re.search(
                    rf"(?m)^      - (?:uses: |name: ){re.escape(step)}\n"
                    r"(?:(?!^      - ).*\n)*", build,
                )
                self.assertIsNotNone(match, step)
                self.assertIn("if: inputs.released_repository == ''", match.group(0))
        downloader = build.split("      - name: Install released shared dependencies\n", 1)[1].split(
            "      - name:", 1
        )[0]
        self.assertIn("install: ${{ inputs.released_repository == '' }}", downloader)
        staging = build.split("      - name: Stage released shared packages for signed index\n", 1)[1].split(
            "      - uses: actions/upload-artifact", 1
        )[0]
        self.assertIn("if: inputs.released_repository != ''", staging)
        self.assertIn("SHARED_PACKAGE_DIRECTORY: ${{ steps.shared.outputs.package-directory }}", staging)
        script = textwrap.dedent(staging.split("        run: |\n", 1)[1])
        subprocess.run(["shellcheck", "--shell=bash", "-"], input=script,
                       text=True, check=True)
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            verified = directory / "verified"
            verified.mkdir()
            expected = {"librptadvtone1_0.1_amd64.deb", "librptadvtone-dev_0.1_amd64.deb"}
            for name in expected:
                (verified / name).write_text(name)
            (directory / "usbradioplus_0.1_amd64.deb").write_text("not released")
            result = subprocess.run(
                ["bash", "-c", script], cwd=directory,
                env=dict(os.environ, SHARED_PACKAGE_DIRECTORY=str(verified)),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({path.name for path in (directory / "package-output").iterdir()}, expected)

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
            return {"id": identifier, "name": name, "conclusion": conclusion,
                    "status": status}

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
                pages = "\n".join(json.dumps({"check_runs": page}) for page in (first, second))
                result = subprocess.run(
                    ["bash", "-c", commands + script],
                    env=dict(os.environ, GITHUB_REPOSITORY="example/source", GATE_PAGES=pages),
                    text=True, capture_output=True, check=False,
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
                    text=True, capture_output=True, check=False,
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
            "/incoming/libusbradioplus-product1_0.1.0~alpha18-1.deb13_amd64.deb",
            "/incoming/libusbradioplus-product-dev_0.1.0~alpha18-1.deb13_arm64.deb",
            "/incoming/libusbradioplus-product1-dbgsym_0.1.0~alpha18-1.deb13_amd64.deb",
            "/incoming/usbradioplus_0.1.0~alpha18-1.deb13_amd64.deb",
            "/incoming/usbradioplus-dbgsym_0.1.0~alpha18-1.deb13_amd64.deb",
            "/incoming/librate-adjusting-pcm-ring1_1.0.1-1.deb13_amd64.deb",
            "/incoming/librate-adjusting-pcm-ring3_3.0.0.alpha2-1_amd64.deb",
            "/incoming/librptadv-portaudio-alsa-adapter1_0.1.0.alpha2-1_amd64.deb",
            "/incoming/librnnoise0_0.2-1_amd64.deb",
            "/incoming/librnnoise-dev_0.2-1_arm64.deb",
            "/incoming/rpt-advanced_0.1.0.alpha9-1_arm64.deb",
            "/incoming/app-rpt-advanced_0.1.0.alpha9-1_amd64.deb",
            "/incoming/librptadv-resolver-adapter1_0.1.0.alpha9-1_arm64.deb",
            "/incoming/librptadv-jitter-adapter1_0.1.0.alpha9-1_amd64.deb",
            "/incoming/librptadv-iax2-client1_0.1.0.alpha5_arm64.deb",
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
            text=True, check=False, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f"{path}:trixie" for path in retained])

    def test_release_asset_selector_rejects_debian12_and_uses_current_abis(self):
        source = (ROOT / "actions/install-shared-dependencies/action.yml").read_text()
        selector = re.search(r"asset=\$\(jq.*?'(.*?)' <<<", source, re.DOTALL).group(1)
        self.assertNotIn("librate-adjusting-pcm-ring1", source)
        self.assertIn("default: v3.0.0-alpha2", source)
        self.assertIn("default: v0.2.0-alpha1", source)
        self.assertIn("librptadvradio4", source)
        self.assertIn("librptadv-portaudio-alsa-adapter2", source)
        self.assertIn("librptadv-rnnoise-adapter1", source)
        self.assertIn("librnnoise0 librnnoise-dev", source)
        self.assertIn('pkg-config --variable=abi_version rptadv_samplerate_adapter)" = 2', source)
        self.assertIn("librate_adjusting_pcm_ring3.so.3", source)
        for architecture in ("amd64", "arm64"):
            for package, version in (
                ("librate-adjusting-pcm-ring3", "3.0.0.alpha2-1"),
                ("librptadv-samplerate-adapter2", "0.2.0.alpha1-1"),
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
        self.assertIn(
            "download_release rptadv-gpio-adapter v0.1.0-alpha.4",
            source,
        )

    def test_normal_download_route_uses_released_owner_and_provider_versions(self):
        source = (ROOT / "actions/install-shared-dependencies/action.yml").read_text()
        ring_tag = re.search(r"(?ms)^  ring-version:\n.*?^    default: ([^\n]+)", source).group(1)
        samplerate_tag = re.search(
            r"(?ms)^  samplerate-adapter-version:\n.*?^    default: ([^\n]+)", source
        ).group(1)
        route = source.split(
            '        if [[ -z "$RELEASED_REPOSITORY" || "$INSTALL_PACKAGES" != false ]]; then\n', 1
        )[1].split('        if [[ -n "$RELEASED_REPOSITORY$RELEASED_TAG" ]]; then\n', 1)[0]
        script = (
            'set -euo pipefail\n'
            'download_release() { printf "%s\\n" "$*" >> "$DOWNLOAD_LOG"; }\n'
            'if [[ -z "$RELEASED_REPOSITORY" || "$INSTALL_PACKAGES" != false ]]; then\n'
            + textwrap.dedent(route)
        )
        with tempfile.TemporaryDirectory() as work:
            log = Path(work) / "downloads.log"
            result = subprocess.run(
                ["bash", "-c", script],
                env=dict(os.environ, DOWNLOAD_LOG=str(log), RELEASED_REPOSITORY="",
                         INSTALL_PACKAGES="false", RING_VERSION=ring_tag,
                         SAMPLERATE_ADAPTER_VERSION=samplerate_tag),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text().splitlines(), [
                "rate_adjusting_pcm_ring v3.0.0-alpha2 librate-adjusting-pcm-ring3 librate-adjusting-pcm-ring3-dev",
                "librptadvradio v0.1.0-alpha.8 librptadvradio4 librptadvradio-dev",
                "rptadv-samplerate-adapter v0.2.0-alpha1 librptadv-samplerate-adapter2 librptadv-samplerate-adapter-dev",
                "rptadv-ffmpeg-adapter v0.1.0-alpha.2 librptadv-ffmpeg-adapter1 librptadv-ffmpeg-adapter-dev",
                "rptadv-portaudio-alsa-adapter v0.2.0-alpha.3 librptadv-portaudio-alsa-adapter2 librptadv-portaudio-alsa-adapter-dev",
                "rptadv-gpio-adapter v0.1.0-alpha.4 librptadv-gpio-adapter1 librptadv-gpio-adapter-dev",
                "rptadv-rnnoise-adapter v0.1.0-alpha.2 librptadv-rnnoise-adapter1 librptadv-rnnoise-adapter-dev librnnoise0 librnnoise-dev",
                "librptadvsquelch v0.1.0-alpha.2 librptadvsquelch2 librptadvsquelch-dev",
                "librptadvctcss v0.1.0-alpha.2 librptadvctcss2 librptadvctcss-dev",
                "librptadvdcs v0.1.0-alpha.1 librptadvdcs1 librptadvdcs-dev",
                "librptadvsignaling v0.1.0-alpha.1 librptadvsignaling1 librptadvsignaling-dev",
            ])
        workflow = (ROOT / ".github/workflows/packages.yml").read_text()
        self.assertIn("default: v3.0.0-alpha2", workflow)
        self.assertIn("default: v0.2.0-alpha1", workflow)

    def test_candidate_pin_is_not_replaced_with_an_older_release(self):
        """A ring3 candidate must survive the installer's released fallback."""
        source = (ROOT / "actions/install-shared-dependencies/action.yml").read_text()
        function = textwrap.dedent(re.search(
            r"(?m)^        download_release\(\) \{\n.*?^        \}\n", source, re.DOTALL
        ).group(0))
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            (directory / ".github").mkdir()
            (directory / ".github/dependencies.json").write_text(json.dumps([{
                "repository": "rate_adjusting_pcm_ring",
                "sha": "1" * 40,
                "packages": ["librate-adjusting-pcm-ring3"],
            }]))
            result = subprocess.run(
                ["bash", "-c", "set -euo pipefail\ngh() { return 79; }\n" + function
                 + "\ndownload_release rate_adjusting_pcm_ring old-tag librate-adjusting-pcm-ring3"],
                cwd=directory, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_package_workflow_passes_release_event_versions_to_dependency_downloads(self):
        source = (ROOT / ".github/workflows/packages.yml").read_text()
        self.assertIn("ring_version:", source)
        self.assertIn("samplerate_adapter_version:", source)
        self.assertIn("ring-version: ${{ inputs.ring_version }}", source)
        self.assertIn(
            "samplerate-adapter-version: ${{ inputs.samplerate_adapter_version }}",
            source,
        )

    def test_package_workflow_forwards_only_released_library_identity(self):
        source = (ROOT / ".github/workflows/packages.yml").read_text()
        self.assertIn("released_repository:", source)
        self.assertIn("released_tag:", source)
        self.assertIn("released-repository: ${{ inputs.released_repository }}", source)
        self.assertIn("released-tag: ${{ inputs.released_tag }}", source)
        self.assertNotIn("released-ref:", source)

    def test_library_and_owner_releases_download_expected_packages(self):
        source = (ROOT / "actions/install-shared-dependencies/action.yml").read_text()
        route = textwrap.dedent(source.split(
            '        if [[ -n "$RELEASED_REPOSITORY$RELEASED_TAG" ]]; then\n', 1
        )[1].split('        if [[ "$INSTALL_PACKAGES" == true ]]; then\n', 1)[0])
        route = 'if [[ -n "$RELEASED_REPOSITORY$RELEASED_TAG" ]]; then\n' + route
        script = ('set -euo pipefail\n'
                  'download_release() { printf "%s\\n" "$@" > "$DOWNLOAD_LOG"; }\n'
                  + route)
        with tempfile.TemporaryDirectory() as work:
            log = Path(work) / "download.log"
            releases = [
                ("librptadvsquelch", "v0.1.0-alpha.2",
                 ("librptadvsquelch2", "librptadvsquelch-dev")),
                ("librptadvctcss", "v0.1.0-alpha.2",
                 ("librptadvctcss2", "librptadvctcss-dev")),
            ] + [
                (f"librptadv{component}", "v0.1.0-alpha.1",
                 (f"librptadv{component}1", f"librptadv{component}-dev"))
                for component in (
                    "dcs", "signaling", "tone", "morse", "dtmf", "template", "schedule",
                )
            ] + [
                ("librptadvradio", "v0.1.0-alpha.8",
                 ("librptadvradio4", "librptadvradio-dev")),
                ("rptadv-gpio-adapter", "v0.1.0-alpha.3",
                 ("librptadv-gpio-adapter1", "librptadv-gpio-adapter-dev")),
            ]
            for repository, tag, packages in releases:
                with self.subTest(repository=repository):
                    result = subprocess.run(
                        ["bash", "-c", script],
                        env=dict(os.environ, RELEASED_REPOSITORY=f"cpeter1207/{repository}",
                                 RELEASED_TAG=tag, DOWNLOAD_LOG=str(log)),
                        text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(log.read_text().splitlines(), [
                        repository, tag, *packages,
                    ])
                    log.unlink()
            for repository, tag in (("cpeter1207/unrelated", "v0.1.0-alpha.1"),
                                    ("cpeter1207/librptadvtone", "main"),
                                    ("cpeter1207/librptadvtone", "")):
                with self.subTest(repository=repository, tag=tag):
                    result = subprocess.run(
                        ["bash", "-c", script],
                        env=dict(os.environ, RELEASED_REPOSITORY=repository,
                                 RELEASED_TAG=tag, DOWNLOAD_LOG=str(log)),
                        text=True, capture_output=True, check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(log.exists())

    def test_index_only_downloads_only_requested_release(self):
        releases = [
            ("librptadvsquelch", "v0.1.0-alpha.2",
             ("librptadvsquelch2", "librptadvsquelch-dev")),
            ("librptadviax2", "v0.1.0-alpha5",
             ("librptadv-iax2-client1", "librptadv-iax2-client-dev")),
            ("rpt_advanced", "v0.1.0-alpha9", (
                "rpt-advanced", "app-rpt-advanced",
                "librptadv-product1", "librptadv-product-dev",
                "librptadv-file-adapter1", "librptadv-file-adapter-dev",
                "librptadv-speech-adapter1", "librptadv-speech-adapter-dev",
                "librptadv-control-asterisk-adapter1", "librptadv-control-asterisk-adapter-dev",
                "librptadv-control-standalone-adapter1", "librptadv-control-standalone-adapter-dev",
                "librptadv-resolver-adapter1", "librptadv-resolver-adapter-dev",
                "librptadv-jitter-adapter1", "librptadv-jitter-adapter-dev",
            )),
        ]
        for release in releases:
            with self.subTest(repository=release[0]):
                self.assert_index_only_downloads([release])

    def test_index_only_ring_downloads_released_pair_despite_candidate_pins(self):
        self.assert_index_only_downloads([
            ("rate_adjusting_pcm_ring", "v3.0.0-alpha3",
             ("librate-adjusting-pcm-ring3", "librate-adjusting-pcm-ring3-dev")),
            ("rptadv-samplerate-adapter", "v0.2.0-alpha2",
             ("librptadv-samplerate-adapter2", "librptadv-samplerate-adapter-dev")),
        ])

    def test_index_only_samplerate_downloads_only_its_release_despite_candidate_pins(self):
        self.assert_index_only_downloads([(
            "rptadv-samplerate-adapter", "v0.2.0-alpha2",
            ("librptadv-samplerate-adapter2", "librptadv-samplerate-adapter-dev"),
        )])

    def assert_index_only_downloads(self, releases):
        """Run the action against real Debian assets and a strict GitHub boundary."""
        source = (ROOT / "actions/install-shared-dependencies/action.yml").read_text()
        download = source.split("    - name: Download released shared libraries\n", 1)[1]
        script = textwrap.dedent(download.split("      run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            architecture = subprocess.run(
                ["dpkg", "--print-architecture"], text=True, capture_output=True,
                check=True,
            ).stdout.strip()
            (directory / ".github").mkdir()
            (directory / ".github/dependencies.json").write_text(json.dumps([
                {"repository": repository, "sha": "1" * 40, "packages": list(names)}
                for repository, _, names in releases
            ]))
            filenames = set()
            responses = []
            expected_calls = []
            for repository, tag, names in releases:
                assets = []
                version = tag[1:].replace("-", ".") + "-1"
                for package in names:
                    source_dir = directory / package
                    control = source_dir / "DEBIAN/control"
                    control.parent.mkdir(parents=True)
                    control.write_text(
                        f"Package: {package}\nVersion: {version}\n"
                        f"Architecture: {architecture}\nMaintainer: Test <test@example.org>\n"
                        "Description: Test package\n"
                    )
                    filename = f"{package}_{version}_{architecture}.deb"
                    package_file = directory / filename
                    subprocess.run(["dpkg-deb", "--build", str(source_dir), str(package_file)],
                                   check=True, capture_output=True)
                    filenames.add(filename)
                    assets.append({
                        "name": filename,
                        "digest": "sha256:" + hashlib.sha256(package_file.read_bytes()).hexdigest(),
                        "browser_download_url": package_file.as_uri(),
                    })
                metadata = directory / f"{repository}.json"
                metadata.write_text(json.dumps({"draft": False, "assets": assets}))
                call = f"api repos/cpeter1207/{repository}/releases/tags/{tag}"
                expected_calls.append(call)
                responses.append(f"'{call}') cat '{metadata}' ;;\n")
            commands = directory / "bin"
            commands.mkdir()
            gh = commands / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$GH_LOG\"\n"
                "case \"$*\" in\n" + "".join(responses) + "*) exit 72 ;;\nesac\n"
            )
            gh.chmod(0o755)
            calls = directory / "gh.log"
            output = directory / "github-output"
            result = subprocess.run(
                ["bash", "-c", script], cwd=directory,
                env=dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
                         GH_LOG=str(calls),
                         GITHUB_OUTPUT=str(output), REQUESTED_PACKAGE_DIRECTORY="dependencies/packages",
                         INSTALL_PACKAGES="false", RING_VERSION="v3.0.0-alpha3",
                         SAMPLERATE_ADAPTER_VERSION="v0.2.0-alpha2",
                         RELEASED_REPOSITORY=f"cpeter1207/{releases[0][0]}",
                         RELEASED_TAG=releases[0][1]),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls.read_text().splitlines() if calls.exists() else [], expected_calls)
            self.assertEqual({path.name for path in (directory / "dependencies/packages").iterdir()},
                             filenames)
            self.assertIn("package_directory=", output.read_text())
            self.assertIn("if: inputs.released-repository == '' || inputs.install != 'false'", source)

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
                SAMPLERATE_ADAPTER_TAG="v0.2.0-alpha.1",
            )
            result = subprocess.run(
                ["bash", "-c", script], env=environment, text=True, capture_output=True,
                check=False,
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
                "v0.2.0-alpha.1",
            )

            args_log.unlink()
            body_log.unlink()
            environment["PACKAGE_REPOSITORY"] = "untrusted/repository"
            result = subprocess.run(
                ["bash", "-c", script], env=environment, text=True, capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(args_log.exists())

            for repository in (
                "librptadvsquelch", "librptadvctcss", "librptadvdcs",
                "librptadvsignaling", "librptadvtone", "librptadvmorse",
                "librptadvdtmf", "librptadvtemplate", "librptadvschedule",
                "librptadvradio", "rptadv-gpio-adapter",
                "librptadviax2", "rpt_advanced",
            ):
                with self.subTest(repository=repository):
                    environment["PACKAGE_REPOSITORY"] = f"cpeter1207/{repository}"
                    result = subprocess.run(
                        ["bash", "-c", script], env=environment,
                        text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        json.loads(body_log.read_text())["client_payload"]["repository"],
                        f"cpeter1207/{repository}",
                    )


if __name__ == "__main__":
    unittest.main()
