#!/usr/bin/env python3
"""Local-only GLT-MGA runner using the held-out-test protocol.

This wrapper loads ``GLT-MGA_test.py`` with the ablation base class, reuses its
model architecture and reporting flow, and changes only the local/global
component under study.  Global propagation/readout and gated fusion are
disabled; the classifier receives the local representation directly.
"""

import importlib.util
import sys
from pathlib import Path

import tensorflow as tf

import BasicModel_gated_test_ablataion as _ablation_basic


PROJECT_ROOT = Path(__file__).resolve().parent
_ORIGINAL_PATH = PROJECT_ROOT / "GLT-MGA_test.py"
_SPEC = importlib.util.spec_from_file_location(
    "_glt_test_protocol_original",
    str(_ORIGINAL_PATH),
)
_ORIGINAL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _ORIGINAL

# GLT-MGA_test.py imports this module by name.  Redirect only that import while
# loading it, so its TMGAModel inherits the test-protocol ablation base.
_previous_basic = sys.modules.get("BasicModel_gated_test")
sys.modules["BasicModel_gated_test"] = _ablation_basic
try:
    _SPEC.loader.exec_module(_ORIGINAL)
finally:
    if _previous_basic is None:
        sys.modules.pop("BasicModel_gated_test", None)
    else:
        sys.modules["BasicModel_gated_test"] = _previous_basic


class TMGAModel(_ORIGINAL.TMGAModel):
    """Reuse the local encoder/readout and skip all global computation."""

    def compute_local_global_graph_embeddings(self):
        local_node_states = self.compute_final_node_representations_for_stream(
            self.local_ph,
            scope="local_tmp",
            reuse=False,
        )

        self.ops["local_final_node_representations"] = local_node_states
        self.ops["global_final_node_representations"] = tf.zeros_like(
            local_node_states,
            name="disabled_global_node_representations",
        )

        local_mha_input = local_node_states
        if self.params.get("use_initial_feature_residual_mha", False):
            residual_scale = float(
                self.params.get("initial_feature_residual_scale", 1.0)
            )
            with tf.variable_scope("local_initial_feature_residual_mha"):
                local_init_proj = tf.layers.dense(
                    self.local_ph["initial_node_representation"],
                    self.params["hidden_size"],
                    activation=tf.nn.tanh,
                    use_bias=True,
                    name="init_feature_projection",
                )
                local_mha_input = (
                    local_mha_input + residual_scale * local_init_proj
                )

        h_local = self.multihead_attention_readout(
            local_mha_input,
            self.local_ph["graph_nodes_list"],
            self.placeholders["num_graphs"],
            scope="local_readout",
            reuse=False,
        )
        h_global_disabled = tf.zeros_like(
            h_local,
            name="disabled_global_readout",
        )
        return h_local, h_global_disabled


# Reuse the original CLI and train/test flow, but instantiate this subclass.
_ORIGINAL.TMGAModel = TMGAModel


def main():
    return _ORIGINAL.main()


if __name__ == "__main__":
    main()
