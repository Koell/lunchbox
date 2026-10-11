import unittest

from lunchbox.guards import Finding, scan, scan_portability, scan_secrets

WIN_HOME = "C:\\Users\\testuser"
POSIX_HOME = "/home/testuser"


def win(text: str) -> list[Finding]:
    return scan_portability("f.txt", text.encode(), WIN_HOME, windows=True)


def posix(text: str) -> list[Finding]:
    return scan_portability("f.txt", text.encode(), POSIX_HOME, windows=False)


def secrets(text: str) -> list[Finding]:
    return scan_secrets("f.txt", text.encode())


def lines(findings: list[Finding]) -> list[int]:
    return [f.line for f in findings]


class PortabilityTest(unittest.TestCase):
    def test_windows_spellings_with_line_numbers(self) -> None:
        cases = {
            "native backslashes": "C:\\Users\\testuser\\.claude\\x.sh",
            "json escaped": '"command": "C:\\\\Users\\\\testuser\\\\.claude\\\\x.sh"',
            "forward slashes": "C:/Users/testuser/.claude/x.sh",
            "git bash lowercase drive": 'export PATH="/c/Users/testuser/bin:$PATH"',
            "git bash uppercase drive": "/C/Users/testuser/bin",
            "case variant": "c:\\users\\TESTUSER\\x",
        }
        for name, line in cases.items():
            with self.subTest(name):
                findings = win(f"first\nsecond\n{line}\nlast\n")
                self.assertEqual(lines(findings), [3])
                self.assertEqual(findings[0].kind, "portability")

    def test_posix_home_with_line_number(self) -> None:
        self.assertEqual(lines(posix("a\nb\nsource /home/testuser/.bashrc\n")), [3])

    def test_posix_is_case_sensitive_when_not_windows(self) -> None:
        self.assertEqual(posix("/HOME/TESTUSER/x\n"), [])

    def test_crlf_line_numbers(self) -> None:
        findings = scan_portability("f", b"a\r\nb\r\n/home/testuser/x\r\n", POSIX_HOME, windows=False)
        self.assertEqual(lines(findings), [3])
        self.assertNotIn("\r", findings[0].excerpt)

    def test_path_boundary(self) -> None:
        self.assertEqual(posix("/home/testuser2/x\n"), [])
        self.assertEqual(win("C:\\Users\\testuser2\\x\n"), [])
        text = "/home/testuser/x\n\"/home/testuser\"\n/home/testuser\n"
        self.assertEqual(lines(posix(text)), [1, 2, 3])

    def test_line_reported_once_for_several_spellings(self) -> None:
        findings = win("C:\\Users\\testuser and C:/Users/testuser and /c/Users/testuser\n")
        self.assertEqual(lines(findings), [1])

    def test_excerpt_shows_match(self) -> None:
        line = "x" * 200 + " /home/testuser/secret-place " + "y" * 200
        (finding,) = posix(line)
        self.assertIn("/home/testuser", finding.excerpt)
        self.assertLess(len(finding.excerpt), 120)

    def test_portable_references_yield_nothing(self) -> None:
        text = "\n".join([
            "~/.claude/statusline.sh",
            "$HOME/.claude/settings.json",
            "${HOME}/.claude",
            "%USERPROFILE%\\.claude",
            "${CLAUDE_CONFIG_DIR}/skills",
            "$env:USERPROFILE\\.claude",
        ])
        self.assertEqual(win(text), [])
        self.assertEqual(posix(text), [])

    def test_degenerate_home_yields_nothing(self) -> None:
        for home in ("", "/", "C:\\", "C:"):
            with self.subTest(home=home):
                self.assertEqual(scan_portability("f", b"/x\nC:\\x\n", home, windows=True), [])


class SecretTest(unittest.TestCase):
    def assert_secret(self, line: str, masked: str) -> None:
        findings = secrets(f"# config\n\n{line}\n")
        self.assertEqual([(f.kind, f.line) for f in findings], [("secret", 3)])
        self.assertIn(masked, findings[0].excerpt)
        self.assertTrue(findings[0].excerpt.endswith("…"))

    def test_token_patterns(self) -> None:
        cases = {
            "anthropic": ("key=" + "sk-ant-" + "api03-" + "a1B2" * 10, "sk-ant…"),
            "github classic": ("ghp_" + "Ab1" * 12, "ghp_Ab…"),
            "github fine-grained": ("github_pat_" + "11ABCDEF" * 3 + "_" + "x9" * 30, "github…"),
            "aws key id": ("aws_access_key_id = " + "AKIA" + "ABCD2345EFGH6789", "AKIAAB…"),
        }
        for name, (line, masked) in cases.items():
            with self.subTest(name):
                self.assert_secret(line, masked)

    def test_secret_value_never_printed(self) -> None:
        value = "sk-ant-" + "Z" * 40
        (finding,) = secrets(value)
        self.assertNotIn("Z" * 10, finding.excerpt)

    def test_pem_private_key(self) -> None:
        for header in ("", "RSA ", "OPENSSH ", "EC ", "ENCRYPTED "):
            with self.subTest(header=header):
                begin = "-----BEGIN " + header + "PRIVATE KEY-----"
                findings = secrets(f"x\n{begin}\nMIIEvQIBADANBg\n-----END {header}PRIVATE KEY-----\n")
                self.assertEqual(lines(findings), [2])

    def test_json_and_toml_pairs(self) -> None:
        value = "q7" * 12
        cases = {
            "json api_key": f'  "api_key": "{value}",',
            "json apiKey": f'{{"apiKey":"{value}"}}',
            "json token": f'"githubToken": "{value}"',
            "json password": f'"password": "{value}"',
            "toml api-key": f'api-key = "{value}"',
            "toml secret single quotes": f"client_secret = '{value}'",
            "toml passwd": f'db.passwd="{value}"',
            "toml quoted key": f'"x-api-key" = "{value}"',
        }
        for name, line in cases.items():
            with self.subTest(name):
                self.assert_secret(line, value[:6] + "…")

    def test_one_finding_per_line(self) -> None:
        line = '"api_key": "' + "sk-ant-" + "b" * 40 + '", "token": "' + "c" * 30 + '"'
        self.assertEqual(lines(secrets(line)), [1])

    def test_boundaries_prevent_false_positives(self) -> None:
        text = "\n".join([
            "myghp_" + "a" * 36,
            "ghp_" + "a" * 37,
            "ghp_" + "a" * 20,
            "XAKIA" + "B" * 16,
            "AKIA" + "B" * 17,
            "keys start with sk-ant- and are long",
            "sk-ant-short",
        ])
        self.assertEqual(secrets(text), [])

    def test_benign_content(self) -> None:
        text = "\n".join([
            "# The token is read from the keychain; never store a token or secret here.",
            "Use an API key or password manager.",
            "format_tokens() {",
            "total_tokens=$(format_tokens $size)",
            "total_tokens=$((input_tokens + cache_read))",
            'if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then',
            '"token": "${GITHUB_TOKEN}",',
            '"token": "$env:GITHUB_PERSONAL_ACCESS_TOKEN",',
            '"api_key": "%ANTHROPIC_API_KEY_VALUE%",',
            '"api_key": "",',
            '"token": "changeme",',
            'password = "hunter2"',
            '"max_tokens": 4096,',
            '"apiKeyHelper": "~/.claude/scripts/get-api-key.sh",',
            '"token_url": "https://auth.example.com/oauth/token",',
            '"secret_hint": "keep this value out of version control",',
            '"api_key": "<your-anthropic-api-key>",',
            "-----BEGIN PUBLIC KEY-----",
            "-----BEGIN CERTIFICATE-----",
        ])
        self.assertEqual(secrets(text), [])

    def test_status_line_token_handling_passes(self) -> None:
        fixture = """\
# Format token counts (e.g., 50k / 200k)
format_tokens() {
    local num=$1
}
input_tokens=$(echo "$input" | jq -r '.context_window.current_usage.input_tokens // 0')
cache_create=$(echo "$input" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0')
used_tokens=$(format_tokens $current)
total_tokens=$(format_tokens $size)
# ===== Cross-platform OAuth token resolution =====
get_oauth_token() {
    local token=""
    if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then
        echo "$CLAUDE_CODE_OAUTH_TOKEN"
        return 0
    fi
    local creds_file="${HOME}/.claude/.credentials.json"
    token=$(jq -r '.claudeAiOauth.accessToken // empty' "$creds_file" 2>/dev/null)
    if [ -n "$token" ] && [ "$token" != "null" ]; then
        echo "$token"
    fi
    blob=$(timeout 2 secret-tool lookup service "Claude Code-credentials" 2>/dev/null)
}
token=$(get_oauth_token)
response=$(curl -s --max-time 5 \\
    -H "Accept: application/json" \\
    -H "Authorization: Bearer $token" \\
    -H "anthropic-beta: oauth-2025-04-20" \\
    "https://api.anthropic.com/api/oauth/usage" 2>/dev/null)
"""
        self.assertEqual(secrets(fixture), [])
        self.assertEqual(scan_portability("statusline.sh", fixture.encode(), WIN_HOME, windows=True), [])


class ScanTest(unittest.TestCase):
    def test_combines_scanners_sorted_by_line(self) -> None:
        text = '"token": "' + "k" * 20 + '"\nok\n/home/testuser/x\n' + "ghp_" + "Q" * 36 + "\n"
        findings = scan("settings.json", text.encode(), POSIX_HOME, windows=False)
        self.assertEqual([(f.line, f.kind) for f in findings], [(1, "secret"), (3, "portability"), (4, "secret")])

    def test_binary_content_does_not_crash(self) -> None:
        data = bytes(range(256)) * 4 + b"\n/home/testuser/x\n"
        findings = scan("img.png", data, POSIX_HOME, windows=False)
        self.assertEqual([f.kind for f in findings], ["portability"])

    def test_empty_content(self) -> None:
        self.assertEqual(scan("empty", b"", POSIX_HOME, windows=False), [])


if __name__ == "__main__":
    unittest.main()
