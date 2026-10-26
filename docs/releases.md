# Installing and updating the beta

CMVL uses `codemap` as its agent-skill and command name. Each release contains the
Python runtime, map templates, bundled React dashboard, offline font and dependency
notices. Viewers do not need Node or a CDN. Video generation additionally needs the
tools reported by `codemap doctor`.

## Does everyone see the same dashboard?

Users on the same installed release generate the same dashboard design and features.
Their project/task data, browser size, theme and personal learning checklist differ.
The curated Notes app example is sample content; it is not everybody's project.

Generated HTML embeds its dashboard bundle at generation time. Updating the skill
does not replace an older HTML file already saved in a project or shared elsewhere.
Refreshing a browser reloads that saved file; it does not download a newer dashboard.
There is no background cloud update service in this beta.

## Install a release

1. Open this repository's **Releases** page and choose a version marked **Pre-release**
   for a beta. Download `CMVL-<version>.zip`; keep the accompanying manifest/checksums
   if you want to verify the download.
2. Extract the ZIP, open a terminal in `CMVL-<version>`, and check the runtime:

   ```bash
   python skills/codemap/scripts/codemap.py --version
   python skills/codemap/scripts/codemap.py doctor
   ```

3. Install into the agents you use, either user-wide or for one project:

   ```bash
   python install.py
   python install.py --project path/to/your/repo
   ```

   Automatic Claude hooks are opt-in. Follow the main README if you want them;
   installing a release does not silently enable automatic task capture.

For SHA-256 verification, compare the ZIP's digest with its line in `SHA256SUMS`:

```powershell
Get-FileHash CMVL-0.3.0-beta.1.zip -Algorithm SHA256
```

On Linux use `sha256sum -c SHA256SUMS` in the download directory; on macOS use
`shasum -a 256 -c SHA256SUMS`. The public manifest records each packaged file's
digest and the exact source commit. It contains no private project captures.

## Update an existing installation

Download/extract the newer release, then rerun the same installation command and
agent/project selection you used before. Restart the agent so it reads the updated
skill instructions. If you installed automatic Claude hooks, rerun the documented
`--auto` install command for that same project to point them at the new skill copy.

Regenerate the project's history with the updated runtime:

```bash
python skills/codemap/scripts/codemap.py task history path/to/your/repo
```

This refreshes the dashboard shell from the saved public task metadata. It does not
reanalyze the source, rerun a model review, or recapture a task. To update an existing
task's map shell too, run `render` on its saved output directory, for example:

```bash
python skills/codemap/scripts/codemap.py render --out path/to/your/repo/.codemap/tasks/TASK_ID/output
```

Replace `TASK_ID` with a real task ID from that project's history. Keep the saved
captures if you need to revisit the original task context. New tasks use the newly
installed templates automatically. Shared copies of old HTML stay on their embedded
version until their owner regenerates and shares them again.

## Maintainer release process

1. Set the application version in
   `skills/codemap/scripts/codemap_lib/__init__.py`, keep the dashboard package
   metadata aligned, and write a changelog entry. Beta versions use names such as
   `0.3.0-beta.1`, `0.3.0-beta.2`; a stable version omits the suffix.
2. Rebuild the dashboard assets and curated example, then run the relevant tests:

   ```bash
   npm ci --prefix dashboard
   npm run build --prefix dashboard
   python scripts/refresh_demo.py
   python -m unittest discover -s tests -v
   ```

3. Review the public file list and commit the release changes. From that clean
   committed checkout, build the download:

   ```bash
   python scripts/build_release.py --expect-version 0.3.0-beta.1
   ```

   The builder reads committed Git blobs, requires a clean tracked worktree,
   rejects private/generated tracked paths and symlinks, and ignores untracked
   files. Its ZIP contains a single `CMVL-<version>` folder and a manifest. Build
   output goes to the ignored `dist/` directory.
4. Push the commit, then create and push the matching tag:

   ```bash
   git tag v0.3.0-beta.1
   git push origin main
   git push origin v0.3.0-beta.1
   ```

The tag workflow first calls the validation workflow: Python 3.9/3.13 on Windows
and Linux, an offline-dashboard rebuild check, and committed-tree packaging. Only
after those checks pass does it publish the tag's ZIP, manifest and `SHA256SUMS`.
Tags with a suffix are marked **Pre-release**. Re-running a tag workflow refreshes
that tag's assets; publish changes under a new version/tag instead of moving an
existing tag. The workflow uses GitHub's scoped built-in token, so no personal
access token belongs in this repository.

The workflows do not run captured applications. Optional media/browser end-to-end
tests still depend on locally available tools and are described separately from
the standard-library CI checks. A successful local run is not a claim that hosted
CI has already passed; check the repository's **Actions** page after pushing.
