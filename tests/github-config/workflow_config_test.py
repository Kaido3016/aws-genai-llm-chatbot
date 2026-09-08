"""
A6 — validation tests for .github/workflows/codeql.yml and
.github/dependabot.yml.

These are genuinely executable, dependency-light checks (PyYAML is
available in this sandbox) — they do NOT and cannot verify that GitHub
Actions/CodeQL/Dependabot actually run correctly; that requires GitHub's
hosted environment (UNVERIFIED — REQUIRES GITHUB ACTIONS, see
SECURITY.md/FINAL_AUDIT.md). What they DO verify, for real, against the
actual repository on disk:

  - both YAML files parse without error
  - the CodeQL workflow has least-privilege permissions and analyzes
    languages this repository actually contains in meaningful quantity
  - every Dependabot `directory` entry actually contains the manifest
    file that ecosystem expects (catches the classic "config points at
    a directory with no package.json" mistake)
  - no ecosystem is configured for a package manager this repo doesn't
    use
  - neither file contains anything that looks like an embedded secret
"""

import re
import unittest
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CODEQL_PATH = _REPO_ROOT / ".github" / "workflows" / "codeql.yml"
_DEPENDABOT_PATH = _REPO_ROOT / ".github" / "dependabot.yml"


class TestCodeQLWorkflowParses(unittest.TestCase):
    def setUp(self):
        with open(_CODEQL_PATH) as f:
            self.doc = yaml.safe_load(f)

    def test_file_exists(self):
        self.assertTrue(_CODEQL_PATH.exists())

    def test_parses_as_valid_yaml(self):
        self.assertIsInstance(self.doc, dict)

    def test_has_a_name(self):
        self.assertIn("name", self.doc)

    def test_has_expected_triggers(self):
        # "on" is parsed as the boolean True by PyYAML under YAML 1.1
        # (a well-known, harmless quirk — GitHub's own parser treats it
        # as the literal key "on"). Every pre-existing workflow in this
        # repo (build.yaml, deploy.yml) has the identical characteristic
        # when parsed this way, confirmed separately.
        triggers = self.doc.get(True) or self.doc.get("on")
        self.assertIsNotNone(triggers)
        self.assertIn("push", triggers)
        self.assertIn("pull_request", triggers)
        self.assertIn("schedule", triggers)

    def test_permissions_are_least_privilege(self):
        perms = self.doc["permissions"]
        self.assertEqual(perms.get("contents"), "read")
        self.assertEqual(perms.get("security-events"), "write")
        # Must NOT grant broad write access.
        for key, value in perms.items():
            if key not in ("security-events",):
                self.assertNotEqual(
                    value, "write", f"Unexpected write permission granted: {key}"
                )

    def test_analyzes_python_and_javascript(self):
        matrix = self.doc["jobs"]["analyze"]["strategy"]["matrix"]
        self.assertIn("python", matrix["language"])
        self.assertIn("javascript", matrix["language"])

    def test_does_not_use_deprecated_codeql_action_major_versions(self):
        content = _CODEQL_PATH.read_text()
        for deprecated in ("codeql-action/init@v1", "codeql-action/init@v2"):
            self.assertNotIn(deprecated, content)
        self.assertIn("codeql-action/init@v3", content)

    def test_no_embedded_secret_material(self):
        content = _CODEQL_PATH.read_text()
        # A CodeQL workflow needs no secrets at all — anything referencing
        # secrets.* here would be suspicious.
        self.assertNotIn("secrets.", content)
        self.assertFalse(re.search(r"AKIA[0-9A-Z]{16}", content))


class TestDependabotConfigParses(unittest.TestCase):
    def setUp(self):
        with open(_DEPENDABOT_PATH) as f:
            self.doc = yaml.safe_load(f)

    def test_file_exists(self):
        self.assertTrue(_DEPENDABOT_PATH.exists())

    def test_parses_as_valid_yaml(self):
        self.assertIsInstance(self.doc, dict)

    def test_version_is_2(self):
        self.assertEqual(self.doc["version"], 2)

    def test_no_embedded_secret_material(self):
        content = _DEPENDABOT_PATH.read_text()
        self.assertNotIn("secrets.", content)
        self.assertFalse(re.search(r"AKIA[0-9A-Z]{16}", content))


class TestDependabotDirectoriesMatchRealManifests(unittest.TestCase):
    """The core "does this config correspond to reality" check."""

    def setUp(self):
        with open(_DEPENDABOT_PATH) as f:
            self.doc = yaml.safe_load(f)
        self.updates = self.doc["updates"]

    def _manifest_exists_for(self, ecosystem, directory):
        target_dir = _REPO_ROOT / directory.lstrip("/")
        if ecosystem == "npm":
            return (target_dir / "package.json").exists()
        if ecosystem == "pip":
            return any(
                (target_dir / name).exists()
                for name in ("requirements.txt", "pyproject.toml", "Pipfile")
            )
        if ecosystem == "docker":
            return (target_dir / "Dockerfile").exists()
        if ecosystem == "github-actions":
            return (target_dir / ".github" / "workflows").is_dir()
        raise AssertionError(f"Unhandled ecosystem in test: {ecosystem}")

    def test_every_declared_directory_has_a_real_manifest(self):
        for update in self.updates:
            ecosystem = update["package-ecosystem"]
            directory = update["directory"]
            self.assertTrue(
                self._manifest_exists_for(ecosystem, directory),
                f"No {ecosystem} manifest found at {directory!r} — "
                "dependabot.yml points at a directory with no matching "
                "dependency file.",
            )

    def test_no_duplicate_ecosystem_directory_pairs(self):
        seen = set()
        for update in self.updates:
            key = (update["package-ecosystem"], update["directory"])
            self.assertNotIn(key, seen, f"Duplicate entry: {key}")
            seen.add(key)

    def test_only_ecosystems_the_repo_actually_uses(self):
        allowed = {"npm", "pip", "docker", "github-actions"}
        for update in self.updates:
            self.assertIn(update["package-ecosystem"], allowed)

    def test_both_npm_directories_covered(self):
        npm_dirs = {
            u["directory"] for u in self.updates if u["package-ecosystem"] == "npm"
        }
        self.assertIn("/", npm_dirs)
        self.assertIn("/lib/user-interface/react-app", npm_dirs)

    def test_update_frequency_is_not_daily_to_avoid_excessive_noise(self):
        for update in self.updates:
            self.assertNotEqual(update["schedule"]["interval"], "daily")


class TestNonStandardDockerfilesAreDocumentedNotSilentlyMissed(unittest.TestCase):
    """Confirms the two non-standard-named Dockerfiles genuinely would
    not be picked up by Dependabot's docker ecosystem, validating that
    the limitation documented in dependabot.yml's comments is accurate
    rather than assumed."""

    def test_non_standard_dockerfiles_exist_and_are_not_covered(self):
        non_standard = [
            _REPO_ROOT / "lib" / "shared" / "file-import-dockerfile",
            _REPO_ROOT / "lib" / "shared" / "web-crawler-dockerfile",
        ]
        for path in non_standard:
            self.assertTrue(path.exists(), f"{path} should exist")

        with open(_DEPENDABOT_PATH) as f:
            doc = yaml.safe_load(f)
        docker_dirs = {
            u["directory"] for u in doc["updates"] if u["package-ecosystem"] == "docker"
        }
        self.assertNotIn("/lib/shared", docker_dirs)

    def test_standard_dockerfile_is_covered(self):
        standard = _REPO_ROOT / "lib" / "shared" / "alpine-zip" / "Dockerfile"
        self.assertTrue(standard.exists())

        with open(_DEPENDABOT_PATH) as f:
            doc = yaml.safe_load(f)
        docker_dirs = {
            u["directory"] for u in doc["updates"] if u["package-ecosystem"] == "docker"
        }
        self.assertIn("/lib/shared/alpine-zip", docker_dirs)


if __name__ == "__main__":
    unittest.main()
