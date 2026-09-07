"""Unit tests for assert-mail-secrets-present.py.

The module's filename has hyphens, so it is loaded by path rather than
imported -- the same reason and the same shape as this directory's sibling
test module.
"""

from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest

_MODULE_PATH = pathlib.Path(__file__).with_name("assert-mail-secrets-present.py")
_spec = importlib.util.spec_from_file_location("assert_mail_secrets_present", _MODULE_PATH)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def write_config(tmp: str, text: str) -> pathlib.Path:
    path = pathlib.Path(tmp) / "Pulumi.blog2.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TogglesAreReadFromTheConfig(unittest.TestCase):
    def test_namespaced_key_with_a_value_counts(self):
        self.assertEqual(guard.toggles_set("  blog2-infra:mailHost: mx1\n"), {"mailHost"})

    def test_a_value_containing_a_colon_does_not_break_the_split(self):
        text = "  blog2-infra:bulkEmailBaseUrl: https://mx1.branchleft.co.uk:8443\n"
        self.assertEqual(guard.toggles_set(text), {"bulkEmailBaseUrl"})

    def test_a_commented_toggle_is_not_a_requirement(self):
        self.assertEqual(guard.toggles_set("  # blog2-infra:mailHost: mx1\n"), set())

    def test_a_toggle_with_no_value_is_not_a_requirement(self):
        self.assertEqual(guard.toggles_set("  blog2-infra:mailHost:\n"), set())

    def test_the_toggle_word_inside_another_value_is_not_a_requirement(self):
        text = "  blog2-infra:mailFrom: 'set mailHost: yes'\n"
        self.assertNotIn("mailHost", guard.toggles_set(text))

    def test_an_unrelated_key_is_ignored(self):
        self.assertEqual(guard.toggles_set("  blog2-infra:slug: blog2\n"), set())

    def test_a_bare_top_level_key_is_not_a_namespaced_toggle(self):
        self.assertEqual(guard.toggles_set("config:\n"), set())


class RequirementIsConditional(unittest.TestCase):
    def test_no_toggle_needs_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "config:\n  blog2-infra:slug: blog2\n")
            status, problems = guard.check(path, {})
            self.assertEqual(status, guard.EXIT_OK)
            self.assertEqual(problems, [])

    def test_mail_toggle_without_its_secret_is_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "config:\n  blog2-infra:mailHost: mx1\n")
            status, problems = guard.check(path, {})
            self.assertEqual(status, guard.EXIT_MISSING)
            self.assertIn("MAIL_SMTP_PASSWORD", problems[0])

    def test_the_message_names_the_config_key_that_made_it_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "config:\n  blog2-infra:mailHost: mx1\n")
            _, problems = guard.check(path, {})
            self.assertIn("mailHost", problems[0])
            self.assertIn("mailPassword", problems[0])

    def test_an_empty_secret_is_treated_as_absent(self):
        # GitHub expands an unset secret to '' rather than failing, so this is
        # the case that actually occurs, not a defensive extra.
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "config:\n  blog2-infra:mailHost: mx1\n")
            status, _ = guard.check(path, {"MAIL_SMTP_PASSWORD": ""})
            self.assertEqual(status, guard.EXIT_MISSING)

    def test_a_whitespace_only_secret_is_treated_as_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "config:\n  blog2-infra:mailHost: mx1\n")
            status, _ = guard.check(path, {"MAIL_SMTP_PASSWORD": "  \t "})
            self.assertEqual(status, guard.EXIT_MISSING)

    def test_each_block_is_reported_independently(self):
        text = "config:\n  blog2-infra:mailHost: mx1\n  blog2-infra:bulkEmailBaseUrl: https://x\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, text)
            status, problems = guard.check(path, {"MAIL_SMTP_PASSWORD": "p"})
            self.assertEqual(status, guard.EXIT_MISSING)
            self.assertEqual(len(problems), 1)
            self.assertIn("BULK_EMAIL_API_KEY", problems[0])

    def test_both_toggles_satisfied_is_clean(self):
        text = "config:\n  blog2-infra:mailHost: mx1\n  blog2-infra:bulkEmailBaseUrl: https://x\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, text)
            status, problems = guard.check(path, {"MAIL_SMTP_PASSWORD": "p", "BULK_EMAIL_API_KEY": "k"})
            self.assertEqual(status, guard.EXIT_OK)
            self.assertEqual(problems, [])


class UnreadableIsNotClean(unittest.TestCase):
    def test_a_missing_config_exits_three_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "absent.yaml"
            status, problems = guard.check(path, {})
            self.assertEqual(status, guard.EXIT_UNREADABLE)
            self.assertIn("refuses to guess", problems[0])

    def test_a_config_that_is_not_utf8_exits_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "Pulumi.blog2.yaml"
            path.write_bytes(b"config:\n  blog2-infra:mailHost: \xff\xfe\n")
            status, _ = guard.check(path, {})
            self.assertEqual(status, guard.EXIT_UNREADABLE)


class TheGuardCanFail(unittest.TestCase):
    def test_the_self_test_passes(self):
        self.assertEqual(guard._self_test(), 0)


if __name__ == "__main__":
    unittest.main()
