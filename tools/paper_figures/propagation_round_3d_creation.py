import json
import os
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  # register 3D projection

# -----------------------------
# Global figure/font settings
# -----------------------------
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 11,
    "axes.labelsize": 9.5,
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "legend.fontsize": 8.5,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})

# -----------------------------
# Input result directories
# -----------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULT_DIR = os.path.join(BASE_DIR, "results", "rq3")
REENTRANCY_JSON = os.path.join(RESULT_DIR, "reentrancy", "propagation_rounds")
TIMESTAMP_JSON = os.path.join(RESULT_DIR, "timestamp", "propagation_rounds")

# -----------------------------
# Output directory
# -----------------------------
OUT_DIR = os.path.join(BASE_DIR, "generated_figures", "figure8")
os.makedirs(OUT_DIR, exist_ok=True)

ROUND_COLORS = {
    1: "#2f6db0",
    2: "#e67e22",
    3: "#2ca25f",
}
ROUND_HATCHES = {
    1: "...",
    2: "///",
    3: "xxx",
}


def load_report(json_path):
    # Latest RQ3 outputs store one summary JSON per round. Assemble the same
    # aggregate rows expected by the original plotting functions.
    if os.path.isdir(json_path):
        task = os.path.basename(os.path.dirname(os.path.normpath(json_path))).lower()
        if task not in ("reentrancy", "timestamp"):
            raise ValueError(f"Cannot infer task from result directory: {json_path}")
        expected_substeps = 10 if task == "reentrancy" else 15
        summary_name = f"summary_{task}.json"
        rows = {}

        for root, _, files in os.walk(json_path):
            if summary_name not in files:
                continue

            summary_path = os.path.join(root, summary_name)
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)

            config = summary.get("experiment_config", {})
            if int(config.get("random_seed", -1)) != 9930:
                continue
            if int(config.get("propagation_substeps", -1)) != expected_substeps:
                continue
            if int(config.get("readout_num_heads", -1)) != 4:
                continue

            rounds = int(config.get("propagation_rounds", -1))
            if rounds not in (1, 2, 3):
                continue
            if rounds in rows:
                raise ValueError(
                    f"Duplicate R{rounds} {task} summaries: "
                    f"{rows[rounds]['source']} and {summary_path}"
                )

            rows[rounds] = {
                "task": task,
                "rounds": rounds,
                "accuracy": summary["accuracy"],
                "precision": summary["precision"],
                "recall": summary["recall"],
                "f1": summary["f1"],
                "auc": summary["auc"],
                "source": summary_path,
            }

        missing = sorted(set((1, 2, 3)) - set(rows))
        if missing:
            raise ValueError(
                f"Missing {task} propagation-round summaries for {missing} "
                f"under {json_path}"
            )
        agg = [rows[rounds] for rounds in (1, 2, 3)]
    else:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        agg = sorted(data["aggregate"], key=lambda x: x["rounds"])

    metrics = ["accuracy", "precision", "recall", "f1", "auc"]
    metric_labels = ["Accuracy", "Precision", "Recall", "F1", "AUC"]

    rounds = [f"R{item['rounds']}" for item in agg]
    values = np.array([[item[m] * 100.0 for m in metrics] for item in agg])  # shape = (3, 5)

    task = agg[0]["task"].strip().lower()
    if task == "reentrancy":
        title = "Reentrancy"
    elif task == "timestamp":
        title = "Timestamp Dependency"
    else:
        title = task.replace("_", " ").title()

    return title, rounds, metric_labels, values


def add_round_legend(ax, round_labels):
    handles = []
    for i, r in enumerate(round_labels):
        round_value = int(r.lstrip("R"))
        handles.append(
            Patch(
                facecolor=ROUND_COLORS[round_value],
                edgecolor="#333333",
                linewidth=0.35,
                hatch=ROUND_HATCHES[round_value],
                label=r,
            )
        )
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 1.08), ncol=3, frameon=False)


def plot_3d_bars(ax, title, round_labels, metric_labels, values, show_legend=True, panel_tag=None):
    """
    values shape: (num_rounds, num_metrics)
    """
    num_rounds = len(round_labels)
    num_metrics = len(metric_labels)

    # Draw one collection per round so each round has its own hatch pattern.
    for i in range(num_rounds):
        round_value = int(round_labels[i].lstrip("R"))
        collection = ax.bar3d(
            np.full(num_metrics, i, dtype=float),
            np.arange(num_metrics, dtype=float),
            np.zeros(num_metrics),
            np.full(num_metrics, 0.52),
            np.full(num_metrics, 0.50),
            values[i, :],
            shade=True,
            color=ROUND_COLORS[round_value],
            edgecolor="#333333",
            linewidth=0.30,
            alpha=0.90,
        )
        collection.set_hatch(ROUND_HATCHES[round_value])

    # view and aspect
    ax.view_init(elev=24, azim=-58)
    try:
        ax.set_box_aspect((1.15, 1.65, 1.0))
    except Exception:
        pass

    # ticks at center of bars
    ax.set_xticks(np.arange(num_rounds) + 0.26)
    ax.set_yticks(np.arange(num_metrics) + 0.25)
    ax.set_xticklabels(round_labels)
    ax.set_yticklabels(metric_labels)

    ax.set_zlim(0, 100)
    ax.set_zticks(np.arange(0, 101, 20))

    ax.set_xlabel("Rounds", labelpad=8)
    ax.set_ylabel("Metric", labelpad=10)
    ax.set_zlabel("Score (%)", labelpad=6)

    main_title = title if panel_tag is None else f"{panel_tag} {title}"
    ax.set_title(main_title, pad=12, fontweight="normal")

    # cleaner panes
    ax.xaxis.pane.set_alpha(0.08)
    ax.yaxis.pane.set_alpha(0.08)
    ax.zaxis.pane.set_alpha(0.00)

    # axis line spacing
    ax.tick_params(axis='x', pad=1)
    ax.tick_params(axis='y', pad=1)
    ax.tick_params(axis='z', pad=1)

    if show_legend:
        add_round_legend(ax, round_labels)


def save_single_chart(json_path, out_pdf, out_png):
    title, rounds, metrics, values = load_report(json_path)

    # single-column friendly size
    fig = plt.figure(figsize=(3.45, 4.20))
    ax = fig.add_subplot(111, projection="3d")

    plot_3d_bars(
        ax=ax,
        title=title,
        round_labels=rounds,
        metric_labels=metrics,
        values=values,
        show_legend=True,
        panel_tag=None
    )

    fig.tight_layout(pad=0.45)
    fig.savefig(out_pdf, bbox_inches="tight", pad_inches=0.03)
    fig.savefig(out_png, dpi=400, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def save_stacked_single_column(re_json, ts_json, out_pdf, out_png):
    # Best if you want both tasks together but still readable in one IEEE column
    re_title, re_rounds, re_metrics, re_values = load_report(re_json)
    ts_title, ts_rounds, ts_metrics, ts_values = load_report(ts_json)

    fig = plt.figure(figsize=(4.35, 7.50))

    ax1 = fig.add_subplot(211, projection="3d")
    plot_3d_bars(
        ax=ax1,
        title=re_title,
        round_labels=re_rounds,
        metric_labels=re_metrics,
        values=re_values,
        show_legend=True,
        panel_tag="(a)"
    )

    ax2 = fig.add_subplot(212, projection="3d")
    plot_3d_bars(
        ax=ax2,
        title=ts_title,
        round_labels=ts_rounds,
        metric_labels=ts_metrics,
        values=ts_values,
        show_legend=True,
        panel_tag="(b)"
    )

    fig.tight_layout(pad=0.55, h_pad=1.0)
    fig.savefig(out_pdf, bbox_inches="tight", pad_inches=0.03)
    fig.savefig(out_png, dpi=400, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def save_side_by_side(re_json, ts_json, out_pdf, out_png):
    # Good for preview / wide layout / two-column layout
    # Not the best choice for strict single-column readability
    re_title, re_rounds, re_metrics, re_values = load_report(re_json)
    ts_title, ts_rounds, ts_metrics, ts_values = load_report(ts_json)

    fig = plt.figure(figsize=(8.40, 3.80))

    ax1 = fig.add_subplot(121, projection="3d")
    plot_3d_bars(
        ax=ax1,
        title=re_title,
        round_labels=re_rounds,
        metric_labels=re_metrics,
        values=re_values,
        show_legend=False,
        panel_tag="(a)"
    )

    ax2 = fig.add_subplot(122, projection="3d")
    plot_3d_bars(
        ax=ax2,
        title=ts_title,
        round_labels=ts_rounds,
        metric_labels=ts_metrics,
        values=ts_values,
        show_legend=False,
        panel_tag="(b)"
    )

    # one shared legend for the whole figure
    handles = [
        Patch(
            facecolor=ROUND_COLORS[i],
            edgecolor="#333333",
            linewidth=0.35,
            hatch=ROUND_HATCHES[i],
            label=f"R{i}",
        )
        for i in range(1, 4)
    ]
    fig.legend(handles=handles, loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 1.01))

    fig.tight_layout(rect=[0, 0, 1, 0.95], pad=0.4, w_pad=0.9)
    fig.savefig(out_pdf, bbox_inches="tight", pad_inches=0.03)
    fig.savefig(out_png, dpi=400, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def main():
    save_single_chart(
        REENTRANCY_JSON,
        os.path.join(OUT_DIR, "propagation_round_3d_reentrancy_singlecol.pdf"),
        os.path.join(OUT_DIR, "propagation_round_3d_reentrancy_singlecol.png")
    )

    save_single_chart(
        TIMESTAMP_JSON,
        os.path.join(OUT_DIR, "propagation_round_3d_timestamp_singlecol.pdf"),
        os.path.join(OUT_DIR, "propagation_round_3d_timestamp_singlecol.png")
    )

    save_stacked_single_column(
        REENTRANCY_JSON,
        TIMESTAMP_JSON,
        os.path.join(OUT_DIR, "propagation_round_3d_stacked_singlecol.pdf"),
        os.path.join(OUT_DIR, "propagation_round_3d_stacked_singlecol.png")
    )

    save_side_by_side(
        REENTRANCY_JSON,
        TIMESTAMP_JSON,
        os.path.join(OUT_DIR, "propagation_round_3d_side_by_side.pdf"),
        os.path.join(OUT_DIR, "propagation_round_3d_side_by_side.png")
    )

    print("Done. Files saved in:", OUT_DIR)


if __name__ == "__main__":
    main()
