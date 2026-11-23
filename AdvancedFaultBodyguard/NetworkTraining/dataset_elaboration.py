from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "mplconfig"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from dataset_analysis import (
    AUTO_LABEL,
    INDUCED_LABEL,
    LABEL_NAMES,
    TraceFile,
    discover_trace_files,
    grouped_pairs,
    load_csv,
)


@dataclass(frozen=True)
class PreprocessConfig:
    name: str
    align: bool
    representation: str
    per_trace_norm: str
    per_dataset_standardize: bool


CONFIGS = [
    PreprocessConfig(
        name="baseline_trace_zscore",
        align=False,
        representation="signal",
        per_trace_norm="zscore",
        per_dataset_standardize=False,
    ),
    PreprocessConfig(
        name="aligned_crop_trace_zscore",
        align=True,
        representation="signal",
        per_trace_norm="zscore",
        per_dataset_standardize=False,
    ),
    PreprocessConfig(
        name="aligned_crop_robust_datasetstd",
        align=True,
        representation="signal",
        per_trace_norm="robust",
        per_dataset_standardize=True,
    ),
    PreprocessConfig(
        name="aligned_crop_derivative_datasetstd",
        align=True,
        representation="derivative",
        per_trace_norm="robust",
        per_dataset_standardize=True,
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Elabora le power trace per rendere coerente il merge: baseline, "
            "allineamento evento, crop, normalizzazione e benchmark domain shift."
        )
    )
    parser.add_argument("--datasets-dir", type=Path, default=Path("Datasets"))
    parser.add_argument("--output-dir", type=Path, default=Path("elaboration_outputs"))
    parser.add_argument("--channel", default="ALIM")
    parser.add_argument("--baseline-samples", type=int, default=300)
    parser.add_argument("--event-search-start", type=int, default=0)
    parser.add_argument("--event-search-end", type=int, default=1200)
    parser.add_argument("--event-smooth", type=int, default=31)
    parser.add_argument("--crop-pre", type=int, default=600)
    parser.add_argument("--crop-post", type=int, default=2600)
    parser.add_argument("--pca-components", type=int, default=40)
    parser.add_argument("--max-samples-per-class", type=int, default=0)
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def model(n_components: int, random_state: int) -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            ("pca", PCA(n_components=n_components, random_state=random_state)),
            (
                "clf",
                LogisticRegression(
                    class_weight="balanced",
                    max_iter=2000,
                    random_state=random_state,
                    solver="liblinear",
                ),
            ),
        ]
    )


def load_grouped_raw(
    pairs: dict[tuple[str, str], list[TraceFile]],
    max_samples_per_class: int,
    random_state: int,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    rng = np.random.default_rng(random_state)
    grouped: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for (dataset, channel), files in sorted(pairs.items()):
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        for trace_file in sorted(files, key=lambda item: item.label):
            _, x = load_csv(trace_file.path)
            if max_samples_per_class > 0 and len(x) > max_samples_per_class:
                idx = rng.choice(len(x), size=max_samples_per_class, replace=False)
                x = x[idx]
            xs.append(x)
            ys.append(np.full(len(x), trace_file.label, dtype=np.int64))
        grouped[f"{dataset}__{channel}"] = (np.vstack(xs), np.concatenate(ys))
    return grouped


def subtract_baseline(x: np.ndarray, baseline_samples: int) -> np.ndarray:
    width = min(baseline_samples, x.shape[1])
    baseline = np.median(x[:, :width], axis=1, keepdims=True)
    return x.astype(np.float32, copy=False) - baseline.astype(np.float32)


def moving_average(x: np.ndarray, width: int) -> np.ndarray:
    width = max(1, int(width))
    if width == 1:
        return x
    kernel = np.ones(width, dtype=np.float32) / width
    return np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="same"), 1, x)


def detect_event_indices(
    x: np.ndarray,
    search_start: int,
    search_end: int,
    smooth_width: int,
) -> np.ndarray:
    search_start = max(0, int(search_start))
    search_end = min(int(search_end), x.shape[1])
    if search_end <= search_start + 2:
        raise ValueError("La finestra di ricerca evento e' troppo corta.")
    energy = moving_average(np.abs(np.diff(x, axis=1)), smooth_width)
    window = energy[:, search_start : search_end - 1]
    return np.argmax(window, axis=1) + search_start + 1


def crop_around_events(
    x: np.ndarray,
    event_indices: np.ndarray,
    crop_pre: int,
    crop_post: int,
) -> np.ndarray:
    crop_len = crop_pre + crop_post
    cropped = np.empty((x.shape[0], crop_len), dtype=np.float32)
    for row_idx, event_idx in enumerate(event_indices):
        start = int(event_idx) - crop_pre
        end = int(event_idx) + crop_post
        source_start = max(start, 0)
        source_end = min(end, x.shape[1])
        dest_start = source_start - start
        dest_end = dest_start + (source_end - source_start)
        cropped[row_idx] = 0.0
        cropped[row_idx, dest_start:dest_end] = x[row_idx, source_start:source_end]
    return cropped


def normalize_per_trace(x: np.ndarray, mode: str) -> np.ndarray:
    if mode == "none":
        return x.astype(np.float32, copy=False)
    if mode == "zscore":
        center = x.mean(axis=1, keepdims=True)
        scale = x.std(axis=1, keepdims=True)
    elif mode == "robust":
        center = np.median(x, axis=1, keepdims=True)
        q75 = np.percentile(x, 75, axis=1, keepdims=True)
        q25 = np.percentile(x, 25, axis=1, keepdims=True)
        scale = q75 - q25
    else:
        raise ValueError(f"Normalizzazione non supportata: {mode}")
    return ((x - center) / np.maximum(scale, 1e-6)).astype(np.float32)


def dataset_standardize(x: np.ndarray) -> np.ndarray:
    center = x.mean(axis=0, keepdims=True)
    scale = x.std(axis=0, keepdims=True)
    return ((x - center) / np.maximum(scale, 1e-6)).astype(np.float32)


def preprocess_one_dataset(
    x: np.ndarray,
    config: PreprocessConfig,
    args: argparse.Namespace,
) -> tuple[np.ndarray, np.ndarray]:
    x = subtract_baseline(x, args.baseline_samples)
    event_indices = detect_event_indices(
        x,
        args.event_search_start,
        args.event_search_end,
        args.event_smooth,
    )
    if config.align:
        x = crop_around_events(x, event_indices, args.crop_pre, args.crop_post)
    if config.representation == "derivative":
        x = np.diff(x, axis=1).astype(np.float32)
    elif config.representation != "signal":
        raise ValueError(f"Rappresentazione non supportata: {config.representation}")
    x = normalize_per_trace(x, config.per_trace_norm)
    if config.per_dataset_standardize:
        x = dataset_standardize(x)
    return x, event_indices


def preprocess_all(
    raw: dict[str, tuple[np.ndarray, np.ndarray]],
    config: PreprocessConfig,
    args: argparse.Namespace,
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], pd.DataFrame]:
    processed: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    event_rows: list[dict[str, object]] = []
    for name, (x, y) in raw.items():
        xp, event_indices = preprocess_one_dataset(x, config, args)
        processed[name] = (xp, y)
        for label in (AUTO_LABEL, INDUCED_LABEL):
            label_events = event_indices[y == label]
            event_rows.append(
                {
                    "config": config.name,
                    "dataset": name,
                    "label": LABEL_NAMES[label],
                    "event_mean": float(label_events.mean()),
                    "event_std": float(label_events.std()),
                    "event_min": int(label_events.min()),
                    "event_max": int(label_events.max()),
                }
            )
    return processed, pd.DataFrame(event_rows)


def evaluate_leave_one_dataset_out(
    processed: dict[str, tuple[np.ndarray, np.ndarray]],
    config_name: str,
    n_components: int,
    random_state: int,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for test_name, (x_test, y_test) in processed.items():
        train_names = [name for name in processed if name != test_name]
        x_train = np.vstack([processed[name][0] for name in train_names])
        y_train = np.concatenate([processed[name][1] for name in train_names])
        usable_components = min(n_components, x_train.shape[0] - 1, x_train.shape[1])
        clf = model(usable_components, random_state)
        clf.fit(x_train, y_train)
        y_pred = clf.predict(x_test)
        y_score = clf.decision_function(x_test)
        rows.append(
            {
                "config": config_name,
                "held_out_dataset": test_name,
                "n_features": x_train.shape[1],
                "pca_components": usable_components,
                "balanced_accuracy": float(balanced_accuracy_score(y_test, y_pred)),
                "auc": float(roc_auc_score(y_test, y_score)),
            }
        )
    return pd.DataFrame(rows)


def save_processed_csv(
    processed: dict[str, tuple[np.ndarray, np.ndarray]],
    output_path: Path,
) -> None:
    rows: list[pd.DataFrame] = []
    for dataset_name, (x, y) in processed.items():
        df = pd.DataFrame(x)
        df.insert(0, "label", [LABEL_NAMES[int(label)] for label in y])
        df.insert(0, "dataset", dataset_name)
        rows.append(df)
    pd.concat(rows, ignore_index=True).to_csv(output_path, index=False)


def plot_event_histograms(event_stats: pd.DataFrame, output_dir: Path) -> None:
    for config_name, config_stats in event_stats.groupby("config"):
        fig, ax = plt.subplots(figsize=(10, 5))
        labels = config_stats["dataset"] + " / " + config_stats["label"]
        x_pos = np.arange(len(labels))
        ax.bar(x_pos, config_stats["event_mean"], yerr=config_stats["event_std"])
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel("indice evento rilevato")
        ax.set_title(config_name)
        fig.tight_layout()
        fig.savefig(output_dir / f"{config_name}_event_indices.png", dpi=160)
        plt.close(fig)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    files = discover_trace_files(args.datasets_dir, args.channel)
    pairs = grouped_pairs(files)
    if not pairs:
        raise SystemExit("Nessuna coppia autoindotto/indotto completa trovata.")

    raw = load_grouped_raw(pairs, args.max_samples_per_class, args.random_state)
    all_scores: list[pd.DataFrame] = []
    all_event_stats: list[pd.DataFrame] = []
    processed_by_config: dict[str, dict[str, tuple[np.ndarray, np.ndarray]]] = {}

    for config in CONFIGS:
        processed, event_stats = preprocess_all(raw, config, args)
        scores = evaluate_leave_one_dataset_out(
            processed, config.name, args.pca_components, args.random_state
        )
        all_scores.append(scores)
        all_event_stats.append(event_stats)
        processed_by_config[config.name] = processed
        mean_bal_acc = scores["balanced_accuracy"].mean()
        mean_auc = scores["auc"].mean()
        print(f"[OK] {config.name}: LODO bal_acc={mean_bal_acc:.3f}, auc={mean_auc:.3f}")

    scores_df = pd.concat(all_scores, ignore_index=True)
    event_stats_df = pd.concat(all_event_stats, ignore_index=True)
    summary_df = (
        scores_df.groupby("config", as_index=False)
        .agg(
            mean_balanced_accuracy=("balanced_accuracy", "mean"),
            min_balanced_accuracy=("balanced_accuracy", "min"),
            mean_auc=("auc", "mean"),
            min_auc=("auc", "min"),
        )
        .sort_values(["mean_balanced_accuracy", "mean_auc"], ascending=False)
    )

    scores_df.to_csv(args.output_dir / "domain_shift_benchmark.csv", index=False)
    summary_df.to_csv(args.output_dir / "domain_shift_summary.csv", index=False)
    event_stats_df.to_csv(args.output_dir / "event_alignment_stats.csv", index=False)
    plot_event_histograms(event_stats_df, args.output_dir)

    best_config = str(summary_df.iloc[0]["config"])
    save_processed_csv(
        processed_by_config[best_config],
        args.output_dir / f"merged_{best_config}.csv",
    )
    with (args.output_dir / "chosen_config.json").open("w") as f:
        json.dump(
            {
                "chosen_config": best_config,
                "reason": (
                    "Massima balanced accuracy media in leave-one-dataset-out tra "
                    "le elaborazioni provate."
                ),
                "parameters": vars(args),
            },
            f,
            indent=2,
            default=str,
        )

    print("\nSintesi domain shift:")
    print(summary_df.to_string(index=False))
    print(f"\nConfig scelta: {best_config}")
    print(f"Output salvati in: {args.output_dir}")


if __name__ == "__main__":
    main()
