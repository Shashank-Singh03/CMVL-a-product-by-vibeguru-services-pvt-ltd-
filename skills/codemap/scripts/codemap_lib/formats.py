"""What is this file? Maps a path (and, for YAML/JSON, a peek at the content) to a format and a domain.

Domains group the map's top level when a change spans more than one of them:
code, api (contracts), data (schemas), infra, ci, deps, ai, config, docs, other.
"""
from __future__ import annotations

import os
import re

DOMAIN_ORDER = ["code", "api", "data", "infra", "ci", "deps", "ai", "config", "docs", "other"]
DOMAIN_NAMES = {
    "code": "Code", "api": "API contracts", "data": "Data & schema", "infra": "Infrastructure",
    "ci": "CI/CD & build", "deps": "Dependencies", "ai": "AI & notebooks", "config": "Configuration",
    "docs": "Docs", "other": "Other files",
}

CODE_EXT = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".vue": "vue", ".svelte": "svelte", ".astro": "astro",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".sc": "scala", ".groovy": "groovy",
    ".cs": "csharp", ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".php": "php", ".swift": "swift", ".dart": "dart", ".rb": "ruby", ".rake": "ruby", ".lua": "lua",
    ".ex": "elixir", ".exs": "elixir", ".r": "r", ".sol": "solidity",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ksh": "shell",
    ".ps1": "powershell", ".psm1": "powershell",
}
OTHER_EXT = {
    ".tf": ("terraform", "infra"), ".tfvars": ("terraform", "infra"), ".hcl": ("terraform", "infra"),
    ".bicep": ("bicep", "infra"),
    ".graphql": ("graphql", "api"), ".gql": ("graphql", "api"), ".graphqls": ("graphql", "api"),
    ".proto": ("proto", "api"),
    ".sql": ("sql", "data"), ".prisma": ("prisma", "data"),
    ".ipynb": ("notebook", "ai"),
    ".md": ("markdown", "docs"), ".mdx": ("markdown", "docs"), ".rst": ("markdown", "docs"),
    ".toml": ("toml", "config"), ".ini": ("ini", "config"), ".cfg": ("ini", "config"), ".conf": ("ini", "config"),
    ".properties": ("ini", "config"),
    ".yaml": ("yaml", "config"), ".yml": ("yaml", "config"), ".json": ("json", "config"),
    ".prompt": ("text", "ai"), ".prompty": ("text", "ai"), ".j2": ("text", "ai"),
}
DEPS_FILES = {
    "package.json": "npm", "requirements.txt": "pip", "constraints.txt": "pip", "pipfile": "pipfile",
    "pyproject.toml": "pyproject", "setup.cfg": "pip-setupcfg", "go.mod": "gomod", "cargo.toml": "cargo",
    "pom.xml": "maven", "build.gradle": "gradle", "build.gradle.kts": "gradle", "gemfile": "gem",
    "composer.json": "composer", "packages.config": "nuget",
}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "go.sum",
             "composer.lock", "gemfile.lock", "pipfile.lock", "uv.lock", "bun.lockb", "packages.lock.json"}
CI_NAMES = {".gitlab-ci.yml": "gitlab-ci", "jenkinsfile": "jenkins", "makefile": "make", "gnumakefile": "make",
            "bitbucket-pipelines.yml": "bitbucket", "cloudbuild.yaml": "cloudbuild", "buildspec.yml": "buildspec"}

_K8S = re.compile(r"(?m)^apiVersion:\s*\S+[\s\S]{0,2000}?^kind:\s*\w+|^kind:\s*\w+[\s\S]{0,2000}?^apiVersion:")
_OPENAPI = re.compile(r'(?m)^\s*["\']?(openapi|swagger)["\']?\s*:\s*["\']?\d')
_CFN = re.compile(r"AWSTemplateFormatVersion|Transform:\s*['\"]?AWS::Serverless|\"Type\"\s*:\s*\"AWS::|Type:\s*['\"]?AWS::")
_COMPOSE = re.compile(r"(?m)^services:\s*$")
_GHA = re.compile(r"(?m)^jobs:\s*$")


def classify(path: str, head: str = "") -> tuple:
    """(format, domain) for a repo-relative POSIX path. `head` is the first few KB of text (for YAML/JSON sniffing).

    Returns (None, None) for files that should never be mapped (lockfiles, binaries, minified bundles).
    """
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1]
    if name in LOCKFILES or ".min." in name or name.endswith((".map", ".d.ts", ".bundle.js")):
        return None, None
    if name in DEPS_FILES or re.match(r"requirements[-_.\w]*\.(txt|in)$", name) or name.endswith(".csproj"):
        return "deps-" + DEPS_FILES.get(name, "pip" if name.startswith("requirements") else "nuget"), "deps"
    if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile") or name == "containerfile":
        return "dockerfile", "infra"
    if name in CI_NAMES:
        return CI_NAMES[name], "ci"
    if name.endswith((".mk",)):
        return "make", "ci"
    if "/.github/workflows/" in "/" + low and ext in (".yml", ".yaml"):
        return "github-actions", "ci"
    if name in ("action.yml", "action.yaml"):
        return "github-actions", "ci"
    if name.startswith("azure-pipelines") and ext in (".yml", ".yaml"):
        return "azure-pipelines", "ci"
    if low.endswith(".circleci/config.yml"):
        return "circleci", "ci"
    if re.match(r"(docker-)?compose([.-][\w.-]+)?\.ya?ml$", name):
        return "compose", "infra"
    if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
        return "env", "config"
    if ext in CODE_EXT:
        if name.endswith(".kts") and name.startswith(("build.", "settings.")):
            return "deps-gradle", "deps"
        return CODE_EXT[ext], "code"
    if ext in OTHER_EXT:
        fmt, domain = OTHER_EXT[ext]
        if fmt in ("yaml", "json"):
            if "/templates/" in "/" + low and "{{" in head:
                return "kubernetes", "infra"           # Helm chart template
            if _OPENAPI.search(head):
                return "openapi", "api"
            if fmt == "yaml" and _K8S.search(head):
                return "kubernetes", "infra"
            if _CFN.search(head):
                return "cloudformation", "infra"
            if fmt == "yaml" and _COMPOSE.search(head) and ("image:" in head or "build:" in head):
                return "compose", "infra"
            if name in ("chart.yaml", "values.yaml") or "/charts/" in "/" + low:
                return "helm-values", "infra"
        if "prompt" in low and fmt in ("markdown", "text", "yaml"):
            return fmt, "ai"
        return fmt, domain
    return "text", "other"


# Formats worth showing in a whole-repo map. Docs, generic config and unknown text are noise there,
# but they are always shown when they changed.
REPO_MODE_DOMAINS = {"code", "api", "data", "infra", "ci", "deps", "ai"}


def worth_mapping(fmt: str, domain: str, diff_mode: bool) -> bool:
    if fmt is None:
        return False
    if diff_mode:
        return True
    return domain in REPO_MODE_DOMAINS and fmt not in ("text", "markdown", "helm-values")
