#!/usr/bin/env node
'use strict';

/**
 * alan-agent - launcher for Alan, a local-first personal assistant.
 *
 * Alan is Python. This bin file is only the front door: it stages Alan's
 * source out of the npm package (npm rewrites node_modules on every upgrade,
 * so nothing may be built there), guarantees a virtual environment holding
 * the pinned requirements, and then execs `python -m alan` with the same
 * environment contract Alan.cmd establishes.
 *
 * Layout on disk:
 *
 *   %LOCALAPPDATA%\alan-agent\
 *     src\                 staged copy of Alan's source
 *     requirements.txt     staged copy of the pinned dependencies
 *     venv\                the Python environment Alan runs on
 *     .alan-agent-version  package version that staged src\ belongs to
 *
 * Everything under install root survives `npm update`, `npm uninstall -g
 * alan-agent` + reinstall, and a change of node manager.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const readline = require('readline');
const { spawnSync } = require('child_process');

const PKG_ROOT = path.resolve(__dirname, '..');
/** Alan's pins were verified on the bundled 3.12; older minors are refused. */
const MIN_PYTHON = [3, 12];

function fail(message) {
  process.stderr.write('alan-agent: ' + message + '\n');
  process.exit(1);
}

/** Where Alan keeps its data. Overridable for tests and portable installs. */
function installRoot() {
  if (process.env.ALAN_AGENT_HOME) return process.env.ALAN_AGENT_HOME;
  const base =
    process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return path.join(base, 'alan-agent');
}

/** Run a command to completion, reporting only its version banner. */
function probePython(command, args) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.error || result.status !== 0) return null;
  const match = /Python (\d+)\.(\d+)(?:\.(\d+))?/.exec(
    String(result.stdout || '') + String(result.stderr || '')
  );
  if (!match) return null;
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    display: match[1] + '.' + match[2] + '.' + (match[3] || '0'),
  };
}

function pythonIsNewEnough(version) {
  return (
    version.major > MIN_PYTHON[0] ||
    (version.major === MIN_PYTHON[0] && version.minor >= MIN_PYTHON[1])
  );
}

/**
 * An interpreter Alan can run on. ALAN_AGENT_PYTHON wins, then whatever the
 * PATH offers. Deliberately does not shell out to `where`: spawnSync gives
 * the same answer without paying for a shell.
 */
function findPython() {
  const candidates = [];
  if (process.env.ALAN_AGENT_PYTHON) {
    candidates.push([process.env.ALAN_AGENT_PYTHON, ['--version']]);
  }
  candidates.push(
    ['python', ['--version']],
    ['python3', ['--version']],
    ['py', ['-3', '--version']]
  );
  for (const [command, args] of candidates) {
    const version = probePython(command, args);
    if (version && pythonIsNewEnough(version)) {
      return { command: command, version: version.display };
    }
  }
  return null;
}

function packageVersion() {
  try {
    return require(path.join(PKG_ROOT, 'package.json')).version;
  } catch (_) {
    return 'unknown';
  }
}

/**
 * Copy src\ and requirements.txt into the install root, replacing them only
 * when the package version moved. The version marker is what stops every
 * invocation from re-copying 82 files.
 */
function stageSource(root) {
  const marker = path.join(root, '.alan-agent-version');
  const staged = path.join(root, 'src');
  const expected = packageVersion();

  let installed = null;
  try {
    installed = fs.readFileSync(marker, 'utf8').trim();
  } catch (_) {
    /* never staged */
  }
  if (installed === expected && fs.existsSync(staged)) return false;

  fs.mkdirSync(root, { recursive: true });
  fs.rmSync(staged, { recursive: true, force: true });
  fs.cpSync(path.join(PKG_ROOT, 'src'), staged, { recursive: true });
  fs.copyFileSync(
    path.join(PKG_ROOT, 'requirements.txt'),
    path.join(root, 'requirements.txt')
  );
  fs.writeFileSync(marker, expected, 'utf8');
  return true;
}

function venvPython(root) {
  return path.join(root, 'venv', 'Scripts', 'python.exe');
}

function venvExists(root) {
  return fs.existsSync(venvPython(root));
}

function step(label, command, args, env) {
  process.stderr.write('  ' + label + '\n');
  const result = spawnSync(command, args, {
    stdio: 'inherit',
    env: env,
    windowsHide: true,
  });
  if (result.error) {
    fail('could not run ' + command + ': ' + result.error.message);
  }
  if (result.status !== 0) {
    fail(
      'setup failed while running:\n    ' +
        command +
        ' ' +
        args.join(' ') +
        '\n  Re-run `alan-agent` after fixing the above, or set ' +
        'ALAN_AGENT_YES=1 to skip this prompt.'
    );
  }
}

function askToInstall(question) {
  if (process.env.ALAN_AGENT_YES === '1') return Promise.resolve(true);
  if (!process.stdin.isTTY) {
    process.stderr.write(
      'alan-agent: refusing to download packages without a terminal to ask on.\n' +
        '  Approve the install by re-running interactively, or set ALAN_AGENT_YES=1.\n'
    );
    process.exit(1);
  }
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  return new Promise(function (resolve) {
    rl.question(question, function (answer) {
      rl.close();
      resolve(/^(y|yes)$/i.test(answer.trim()));
    });
  });
}

function envFor(root) {
  const env = Object.assign({}, process.env, {
    ALAN_HOME: root,
    PYTHONPATH: path.join(root, 'src'),
    PYTHONUTF8: '1',
    PYTHONDONTWRITEBYTECODE: '1',
  });
  // Unlike Alan.cmd, OLLAMA_HOST is deliberately left alone. Alan.cmd pins it
  // to 11435 so a bundled Ollama cannot collide with a host one -- but this
  // package ships no Ollama, so pinning the port would aim Alan at nothing.
  // The default 11434 is where a host-installed Ollama actually listens.
  delete env.OLLAMA_HOST;
  delete env.OLLAMA_MODELS;
  return env;
}

async function main() {
  const root = installRoot();
  const python = findPython();
  if (!python) {
    fail(
      'Alan needs Python ' +
        MIN_PYTHON.join('.') +
        ' or newer and none was found.\n' +
        '  Install it from https://www.python.org/downloads/ (check ' +
        '"Add python.exe to PATH"), then re-run `alan-agent`.\n' +
        '  Already installed? Point ALAN_AGENT_PYTHON at the interpreter.'
    );
  }

  const staged = stageSource(root);
  const env = envFor(root);

  if (!venvExists(root)) {
    const requirements = path.join(root, 'requirements.txt');
    let pins = 'the pinned dependencies';
    try {
      pins = fs
        .readFileSync(requirements, 'utf8')
        .split(/\r?\n/)
        .filter(function (line) {
          return line.trim() && line.trim().charAt(0) !== '#';
        }).length + ' pinned packages';
    } catch (_) {
      /* stageSource always writes it; fall back to the generic wording */
    }

    const approved = await askToInstall(
      '\nAlan has not been set up on this machine yet.\n\n' +
        '  Python : ' +
        python.command +
        ' (' +
        python.version +
        ')\n' +
        '  Where  : ' +
        root +
        '\n' +
        '  Does  : create a virtual environment, then pip install\n' +
        '           ' +
        pins +
        ' from requirements.txt\n' +
        '  Source : PyPI\n\n' +
        'Install now? [y/N] '
    );
    if (!approved) {
      process.stderr.write('alan-agent: nothing was installed.\n');
      process.exit(1);
    }

    process.stderr.write('alan-agent: setting up ' + root + '\n');
    step('creating venv', python.command, ['-m', 'venv', path.join(root, 'venv')], env);
    step(
      'installing ' + pins,
      venvPython(root),
      [
        '-m',
        'pip',
        'install',
        '--disable-pip-version-check',
        '-r',
        requirements,
      ],
      env
    );
    if (staged) {
      process.stderr.write('  staged Alan ' + packageVersion() + '\n');
    }
    process.stderr.write('alan-agent: ready\n\n');
  }

  const argv = process.argv.slice(2);
  if (argv.length === 0) argv.push('chat');

  const result = spawnSync(venvPython(root), ['-m', 'alan'].concat(argv), {
    stdio: 'inherit',
    env: env,
    windowsHide: true,
  });
  if (result.error) {
    fail('could not start Alan: ' + result.error.message);
  }
  if (result.signal) {
    process.exit(1);
  }
  process.exit(result.status === null ? 1 : result.status);
}

main().catch(function (error) {
  fail(error && error.stack ? error.stack : String(error));
});
