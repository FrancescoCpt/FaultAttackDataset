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
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


AUTO_LABEL = 0
INDUCED_LABEL = 1
LABEL_NAMES = {AUTO_LABEL: "autoindotto", INDUCED_LABEL: "indotto"}


@dataclass(frozen=True)
class TraceFile:
    path: Path
    dataset: str
    channel: str
    label: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analisi separabilita' dei dataset di power trace e merge "
            "incrementale tra sorgenti diverse."
        )
    )
    parser.add_argument("--datasets-dir", type=Path, default=Path("Datasets"))
    parser.add_argument("--output-dir", type=Path, default=Path("analysis_outputs"))
    parser.add_argument(
        "--channel",
        default="ALIM",
        help=(
            "Canale da analizzare: ALIM, TRG/TRIG, ALL. Default: ALIM, "
            "per evitare di mischiare alimentazione e trigger."
        ),
    )
    parser.add_argument(
        "--normalization",
        choices=("none", "trace-zscore", "trace-minmax"),
        default="trace-zscore",
        help="Normalizzazione applicata a ogni traccia prima delle analisi.",
    )
    parser.add_argument(
        "--max-samples-per-class",
        type=int,
        default=0,
        help="Sottocampiona ogni classe per dataset. 0 = usa tutto.",
    )
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def detect_label(path: Path) -> int | None:
    name = path.name.upper()
    if any(token in name for token in ("AUTO", "AUTINDOTTO", "AUTOINDOTT0")):
        return AUTO_LABEL
    if any(token in name for token in ("INDOTTO", "ATTACCO")):
        return INDUCED_LABEL
    return None


def detect_channel(path: Path) -> str:
    name = path.name.upper()
    if "TRIG" in name or "TRG" in name:
        return "TRG"
    if "ALIM" in name:
        return "ALIM"
    return "UNKNOWN"


def discover_trace_files(datasets_dir: Path, channel: str) -> list[TraceFile]:
    channel = channel.upper()
    target_channel = normalize_channel(channel)
    candidates: list[tuple[Path, int, str]] = []
    for path in sorted(datasets_dir.glob("*/*.csv")):
        label = detect_label(path)
        detected_channel = detect_channel(path)
        if label is None:
            print(f"[WARN] label non riconosciuta, salto: {path}")
            continue
        candidates.append((path, label, detected_channel))

    known_channels_by_dataset: dict[str, set[str]] = {}
    for path, _, detected_channel in candidates:
        if detected_channel != "UNKNOWN":
            known_channels_by_dataset.setdefault(path.parent.name, set()).add(
                detected_channel
            )

    files: list[TraceFile] = []
    for path, label, detected_channel in candidates:
        sibling_channels = known_channels_by_dataset.get(path.parent.name, set())
        if (
            channel != "ALL"
            and detected_channel == "UNKNOWN"
            and target_channel in sibling_channels
        ):
            detected_channel = target_channel
        elif channel == "ALL" and detected_channel == "UNKNOWN" and len(sibling_channels) == 1:
            detected_channel = next(iter(sibling_channels))
        if channel != "ALL" and detected_channel != target_channel:
            continue
        files.append(
            TraceFile(
                path=path,
                dataset=path.parent.name,
                channel=detected_channel,
                label=label,
            )
        )
    return files


def normalize_channel(channel: str) -> str:
    channel = channel.upper()
    if channel in {"TRIG", "TRIGGER"}:
        return "TRG"
    return channel


def load_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    df = pd.read_csv(path, sep=";", header=None)
    meta = df.iloc[:, 0].to_numpy()
    traces = df.iloc[:, 1:].to_numpy(dtype=np.float32)
    return meta, traces


def normalize_traces(x: np.ndarray, mode: str) -> np.ndarray:
    if mode == "none":
        return x.astype(np.float32, copy=False)
    x = x.astype(np.float32, copy=True)
    if mode == "trace-zscore":
        mean = x.mean(axis=1, keepdims=True)
        std = x.std(axis=1, keepdims=True)
        return (x - mean) / np.maximum(std, 1e-6)
    if mode == "trace-minmax":
        min_value = x.min(axis=1, keepdims=True)
        max_value = x.max(axis=1, keepdims=True)
        return (x - min_value) / np.maximum(max_value - min_value, 1e-6)
    raise ValueError(f"Normalizzazione non supportata: {mode}")


def load_pair(
    files: list[TraceFile],
    normalization: str,
    max_samples_per_class: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    rows: list[dict[str, object]] = []

    for trace_file in files:
        meta, x = load_csv(trace_file.path)
        if max_samples_per_class > 0 and len(x) > max_samples_per_class:
            idx = rng.choice(len(x), size=max_samples_per_class, replace=False)
            x = x[idx]
            meta = meta[idx]
        x = normalize_traces(x, normalization)
        y = np.full(len(x), trace_file.label, dtype=np.int64)
        xs.append(x)
        ys.append(y)
        rows.append(
            {
                "dataset": trace_file.dataset,
                "channel": trace_file.channel,
                "label": LABEL_NAMES[trace_file.label],
                "file": str(trace_file.path),
                "n_traces": len(x),
                "n_points": x.shape[1],
                "meta_min": float(np.min(meta)),
                "meta_max": float(np.max(meta)),
                "signal_mean": float(np.mean(x)),
                "signal_std": float(np.std(x)),
            }
        )

    return np.vstack(xs), np.concatenate(ys), pd.DataFrame(rows)


def make_model(random_state: int) -> Pipeline:
    return Pipeline(
        steps=[
            ("scaler", StandardScaler()),
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


def separability_metrics(
    x: np.ndarray, y: np.ndarray, random_state: int
) -> dict[str, float | int]:
    class_counts = np.bincount(y, minlength=2)
    min_class = int(class_counts.min())
    if min_class < 2:
        return {
            "n_autoindotto": int(class_counts[AUTO_LABEL]),
            "n_indotto": int(class_counts[INDUCED_LABEL]),
            "cv_accuracy": np.nan,
            "cv_balanced_accuracy": np.nan,
            "cv_auc": np.nan,
            "pca_fisher_ratio": np.nan,
            "pca_var_1": np.nan,
            "pca_var_2": np.nan,
        }

    n_splits = min(5, min_class)
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    model = make_model(random_state)
    y_pred = cross_val_predict(model, x, y, cv=cv, method="predict")
    y_score = cross_val_predict(model, x, y, cv=cv, method="decision_function")
    pca = PCA(n_components=2, random_state=random_state)
    pcs = pca.fit_transform(StandardScaler().fit_transform(x))
    pca_fisher = fisher_ratio_1d(pcs[:, 0], y)
    return {
        "n_autoindotto": int(class_counts[AUTO_LABEL]),
        "n_indotto": int(class_counts[INDUCED_LABEL]),
        "cv_accuracy": float(accuracy_score(y, y_pred)),
        "cv_balanced_accuracy": float(balanced_accuracy_score(y, y_pred)),
        "cv_auc": float(roc_auc_score(y, y_score)),
        "pca_fisher_ratio": float(pca_fisher),
        "pca_var_1": float(pca.explained_variance_ratio_[0]),
        "pca_var_2": float(pca.explained_variance_ratio_[1]),
    }


def fisher_ratio_1d(values: np.ndarray, y: np.ndarray) -> float:
    a = values[y == AUTO_LABEL]
    b = values[y == INDUCED_LABEL]
    numerator = (a.mean() - b.mean()) ** 2
    denominator = a.var() + b.var() + 1e-9
    return float(numerator / denominator)


def plot_dataset(
    x: np.ndarray,
    y: np.ndarray,
    title: str,
    output_dir: Path,
    random_state: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    time = np.arange(x.shape[1])

    fig, ax = plt.subplots(figsize=(13, 5))
    for label, color in [(AUTO_LABEL, "tab:blue"), (INDUCED_LABEL, "tab:red")]:
        traces = x[y == label]
        mean = traces.mean(axis=0)
        std = traces.std(axis=0)
        ax.plot(time, mean, color=color, label=LABEL_NAMES[label])
        ax.fill_between(time, mean - std, mean + std, color=color, alpha=0.18)
    ax.set_title(title)
    ax.set_xlabel("campione")
    ax.set_ylabel("ampiezza normalizzata")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "mean_std.png", dpi=160)
    plt.close(fig)

    pcs = PCA(n_components=2, random_state=random_state).fit_transform(
        StandardScaler().fit_transform(x)
    )
    fig, ax = plt.subplots(figsize=(7, 6))
    for label, color in [(AUTO_LABEL, "tab:blue"), (INDUCED_LABEL, "tab:red")]:
        ax.scatter(
            pcs[y == label, 0],
            pcs[y == label, 1],
            s=18,
            alpha=0.75,
            color=color,
            label=LABEL_NAMES[label],
        )
    ax.set_title(f"PCA - {title}")
    ax.set_xlabel("PC1")
    ax.set_ylabel("PC2")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "pca.png", dpi=160)
    plt.close(fig)


def grouped_pairs(files: list[TraceFile]) -> dict[tuple[str, str], list[TraceFile]]:
    groups: dict[tuple[str, str], list[TraceFile]] = {}
    for trace_file in files:
        key = (trace_file.dataset, trace_file.channel)
        groups.setdefault(key, []).append(trace_file)
    return {
        key: value
        for key, value in groups.items()
        if {item.label for item in value} == {AUTO_LABEL, INDUCED_LABEL}
    }


def analyze_single_datasets(
    pairs: dict[tuple[str, str], list[TraceFile]],
    args: argparse.Namespace,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, dict[str, tuple[np.ndarray, np.ndarray]]]:
    metrics_rows: list[dict[str, object]] = []
    loaded: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    for (dataset, channel), files in sorted(pairs.items()):
        name = f"{dataset}__{channel}"
        x, y, file_stats = load_pair(
            files, args.normalization, args.max_samples_per_class, rng
        )
        loaded[name] = (x, y)
        metrics = separability_metrics(x, y, args.random_state)
        metrics_rows.append({"dataset": dataset, "channel": channel, **metrics})
        dataset_dir = args.output_dir / "single_datasets" / name
        dataset_dir.mkdir(parents=True, exist_ok=True)
        file_stats.to_csv(dataset_dir / "file_stats.csv", index=False)
        plot_dataset(x, y, name, dataset_dir, args.random_state)
        print(
            f"[OK] {name}: bal_acc={metrics['cv_balanced_accuracy']:.3f}, "
            f"auc={metrics['cv_auc']:.3f}, n={len(y)}"
        )

    metrics_df = pd.DataFrame(metrics_rows).sort_values(
        ["cv_balanced_accuracy", "cv_auc"], ascending=False
    )
    return metrics_df, loaded


def incremental_merge_report(
    loaded: dict[str, tuple[np.ndarray, np.ndarray]],
    ordered_names: list[str],
    random_state: int,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    selected: list[str] = []

    for name in ordered_names:
        selected.append(name)
        x_train = np.vstack([loaded[item][0] for item in selected])
        y_train = np.concatenate([loaded[item][1] for item in selected])
        model = make_model(random_state)
        model.fit(x_train, y_train)

        for test_name, (x_test, y_test) in loaded.items():
            y_pred = model.predict(x_test)
            decision = model.decision_function(x_test)
            rows.append(
                {
                    "merge_step": len(selected),
                    "train_datasets": " + ".join(selected),
                    "test_dataset": test_name,
                    "seen_in_train": test_name in selected,
                    "accuracy": float(accuracy_score(y_test, y_pred)),
                    "balanced_accuracy": float(
                        balanced_accuracy_score(y_test, y_pred)
                    ),
                    "auc": float(roc_auc_score(y_test, decision)),
                }
            )

    return pd.DataFrame(rows)


def save_final_lodo_report(
    loaded: dict[str, tuple[np.ndarray, np.ndarray]],
    output_dir: Path,
    random_state: int,
) -> None:
    rows: list[dict[str, object]] = []
    for test_name, (x_test, y_test) in loaded.items():
        train_names = [name for name in loaded if name != test_name]
        if not train_names:
            continue
        x_train = np.vstack([loaded[name][0] for name in train_names])
        y_train = np.concatenate([loaded[name][1] for name in train_names])
        model = make_model(random_state)
        model.fit(x_train, y_train)
        y_pred = model.predict(x_test)
        decision = model.decision_function(x_test)
        rows.append(
            {
                "held_out_dataset": test_name,
                "train_datasets": " + ".join(train_names),
                "accuracy": float(accuracy_score(y_test, y_pred)),
                "balanced_accuracy": float(balanced_accuracy_score(y_test, y_pred)),
                "auc": float(roc_auc_score(y_test, decision)),
                "report": classification_report(
                    y_test,
                    y_pred,
                    target_names=[LABEL_NAMES[0], LABEL_NAMES[1]],
                    zero_division=0,
                ),
            }
        )
    pd.DataFrame(rows).to_csv(output_dir / "leave_one_dataset_out.csv", index=False)


def write_summary(
    args: argparse.Namespace,
    files: list[TraceFile],
    single_metrics: pd.DataFrame,
    merge_metrics: pd.DataFrame,
) -> None:
    summary = {
        "datasets_dir": str(args.datasets_dir),
        "channel": args.channel,
        "normalization": args.normalization,
        "n_files": len(files),
        "n_single_pairs": len(single_metrics),
        "best_single_dataset": (
            single_metrics.iloc[0].to_dict() if not single_metrics.empty else None
        ),
        "merge_unseen_mean_balanced_accuracy_by_step": (
            merge_metrics[~merge_metrics["seen_in_train"]]
            .groupby("merge_step")["balanced_accuracy"]
            .mean()
            .to_dict()
            if not merge_metrics.empty
            else {}
        ),
    }
    with (args.output_dir / "summary.json").open("w") as f:
        json.dump(summary, f, indent=2)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(args.output_dir)
    rng = np.random.default_rng(args.random_state)

    files = discover_trace_files(args.datasets_dir, args.channel)
    if not files:
        raise SystemExit("Nessun CSV compatibile trovato.")

    pd.DataFrame([file.__dict__ for file in files]).to_csv(
        args.output_dir / "discovered_files.csv", index=False
    )

    pairs = grouped_pairs(files)
    if not pairs:
        raise SystemExit("Nessuna coppia autoindotto/indotto completa trovata.")

    single_metrics, loaded = analyze_single_datasets(pairs, args, rng)
    single_metrics.to_csv(args.output_dir / "single_dataset_metrics.csv", index=False)

    ordered_names = [
        f"{row.dataset}__{row.channel}"
        for row in single_metrics.itertuples(index=False)
    ]
    merge_metrics = incremental_merge_report(loaded, ordered_names, args.random_state)
    merge_metrics.to_csv(args.output_dir / "incremental_merge_metrics.csv", index=False)
    merge_metrics.groupby(["merge_step", "seen_in_train"], as_index=False).agg(
        mean_balanced_accuracy=("balanced_accuracy", "mean"),
        mean_auc=("auc", "mean"),
        n_tests=("test_dataset", "count"),
    ).to_csv(args.output_dir / "incremental_merge_summary.csv", index=False)
    save_final_lodo_report(loaded, args.output_dir, args.random_state)
    write_summary(args, files, single_metrics, merge_metrics)

    print("\nRisultati principali:")
    print(single_metrics.to_string(index=False))
    print(f"\nReport salvati in: {args.output_dir}")


if __name__ == "__main__":
    main()
