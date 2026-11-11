from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "mplconfig"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    roc_auc_score,
)

from training import ID_TO_LABEL, LABEL_TO_ID, load_dataset, split_final_train_validation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida un modello Keras/TFLite e genera la matrice di confusione."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            "elaboration_outputs/merged_aligned_crop_derivative_datasetstd.csv"
        ),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("training_outputs/final_model.keras"),
        help="Path a modello .keras oppure .tflite.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("confusion_outputs"))
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--validation-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument(
        "--validation-mode",
        choices=("domain-stratified", "domain-holdout", "all"),
        default="domain-stratified",
    )
    parser.add_argument(
        "--validation-datasets",
        default="Dataset_7Nani__ALIM,Dataset_GPIOs__ALIM",
        help="Usato solo con --validation-mode domain-holdout.",
    )
    parser.add_argument(
        "--per-dataset",
        action="store_true",
        help="Salva anche matrici di confusione separate per ogni dataset.",
    )
    return parser.parse_args()


def import_tensorflow():
    try:
        import tensorflow as tf
    except Exception as exc:
        raise SystemExit(
            "Impossibile importare TensorFlow. Serve per caricare .keras o eseguire "
            ".tflite. Nel tuo ambiente potrebbe esserci ancora il conflitto con "
            "tensorflow-metal.\n"
            f"Errore originale: {type(exc).__name__}: {exc}"
        ) from exc
    return tf


def to_conv2d_input(x: np.ndarray) -> np.ndarray:
    return x.reshape((x.shape[0], 1, x.shape[1], 1)).astype(np.float32)


def select_validation(
    x: np.ndarray,
    y: np.ndarray,
    domains: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if args.validation_mode == "all":
        return x, y, domains

    validation_domains = {
        item.strip()
        for item in args.validation_datasets.split(",")
        if item.strip()
    }
    train_mask, val_mask = split_final_train_validation(
        x,
        y,
        domains,
        validation_domains,
        args.validation_size,
        args.random_state,
        args.validation_mode,
    )
    del train_mask
    return x[val_mask], y[val_mask], domains[val_mask]


def predict_keras(tf, model_path: Path, x: np.ndarray, batch_size: int) -> np.ndarray:
    model = tf.keras.models.load_model(model_path)
    return model.predict(to_conv2d_input(x), batch_size=batch_size, verbose=0)


def quantize_input(interpreter, x_batch: np.ndarray) -> np.ndarray:
    input_details = interpreter.get_input_details()[0]
    scale, zero_point = input_details["quantization"]
    dtype = input_details["dtype"]
    if dtype == np.float32:
        return x_batch.astype(np.float32)
    if scale == 0:
        raise ValueError("Quantizzazione input TFLite non valida: scale=0.")
    q = np.round(x_batch / scale + zero_point)
    info = np.iinfo(dtype)
    return np.clip(q, info.min, info.max).astype(dtype)


def dequantize_output(interpreter, output: np.ndarray) -> np.ndarray:
    output_details = interpreter.get_output_details()[0]
    scale, zero_point = output_details["quantization"]
    if output_details["dtype"] == np.float32 or scale == 0:
        return output.astype(np.float32)
    return (output.astype(np.float32) - zero_point) * scale


def predict_tflite(tf, model_path: Path, x: np.ndarray) -> np.ndarray:
    interpreter = tf.lite.Interpreter(model_path=str(model_path))
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()[0]
    expected_shape = input_details["shape"].tolist()

    probabilities: list[np.ndarray] = []
    for row in to_conv2d_input(x):
        sample = row[np.newaxis, ...]
        if len(expected_shape) == 4 and expected_shape[0] not in (1, -1):
            raise ValueError(f"Batch size TFLite inatteso: {expected_shape}")
        interpreter.set_tensor(
            input_details["index"], quantize_input(interpreter, sample)
        )
        interpreter.invoke()
        output = interpreter.get_tensor(output_details["index"])
        probabilities.append(dequantize_output(interpreter, output)[0])
    return np.vstack(probabilities)


def compute_metrics(y_true: np.ndarray, probabilities: np.ndarray) -> dict[str, object]:
    y_pred = probabilities.argmax(axis=1)
    labels = [0, 1]
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    metrics: dict[str, object] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "confusion_matrix": cm.tolist(),
        "classification_report": classification_report(
            y_true,
            y_pred,
            labels=labels,
            target_names=[ID_TO_LABEL[0], ID_TO_LABEL[1]],
            zero_division=0,
        ),
    }
    if len(np.unique(y_true)) == 2:
        metrics["auc"] = float(roc_auc_score(y_true, probabilities[:, 1]))
    else:
        metrics["auc"] = None
    return metrics


def save_confusion_matrix_csv(cm: np.ndarray, output_path: Path) -> None:
    labels = [ID_TO_LABEL[0], ID_TO_LABEL[1]]
    df = pd.DataFrame(cm, index=[f"true_{x}" for x in labels])
    df.columns = [f"pred_{x}" for x in labels]
    df.to_csv(output_path)


def plot_confusion_matrix(cm: np.ndarray, title: str, output_path: Path) -> None:
    labels = [ID_TO_LABEL[0], ID_TO_LABEL[1]]
    fig, ax = plt.subplots(figsize=(5.5, 4.8))
    image = ax.imshow(cm, cmap="Blues")
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    ax.set_xticks(np.arange(len(labels)))
    ax.set_yticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.set_yticklabels(labels)
    ax.set_xlabel("Predetta")
    ax.set_ylabel("Reale")
    ax.set_title(title)

    threshold = cm.max() / 2 if cm.size else 0
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(
                j,
                i,
                str(cm[i, j]),
                ha="center",
                va="center",
                color="white" if cm[i, j] > threshold else "black",
            )
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def save_predictions(
    y_true: np.ndarray,
    domains: np.ndarray,
    probabilities: np.ndarray,
    output_path: Path,
) -> None:
    y_pred = probabilities.argmax(axis=1)
    df = pd.DataFrame(
        {
            "dataset": domains,
            "true_label": [ID_TO_LABEL[int(value)] for value in y_true],
            "pred_label": [ID_TO_LABEL[int(value)] for value in y_pred],
            "score_autoindotto": probabilities[:, 0],
            "score_indotto": probabilities[:, 1],
        }
    )
    df.to_csv(output_path, index=False)


def save_per_dataset_outputs(
    y_true: np.ndarray,
    domains: np.ndarray,
    probabilities: np.ndarray,
    output_dir: Path,
) -> None:
    rows: list[dict[str, object]] = []
    for dataset in sorted(np.unique(domains)):
        mask = domains == dataset
        metrics = compute_metrics(y_true[mask], probabilities[mask])
        cm = np.array(metrics["confusion_matrix"])
        safe_name = dataset.replace("/", "_").replace(" ", "_")
        save_confusion_matrix_csv(cm, output_dir / f"{safe_name}_confusion_matrix.csv")
        plot_confusion_matrix(
            cm,
            f"Matrice di confusione - {dataset}",
            output_dir / f"{safe_name}_confusion_matrix.png",
        )
        rows.append(
            {
                "dataset": dataset,
                "n": int(mask.sum()),
                "accuracy": metrics["accuracy"],
                "balanced_accuracy": metrics["balanced_accuracy"],
                "auc": metrics["auc"],
            }
        )
    pd.DataFrame(rows).to_csv(output_dir / "per_dataset_metrics.csv", index=False)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tf = import_tensorflow()

    x, y, domains, _ = load_dataset(args.dataset)
    x_val, y_val, domains_val = select_validation(x, y, domains, args)

    suffix = args.model.suffix.lower()
    if suffix == ".tflite":
        probabilities = predict_tflite(tf, args.model, x_val)
    else:
        probabilities = predict_keras(tf, args.model, x_val, args.batch_size)

    metrics = compute_metrics(y_val, probabilities)
    cm = np.array(metrics["confusion_matrix"])
    save_confusion_matrix_csv(cm, args.output_dir / "confusion_matrix.csv")
    plot_confusion_matrix(
        cm,
        f"Matrice di confusione - {args.model.name}",
        args.output_dir / "confusion_matrix.png",
    )
    save_predictions(y_val, domains_val, probabilities, args.output_dir / "predictions.csv")
    with (args.output_dir / "metrics.json").open("w") as f:
        json.dump(metrics, f, indent=2)

    if args.per_dataset:
        save_per_dataset_outputs(y_val, domains_val, probabilities, args.output_dir)

    print(f"Validazione su {len(y_val)} tracce")
    print(f"Model: {args.model}")
    print(f"Accuracy: {metrics['accuracy']:.4f}")
    print(f"Balanced accuracy: {metrics['balanced_accuracy']:.4f}")
    if metrics["auc"] is not None:
        print(f"AUC: {metrics['auc']:.4f}")
    print("\nMatrice di confusione:")
    print(pd.read_csv(args.output_dir / "confusion_matrix.csv", index_col=0).to_string())
    print(f"\nOutput salvati in: {args.output_dir}")


if __name__ == "__main__":
    main()
