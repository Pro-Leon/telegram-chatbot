"""Reports — EvaluationReport with dataset_hash, file-only."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from simulation.evaluation.metrics import Metrics


def dataset_hash(bundles: list[Any]) -> str:
    h = hashlib.sha256()
    for b in sorted(bundles, key=lambda x: (int(x.input.creator_id), int(x.input.opportunity_id))):
        ex = None
        try:
            from commerce.offline_optimizer import make_training_example

            ex, _ = make_training_example(input=b.input, evidence=b.evidence, ledger_row=b.ledger_row)
        except Exception:
            pass
        label = ex.label if ex is not None else "UNKNOWN"
        h.update(f"{b.input.creator_id}:{b.input.opportunity_id}:{label}".encode())
    return h.hexdigest()[:16]


@dataclass(frozen=True)
class EvaluationReport:
    simulation_id: str
    scenario_id: str
    seed: int
    strategy_id: str
    dataset_hash: str
    train_n: int
    valid_n: int
    test_n: int
    metrics: Metrics
    by_cohort: dict[str, Any]
    baselines: dict[str, float]
    calibration: Any
    as_of: datetime | None = None
    # P22-P24 extensions — optional with defaults for backward compat
    scenario_version: str = "v1"
    optimizer_version: str = "p356.offline.proto.v1"
    code_revision: str | None = None
    counterfactual: Any | None = None
    stress: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        # handle by_cohort values that may be Metrics or dict
        def _cohort_to_dict(v: Any) -> Any:
            if hasattr(v, "log_loss"):
                return {"log_loss": v.log_loss, "brier": v.brier, "n": getattr(v, "n", 0)}
            if isinstance(v, dict):
                return v
            return str(v)

        return {
            "simulation_id": self.simulation_id,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "strategy_id": self.strategy_id,
            "dataset_hash": self.dataset_hash,
            "train_n": self.train_n,
            "valid_n": self.valid_n,
            "test_n": self.test_n,
            "metrics": {
                "log_loss": self.metrics.log_loss,
                "brier": self.metrics.brier,
                "calibration_gap": self.metrics.calibration_gap,
                "accuracy": self.metrics.accuracy,
                "precision": self.metrics.precision,
                "recall": self.metrics.recall,
                "coverage": self.metrics.coverage,
                "mean_predicted": self.metrics.mean_predicted,
                "observed_rate": self.metrics.observed_rate,
                "n": self.metrics.n,
            },
            "by_cohort": {k: _cohort_to_dict(v) for k, v in self.by_cohort.items()},
            "baselines": dict(self.baselines),
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "scenario_version": self.scenario_version,
            "optimizer_version": self.optimizer_version,
            "code_revision": self.code_revision,
            "counterfactual": self.counterfactual,
            "stress": self.stress,
            "calibration": self.calibration,
        }

    def save(self, base: Path | str | None = None) -> Path:
        from simulation.persistence import run_dir

        # simulation_id determines dir
        d = run_dir(self.simulation_id, base) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "evaluation.json"
        p.write_text(json.dumps(self.to_dict(), sort_keys=True, indent=2), encoding="utf-8")
        return p

    def save_markdown(self, base: Path | str | None = None) -> Path:
        """Generate markdown report file-only."""
        from simulation.persistence import run_dir

        d = run_dir(self.simulation_id, base) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "report.md"
        lines: list[str] = []
        lines.append(f"# Evaluation Report — {self.scenario_id}")
        lines.append("")
        lines.append(f"**simulation_id:** `{self.simulation_id}`")
        lines.append(f"**seed:** `{self.seed}` **scenario:** `{self.scenario_id}` **scenario_version:** `{self.scenario_version}`")
        lines.append(f"**strategy_id:** `{self.strategy_id}` **dataset_hash:** `{self.dataset_hash}`")
        lines.append(f"**optimizer_version:** `{self.optimizer_version}` **code_revision:** `{self.code_revision or 'unknown'}`")
        lines.append(f"**as_of:** `{self.as_of.isoformat() if self.as_of else 'n/a'}`")
        lines.append("")
        lines.append(f"**Split:** train `{self.train_n}` valid `{self.valid_n}` test `{self.test_n}` (chronological 60/15/15)")
        lines.append("")
        lines.append("## Metrics (past->future)")
        lines.append("")
        m = self.metrics
        lines.append("| metric | value |")
        lines.append("|---|---|")
        lines.append(f"| log_loss | {m.log_loss:.4f}" + f"|" if m.log_loss is not None else "| log_loss | n/a |")
        lines.append(f"| brier | {m.brier:.4f}" + f"|" if m.brier is not None else "| brier | n/a |")
        lines.append(f"| calibration_gap | {m.calibration_gap:.4f}" + f"|" if m.calibration_gap is not None else "| calibration_gap | n/a |")
        lines.append(f"| accuracy | {m.accuracy:.3f}" + f"|" if m.accuracy is not None else "| accuracy | n/a |")
        lines.append(f"| precision | {m.precision:.3f}" + f"|" if m.precision is not None else "| precision | n/a |")
        lines.append(f"| recall | {m.recall:.3f}" + f"|" if m.recall is not None else "| recall | n/a |")
        lines.append(f"| coverage | {m.coverage:.3f}" + f"|" if m.coverage is not None else "| coverage | n/a |")
        lines.append("")
        lines.append("## By Cohort")
        lines.append("")
        if self.by_cohort:
            lines.append("| cohort | n | brier | log_loss |")
            lines.append("|---|---|---|---|")
            for k, v in sorted(self.by_cohort.items()):
                try:
                    b = getattr(v, "brier", None)
                    ll = getattr(v, "log_loss", None)
                    n = getattr(v, "n", 0)
                    if isinstance(v, dict):
                        b = v.get("brier")
                        ll = v.get("log_loss")
                        n = v.get("n", 0)
                    b_str = f"{b:.4f}" if isinstance(b, float) else "n/a"
                    ll_str = f"{ll:.4f}" if isinstance(ll, float) else "n/a"
                    lines.append(f"| {k} | {n} | {b_str} | {ll_str} |")
                except Exception:
                    lines.append(f"| {k} | n/a | n/a | n/a |")
        else:
            lines.append("_no cohorts_")
        lines.append("")
        lines.append("## Baselines (5)")
        lines.append("")
        lines.append("| baseline | rate |")
        lines.append("|---|---|")
        for k, v in sorted(self.baselines.items()):
            try:
                lines.append(f"| {k} | {float(v):.3f} |")
            except Exception:
                lines.append(f"| {k} | {v} |")
        lines.append("")
        lines.append("## Counterfactual (same fan x same context x different offer)")
        lines.append("")
        if self.counterfactual:
            cf = self.counterfactual
            # handle list or dict
            if isinstance(cf, list) and cf:
                lines.append(f"is_extrapolative: {cf[0].get('is_extrapolative')} warning: {cf[0].get('warning','')[:60]}...")
                lines.append("")
                lines.append("| opp | primary_prob | alternatives |")
                lines.append("|---|---|---|")
                for entry in cf[:5]:
                    pp = entry.get("primary_prob")
                    pp_s = f"{pp:.3f}" if isinstance(pp, float) else "n/a"
                    scores = entry.get("candidate_scores", [])
                    lines.append(f"| {entry.get('opportunity_id')} | {pp_s} | {len(scores)} |")
            else:
                lines.append(f"```json\n{json.dumps(cf, indent=2)[:800]}\n```")
        else:
            lines.append("_none (model abstained or not run)_")
            lines.append("")
            lines.append("> EXTRAPOLATIVE / OFF-POLICY ADVISORY: alternatives were not exposed")
        lines.append("")
        lines.append("## Stress (n->Brier)")
        lines.append("")
        if self.stress and isinstance(self.stress, dict):
            lines.append("| n | dataset_hash | brier | log_loss |")
            lines.append("|---|---|---|---|")
            for n in sorted(self.stress.keys()):
                entry = self.stress[n]
                if isinstance(entry, dict):
                    dh = entry.get("dataset_hash", "")[:8]
                    b = entry.get("brier")
                    ll = entry.get("log_loss")
                else:
                    dh = getattr(entry, "dataset_hash", "")[:8] if hasattr(entry, "dataset_hash") else ""
                    b = getattr(entry, "metrics", None)
                    b = b.brier if b else None
                    ll = None
                b_s = f"{b:.4f}" if isinstance(b, float) else "n/a"
                ll_s = f"{ll:.4f}" if isinstance(ll, float) else "n/a"
                lines.append(f"| {n} | {dh} | {b_s} | {ll_s} |")
        else:
            lines.append("_no stress sweep (n<90)_")
        lines.append("")
        lines.append("## Reproducibility")
        lines.append("")
        lines.append("| field | value |")
        lines.append("|---|---|")
        lines.append(f"| simulation_id | `{self.simulation_id}` |")
        lines.append(f"| seed | `{self.seed}` |")
        lines.append(f"| scenario_id | `{self.scenario_id}` |")
        lines.append(f"| scenario_version | `{self.scenario_version}` |")
        lines.append(f"| behavior_model_version | v1 |")
        lines.append(f"| optimizer_version | `{self.optimizer_version}` |")
        lines.append(f"| code_revision | `{self.code_revision or 'unknown'}` |")
        lines.append(f"| dataset_hash | `{self.dataset_hash}` |")
        lines.append(f"| FEATURE_SCHEMA_VERSION | `p356.features.v1` |")
        lines.append("")
        p.write_text("\n".join(lines), encoding="utf-8")
        return p
