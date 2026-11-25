#!/usr/bin/env python3
"""Generate the Cortex-M55 model, preserving the compile-time backend guard."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stedgeai", default="stedgeai", help="ST Edge AI 2.2 / X-CUBE-AI 10.2 executable")
    parser.add_argument("--model", type=Path,
                        default=project / "NetworkTraining/training_outputs/final_model_int8.tflite")
    args = parser.parse_args()
    destination = project / "FSBL/X-CUBE-AI/App"
    with tempfile.TemporaryDirectory(prefix="bodyguard-cpu-") as temporary:
        output = Path(temporary) / "output"
        subprocess.run([
            args.stedgeai, "generate", "--target", "stm32n6", "--name", "network_cpu",
            "--c-api", "legacy", "--model", str(args.model.resolve()),
            "--workspace", str(Path(temporary) / "workspace"), "--output", str(output),
        ], check=True)
        for source in sorted(output.glob("network_cpu*")):
            if source.suffix not in {".c", ".h", ".txt", ".json"}:
                continue
            target = destination / source.name
            if source.suffix == ".c":
                target.write_bytes(
                    b'#include "bodyguard_ml_config.h"\n'
                    b'#if BODYGUARD_ML_BACKEND == BODYGUARD_ML_CPU\n'
                    + source.read_bytes() + b'\n#endif /* selected ML backend */\n')
            else:
                shutil.copyfile(source, target)
            print(target.relative_to(project))


if __name__ == "__main__":
    main()
