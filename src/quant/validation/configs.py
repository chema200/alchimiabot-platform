"""Experiment batch configurations for validation."""

from ..experiments.engine import ExperimentConfig


def get_batches() -> dict[str, dict]:
    """Return all experiment batches with their configs."""
    return {
        "score_threshold": {
            "hypothesis": "Low-score trades lose money. Raising threshold improves expectancy by filtering noise.",
            "configs": [
                ExperimentConfig(name="score_baseline", description="Current baseline (no filter)", score_min=0),
                ExperimentConfig(name="score_60", description="Score min 60", score_min=60),
                ExperimentConfig(name="score_63", description="Score min 63", score_min=63),
                ExperimentConfig(name="score_65", description="Score min 65", score_min=65),
                ExperimentConfig(name="score_68", description="Score min 68", score_min=68),
            ],
        },
        "trailing_optimization": {
            "hypothesis": "Trailing too tight kills winners. Wider trailing captures more MFE.",
            "configs": [
                ExperimentConfig(name="trail_050_p30_baseline", description="Current baseline",
                                 trailing_distance_pct=0.50, partial_close_pct=30),
                ExperimentConfig(name="trail_075_p30", description="1.5x wider trailing",
                                 trailing_distance_pct=0.75, partial_close_pct=30),
                ExperimentConfig(name="trail_100_p30", description="2x wider trailing",
                                 trailing_distance_pct=1.00, partial_close_pct=30),
                ExperimentConfig(name="trail_075_p50", description="Wider trailing + more partial",
                                 trailing_distance_pct=0.75, partial_close_pct=50),
            ],
        },
        "sl_fees": {
            "hypothesis": "SL is in the fee dead zone. Wider SL reduces noise and fee-killed trades.",
            "configs": [
                ExperimentConfig(name="sl040_tp045_baseline", description="Current baseline",
                                 sl_max_pct=0.40, tp_min_pct=0.45),
                ExperimentConfig(name="sl050_tp045", description="+25% SL room",
                                 sl_max_pct=0.50, tp_min_pct=0.45),
                ExperimentConfig(name="sl060_tp045", description="+50% SL room",
                                 sl_max_pct=0.60, tp_min_pct=0.45),
                ExperimentConfig(name="sl055_tp060", description="Both wider, better RR",
                                 sl_max_pct=0.55, tp_min_pct=0.60),
            ],
        },
    }
