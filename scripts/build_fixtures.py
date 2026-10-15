"""Generate the repository's synthetic PDF fixtures (deterministic, tiny).

These files are *synthetic*: they exist so the offline test suite can exercise
the PDF pipeline without touching the network. They must never be reported as
real disclosures.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minereport.common import config  # noqa: E402
from minereport.common.pdfgen import build_text_pdf  # noqa: E402

BANNER = "SYNTHETIC SAMPLE - generated for offline tests, NOT a real disclosure"

NI_TEXT = [
    BANNER,
    "",
    "Synthetic Mineral Resource Statement",
    "Prepared in accordance with National Instrument 43-101 Standards of Disclosure",
    "for Mineral Projects (the sample issuer is fictional).",
    "",
    "Effective date: 1 January 2024",
    "",
    "Category        Tonnes (Mt)   Grade (% Li2O)   Contained Li2O (Mt)",
    "(Mt)            (%)           (Mt)",
    "Measured        12.5          1.05             0.13",
    "Indicated       48.0          0.95             0.46",
    "Inferred        31.2          0.88             0.27",
    "Total           91.7          0.94             0.86",
]

JORC_TEXT = [
    BANNER,
    "",
    "Synthetic JORC 2012 Resource and Reserve Statement",
    "Reported in accordance with the JORC Code 2012 Edition.",
    "",
    "Effective date: 30 June 2024",
    "",
    "Category   Tonnes (Mt)   Li2O (%)   Ta2O5 (ppm)   Contained Li2O (Mt)",
    "(Mt)       (%)           (ppm)      (Mt)",
    "Measured   22.1          1.34       146           0.3",
    "Indicated  315.2         1.15       106           3.6",
    "Inferred   76.6          1.07       124           0.8",
    "Total      413.8         1.15       112           4.8",
    "",
    "Category   Tonnes (Mt)   Li2O (%)   Contained Li2O (Mt)",
    "(Mt)       (%)           (Mt)",
    "Proved     19.1          1.32       0.3",
    "Probable   195.1         1.18       2.3",
    "Total      214.2         1.19       2.5",
]


def main() -> int:
    out_dir = config.FIXTURES_DIR / "pdf"
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "synthetic_ni43101_statement.pdf": NI_TEXT,
        "synthetic_jorc_statement.pdf": JORC_TEXT,
    }
    for name, lines in targets.items():
        path = out_dir / name
        path.write_bytes(build_text_pdf(lines))
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

