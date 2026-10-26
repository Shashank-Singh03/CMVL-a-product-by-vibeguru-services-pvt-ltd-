"""Security & risk lens: deterministic rules over changed lines (and, with --audit, whole files).

Flags are hints for a human reviewer, not verdicts. Each one says what was found, where,
and why it matters in plain words. Secrets are always masked in every output.
"""
from __future__ import annotations

import re

from .diffmap import CHANGED
from .redact import CONFIG_LITERAL_PATTERN, SENSITIVE_KEY, SecretRedactor, is_placeholder, secret_value

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# (rule id, severity, domains or None for any, pattern, message, on: "added" | "removed", is_secret)
RULES = [
    ("secret.private_key", "high", None, r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----",
     "A private key is committed in the repository.", "added", True),
    ("secret.aws_key", "high", None, r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "An AWS access key is hard-coded.", "added", True),
    ("secret.github_token", "high", None, r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", "A GitHub token is hard-coded.", "added", True),
    ("secret.slack_token", "high", None, r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "A Slack token is hard-coded.", "added", True),
    ("secret.ai_key", "high", None, r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}", "An AI provider API key is hard-coded.", "added", True),
    ("secret.google_key", "high", None, r"\bAIza[0-9A-Za-z_-]{35}\b", "A Google API key is hard-coded.", "added", True),
    ("secret.stripe_key", "high", None, r"\b(?:sk|rk)_live_[0-9a-zA-Z]{20,}", "A live Stripe key is hard-coded.", "added", True),
    ("secret.hardcoded", "medium", None,
     r"(?i)\b" + SENSITIVE_KEY + r"""\b["']?\s*[:=]\s*["'][^"'\s]{6,}["']""",
     "A password or token looks hard-coded; load it from a secret store or environment.", "added", True),
    ("secret.config_literal", "medium", {"config", "infra", "ci"}, CONFIG_LITERAL_PATTERN,
     "A password or token is stored as a literal configuration value.", "added", True),
    ("secret.jwt", "medium", None, r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
     "A JSON Web Token is hard-coded.", "added", True),
    ("code.tls_disabled", "high", None,
     r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0|"
     r"CURLOPT_SSL_VERIFYPEER\s*,\s*(?:false|0)|ServerCertificateValidationCallback\s*=.*true|--insecure\b|-k\s+https://",
     "TLS certificate checks are turned off, so traffic can be intercepted.", "added", False),
    ("code.jwt_unverified", "high", None,
     r"""verify_signature["']?\s*:\s*False|algorithms\s*=\s*\[\s*["']none["']|jwt\.decode\([^)]*verify\s*=\s*False|ignoreExpiration\s*:\s*true""",
     "JWT verification is weakened or disabled.", "added", False),
    ("code.sql_injection", "medium", {"code"},
     r"""(?i)(?:execute|executemany|query|raw|exec)\s*\(\s*(?:f["']|["'][^"']*["']\s*(?:\+|%|\.format)|`[^`]*\$\{)[^)]*\b(?:select|insert|update|delete)\b|"""
     r"""["']\s*(?:select|insert into|update|delete from)\b[^"']*["']\s*\+\s*\w""",
     "SQL is built by joining strings, which allows SQL injection; use query parameters.", "added", False),
    ("code.shell_injection", "medium", {"code"},
     r"subprocess\.\w+\([^)]*shell\s*=\s*True|\bos\.system\(|\bos\.popen\(|child_process\.exec\(|\bexecSync\(`|"
     r"Runtime\.getRuntime\(\)\.exec\(|\bshell_exec\(|\bpassthru\(|exec\.Command\(\s*\"(?:sh|bash)\"\s*,\s*\"-c\"",
     "Runs a shell command; make sure no user input can reach it.", "added", False),
    ("code.eval", "medium", {"code"}, r"(?<![\w.])eval\s*\(|new Function\s*\(|\bexec\s*\(\s*(?:compile|f[\"']|request|input)",
     "Runs code built at runtime (eval/exec), which is dangerous with untrusted input.", "added", False),
    ("code.unsafe_deserialize", "medium", {"code", "ai"},
     r"\bpickle\.loads?\(|\byaml\.load\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)|\bunserialize\(\$_|ObjectInputStream\(|\bMarshal\.load\(|torch\.load\((?![^)]*weights_only\s*=\s*True)",
     "Deserializes data in an unsafe way; untrusted input could run code.", "added", False),
    ("code.debug_enabled", "medium", {"code", "config"},
     r"^\s*DEBUG\s*=\s*True\b|\.run\([^)]*debug\s*=\s*True|app\.debug\s*=\s*True|\"debug\"\s*:\s*true",
     "Debug mode is on; it leaks internals if shipped to production.", "added", False),
    ("code.cors_any", "medium", None,
     r"""Access-Control-Allow-Origin["']?\s*[:,]\s*["']\*|allow_origins\s*=\s*\[\s*["']\*|AllowAnyOrigin\(\)|origin\s*:\s*["']\*["']|\bcors\(\s*\)""",
     "CORS lets any website call this API with the user's browser.", "added", False),
    ("code.weak_hash", "low", {"code"}, r"\bhashlib\.(?:md5|sha1)\(|MessageDigest\.getInstance\(\"(?:MD5|SHA-?1)\"|\bmd5\(|createHash\(['\"](?:md5|sha1)['\"]",
     "Uses MD5/SHA-1, which must not protect passwords or signatures.", "added", False),
    ("code.xss_html", "low", {"code"}, r"dangerouslySetInnerHTML|\bv-html\b|\.innerHTML\s*=\s*`[^`]*\$\{|\{\{\{|\|\s*safe\b|mark_safe\(",
     "Inserts raw HTML into a page; if it contains user input this is an XSS hole.", "added", False),
    ("code.world_writable", "medium", None, r"chmod\s+(?:-R\s+)?0?777\b|os\.chmod\([^)]*0o?777",
     "Makes files writable by everyone (777).", "added", False),
    ("code.curl_pipe_shell", "high", None, r"(?:curl|wget)[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b",
     "Downloads a script and pipes it straight into a shell.", "added", False),
    ("auth.check_removed", "high", {"code"},
     r"@login_required|@PreAuthorize|@Secured|@RolesAllowed|\[Authorize|@UseGuards|permission_classes|requireAuth|isAuthenticated\(|"
     r"authenticate\(|@auth\.|Depends\(\s*(?:get_current_user|verify|auth)|\bauthorize\(",
     "An authentication or authorization check was removed.", "removed", False),
    ("iam.admin", "high", {"infra", "config", "code"},
     r"""["']?Action["']?\s*[:=]\s*\[?\s*["']\*["']|actions\s*=\s*\[\s*["']\*["']\s*\]|AdministratorAccess""",
     "Grants every permission (Action: \"*\").", "added", False),
    ("iam.service_wildcard", "medium", {"infra", "config", "code"}, r"""["'][a-z0-9-]{2,30}:\*["']""",
     "Grants every action of a cloud service (for example \"s3:*\").", "added", False),
    ("iam.any_resource", "low", {"infra", "config"}, r"""["']?Resource["']?\s*[:=]\s*\[?\s*["']\*["']|resources\s*=\s*\[\s*["']\*["']""",
     "Applies a permission to every resource (Resource: \"*\").", "added", False),
    # only source/ingress keys: routes, NACL `cidr_block`s and egress rules to 0.0.0.0/0 are normal
    ("net.open_to_internet", "high", {"infra", "config", "ci"},
     r"""(?i)\b(?:cidr_blocks|ipv6_cidr_blocks|cidr_ipv[46]|source_ranges|source_address_prefix(?:es)?|sourceAddressPrefix(?:es)?|"""
     r"""CidrIp(?:v6)?|cidr|ip_?ranges?|authorized_networks|allowed_ips?|ipRules)\b["']?\s*[:=]\s*[^\n]*?(?:0\.0\.0\.0/0|::/0)""",
     "Opens network access to the whole internet (0.0.0.0/0).", "added", False),
    ("storage.public", "high", {"infra", "config"},
     r"""acl\s*=\s*["']public-read(?:-write)?["']|PublicRead|block_public_(?:acls|policy)\s*=\s*false|restrict_public_buckets\s*=\s*false|"""
     r"""allow_blob_public_access\s*=\s*true|AllowBlobPublicAccess:\s*true|publicAccess:\s*['"]?(?:Blob|Container)""",
     "Makes storage publicly readable on the internet.", "added", False),
    ("storage.unencrypted", "medium", {"infra", "config"},
     r"\b(?:storage_)?encrypted\s*=\s*false|encryption\s*=\s*false|enable_encryption\s*=\s*false|StorageEncrypted:\s*false",
     "Turns off encryption at rest.", "added", False),
    ("db.public", "high", {"infra", "config"}, r"publicly_accessible\s*=\s*true|PubliclyAccessible:\s*true",
     "Makes the database reachable from the internet.", "added", False),
    ("protect.deletion_off", "low", {"infra"}, r"deletion_protection\s*=\s*false|skip_final_snapshot\s*=\s*true|force_destroy\s*=\s*true",
     "Removes protection against accidental data deletion.", "added", False),
    ("k8s.privileged", "high", {"infra", "config"}, r"\bprivileged:\s*true",
     "Runs a container privileged, with full access to the host.", "added", False),
    ("k8s.host_access", "high", {"infra"}, r"host(?:Network|PID|IPC):\s*true|\bhostPath:",
     "Gives a pod the host's network, processes or file system.", "added", False),
    ("k8s.root", "medium", {"infra"}, r"runAsUser:\s*0\b|runAsNonRoot:\s*false|allowPrivilegeEscalation:\s*true",
     "Lets a container run as root or escalate privileges.", "added", False),
    ("k8s.capabilities", "high", {"infra"}, r"add:\s*\[?.*\b(?:SYS_ADMIN|NET_ADMIN|ALL)\b",
     "Adds powerful Linux capabilities to a container.", "added", False),
    ("docker.root_user", "medium", {"infra"}, r"(?i)^\s*USER\s+(?:root|0)\b", "The image runs as root.", "added", False),
    ("docker.unpinned_base", "low", {"infra"}, r"(?i)^\s*FROM\s+(?!scratch\b)[^\s:@]+(?::latest)?(?:\s+AS\s+\S+)?\s*$",
     "The base image is not pinned to a version.", "added", False),
    ("docker.add_url", "medium", {"infra"}, r"(?i)^\s*ADD\s+https?://", "ADD downloads from a URL without a checksum.", "added", False),
    ("docker.secret_in_image", "medium", {"infra"}, r"(?i)^\s*(?:ENV|ARG)\s+\w*(?:PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY)\w*",
     "A secret is baked into the image at build time.", "added", False),
    ("compose.host_network", "medium", {"infra"}, r"network_mode:\s*['\"]?host", "A container shares the host network.", "added", False),
    ("ci.pull_request_target", "medium", {"ci"}, r"\bpull_request_target\b",
     "pull_request_target runs fork code with repository secrets unless carefully restricted.", "added", False),
    ("ci.write_all", "medium", {"ci"}, r"permissions:\s*write-all", "The workflow token can write to everything.", "added", False),
    ("ci.unpinned_action", "medium", {"ci"}, r"uses:\s*[\w.-]+/[\w./-]+@(?:main|master|latest|HEAD)\b",
     "A third-party action is pinned to a moving branch, not a version or commit.", "added", False),
    ("ci.secret_echo", "high", {"ci"}, r"echo\s+[^\n]*\$\{\{\s*secrets\.", "A secret is printed into the CI log.", "added", False),
    ("data.destructive", "high", {"data", "code"},
     r"""(?i)\bDROP\s+(?:TABLE|COLUMN|DATABASE|SCHEMA)\b|\bTRUNCATE\s+TABLE\b|^\s*TRUNCATE\s+[\w."`]+\s*;""",
     "Permanently deletes data (DROP/TRUNCATE).", "added", False),
    ("data.delete_all", "medium", {"data"}, r"(?i)^\s*DELETE\s+FROM\s+[\w.\"`]+\s*;", "Deletes every row (no WHERE clause).", "added", False),
    ("deps.unpinned", "low", {"deps"}, r"""["']\s*:\s*["'](?:\*|latest)["']|^\s*[A-Za-z0-9_.\-]+\s*$""",
     "A dependency version is not pinned.", "added", False),
]
_COMPILED = [(rid, sev, doms, re.compile(pat, re.M), msg, on, secret) for rid, sev, doms, pat, msg, on, secret in RULES]
_MESSAGE = re.compile(r"(?i)\b(?:echo|printf|print|warning|info|error|log|puts|Write-Host)\b")
_DROPPED = re.compile(r"""(?i)\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`"\[]?([\w.]+)""")
_DIRECTION = re.compile(r"(?i)\b(egress|outbound|destination|ingress|inbound)")


def _is_comment(lang: str, text: str) -> bool:
    s = text.lstrip()
    if s.startswith("#"):
        return not s.startswith("#!") and lang not in ("c", "cpp", "csharp", "markdown")
    if s.startswith(("//", "/*", "<!--")) or (s.startswith("*") and lang != "markdown"):
        return True
    return (s.startswith("--") and lang in ("sql", "lua")) or (s.startswith(";") and lang == "ini")


def _outbound(text: str, above: list) -> bool:
    """True when the nearest ingress/egress keyword (this line first, then upwards) points outwards."""
    for line in [text] + list(reversed(above)):
        m = _DIRECTION.search(line)
        if m:
            return m.group(1).lower() in ("egress", "outbound", "destination")
    return False


def _excused(rid: str, fi, text: str, m, above: list) -> bool:
    """Rule-specific checks that turn common, harmless matches into non-findings."""
    if rid == "net.open_to_internet":
        return _outbound(text, above)
    if rid == "data.destructive":       # `DROP TABLE x` right before re-creating x: a reset script
        d = _DROPPED.search(text)
        created = {s.name.lower() for s in fi.symbols if s.sub == "table"}
        return bool(d) and (d.group(1).lower() in created or d.group(1).lower().rsplit(".", 1)[-1] in created)
    if rid == "code.curl_pipe_shell":   # a printed hint ("run: curl ... | sh"), not a command
        return bool(_MESSAGE.search(text[:m.start()]))
    return False


def _innermost(symbols: list, line: int):
    best = None
    for s in symbols:
        if s.line <= line <= s.end and (best is None or (s.end - s.line) < (best.end - best.line)):
            best = s
    return best


class RiskScanner:
    def __init__(self):
        self.flags: list = []

    def _flag(self, rule, sev, msg, file, line, text, holder, fi):
        redactor = SecretRedactor()
        redactor.add_text(text, env=fi is not None and fi.lang == "env",
                          config=fi is not None and fi.domain in ("config", "infra", "ci"))
        snippet = " ".join(redactor.text(text).strip().split())[:140]
        flag = {"id": f"r{len(self.flags) + 1}", "rule": rule, "sev": sev, "msg": msg, "file": file, "line": line,
                "text": snippet}
        if any(f["rule"] == rule and f["file"] == file and f["line"] == line for f in self.flags):
            return
        self.flags.append(flag)
        if holder is not None:
            holder.flags.append(flag)
        elif fi is not None:
            fi.flags.append(flag)

    def scan_line(self, fi, line_no: int, text: str, on: str, holder, min_sev: str = "low", above=()) -> None:
        """Check one line. `above` holds a few preceding lines of the same file (nearest last) for context."""
        comment = None
        for rid, sev, doms, pat, msg, rule_on, secret in _COMPILED:
            if rule_on != on or (doms and fi.domain not in doms) or SEVERITY_ORDER[sev] > SEVERITY_ORDER[min_sev]:
                continue
            if not secret:              # a key in a comment still leaks; a commented-out setting does nothing
                if len(text) > 600:
                    continue
                if fi.domain == "docs":
                    continue
                if comment is None:
                    comment = _is_comment(fi.lang, text)
                if comment:
                    continue
            if rid == "deps.unpinned" and (fi.lang not in ("deps-pip", "deps-npm") or text.strip().startswith(("-", "#"))):
                continue
            m = pat.search(text)
            if not m or (secret and is_placeholder(secret_value(m))) or _excused(rid, fi, text, m, above):
                continue
            self._flag(rid, sev, msg, fi.path, line_no, text, holder, fi)

    # ------------------------------------------------------------ diff mode
    def scan_changes(self, files: dict, changes, base_infos: dict, texts: dict | None = None) -> None:
        for path, fd in changes.files.items():
            fi = files.get(path)
            if fi is None:
                continue
            base = base_infos.get(path)
            current = [s for s in fi.symbols if s.status != "deleted"]
            lines = (texts or {}).get(path, "").splitlines()
            for h in fd.hunks:
                for ln, txt in h.plus:
                    self.scan_line(fi, ln, txt, "added", _innermost(current, ln), above=lines[max(0, ln - 9): ln - 1])
                for ln, txt in h.minus:
                    holder = None
                    if base is not None:
                        old = _innermost(base.symbols, ln)
                        if old is not None:
                            holder = next((s for s in fi.symbols if s.qual == old.qual), None)
                    elif fi.status == "deleted":
                        holder = _innermost(fi.symbols, ln)
                    self.scan_line(fi, ln, txt, "removed", holder)
        self.structural(files, base_infos)

    def structural(self, files: dict, base_infos: dict) -> None:
        for path, fi in files.items():
            for s in fi.symbols:
                if s.status == "deleted" and s.sub in ("endpoint", "rpc", "query", "mutation", "subscription"):
                    self._flag("api.removed_endpoint", "high",
                               f"API operation {s.name} was removed; existing clients calling it will break.",
                               path, s.line, s.signature or s.name, s, fi)
                elif s.status == "deleted" and s.sub in ("field", "column") and fi.domain in ("api", "data"):
                    parent = s.qual.rsplit(".", 1)[0]
                    sev = "high" if s.sub == "column" else "medium"
                    self._flag("api.removed_field" if s.sub == "field" else "data.removed_column", sev,
                               f"'{s.name}' was removed from {parent}; anything reading it will break.", path, s.line,
                               s.signature or s.name, s, fi)
                elif s.status == "added" and fi.domain == "deps" and ("package" in s.sub or s.sub in ("module", "gem")):
                    self._flag("deps.new", "low", f"New dependency {s.name}: check its license, maintenance and size.",
                               path, s.line, s.signature, s, fi)
            base = base_infos.get(path)
            if base is None or fi.domain not in ("api", "data"):
                continue
            old = {s.qual: s for s in base.symbols}
            for s in fi.symbols:
                before = old.get(s.qual)
                if before is None or not before.attrs or s.status == "deleted":
                    continue
                for field, typ in before.attrs.items():
                    if field not in s.attrs:
                        self._flag("api.removed_field", "medium",
                                   f"Field '{field}' was removed from {s.name}; clients that read it will break.",
                                   path, s.line, s.signature or s.name, s, fi)
                    elif typ != s.attrs[field] and typ[0] and s.attrs[field][0]:
                        self._flag("api.type_changed", "medium",
                                   f"Field '{field}' of {s.name} changed type ({typ[0]} -> {s.attrs[field][0]}).",
                                   path, s.line, s.signature or s.name, s, fi)

    # ------------------------------------------------------------ audit mode
    def scan_files(self, files: dict, texts: dict, limit: int = 300) -> None:
        """Whole-file scan (medium and high only). One combined regex finds candidate lines; only those get the
        per-rule checks, which keeps a 300k-line repository to a few seconds."""
        for path, fi in files.items():
            text = texts.get(path)
            pre = _prefilter(fi.domain)
            if not text or pre is None:
                continue
            candidates, pos, line_no = set(), 0, 1
            for m in pre.finditer(text):
                line_no += text.count("\n", pos, m.start())
                pos = m.start()
                candidates.update(range(line_no, line_no + text.count("\n", m.start(), m.end()) + 1))
            if not candidates:
                continue
            lines = text.split("\n")
            for no in sorted(candidates):
                if len(self.flags) >= limit:
                    return
                if no <= len(lines):
                    self.scan_line(fi, no, lines[no - 1], "added", _innermost(fi.symbols, no), min_sev="medium",
                                   above=lines[max(0, no - 9): no - 1])


_PREFILTERS: dict = {}


def _prefilter(domain: str):
    """All medium/high rules that can fire in `domain`, as one regex (None if there are none)."""
    if domain not in _PREFILTERS:
        parts = []
        for _rid, sev, doms, pat, _msg, on, secret in _COMPILED:
            if on != "added" or sev == "low" or (doms and domain not in doms) or (domain == "docs" and not secret):
                continue
            src = pat.pattern
            flags = re.match(r"^\(\?([ims]+)\)", src)
            parts.append("(?" + flags.group(1) + ":" + src[flags.end():] + ")" if flags else "(?:" + src + ")")
        _PREFILTERS[domain] = re.compile("|".join(parts), re.M) if parts else None
    return _PREFILTERS[domain]


def summarize(flags: list) -> dict:
    out = {"high": 0, "medium": 0, "low": 0}
    for f in flags:
        out[f["sev"]] += 1
    return out


def changed_status(sym) -> bool:
    return sym.status in CHANGED
