# duty-roster-planner-python

Live site: https://nitkeinstinct-del.github.io/duty-roster-planner-python/

Deployment mirror of the `python-engine` branch of
[duty-roster-planner](https://github.com/nitkeinstinct-del/duty-roster-planner).
Same React UI, but every roster is computed by a Python engine running in the
browser via Pyodide (a module web worker loads the pinned Pyodide v314.0.7 from
the jsDelivr CDN, so the first load needs an internet connection).

- `index.html` - the whole app (JS and CSS inlined into one file).
- `pyworker.mjs` - module web worker that loads Pyodide and runs the engine.
- `roster_engine.py` - the Python scheduling engine (standard library only).

Served with GitHub Pages "deploy from a branch" (main, root); the built files
are uploaded as-is, there is no build step in this repo. Source, tests and the
method's limits (heuristic, no proven optimum, rest at the horizon edges not
checked) are documented in the source repo's README on the `python-engine`
branch.

The TypeScript version at
https://nitkeinstinct-del.github.io/duty-roster-planner/ is deployed separately
from the source repo's `main` branch and is unchanged.
