# USBRadioPlus Workflows

Reusable GitHub Actions workflow implementations for
[USBRadioPlus](https://github.com/cpeter1207/USBRadioPlus).

The USBRadioPlus repository owns event triggers, permissions, secrets, and
references to this repository's protected `main` branch. This repository owns the implementation of its
quality, platform-test, package, container, documentation, site, and release
automation.

Changes here validate workflow structure without building USBRadioPlus. Once a
change reaches `main`, subsequent caller runs use it without changing the
USBRadioPlus repository.
