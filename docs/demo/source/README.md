# Video and audit report sources

Everything needed to rebuild the silent walkthrough (`docs/demo/demo.mp4`) and the audit report
(`docs/demo/AUDIT_REPORT.*`) from this repository alone. Paths below are relative to the repo root.

## What is here

| Path | What it is |
|---|---|
| `index.html` | Root of the video: theme (colours, type), and the timing of every slide |
| `compositions/bg.html`, `compositions/chrome.html` | Background grid, and the persistent project label, slide counter and progress bar |
| `compositions/s01-context.html` | Slide 01, the industry and four everyday problems |
| `compositions/s02-hook.html` | Slide 02, the project in one sentence |
| `compositions/explainer.html` | Slides 03-04, the white "how it works" chapter (edit its `CONFIG` block) |
| `compositions/s03-architecture.html` | Slide 05, the architecture diagram (`NODES` and `W` wires; every box names a file in `src/pipeline_builder/`) |
| `compositions/s04-routes.html` | Slide 06, the run log, quarantine per table and defects caught by type (arrays copied from `outputs/metrics.json`) |
| `compositions/value.html` | Slide 07, the orders data contract and the engineer scene (data injected by `tools/inject_contract.py`) |
| `compositions/s05-results.html`, `s06-twist.html`, `s07-end.html` | Slides 08-10, results, what didn't win, end card |
| `assets/contract_sheet.json` | Every rule the validation agent writes for `orders.csv`, with evidence and rows held, exported by `tools/export_contract_sheet.py` |
| `audit/facts.yaml` | Every figure (F-xx), assumption (A-xx), slide text and check expression used by the report |
| `tools/build_audit_report.py` | Builds the slide stills, re-checks every figure against the repo outputs, writes MD, DOCX and PDF |
| `tools/export_contract_sheet.py` | Re-runs the repo's own discovery and validation agents on `data/raw` and exports the contract sheet as JSON |
| `tools/inject_contract.py` | Inlines that JSON into `compositions/value.html` (renders cannot fetch files) |
| `package.json`, `hyperframes.json`, `meta.json` | HyperFrames project files (CLI pinned to 0.8.156) |

Fonts (Inter, JetBrains Mono, Montserrat) are referenced by name and embedded by the HyperFrames
renderer; all three are SIL Open Font License. The video uses no images: every chart and diagram is
drawn from the numbers in `outputs/`.

## Requirements

- Node 22 and `npx` (HyperFrames 0.8.156 is fetched by `npx`), plus Chrome/Chromium for rendering.
- `ffmpeg` for compressing the video and extracting slide stills.
- LibreOffice (`soffice`) to convert the report to PDF.
- The repo's own environment (`uv sync`) for `make e2e`, `make stress` and `export_contract_sheet.py`.
- For `build_audit_report.py` and `inject_contract.py` (any Python 3.11+, not added to the repo's
  `pyproject.toml`): `pip install pyyaml pandas python-docx pillow numpy matplotlib`.

## Rebuild the numbers, the video and the report

```bash
# 1. repo outputs (data/raw + answer key, then the full run; then the stress sweep)
make e2e
make stress

# 2. input for slide 07 (about 40 s)
PYTHONPATH=src .venv/bin/python docs/demo/source/tools/export_contract_sheet.py docs/demo/source/assets/contract_sheet.json
python3 docs/demo/source/tools/inject_contract.py docs/demo/source/assets/contract_sheet.json \
  docs/demo/source/compositions/value.html \
  --rules orders__pk__unique,orders__customer_id__not_null,orders__customer_id__references_customers,orders__ordered_at__window,orders__order_total__type,orders__status__in_set

# 3. render (86 s, 1920x1080, 30 fps, silent) and compress
cd docs/demo/source
npx --yes hyperframes@0.8.156 check
npx --yes hyperframes@0.8.156 render -f 30 -q looks -o renders/agentic-pipeline-builder-master.mp4
cd -
ffmpeg -y -i docs/demo/source/renders/agentic-pipeline-builder-master.mp4 -c:v libx264 -preset slow -crf 25 \
  -pix_fmt yuv420p -movflags +faststart -an docs/demo/demo.mp4

# GIFs: slide 07 (end-user value) and the architecture data flow
VF="fps=12,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
ffmpeg -y -ss 54.9 -t 12 -i docs/demo/source/renders/agentic-pipeline-builder-master.mp4 -vf "$VF" -loop 0 docs/demo/demo.gif
ffmpeg -y -ss 30.6 -t 7.8 -i docs/demo/source/renders/agentic-pipeline-builder-master.mp4 -vf "$VF" -loop 0 docs/demo/architecture.gif

# 4. audit report: slide stills, automated checks, AUDIT_REPORT.md/.docx/.pdf
python3 docs/demo/source/tools/build_audit_report.py docs/demo/source/audit/facts.yaml
```

`build_audit_report.py` exits with code 1 if any figure on screen no longer matches the repo outputs.
The slide 06 numbers are copied into `compositions/s04-routes.html`; after a fresh `make e2e` (timings
change from run to run) the report flags any that moved, and the change-impact matrix lists which
figures and slides each input affects.

The `renders/` folder (about 13 MB) is not committed; step 3 recreates it.
