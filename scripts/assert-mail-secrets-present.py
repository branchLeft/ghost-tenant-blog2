#!/usr/bin/env python3
"""Fail the deploy when a mail toggle is set in the stack config but its
credential is not supplied by the environment.

Both mail blocks in `config.ts` are all-or-nothing, and each is gated on one
key: `mailHost` makes `mailPassword` a `requireSecret`, and `bulkEmailBaseUrl`
makes `bulkEmailApiKey` one. Ghost additionally treats a partial
`bulkEmail.mailgun` object as configured and crashes on `new URL(undefined)`,
so a half-supplied block is worse than an absent one.

The requirement is therefore conditional, and so is this check: it reads the
toggles out of the stack config rather than assuming them. An unconditional
check would demand a credential nothing needs the moment a tenant turns mail
off, and would cite a config line that is no longer there.

GitHub expands an unset secret to the empty string rather than failing, so a
missing credential is indistinguishable from an empty one here, and both are
refused.

    assert-mail-secrets-present.py --config Pulumi.blog2.yaml
    assert-mail-secrets-present.py --self-test

Exit codes: 0 nothing missing, 1 a required credential is absent or empty,
3 the config could not be read. Not 2 -- argparse exits 2 on a usage error,
and a caller that has to tell a usage error from a finding cannot.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile

EXIT_OK = 0
EXIT_MISSING = 1
EXIT_UNREADABLE = 3

# toggle key -> (env var the workflow supplies, pulumi config key it feeds)
REQUIREMENTS: dict[str, tuple[str, str]] = {
    "mailHost": ("MAIL_SMTP_PASSWORD", "mailPassword"),
    "bulkEmailBaseUrl": ("BULK_EMAIL_API_KEY", "bulkEmailApiKey"),
}


def is_commented(line: str) -> bool:
    return line.lstrip().startswith("#")


def toggles_set(text: str) -> set[str]:
    """Which toggle keys carry a non-empty value in this stack config.

    Deliberately textual rather than a YAML parse: this runs on a deploy
    runner before dependencies are guaranteed, and the sibling guard in this
    directory reads the same file the same way. A key is recognised only as
    `<namespace>:<key>:` so that a `mailHost` appearing inside a comment or a
    value never counts.
    """
    found: set[str] = set()
    for raw in text.splitlines():
        if is_commented(raw):
            continue
        stripped = raw.strip()
        # Split key from value at the first colon-SPACE, not the first colon:
        # the key is itself namespaced (`blog2-infra:mailHost`) and values
        # contain colons of their own (`https://...`, `<blog@...>`).
        if ": " in stripped:
            head, _, value = stripped.partition(": ")
        elif stripped.endswith(":"):
            head, value = stripped[:-1], ""
        else:
            continue
        if ":" not in head:
            # Not a namespaced config key.
            continue
        key = head.rsplit(":", 1)[1].strip()
        if key in REQUIREMENTS and value.strip():
            found.add(key)
    return found


def check(config_path: pathlib.Path, env: dict[str, str]) -> tuple[int, list[str]]:
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return EXIT_UNREADABLE, [
            f"{config_path} could not be read ({exc.__class__.__name__}). "
            "Whether this stack needs mail credentials is therefore unknown, "
            "and this refuses to guess."
        ]

    problems: list[str] = []
    for toggle in sorted(toggles_set(text)):
        env_var, config_key = REQUIREMENTS[toggle]
        if not env.get(env_var, "").strip():
            problems.append(
                f"{config_path} sets {toggle}, which makes {config_key} a required "
                f"secret, but the {env_var} environment secret is empty or unset on "
                "this repository's production environment. Set it before deploying."
            )
    return (EXIT_MISSING if problems else EXIT_OK), problems


def _self_test() -> int:
    """Prove the check can fail, not merely that it passes.

    A guard whose only evidence is a green run has never been shown to have a
    red one.
    """
    cases: list[tuple[str, str, dict[str, str], int]] = [
        (
            "both toggles set and both secrets present -> clean",
            "config:\n  t:mailHost: mx1\n  t:bulkEmailBaseUrl: https://x\n",
            {"MAIL_SMTP_PASSWORD": "p", "BULK_EMAIL_API_KEY": "k"},
            EXIT_OK,
        ),
        (
            "mailHost set, password unset -> finding",
            "config:\n  t:mailHost: mx1\n",
            {},
            EXIT_MISSING,
        ),
        (
            "mailHost set, password whitespace only -> finding",
            "config:\n  t:mailHost: mx1\n",
            {"MAIL_SMTP_PASSWORD": "   "},
            EXIT_MISSING,
        ),
        (
            "bulk toggle set, api key unset -> finding even with mail fine",
            "config:\n  t:mailHost: mx1\n  t:bulkEmailBaseUrl: https://x\n",
            {"MAIL_SMTP_PASSWORD": "p"},
            EXIT_MISSING,
        ),
        (
            "no toggles -> clean with no secrets at all",
            "config:\n  t:slug: blog2\n",
            {},
            EXIT_OK,
        ),
        (
            "toggle only inside a comment -> not a requirement",
            "config:\n  # t:mailHost: mx1\n",
            {},
            EXIT_OK,
        ),
        (
            "toggle key with an empty value -> not a requirement",
            "config:\n  t:mailHost:\n",
            {},
            EXIT_OK,
        ),
    ]

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "Pulumi.t.yaml"
        for name, text, env, expected in cases:
            path.write_text(text, encoding="utf-8")
            status, _ = check(path, env)
            if status == expected:
                print(f"PASS: {name}")
            else:
                print(f"FAIL: {name} -- expected {expected}, got {status}")
                failures += 1

        missing = pathlib.Path(tmp) / "does-not-exist.yaml"
        status, _ = check(missing, {})
        if status == EXIT_UNREADABLE:
            print("PASS: an unreadable config exits 3, not 0 and not a finding")
        else:
            print(f"FAIL: an unreadable config exited {status}, expected {EXIT_UNREADABLE}")
            failures += 1

    if failures:
        print(f"\nFAILED: {failures} self-test case(s) did not behave as documented")
        return 1
    print("\nOK: assert-mail-secrets-present.py self-test passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="the stack config to read the toggles from")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.config:
        parser.error("pass --config PATH, or --self-test")

    status, problems = check(pathlib.Path(args.config), dict(os.environ))
    for problem in problems:
        print(f"::error::{problem}")
    return status


if __name__ == "__main__":
    sys.exit(main())
