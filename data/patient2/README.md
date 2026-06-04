# data/patient2/

The source PDF (`source.pdf`) is the synthetic patient record provided with the
assignment. It is **not committed** to this public repo (see `.gitignore`): it is
Dscribe's synthetic data and the file is large.

To run the live vision-OCR path, place the provided PDF here as `source.pdf`:

```bash
python -m discharge_agent run --pdf data/patient2/source.pdf --patient patient2
```

You do not need the PDF to reproduce the documented run: a pre-extracted transcript is
committed at `reference/ground_truth_transcript.json`, used via `--transcript` (see the
top-level README, step 4b). The generated outputs are in `outputs/patient2/`.
