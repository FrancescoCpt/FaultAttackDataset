from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, classification_report, roc_auc_score
from sklearn.model_selection import train_test_split


LABEL_TO_ID = {"autoindotto": 0, "indotto": 1}
ID_TO_LABEL = {value: key for key, value in LABEL_TO_ID.items()}
SENSITIVE_DATASETS = {"Dataset_7Nani__ALIM", "Dataset_GPIOs__ALIM"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Training Keras Conv2D-only friendly per STM32N6, con export TFLite "
            "full-int8 e validazione leave-one-dataset-out."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            "elaboration_outputs/merged_aligned_crop_derivative_datasetstd.csv"
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("training_outputs"))
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--validation-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument(
        "--skip-lodo",
        action="store_true",
        help="Salta la valutazione leave-one-dataset-out e addestra solo il finale.",
    )
    parser.add_argument(
        "--final-validation-datasets",
        default="Dataset_7Nani__ALIM,Dataset_GPIOs__ALIM",
        help=(
            "Dataset tenuti come validation solo con --final-validation-mode "
            "domain-holdout. Default: 7Nani e GPIOs."
        ),
    )
    parser.add_argument(
        "--final-validation-mode",
        choices=("domain-stratified", "domain-holdout"),
        default="domain-stratified",
        help=(
            "domain-stratified mette campioni di ogni dataset sia in train sia in "
            "validation; domain-holdout lascia fuori interi dataset."
        ),
    )
    parser.add_argument(
        "--representative-samples",
        type=int,
        default=256,
        help="Numero massimo di tracce usate per calibrare la quantizzazione int8.",
    )
    return parser.parse_args()


def import_tensorflow():
    try:
        import tensorflow as tf
    except Exception as exc:
        raise SystemExit(
            "Impossibile importare TensorFlow/Keras. In questo ambiente sembra esserci "
            "un conflitto con tensorflow-metal. Prova a riallineare le versioni di "
            "tensorflow e tensorflow-metal, oppure disinstalla temporaneamente "
            "tensorflow-metal se non ti serve la GPU Apple.\n"
            f"Errore originale: {type(exc).__name__}: {exc}"
        ) from exc
    return tf


def set_reproducibility(seed: int) -> None:
    os.environ.setdefault("PYTHONHASHSEED", str(seed))
    random.seed(seed)
    np.random.seed(seed)


def load_dataset(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    df = pd.read_csv(path)
    missing = {"dataset", "label"} - set(df.columns)
    if missing:
        raise ValueError(f"Colonne mancanti nel dataset: {sorted(missing)}")

    dataset_names = df["dataset"].astype(str).to_numpy()
    labels = df["label"].astype(str).map(LABEL_TO_ID)
    if labels.isna().any():
        bad = sorted(df.loc[labels.isna(), "label"].astype(str).unique())
        raise ValueError(f"Label non riconosciute: {bad}")

    feature_columns = [col for col in df.columns if col not in {"dataset", "label"}]
    x = df[feature_columns].to_numpy(dtype=np.float32)
    y = labels.to_numpy(dtype=np.int64)
    return x, y, dataset_names, feature_columns


def to_conv2d_input(x: np.ndarray) -> np.ndarray:
    return x.reshape((x.shape[0], 1, x.shape[1], 1)).astype(np.float32)


def compute_domain_class_weights(y: np.ndarray, domains: np.ndarray) -> np.ndarray:
    weights = np.zeros(len(y), dtype=np.float32)
    unique_domains = np.unique(domains)
    for domain in unique_domains:
        domain_mask = domains == domain
        domain_count = int(domain_mask.sum())
        domain_weight = len(y) / (len(unique_domains) * domain_count)
        for label in np.unique(y):
            class_mask = domain_mask & (y == label)
            class_count = int(class_mask.sum())
            if class_count == 0:
                continue
            class_weight = domain_count / (len(np.unique(y)) * class_count)
            weights[class_mask] = domain_weight * class_weight
    return weights


def conv_width(input_width: int, strides: list[int]) -> int:
    width = input_width
    for stride in strides:
        width = int(np.ceil(width / stride))
    return width


def build_model(tf, input_length: int, learning_rate: float):
    # Shape: altezza 1, larghezza = campioni della traccia, canale 1.
    inputs = tf.keras.Input(shape=(1, input_length, 1), name="trace")
    x = tf.keras.layers.Conv2D(
        8, kernel_size=(1, 9), strides=(1, 2), padding="same", activation="relu"
    )(inputs)
    x = tf.keras.layers.Conv2D(
        16, kernel_size=(1, 7), strides=(1, 2), padding="same", activation="relu"
    )(x)
    x = tf.keras.layers.Conv2D(
        24, kernel_size=(1, 5), strides=(1, 2), padding="same", activation="relu"
    )(x)
    x = tf.keras.layers.Conv2D(
        32, kernel_size=(1, 3), strides=(1, 2), padding="same", activation="relu"
    )(x)

    reduced_width = conv_width(input_length, [2, 2, 2, 2])
    x = tf.keras.layers.Conv2D(
        2,
        kernel_size=(1, reduced_width),
        padding="valid",
        activation=None,
        name="class_logits_conv",
    )(x)
    x = tf.keras.layers.Flatten(name="flatten_logits")(x)
    outputs = tf.keras.layers.Activation("softmax", name="class_probabilities")(x)

    model = tf.keras.Model(inputs=inputs, outputs=outputs, name="reset_conv2d_classifier")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def train_once(
    tf,
    x_train: np.ndarray,
    y_train: np.ndarray,
    domain_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    args: argparse.Namespace,
    run_dir: Path,
):
    run_dir.mkdir(parents=True, exist_ok=True)
    model = build_model(tf, x_train.shape[1], args.learning_rate)
    weights = compute_domain_class_weights(y_train, domain_train)
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss",
            patience=12,
            restore_best_weights=True,
        ),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss",
            factor=0.5,
            patience=5,
            min_lr=1e-5,
        ),
    ]
    history = model.fit(
        to_conv2d_input(x_train),
        y_train,
        validation_data=(to_conv2d_input(x_val), y_val),
        sample_weight=weights,
        epochs=args.epochs,
        batch_size=args.batch_size,
        callbacks=callbacks,
        verbose=2,
    )

    probabilities = model.predict(to_conv2d_input(x_val), batch_size=args.batch_size)
    y_pred = probabilities.argmax(axis=1)
    metrics = {
        "balanced_accuracy": float(balanced_accuracy_score(y_val, y_pred)),
        "auc": float(roc_auc_score(y_val, probabilities[:, 1])),
        "report": classification_report(
            y_val,
            y_pred,
            target_names=[ID_TO_LABEL[0], ID_TO_LABEL[1]],
            zero_division=0,
        ),
    }
    pd.DataFrame(history.history).to_csv(run_dir / "history.csv", index=False)
    with (run_dir / "metrics.json").open("w") as f:
        json.dump(metrics, f, indent=2)
    return model, metrics


def run_leave_one_dataset_out(
    tf,
    x: np.ndarray,
    y: np.ndarray,
    domains: np.ndarray,
    args: argparse.Namespace,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for held_out in sorted(np.unique(domains)):
        print(f"\n[LODO] validation domain: {held_out}")
        val_mask = domains == held_out
        train_mask = ~val_mask
        _, metrics = train_once(
            tf,
            x[train_mask],
            y[train_mask],
            domains[train_mask],
            x[val_mask],
            y[val_mask],
            args,
            args.output_dir / "lodo" / held_out,
        )
        rows.append(
            {
                "held_out_dataset": held_out,
                "is_sensitive_dataset": held_out in SENSITIVE_DATASETS,
                "balanced_accuracy": metrics["balanced_accuracy"],
                "auc": metrics["auc"],
            }
        )
    scores = pd.DataFrame(rows)
    scores.to_csv(args.output_dir / "lodo_scores.csv", index=False)
    return scores


def split_final_train_validation(
    x: np.ndarray,
    y: np.ndarray,
    domains: np.ndarray,
    validation_domains: set[str],
    validation_size: float,
    random_state: int,
    mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    train_mask = np.zeros(len(y), dtype=bool)
    val_mask = np.zeros(len(y), dtype=bool)

    if mode == "domain-holdout":
        val_mask = np.isin(domains, sorted(validation_domains))
        if val_mask.any() and (~val_mask).any():
            return ~val_mask, val_mask

    if mode != "domain-stratified":
        raise ValueError(f"Modalita' validation non supportata: {mode}")

    rng = np.random.default_rng(random_state)
    for domain in sorted(np.unique(domains)):
        domain_indices = np.flatnonzero(domains == domain)
        domain_y = y[domain_indices]
        train_idx, val_idx = train_test_split(
            domain_indices,
            test_size=validation_size,
            random_state=int(rng.integers(0, 1_000_000)),
            stratify=domain_y,
        )
        train_mask[train_idx] = True
        val_mask[val_idx] = True
    return train_mask, val_mask


def final_per_domain_scores(
    model,
    x_val: np.ndarray,
    y_val: np.ndarray,
    domains_val: np.ndarray,
    batch_size: int,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    probabilities = model.predict(to_conv2d_input(x_val), batch_size=batch_size)
    y_pred = probabilities.argmax(axis=1)
    for domain in sorted(np.unique(domains_val)):
        mask = domains_val == domain
        rows.append(
            {
                "dataset": domain,
                "is_sensitive_dataset": domain in SENSITIVE_DATASETS,
                "n": int(mask.sum()),
                "balanced_accuracy": float(
                    balanced_accuracy_score(y_val[mask], y_pred[mask])
                ),
                "auc": float(roc_auc_score(y_val[mask], probabilities[mask, 1])),
            }
        )
    return pd.DataFrame(rows)


def representative_dataset(x: np.ndarray, max_samples: int):
    count = min(max_samples, len(x))
    for sample in to_conv2d_input(x[:count]):
        yield [sample[np.newaxis, ...].astype(np.float32)]


def export_full_int8_tflite(
    tf,
    model,
    representative_x: np.ndarray,
    output_path: Path,
    max_samples: int,
) -> None:
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = lambda: representative_dataset(
        representative_x, max_samples
    )
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    tflite_model = converter.convert()
    output_path.write_bytes(tflite_model)


def save_metadata(
    output_dir: Path,
    feature_columns: list[str],
    args: argparse.Namespace,
    model,
) -> None:
    metadata = {
        "labels": ID_TO_LABEL,
        "input_shape": model.input_shape,
        "output_shape": model.output_shape,
        "feature_count": len(feature_columns),
        "notes": [
            "Input Keras: (batch, 1, n_features, 1).",
            "Architettura senza Dense: classificatore finale implementato con Conv2D.",
            "Sample weights bilanciano dominio e classe per evitare che 7Nani o altri dataset dominino.",
            "Default finale: split stratificato per dominio, quindi tutti i domini sono visti in training.",
        ],
        "args": vars(args),
    }
    with (output_dir / "metadata.json").open("w") as f:
        json.dump(metadata, f, indent=2, default=str)


def main() -> None:
    args = parse_args()
    set_reproducibility(args.random_state)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tf = import_tensorflow()
    tf.keras.utils.set_random_seed(args.random_state)

    x, y, domains, feature_columns = load_dataset(args.dataset)
    print(f"Dataset: {args.dataset}")
    print(f"Tracce: {len(y)}, feature: {x.shape[1]}, domini: {len(np.unique(domains))}")
    print(pd.Series(domains).value_counts().to_string())

    if not args.skip_lodo:
        lodo_scores = run_leave_one_dataset_out(tf, x, y, domains, args)
        print("\nLeave-one-dataset-out:")
        print(lodo_scores.to_string(index=False))

    validation_domains = {
        item.strip()
        for item in args.final_validation_datasets.split(",")
        if item.strip()
    }
    train_mask, val_mask = split_final_train_validation(
        x,
        y,
        domains,
        validation_domains,
        args.validation_size,
        args.random_state,
        args.final_validation_mode,
    )
    print("\n[FINAL] validation domains:")
    print(pd.Series(domains[val_mask]).value_counts().to_string())

    final_model, final_metrics = train_once(
        tf,
        x[train_mask],
        y[train_mask],
        domains[train_mask],
        x[val_mask],
        y[val_mask],
        args,
        args.output_dir / "final",
    )
    per_domain = final_per_domain_scores(
        final_model,
        x[val_mask],
        y[val_mask],
        domains[val_mask],
        args.batch_size,
    )
    per_domain.to_csv(args.output_dir / "final_per_domain_scores.csv", index=False)
    final_model.save(args.output_dir / "final_model.keras")
    save_metadata(args.output_dir, feature_columns, args, final_model)
    export_full_int8_tflite(
        tf,
        final_model,
        x[train_mask],
        args.output_dir / "final_model_int8.tflite",
        args.representative_samples,
    )

    print("\nMetriche final validation:")
    print(json.dumps(final_metrics, indent=2))
    print(f"\nOutput salvati in: {args.output_dir}")


if __name__ == "__main__":
    main()
