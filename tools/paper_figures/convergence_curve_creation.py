import argparse
import json
import csv
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

# ============================================================
# GLT-MGA Fig. 9: Training/Validation Convergence Curves
# Publication-ready, IEEE single-column friendly
#
# IMPORTANT:
# Set the TWO history paths below to the files that contain the
# epoch-wise training/validation loss and accuracy values.
# Supported input formats: JSON and CSV.
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[2]

# -----------------------------------------------------------------
# Current paper histories:
# Reentrancy = E500/P50/R2/S10/M4; Timestamp = R3/S15/M4.
# -----------------------------------------------------------------
REENTRANCY_HISTORY = BASE_DIR / "results/main/reentrancy/reentrancy_hist.json"

TIMESTAMP_HISTORY = BASE_DIR / "results/main/timestamp/timestamp_hist.json"

OUT_DIR = BASE_DIR / "generated_figures/figure9/reentrancy"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# Publication settings
# ============================================================

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 8.5,
    "axes.titlesize": 9.3,
    "axes.labelsize": 8.6,
    "xtick.labelsize": 7.8,
    "ytick.labelsize": 7.8,
    "legend.fontsize": 7.7,
    "axes.linewidth": 0.7,
    "lines.linewidth": 1.35,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
})

# Solid lines plus different markers keep training/validation distinguishable.
LOSS_TRAIN_STYLE = dict(
    color="#2f6db0", linestyle="-", linewidth=1.45,
)
LOSS_VALID_STYLE = dict(
    color="#c62828", linestyle="--", linewidth=1.45,
)
ACC_TRAIN_STYLE = dict(
    color="#2f8f2f", linestyle="-", linewidth=1.45,
)
ACC_VALID_STYLE = dict(
    color="#e68a00", linestyle="--", linewidth=1.45,
)

# ============================================================
# Flexible history loader
# ============================================================

def _norm_key(key):
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


ALIASES = {
    "epoch": {
        "epoch", "epochs", "step", "steps",
    },
    "train_loss": {
        "trainloss", "trainingloss", "losstrain",
    },
    "val_loss": {
        "valloss", "validationloss", "validloss", "devloss", "losssval",
    },
    "train_acc": {
        "trainacc", "trainaccuracy", "trainingacc", "trainingaccuracy",
        "accuracytrain",
    },
    "val_acc": {
        "valacc", "valaccuracy", "validationacc", "validationaccuracy",
        "validacc", "validaccuracy", "devacc", "devaccuracy",
    },
}


def _find_array_in_dict(obj, aliases):
    """Recursively find a list/array whose normalized key matches aliases."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _norm_key(key) in aliases and isinstance(value, (list, tuple)):
                return np.asarray(value, dtype=float)

        # Search common nested history dictionaries recursively.
        for value in obj.values():
            result = _find_array_in_dict(value, aliases)
            if result is not None:
                return result

    return None


def _from_row_records(records, aliases):
    """Extract a numeric column from a list of dictionaries."""
    if not records or not isinstance(records[0], dict):
        return None

    key_map = {_norm_key(k): k for k in records[0].keys()}
    matched_key = None

    for alias in aliases:
        if alias in key_map:
            matched_key = key_map[alias]
            break

    if matched_key is None:
        return None

    values = []
    for row in records:
        if matched_key in row and row[matched_key] not in ("", None):
            values.append(float(row[matched_key]))

    return np.asarray(values, dtype=float) if values else None


def load_history(path):
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"\nHistory file not found:\n{path}\n"
            "Set REENTRANCY_HISTORY and TIMESTAMP_HISTORY to the actual "
            "epoch-wise history files produced by your training runs."
        )

    suffix = path.suffix.lower()

    if suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Format A: list of per-epoch dictionaries
        if isinstance(data, list) and data and isinstance(data[0], dict):
            epoch = _from_row_records(data, ALIASES["epoch"])
            train_loss = _from_row_records(data, ALIASES["train_loss"])
            val_loss = _from_row_records(data, ALIASES["val_loss"])
            train_acc = _from_row_records(data, ALIASES["train_acc"])
            val_acc = _from_row_records(data, ALIASES["val_acc"])

        # Format B: dictionary containing arrays, possibly nested
        elif isinstance(data, dict):
            epoch = _find_array_in_dict(data, ALIASES["epoch"])
            train_loss = _find_array_in_dict(data, ALIASES["train_loss"])
            val_loss = _find_array_in_dict(data, ALIASES["val_loss"])
            train_acc = _find_array_in_dict(data, ALIASES["train_acc"])
            val_acc = _find_array_in_dict(data, ALIASES["val_acc"])
        else:
            raise ValueError(f"Unsupported JSON structure in {path}")

    elif suffix == ".csv":
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            data = list(reader)

        epoch = _from_row_records(data, ALIASES["epoch"])
        train_loss = _from_row_records(data, ALIASES["train_loss"])
        val_loss = _from_row_records(data, ALIASES["val_loss"])
        train_acc = _from_row_records(data, ALIASES["train_acc"])
        val_acc = _from_row_records(data, ALIASES["val_acc"])

    else:
        raise ValueError(
            f"Unsupported history file type: {suffix}. Use JSON or CSV."
        )

    required = {
        "train_loss": train_loss,
        "val_loss": val_loss,
        "train_acc": train_acc,
        "val_acc": val_acc,
    }

    missing = [name for name, arr in required.items() if arr is None]
    if missing:
        raise KeyError(
            f"Could not find these required series in {path}: {missing}\n"
            "Expected names similar to train_loss, val_loss, "
            "train_accuracy, val_accuracy."
        )

    lengths = [len(v) for v in required.values()]
    if epoch is not None:
        lengths.append(len(epoch))

    n = min(lengths)

    train_loss = train_loss[:n]
    val_loss = val_loss[:n]
    train_acc = train_acc[:n]
    val_acc = val_acc[:n]

    if epoch is None:
        # Epochs are conventionally shown as 1..N in the paper figures.
        epoch = np.arange(1, n + 1, dtype=float)
    else:
        epoch = epoch[:n]

    # Automatically convert [0,1] accuracy to percentage.
    if np.nanmax(train_acc) <= 1.5:
        train_acc = train_acc * 100.0
    if np.nanmax(val_acc) <= 1.5:
        val_acc = val_acc * 100.0

    return {
        "epoch": epoch,
        "train_loss": train_loss,
        "val_loss": val_loss,
        "train_acc": train_acc,
        "val_acc": val_acc,
    }


# ============================================================
# Plot helpers
# ============================================================

def clean_axis(ax):
    """Calm journal-style axis treatment."""
    ax.grid(
        True,
        which="major",
        linestyle=":",
        linewidth=0.55,
        alpha=0.55,
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(direction="out", length=2.5, width=0.65)


def set_epoch_axis(ax, epoch):
    """Use readable ticks for both short and extended training runs."""
    first_epoch = int(np.floor(float(epoch[0])))
    last_epoch = int(np.ceil(float(epoch[-1])))
    ax.set_xlim(first_epoch, last_epoch)

    if last_epoch <= 120:
        ticks = [1, 20, 40, 60, 80, 100]
        ticks = [tick for tick in ticks if tick <= last_epoch]
    else:
        ticks = [1, 100, 200, 300, 400]
        ticks = [tick for tick in ticks if tick <= last_epoch]

    # Drop any "nice" tick that sits too close to the final epoch so its
    # label does not overlap with the last-epoch label (e.g. 300 vs 303).
    min_gap = max(10, 0.05 * (last_epoch - first_epoch))
    ticks = [tick for tick in ticks if abs(last_epoch - tick) >= min_gap]

    if last_epoch not in ticks:
        ticks.append(last_epoch)

    ax.set_xticks(ticks)
    ax.set_xlabel("Epoch")


def mark_best_validation_loss(ax, history, label_y=None):
    """Mark the checkpoint selected by validation-loss early stopping."""
    epoch = np.asarray(history["epoch"], dtype=float)
    validation_loss = np.asarray(history["val_loss"], dtype=float)
    best_index = int(np.nanargmin(validation_loss))
    best_epoch = int(epoch[best_index])
    best_loss = float(validation_loss[best_index])

    ax.axvline(
        best_epoch,
        color="#555555",
        linestyle=":",
        linewidth=0.95,
        alpha=0.75,
        zorder=1,
    )
    label_point = (
        (best_epoch, float(label_y))
        if label_y is not None
        else (best_epoch, best_loss)
    )
    ax.annotate(
        f"Best epoch: {best_epoch}",
        xy=label_point,
        # Keep the Timestamp placement unchanged; lift the data-anchored
        # Reentrancy label well clear of the noisy curve band around it.
        xytext=(-5, 0 if label_y is not None else 24),
        textcoords="offset points",
        ha="right",
        va="center" if label_y is not None else "bottom",
        fontsize=8.0,
        color="#333333",
        bbox=dict(
            facecolor="white",
            edgecolor="none",
            alpha=0.88,
            pad=1.4,
        ),
    )


def plot_task_row(ax_loss, ax_acc, history, task_name):
    epoch = history["epoch"]

    # ---- Loss panel ----
    ax_loss.plot(epoch, history["train_loss"], label="Training", **LOSS_TRAIN_STYLE)
    ax_loss.plot(epoch, history["val_loss"], label="Validation", **LOSS_VALID_STYLE)
    ax_loss.set_title("Loss", pad=3)
    ax_loss.set_ylabel("Loss")
    ax_loss.set_ylim(bottom=0)
    set_epoch_axis(ax_loss, epoch)
    mark_best_validation_loss(
        ax_loss,
        history,
        label_y=0.045 if task_name.lower().startswith("timestamp") else None,
    )
    clean_axis(ax_loss)

    # ---- Accuracy panel ----
    ax_acc.plot(epoch, history["train_acc"], label="Training", **ACC_TRAIN_STYLE)
    ax_acc.plot(epoch, history["val_acc"], label="Validation", **ACC_VALID_STYLE)
    ax_acc.set_title("Accuracy", pad=3)
    ax_acc.set_ylabel("Accuracy (%)")
    set_epoch_axis(ax_acc, epoch)

    # Use data-driven limits, but keep a sensible visible margin.
    ymin = min(np.nanmin(history["train_acc"]), np.nanmin(history["val_acc"]))
    ymax = max(np.nanmax(history["train_acc"]), np.nanmax(history["val_acc"]))
    margin = max(2.0, 0.08 * (ymax - ymin if ymax > ymin else 10.0))
    ax_acc.set_ylim(max(0, ymin - margin), min(100, ymax + margin))
    clean_axis(ax_acc)

    # Small task name above the row, not inside either plot.
    return task_name


def add_shared_legend(fig, axes):
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper right",
        bbox_to_anchor=(0.985, 0.985),
        ncol=2,
        frameon=False,
        handlelength=2.4,
        columnspacing=1.2,
    )


# ============================================================
# Outputs
# ============================================================

def save_single_metric(history_path, task_name, metric, basename):
    """Save one spacious loss or accuracy panel for side-by-side placement."""
    history = load_history(history_path)
    epoch = history["epoch"]

    if metric == "accuracy":
        train_values = history["train_acc"]
        validation_values = history["val_acc"]
        ylabel = "Accuracy (%)"
        title_metric = "Accuracy"
        legend_location = "lower right"
        train_style = ACC_TRAIN_STYLE
        validation_style = ACC_VALID_STYLE
    elif metric == "loss":
        train_values = history["train_loss"]
        validation_values = history["val_loss"]
        ylabel = "Loss"
        title_metric = "Loss"
        legend_location = "upper right"
        train_style = LOSS_TRAIN_STYLE
        validation_style = LOSS_VALID_STYLE
    else:
        raise ValueError(f"Unsupported metric: {metric}")

    display_task_name = (
        "Timestamp" if task_name.lower().startswith("timestamp") else task_name
    )

    # A separate 3.35-inch panel remains readable when placed beside its pair.
    fig, ax = plt.subplots(figsize=(3.50, 2.85), dpi=200)
    ax.plot(epoch, train_values, label="Training", **train_style)
    ax.plot(epoch, validation_values, label="Validation", **validation_style)
    ax.set_title(
        f"{title_metric} over Epochs ({display_task_name})",
        pad=7,
        fontsize=10.2,
    )
    ax.set_ylabel(ylabel, fontsize=9.2)
    ax.tick_params(axis="both", labelsize=8.2)
    if metric == "loss":
        ax.set_ylim(bottom=0)
    set_epoch_axis(ax, epoch)

    if metric == "loss":
        mark_best_validation_loss(
            ax,
            history,
            label_y=0.045 if task_name.lower().startswith("timestamp") else None,
        )

    if metric == "accuracy":
        ymin = min(np.nanmin(train_values), np.nanmin(validation_values))
        ymax = max(np.nanmax(train_values), np.nanmax(validation_values))
        margin = max(2.0, 0.08 * (ymax - ymin if ymax > ymin else 10.0))
        ax.set_ylim(max(0, ymin - margin), min(100, ymax + margin))

    clean_axis(ax)
    ax.legend(
        loc=legend_location,
        frameon=True,
        framealpha=0.9,
        borderpad=0.35,
        handlelength=2.2,
        fontsize=8.2,
    )
    fig.subplots_adjust(left=0.16, right=0.985, bottom=0.19, top=0.82)

    pdf_path = OUT_DIR / f"{basename}.pdf"
    png_path = OUT_DIR / f"{basename}.png"
    fig.savefig(pdf_path)
    fig.savefig(png_path, dpi=600)
    plt.close(fig)

    print(f"Saved: {pdf_path}")
    print(f"Saved: {png_path}")

def save_single_task(history_path, task_name, basename):
    """
    Creates ONE PDF containing loss and accuracy side-by-side.
    Both panels are guaranteed to have identical physical height.
    Designed to be inserted at width=\\columnwidth.
    """
    history = load_history(history_path)

    # IEEE single-column width is approximately 3.5 in.
    # Using one canvas prevents unequal subfigure heights.
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(3.50, 2.30),
        dpi=150,
    )

    plot_task_row(axes[0], axes[1], history, task_name)
    add_shared_legend(fig, axes)
    display_task_name = (
        "Timestamp" if task_name.lower().startswith("timestamp") else task_name
    )
    fig.text(
        0.125,
        0.985,
        display_task_name,
        ha="left",
        va="top",
        fontsize=9.3,
    )

    # Explicit margins produce equal panel geometry in both tasks.
    fig.subplots_adjust(
        left=0.125,
        right=0.985,
        bottom=0.245,
        top=0.72,
        wspace=0.36,
    )

    pdf_path = OUT_DIR / f"{basename}.pdf"
    png_path = OUT_DIR / f"{basename}.png"

    fig.savefig(pdf_path)
    fig.savefig(png_path, dpi=600)
    plt.close(fig)

    print(f"Saved: {pdf_path}")
    print(f"Saved: {png_path}")


def save_complete_fig9(re_history_path, ts_history_path):
    """
    Optional all-in-one Fig. 9:
      top row    = Reentrancy [Loss | Accuracy]
      bottom row = Timestamp  [Loss | Accuracy]

    This is useful if you want one single-column PDF for the entire figure.
    """
    re = load_history(re_history_path)
    ts = load_history(ts_history_path)

    fig, axes = plt.subplots(
        2,
        2,
        figsize=(3.50, 4.25),
        dpi=150,
    )

    plot_task_row(axes[0, 0], axes[0, 1], re, "Reentrancy")
    plot_task_row(axes[1, 0], axes[1, 1], ts, "Timestamp Dependency")

    # Shared legend for all four panels.
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.992),
        ncol=2,
        frameon=False,
        handlelength=2.4,
        columnspacing=1.6,
    )

    # Row labels placed outside the axes so plot sizes remain identical.
    fig.text(
        0.5, 0.855,
        "(a) Reentrancy",
        ha="center", va="center",
        fontsize=8.5,
    )
    fig.text(
        0.5, 0.405,
        "(b) Timestamp Dependency",
        ha="center", va="center",
        fontsize=8.5,
    )

    fig.subplots_adjust(
        left=0.125,
        right=0.985,
        bottom=0.10,
        top=0.80,
        wspace=0.42,
        hspace=0.86,
    )

    pdf_path = OUT_DIR / "fig9_convergence_stacked_singlecol.pdf"
    png_path = OUT_DIR / "fig9_convergence_stacked_singlecol.png"

    fig.savefig(pdf_path)
    fig.savefig(png_path, dpi=600)
    plt.close(fig)

    print(f"Saved: {pdf_path}")
    print(f"Saved: {png_path}")


# ============================================================
# Main
# ============================================================

def generate_task_curves(history_path, task_name, prefix, output_dir):
    """Generate the clean single-task curves in a task-specific folder."""
    global OUT_DIR
    OUT_DIR = Path(output_dir)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    save_single_metric(
        history_path,
        task_name,
        "accuracy",
        f"{prefix}_acc_curve",
    )
    save_single_metric(
        history_path,
        task_name,
        "loss",
        f"{prefix}_loss_curve",
    )
    save_single_task(
        history_path,
        task_name,
        f"{prefix}_convergence_singlecol",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate publication-ready GLT-MGA convergence curves."
    )
    parser.add_argument(
        "--task",
        choices=("reentrancy", "timestamp", "both"),
        default="both",
        help="Which history to plot; default: both.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for the selected task.",
    )
    args = parser.parse_args()

    re_output = (
        args.output_dir
        if args.task == "reentrancy" and args.output_dir
        else BASE_DIR / "generated_figures/figure9/reentrancy"
    )
    ts_output = (
        args.output_dir
        if args.task == "timestamp" and args.output_dir
        else BASE_DIR / "generated_figures/figure9/timestamp"
    )

    if args.task in ("reentrancy", "both"):
        generate_task_curves(
            REENTRANCY_HISTORY,
            "Reentrancy",
            "reentrancy",
            re_output,
        )

    if args.task in ("timestamp", "both"):
        generate_task_curves(
            TIMESTAMP_HISTORY,
            "Timestamp Dependency",
            "timestamp",
            ts_output,
        )

    if args.task == "both":
        combined_output = args.output_dir or (
            BASE_DIR / "generated_figures/figure9"
        )
        global OUT_DIR
        OUT_DIR = Path(combined_output)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        save_complete_fig9(REENTRANCY_HISTORY, TIMESTAMP_HISTORY)

    print("\nDone. Files saved in:")
    if args.task == "reentrancy":
        print(re_output)
    elif args.task == "timestamp":
        print(ts_output)
    else:
        print(re_output)
        print(ts_output)


if __name__ == "__main__":
    main()
