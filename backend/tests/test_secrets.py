# tests/test_secrets.py
"""Build-context secret scanner.

Prevents the most common cause of leaked credentials: a developer committing
a real key to the repo. Runs as a normal unit test so it fits into the
existing `python -m pytest backend/tests` workflow; in CI wire it as a
required check.

Scans every file under the backend/ + frontend/ + repo root looking for
patterns that match known API key formats. False positives are minimized by
restricting to known prefixes + minimum lengths.

Run: `cd backend && python tests/test_secrets.py`
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

# Files we never scan — these legitimately contain test fixtures, docs, etc.
_EXCLUDE_DIRS = {
    "node_modules", ".next", "__pycache__", ".git", ".claude",
    "data", "outputs", "_smoke", "tests/fixtures",
    "visual-testing",  # E2E tests often contain fixture creds
}
_EXCLUDE_FILE_NAMES = {
    ".gitignore", ".env.example", "test_secrets.py",
}
# Scan extensions only — binary files would produce too many false positives
_SCAN_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".json", ".yml", ".yaml", ".env", ".sh", ".md",
}

# Patterns matched (all require known prefix + minimum entropy)
_PATTERNS = [
    # Google AI Studio (Gemini)
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "Google API key (Gemini)"),
    # OpenAI
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "OpenAI API key"),
    # GitHub PAT
    (re.compile(r"ghp_[a-zA-Z0-9]{36,}"), "GitHub Personal Access Token"),
    # GitHub fine-grained PAT
    (re.compile(r"github_pat_[a-zA-Z0-9_]{82,}"), "GitHub fine-grained PAT"),
    # Anthropic
    (re.compile(r"sk-ant-[a-zA-Z0-9-]{32,}"), "Anthropic API key"),
    # AWS Access Key ID
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
    # Stripe
    (re.compile(r"sk_live_[a-zA-Z0-9]{24,}"), "Stripe live secret key"),
    (re.compile(r"pk_live_[a-zA-Z0-9]{24,}"), "Stripe live publishable key"),
    # Slack tokens
    (re.compile(r"xox[baprs]-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24,}"), "Slack token"),
    # NOTE: we deliberately do NOT scan for PEM private keys here — too many
    # legitimate test fixtures and example keys exist. License signing keys
    # live in `backend/licensing/keys/private.pem` which is gitignored.
]


def _build_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


class SecretScanTests(unittest.TestCase):
    """Scan the build context for accidentally-committed secrets."""

    def test_no_secrets_in_build_context(self):
        root = _build_root()
        offenders: list[str] = []

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _EXCLUDE_DIRS for part in path.parts):
                continue
            if path.name in _EXCLUDE_FILE_NAMES:
                continue
            if path.suffix.lower() not in _SCAN_EXTENSIONS:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern, label in _PATTERNS:
                for match in pattern.finditer(content):
                    # Skip false positives in obvious contexts
                    snippet_start = max(0, match.start() - 20)
                    snippet = content[snippet_start:match.end() + 5]
                    if any(fp in snippet.lower() for fp in (
                        "example", "placeholder", "your-key-here",
                        "<your-", "${", "xxxxxx", "fake", "test_key",
                    )):
                        continue
                    offenders.append(
                        f"{path.relative_to(root)}: {label} -> {match.group(0)[:12]}..."
                    )

        if offenders:
            self.fail(
                "Found suspected secrets in build context. "
                "Move them to runtime mounts (env-file / Docker secret / vault).\n\n"
                + "\n".join(offenders)
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)