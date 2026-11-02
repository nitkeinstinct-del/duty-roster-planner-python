/* Module web worker that runs the Python roster engine (public/roster_engine.py) inside Pyodide.
   Pyodide requires a module-type worker (its wasm glue is an ES module); classic importScripts workers
   are not supported. The runtime is imported from the jsDelivr CDN (CORS-open), pinned by version.
   Messages in:  { id, payload } where payload = { cfg, people, tasks, maxRest }
   Messages out: { id, status } progress text, then { id, ok:true, result } or { id, ok:false, error } */
'use strict';

import { loadPyodide } from 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs';

const PYODIDE_BASE = 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';
let initPromise = null;
let solveJson = null;

async function init() {
  const py = await loadPyodide({ indexURL: PYODIDE_BASE });
  const resp = await fetch('roster_engine.py');
  if (!resp.ok) throw new Error('roster_engine.py fetch failed: HTTP ' + resp.status);
  const src = await resp.text();
  py.FS.writeFile('/roster_engine.py', src);
  py.runPython('import sys; sys.path.insert(0, "/"); import roster_engine');
  solveJson = py.globals.get('roster_engine').solve_json;
}

self.onmessage = function (e) {
  const msg = e.data;
  const id = msg.id;
  if (!initPromise) {
    self.postMessage({ id, status: 'טוען את מנוע הפייתון בדפדפן (הורדה חד-פעמית של כמה מגה-בייט)...' });
    initPromise = init();
  }
  initPromise.then(() => {
    self.postMessage({ id, status: 'מחשב שיבוץ בפייתון...' });
    const out = solveJson(JSON.stringify(msg.payload));
    self.postMessage({ id, ok: true, result: JSON.parse(out) });
  }).catch((err) => {
    initPromise = null; // allow retry on next message
    self.postMessage({ id, ok: false, error: String((err && err.message) || err) });
  });
};
