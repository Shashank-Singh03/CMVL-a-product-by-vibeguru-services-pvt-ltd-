/* Compatibility entry point for the studio redesign's browser acceptance.
 *
 * The current brand suite supersedes this file's original collapse-button and
 * external-map assumptions. It exercises the same workspace foundations with
 * light-dismiss navigation, embedded maps/video, both themes, real local server
 * CSP, fluid widths through 3221px, and reduced-motion-aware snapshot controls.
 * Keep this entry point so existing local validation commands remain useful.
 */
const path = require('node:path');
const {spawnSync} = require('node:child_process');

const env = {...process.env};
if (env.CODEMAP_STUDIO_EVIDENCE && !env.CODEMAP_BRAND_EVIDENCE)
  env.CODEMAP_BRAND_EVIDENCE = env.CODEMAP_STUDIO_EVIDENCE;

const result = spawnSync(process.execPath, [path.join(__dirname, 'brand-dashboard-smoke.cjs')], {
  env,
  stdio: 'inherit',
  windowsHide: true,
});
if (result.error) throw result.error;
process.exitCode = result.status === null ? 1 : result.status;
