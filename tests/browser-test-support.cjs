/* Shared browser discovery for the optional browser acceptance suites.
 * Runtime users do not need Node or Playwright; this is development tooling.
 */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

let playwright;
try {
  playwright = require(require.resolve('playwright-core', {
    paths: [path.resolve(__dirname, '../dashboard'), __dirname],
  }));
} catch (error) {
  throw new Error('Browser tests need the dashboard dev dependencies. Run "npm --prefix dashboard ci" first.', {cause: error});
}

function isFile(candidate) {
  try { return fs.statSync(candidate).isFile(); } catch { return false; }
}

function installedBrowser() {
  if (process.env.CODEMAP_BROWSER) {
    const selected = path.resolve(process.env.CODEMAP_BROWSER);
    if (!isFile(selected)) throw new Error('CODEMAP_BROWSER must point to an existing Chrome, Edge or Chromium executable.');
    return selected;
  }
  let candidates = [];
  if (process.platform === 'win32') {
    const roots = [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)'], process.env.LOCALAPPDATA].filter(Boolean);
    candidates = roots.flatMap(root => [
      path.join(root, 'Google/Chrome/Application/chrome.exe'),
      path.join(root, 'Microsoft/Edge/Application/msedge.exe'),
      path.join(root, 'Chromium/Application/chrome.exe'),
    ]);
  } else if (process.platform === 'darwin') {
    candidates = ['/Applications', path.join(os.homedir(), 'Applications')].flatMap(root => [
      path.join(root, 'Google Chrome.app/Contents/MacOS/Google Chrome'),
      path.join(root, 'Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),
      path.join(root, 'Chromium.app/Contents/MacOS/Chromium'),
    ]);
  }
  const commands = process.platform === 'win32'
    ? ['chrome.exe', 'msedge.exe', 'chromium.exe']
    : ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge'];
  candidates.push(...(process.env.PATH || '').split(path.delimiter).filter(Boolean)
    .flatMap(directory => commands.map(command => path.join(directory, command))));
  return candidates.find(isFile);
}

const executablePath = installedBrowser();
// Without an installed browser, Playwright uses its locally downloaded Chromium.
const browserLaunchOptions = executablePath ? {executablePath} : {};
module.exports = {chromium: playwright.chromium, browserLaunchOptions};
