# USBRadioPlus Workflows

Reusable GitHub Actions workflow implementations for
[USBRadioPlus](https://github.com/cpeter1207/USBRadioPlus).

The USBRadioPlus repository owns event triggers, permissions, secrets, and
immutable workflow references. This repository owns the implementation of its
quality, platform-test, package, container, documentation, site, and release
automation.

Changes here validate workflow structure without building USBRadioPlus. Product
verification runs only when a caller in the USBRadioPlus repository invokes a
pinned workflow revision.
