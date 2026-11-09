# ReMA: Review–Modify Multi-Agent Reconstruction of Hand-Drawn BPMN Diagrams

ReMA converts a **photograph of a hand-drawn BPMN diagram** into a **valid BPMN 2.0 XML** file
through a multi-stage.

---

Design principles:

- **LLMs perceive and adjudicate; code guarantees correctness.** All coordinate math,
  diff computation, conservation checks, and topology validation are pure Python
  (standard library only) — the LLM never computes geometry.
- **Every improvement is an ablation switch.** Each module can be toggled
  independently via presets, JSON config files, or environment variables.
- **Full traceability.** Every run writes its resolved ablation configuration to disk
  (`{image}_ablation.json`) next to its outputs.


Put the diagram you want to reconstruct into `data/image/` and pass its file name
with `--image-name`. Outputs are written to `Gemini_blind_modify/` in the project root.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Configure credentials (never commit real keys):

```bash
cp .env.example .env             # then edit .env
# or, in the current shell:
export REMA_API_KEY="your-key"
export REMA_MODEL="gemini-3-pro-preview"   # optional override
```

## Quick Start

```bash
# Single image with the complete configuration
python detect13_blind.py --image image_name --ablation-preset rema_full

```
## Outputs

For each input image the pipeline writes (under the experiment output directory):

- `{name}_processed.bpmn` — final BPMN XML (`_needs_revision` suffix if the review
  stage still reported errors after the maximum number of rounds)
- `{name}_status.json` — full status summary (review status, termination report,
  RC1/RC2 counts, sync counts, render-compare findings, …)
- `{name}_ablation.json` — resolved ablation configuration (reproducibility log)
- per-stage intermediates: inventory, edge completion, review rounds, render compare

## License

Released under the [MIT License](LICENSE).
