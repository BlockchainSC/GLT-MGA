#!/usr/bin/env python3

"""
Usage:
   GLT-MGA.py [options]

Options:
    -h --help                Show this screen.
    --config-file FILE       Hyperparameter configuration file path (in JSON format).
    --config CONFIG          Hyperparameter configuration dictionary (in JSON format).
    --task TASK              Select task section from config file: reentrancy or timestamp.
    --log_dir DIR            Log dir name.
    --data_dir DIR           Data dir name.
    --random_seed seed       Random seed
    --thresholds threshold   threshold
    --restore FILE           File to restore weights from.
    -t                       output
"""
from __future__ import print_function
from typing import List, Tuple, Dict, Sequence, Any
from docopt import docopt
from collections import defaultdict, deque
from BasicModel import DetectModel
from utils import glorot_init
import matplotlib.pyplot as plt
from sklearn.metrics import roc_curve, auc, roc_auc_score
from time import perf_counter
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable


import numpy as np
import tensorflow as tf

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

print(tf.__version__)
# tf.enable_eager_execution()


def bfs_depths(outgoing_edges: Dict[int, Sequence[Tuple[int, int, int]]], seed: int) -> Dict[int, int]:
    node_depths = {seed: 0}
    q = deque([seed])
    while q:
        v = q.popleft()
        dv = node_depths[v]
        for (_, __, w) in outgoing_edges[v]:
            if w not in node_depths:
                node_depths[w] = dv + 1
                q.append(w)
    return node_depths


def bfs_depths_forward_only(adj: Dict[int, Sequence[int]], seed: int) -> Dict[int, int]:
    node_depths = {seed: 0}
    q = deque([seed])
    while q:
        v = q.popleft()
        dv = node_depths[v]
        for w in adj.get(v, []):
            if w not in node_depths:
                node_depths[w] = dv + 1
                q.append(w)
    return node_depths


class TMGAModel(DetectModel):
    def __init__(self, args):
        super().__init__(args)

    @classmethod
    def default_params(cls):
        params = dict(super().default_params())
        params.update({
            'num_nodes': 100000,
            'use_edge_bias': False,  # False or True

            'propagation_rounds': 2,
            # Task-aware final defaults are applied by DetectModel:
            # reentrancy=10 and timestamp dependency=15. RQ3 configs may
            # override this value explicitly (for example, Smax=20).
            'propagation_substeps': 15,

            'graph_rnn_cell': 'gru',  # gru or rnn
            'graph_rnn_activation': 'tanh',  # tanh or relu
            'graph_state_dropout_keep_prob': 0.75,  # [0.5, 1.0]

            'task_sample_ratios': {},           
            #  Multi-head attention readout params
            'readout_num_heads': 4,          # multi-head count
            'readout_attn_hidden': 128,      # attention MLP hidden size

        })
        return params

    def make_stream_placeholders(self, prefix):
        h_dim = self.params['hidden_size']
        ph = {}

        ph['initial_node_representation'] = tf.placeholder(
            tf.float32, [None, h_dim], name=prefix + '_node_features'
        )

        ph['initial_nodes'] = [
            tf.placeholder(tf.int32, [None], name="%s_initial_nodes_round%i" % (prefix, prop_round))
            for prop_round in range(self.params['propagation_rounds'])
        ]

        ph['sending_nodes'] = [[[tf.placeholder(
            tf.int32, [None],
            name="%s_sending_nodes_round%i_step%i_edgetyp%i" % (prefix, prop_round, step, edge_typ)
        )
            for edge_typ in range(self.num_edge_types)]
            for step in range(self.params['propagation_substeps'])]
            for prop_round in range(self.params['propagation_rounds'])]

        ph['msg_targets'] = [[tf.placeholder(
            tf.int32, [None],
            name="%s_msg_targets_nodes_round%i_step%i" % (prefix, prop_round, step)
        )
            for step in range(self.params['propagation_substeps'])]
            for prop_round in range(self.params['propagation_rounds'])]

        ph['receiving_nodes'] = [[tf.placeholder(
            tf.int32, [None],
            name="%s_receiving_nodes_round%i_step%i" % (prefix, prop_round, step)
        )
            for step in range(self.params['propagation_substeps'])]
            for prop_round in range(self.params['propagation_rounds'])]

        ph['receiving_node_num'] = [
            tf.placeholder(tf.int32, [self.params['propagation_substeps']],
                           name="%s_receiving_nodes_num_round%i" % (prefix, prop_round))
            for prop_round in range(self.params['propagation_rounds'])
        ]

        ph['graph_nodes_list'] = tf.placeholder(
            tf.int32, [None], name=prefix + '_graph_nodes_list'
        )

        return ph

    def prepare_specific_graph_model(self) -> None:       #graph model input + weights
        h_dim = self.params['hidden_size']

        self.local_ph = self.make_stream_placeholders("local")
        self.global_ph = self.make_stream_placeholders("global")

        for name, placeholder in self.local_ph.items():
            self.placeholders['local_' + name] = placeholder
            self.placeholders[name] = placeholder

        for name, placeholder in self.global_ph.items():
            self.placeholders['global_' + name] = placeholder

        self.placeholders['graph_state_keep_prob'] = tf.placeholder(tf.float32, None, name='graph_state_keep_prob')

    def make_graph_rnn_cell(self):
        h_dim = self.params['hidden_size']
        activation_name = self.params['graph_rnn_activation'].lower()

        if activation_name == 'tanh':
            activation_fun = tf.nn.tanh
        elif activation_name == 'relu':
            activation_fun = tf.nn.relu
        else:
            raise Exception("Unknown activation function type '%s'." % activation_name)

        cell_type = self.params['graph_rnn_cell'].lower()
        if cell_type == 'gru':
            cell = tf.nn.rnn_cell.GRUCell(h_dim, activation=activation_fun)
        elif cell_type == 'rnn':
            cell = tf.nn.rnn_cell.BasicRNNCell(h_dim, activation=activation_fun)
        else:
            raise Exception("Unknown RNN cell type '%s'." % cell_type)

        return tf.nn.rnn_cell.DropoutWrapper(
            cell,
            state_keep_prob=self.placeholders['graph_state_keep_prob']
        )

    def compute_final_node_representations_for_stream(self, ph, scope, reuse=False):
        with tf.variable_scope(scope, reuse=reuse):
            h_dim = self.params['hidden_size']
            edge_weights = [
                tf.get_variable(
                    'gnn_edge_weights_typ%i' % e_typ,
                    shape=[h_dim, h_dim],
                    initializer=tf.glorot_uniform_initializer()
                )
                for e_typ in range(self.num_edge_types)
            ]

            if self.params['use_edge_bias']:
                edge_biases = [
                    tf.get_variable(
                        'gnn_edge_biases_typ%i' % e_typ,
                        shape=[h_dim],
                        initializer=tf.zeros_initializer()
                    )
                    for e_typ in range(self.num_edge_types)
                ]
            else:
                edge_biases = None

            rnn_cell = self.make_graph_rnn_cell()
            cur_node_states = ph['initial_node_representation']

            for prop_round in range(self.params['propagation_rounds']):
                with tf.variable_scope('prop_round%i' % (prop_round,)):
                # ---- Declare and fill tensor arrays used in tf.while_loop:
                    sending_nodes_ta = tf.TensorArray(tf.int32,
                                                      infer_shape=False,
                                                      element_shape=[None],
                                                      size=self.params['propagation_substeps'] * self.num_edge_types,
                                                      name='sending_nodes')
                    msg_targets_ta = tf.TensorArray(tf.int32,
                                                    infer_shape=False,
                                                    element_shape=[None],
                                                    size=self.params['propagation_substeps'],
                                                    name='msg_targets')
                    receiving_nodes_ta = tf.TensorArray(tf.int32,
                                                        infer_shape=False,
                                                        element_shape=[None],
                                                        size=self.params['propagation_substeps'],
                                                        clear_after_read=False,
                                                        name='receiving_nodes')
                    receiving_node_num_ta = tf.TensorArray(tf.int32,
                                                           infer_shape=False,
                                                           element_shape=[],
                                                           size=self.params['propagation_substeps'],
                                                           clear_after_read=False,
                                                           name='receiving_nodes_num')

                    for step in range(self.params['propagation_substeps']):
                        for edge_typ in range(self.num_edge_types):
                            sending_nodes_ta = sending_nodes_ta.write(
                                step * self.num_edge_types + edge_typ,
                                ph['sending_nodes'][prop_round][step][edge_typ]
                            )
                        msg_targets_ta = msg_targets_ta.write(step, ph['msg_targets'][prop_round][step])
                        receiving_nodes_ta = receiving_nodes_ta.write(
                            step,
                            ph['receiving_nodes'][prop_round][step]
                        )
                    receiving_node_num_ta = receiving_node_num_ta.unstack(ph['receiving_node_num'][prop_round])
                    active_step_ids = tf.squeeze(
                        tf.where(tf.greater(ph['receiving_node_num'][prop_round], 0)),
                        axis=1
                    )
                    max_active_substep = tf.cond(
                        tf.greater(tf.size(active_step_ids), 0),
                        lambda: tf.reduce_max(active_step_ids) + 1,
                        lambda: tf.constant(0, dtype=tf.int64)
                    )

                    # Maintain round states as a dense tensor to avoid TensorArray read/write conflicts.
                    # Initialize all nodes from previous round state to remain robust when schedule has cycles.
                    new_node_states = cur_node_states

                    def do_substep(substep_id, new_node_states):
                        def run_substep():
                            # For each edge active in this substep, pull source state and transform:
                            sent_messages = []
                            for edge_typ in range(self.num_edge_types):
                                sending_states = tf.gather(
                                    new_node_states,
                                    sending_nodes_ta.read(substep_id * self.num_edge_types + edge_typ))
                                messages = tf.matmul(sending_states, edge_weights[edge_typ])
                                if edge_biases is not None:
                                    messages += edge_biases[edge_typ]
                                sent_messages.append(messages)

                            # Stack all edge messages and aggregate as sum for each receiving nodes:
                            sent_messages_cat = tf.concat(sent_messages, axis=0)
                            aggregated_received_messages = tf.unsorted_segment_sum(
                                sent_messages_cat,
                                msg_targets_ta.read(substep_id),
                                receiving_node_num_ta.read(substep_id)
                            )

                            # Collect old states for receiving nodes, and combine in RNN cell with incoming message
                            substep_receiving_nodes = receiving_nodes_ta.read(substep_id)
                            old_receiving_node_states = tf.gather(new_node_states, substep_receiving_nodes)
                            aggregated_received_messages.set_shape([None, self.params['hidden_size']])
                            old_receiving_node_states.set_shape([None, self.params['hidden_size']])
                            substep_new_node_states = \
                                rnn_cell(aggregated_received_messages, old_receiving_node_states)[1]
                            current_receiving_states = tf.gather(new_node_states, substep_receiving_nodes)
                            delta_states = substep_new_node_states - current_receiving_states
                            state_delta = tf.scatter_nd(
                                indices=tf.expand_dims(substep_receiving_nodes, 1),
                                updates=delta_states,
                                shape=tf.shape(new_node_states)
                            )
                            return new_node_states + state_delta

                        updated_states = tf.cond(
                            tf.greater(receiving_node_num_ta.read(substep_id), 0),
                            run_substep,
                            lambda: new_node_states
                        )
                        return substep_id + 1, updated_states

                    def is_done(substep_id, _):
                        return tf.less(tf.cast(substep_id, tf.int64), max_active_substep)

                    _, new_node_states = tf.while_loop(
                        cond=is_done,
                        body=do_substep,
                        loop_vars=[tf.constant(0), new_node_states],
                        shape_invariants=[tf.TensorShape([]), tf.TensorShape([None, self.params['hidden_size']])
                                          ]
                    )

                    cur_node_states = tf.identity(new_node_states, name="state_stack_round%i" % (prop_round,))

            return cur_node_states

    def compute_final_node_representations(self) -> tf.Tensor:     
        return self.compute_final_node_representations_for_stream(
            self.local_ph,
            scope="single_stream_tmp",
            reuse=False
        )

    def compute_local_global_graph_embeddings(self):
        local_node_states = self.compute_final_node_representations_for_stream(
            self.local_ph,
            scope="local_tmp",
            reuse=False
        )
        global_node_states = self.compute_final_node_representations_for_stream(
            self.global_ph,
            scope="global_tmp",
            reuse=False
        )

        self.ops['local_final_node_representations'] = local_node_states
        self.ops['global_final_node_representations'] = global_node_states

        local_mha_input = local_node_states
        global_mha_input = global_node_states

        if self.params.get("use_initial_feature_residual_mha", False):
            residual_scale = float(self.params.get("initial_feature_residual_scale", 1.0))

            with tf.variable_scope("local_initial_feature_residual_mha"):
                local_init_proj = tf.layers.dense(
                    self.local_ph['initial_node_representation'],
                    self.params['hidden_size'],
                    activation=tf.nn.tanh,
                    use_bias=True,
                    name="init_feature_projection"
                )
                local_mha_input = local_mha_input + residual_scale * local_init_proj

            with tf.variable_scope("global_initial_feature_residual_mha"):
                global_init_proj = tf.layers.dense(
                    self.global_ph['initial_node_representation'],
                    self.params['hidden_size'],
                    activation=tf.nn.tanh,
                    use_bias=True,
                    name="init_feature_projection"
                )
                global_mha_input = global_mha_input + residual_scale * global_init_proj

        h_local = self.multihead_attention_readout(
            local_mha_input,
            self.local_ph['graph_nodes_list'],
            self.placeholders['num_graphs'],
            scope="local_readout",
            reuse=False
        )
        h_global = self.multihead_attention_readout(
            global_mha_input,
            self.global_ph['graph_nodes_list'],
            self.placeholders['num_graphs'],
            scope="global_readout",
            reuse=False
        )

        return h_local, h_global
    

   
    def segment_softmax(self, scores, segment_ids, num_segments):
        """
        scores: [V, Hh]  (V=nodes, Hh=heads)
        segment_ids: [V] (graph id per node)
        returns: [V, Hh] softmax per graph
        """
        max_per_graph = tf.unsorted_segment_max(scores, segment_ids, num_segments)
        max_per_node = tf.gather(max_per_graph, segment_ids)

        exp_scores = tf.exp(scores - max_per_node)
        sum_per_graph = tf.unsorted_segment_sum(exp_scores, segment_ids, num_segments)
        sum_per_node = tf.gather(sum_per_graph, segment_ids)

        return exp_scores / (sum_per_node + 1e-9)
    
    #STEP-2: MHA

    def multihead_attention_readout(
        self,
        node_states,
        graph_nodes_list,
        num_graphs,
        scope="mh_attn_readout",
        reuse=False
    ):
        h = node_states                         # [V, H]
        heads = self.params['readout_num_heads']
        H = self.params['hidden_size']
        dk = H // heads

        with tf.variable_scope(scope, reuse=reuse):
            K = tf.layers.dense(h, heads * dk, activation=None, name="WK")  # [V, heads*dk]
            V = tf.layers.dense(h, heads * dk, activation=None, name="WV")  # [V, heads*dk]

            K = tf.reshape(K, [-1, heads, dk])  # [V, heads, dk]
            V = tf.reshape(V, [-1, heads, dk])  # [V, heads, dk]

            q = tf.get_variable("q", shape=[heads, dk],
                                initializer=tf.glorot_uniform_initializer())  # [heads, dk]

            scores = tf.reduce_sum(K * q[None, :, :], axis=-1) / tf.sqrt(tf.cast(dk, tf.float32))  # [V, heads]

            alpha = self.segment_softmax(
                scores,
                graph_nodes_list,
                num_graphs
            )  # [V, heads]

            a_exp = tf.expand_dims(alpha, axis=-1)  # [V, heads, 1]
            weighted = V * a_exp                    # [V, heads, dk]

            graph_emb = tf.unsorted_segment_sum(
                weighted,
                graph_nodes_list,
                num_graphs
            )  # [G, heads, dk]

            graph_emb = tf.reshape(graph_emb, [num_graphs, heads * dk])  # [G, heads*dk]

        return graph_emb
        


    def gated_regression(self, last_h, regression_gate, regression_transform):
        # last_h: [v x h]

        gate_input = tf.concat([last_h, self.placeholders['initial_node_representation']], axis=-1)  # [v x 2h]
        gated_outputs = tf.nn.sigmoid(regression_gate(gate_input)) * regression_transform(last_h)  # [v x 1] new_last_h

        # Sum up all nodes per graph
        graph_representations = tf.unsorted_segment_sum(data=gated_outputs,
                                                        segment_ids=self.placeholders['graph_nodes_list'],
                                                        num_segments=self.placeholders['num_graphs'])  # [g x 1]
        # output2=self.placeholders['graph_nodes_list']
        return tf.squeeze(
            graph_representations), graph_representations, self.placeholders['initial_node_representation']

    def _get_label(self, sample: Dict[str, Any], task_id: int = 0) -> float:
        targets = sample.get("targets", None)
        contract_name = sample.get("contract_name", "unknown")

        try:
            # Case A: scalar numeric target
            if isinstance(targets, (int, float, np.integer, np.floating)):
                return float(targets)

            # Case B: scalar string target, e.g. "0"/"1"
            if isinstance(targets, str):
                return float(int(targets.strip()))

            # Case C: list / nested list target
            if isinstance(targets, list):
                x = targets[task_id]
                if isinstance(x, list):
                    x = x[0]
                if isinstance(x, str):
                    return float(int(x.strip()))
                return float(x)

            # Case D: dict target, e.g. {"0":[0]} or {"0":0}
            if isinstance(targets, dict):
                x = targets.get(str(task_id), targets.get(task_id))
                if isinstance(x, list):
                    x = x[0]
                if isinstance(x, str):
                    return float(int(x.strip()))
                return float(x)
        except (IndexError, TypeError, ValueError):
            raise ValueError(
                "Invalid targets format for contract %s (task_id=%s): %r"
                % (contract_name, task_id, targets)
            )

        raise ValueError(
            "Unknown targets format for contract %s (task_id=%s): %r"
            % (contract_name, task_id, targets)
        )

    # Data preprocessing and chunking into minibatches:
    def _get_raw_stream(self, sample: Dict[str, Any], stream_name: str):
        graph_key = stream_name + "_graph"
        feature_key = stream_name + "_node_features"

        if graph_key in sample or feature_key in sample:
            return sample.get(graph_key, []), sample.get(feature_key, [])

        if stream_name == "local" and "graph" in sample and "node_features" in sample:
            return sample["graph"], sample["node_features"]

        if stream_name == "global" and "graph" in sample and "node_features" in sample:
            return [], []

        raise KeyError(
            "Gated model expects local_graph/local_node_features and "
            "global_graph/global_node_features. Missing stream '%s' in sample %s."
            % (stream_name, sample.get("contract_name", "unknown"))
        )

    def process_raw_graphs(self, raw_data: Sequence[Any], is_training_data: bool) -> Any:
        processed_graphs = []
        count = 0
        for d in tqdm(raw_data, desc="Preprocessing graphs", leave=False):
            count += 1
            # JSON file to model friendly local/global temporal propagation schedules.
            local_graph, local_node_features = self._get_raw_stream(d, "local")
            global_graph, global_node_features = self._get_raw_stream(d, "global")
            global_mask = float(d.get(
                "global_mask",
                1.0 if len(global_node_features) > 0 else 0.0
            ))
            if len(global_node_features) == 0:
                global_mask = 0.0

            t0 = perf_counter()
            local_prop_schedules = self.__graph_to_propagation_schedules(
                local_graph,
                num_nodes=len(local_node_features)
            )
            global_prop_schedules = self.__graph_to_propagation_schedules(
                global_graph,
                num_nodes=len(global_node_features)
            )
            t1 = perf_counter()

            dt = (t1 - t0)
            self.time_meter.add("graph_schedule_sec", dt, n=1)

            num_edges = len(local_graph) + len(global_graph)
            self.time_meter.add("graph_edges", num_edges, n=1)
            self.time_meter.add("local_graph_edges", len(local_graph), n=1)
            self.time_meter.add("global_graph_edges", len(global_graph), n=1)

            # true global avg per-edge time:
            self.time_meter.add("graph_schedule_sec_per_edge", dt, n=max(1, num_edges))

            processed_graphs.append({
                "local_init": local_node_features,
                "global_init": global_node_features,
                "local_prop_schedules": local_prop_schedules,
                "global_prop_schedules": global_prop_schedules,
                "global_mask": global_mask,
                # Compatibility aliases for local-only debug paths.
                "init": local_node_features,
                "prop_schedules": local_prop_schedules,
                "contract_key": d.get("contract_key"),
                "contract_n": d.get("contract_name", "unknown"),
                "target_values": [self._get_label(d, task_id) for task_id in self.params['task_ids']]
            })


        if is_training_data:
            # np.random.shuffle(processed_graphs)
            for task_id in self.params['task_ids']:
                task_sample_ratio = self.params.get('task_sample_ratios', {}).get(str(task_id))
                if task_sample_ratio is not None:
                    ex_to_sample = int(len(processed_graphs) * task_sample_ratio)
                    for ex_id in range(ex_to_sample, len(processed_graphs)):
                        processed_graphs[ex_id]['target_values'][task_id] = None

        return processed_graphs, count

    def _pad_node_features(self, node_features):
        features = np.asarray(node_features, dtype=np.float32)
        if features.size == 0:
            return np.zeros((0, self.params['hidden_size']), dtype=np.float32)
        if features.ndim == 1:
            features = np.reshape(features, (1, -1))

        feature_dim = int(features.shape[1])
        if feature_dim > self.params['hidden_size']:
            raise ValueError(
                "Node feature dimension %d exceeds hidden_size %d."
                % (feature_dim, self.params['hidden_size'])
            )

        if feature_dim < self.params['hidden_size']:
            features = np.pad(
                features,
                ((0, 0), (0, self.params['hidden_size'] - feature_dim)),
                'constant'
            )

        return features

    def _concat_int_chunks(self, chunks):
        return np.concatenate(chunks, axis=0) if len(chunks) > 0 else np.empty(shape=(0,), dtype=np.int32)

    def pack_stream_graphs(self, batch_graphs, stream_name):
        ph = self.local_ph if stream_name == "local" else self.global_ph
        init_key = stream_name + "_init"
        schedule_key = stream_name + "_prop_schedules"

        batch_node_features = []
        batch_graph_nodes_list = []
        node_offset = 0

        batch_initial_nodes = [[] for _ in range(self.params['propagation_rounds'])]
        batch_sending_nodes = [[[[] for _ in range(self.num_edge_types)]
                                for _ in range(self.params['propagation_substeps'])]
                               for _ in range(self.params['propagation_rounds'])]
        batch_msg_targets = [[[[] for _ in range(self.num_edge_types)]
                              for _ in range(self.params['propagation_substeps'])]
                             for _ in range(self.params['propagation_rounds'])]
        batch_receiving_nodes = [[[] for _ in range(self.params['propagation_substeps'])]
                                 for _ in range(self.params['propagation_rounds'])]
        batch_receiving_node_num = [[0 for _ in range(self.params['propagation_substeps'])]
                                    for _ in range(self.params['propagation_rounds'])]
        msg_target_offsets = [[[0 for _ in range(self.num_edge_types)]
                               for _ in range(self.params['propagation_substeps'])]
                              for _ in range(self.params['propagation_rounds'])]

        for graph_idx, cur_graph in enumerate(batch_graphs):
            padded_features = self._pad_node_features(cur_graph[init_key])
            num_nodes_in_graph = int(padded_features.shape[0])
            batch_node_features.append(padded_features)
            batch_graph_nodes_list.append(
                np.full(shape=[num_nodes_in_graph], fill_value=graph_idx, dtype=np.int32)
            )

            for prop_round in range(self.params['propagation_rounds']):
                cur_prop_schedule = cur_graph[schedule_key][prop_round]
                (graph_initial_nodes,
                 graph_sending_nodes,
                 graph_msg_targets,
                 graph_recv_nodes) = cur_prop_schedule
                batch_initial_nodes[prop_round].append(graph_initial_nodes + node_offset)

                for step in range(self.params['propagation_substeps']):
                    if step >= len(graph_sending_nodes):
                        break

                    for e_typ in range(self.num_edge_types):
                        batch_sending_nodes[prop_round][step][e_typ].append(
                            graph_sending_nodes[step][e_typ] + node_offset
                        )
                        batch_msg_targets[prop_round][step][e_typ].append(
                            graph_msg_targets[step][e_typ] + msg_target_offsets[prop_round][step][e_typ]
                        )
                        if len(graph_msg_targets[step][e_typ]) > 0:
                            msg_target_offsets[prop_round][step][e_typ] += max(graph_msg_targets[step][e_typ]) + 1

                    batch_receiving_nodes[prop_round][step].append(graph_recv_nodes[step] + node_offset)
                    batch_receiving_node_num[prop_round][step] += len(graph_recv_nodes[step])

            node_offset += num_nodes_in_graph

        feed_dict = {
            ph['initial_node_representation']: (
                np.concatenate(batch_node_features, axis=0)
                if len(batch_node_features) > 0
                else np.zeros((0, self.params['hidden_size']), dtype=np.float32)
            ),
            ph['graph_nodes_list']: self._concat_int_chunks(batch_graph_nodes_list),
        }

        for prop_round in range(self.params['propagation_rounds']):
            feed_dict[ph['initial_nodes'][prop_round]] = self._concat_int_chunks(batch_initial_nodes[prop_round])
            for step in range(self.params['propagation_substeps']):
                msg_targets = []
                for edge_typ in range(self.num_edge_types):
                    raw_senders = batch_sending_nodes[prop_round][step][edge_typ]
                    feed_dict[ph['sending_nodes'][prop_round][step][edge_typ]] = self._concat_int_chunks(raw_senders)

                    raw_targets = batch_msg_targets[prop_round][step][edge_typ]
                    msg_targets.extend(self._concat_int_chunks(raw_targets))

                feed_dict[ph['msg_targets'][prop_round][step]] = np.array(msg_targets, dtype=np.int32)
                raw_recvs = batch_receiving_nodes[prop_round][step]
                feed_dict[ph['receiving_nodes'][prop_round][step]] = self._concat_int_chunks(raw_recvs)

            feed_dict[ph['receiving_node_num'][prop_round]] = np.array(
                batch_receiving_node_num[prop_round],
                dtype=np.int32
            )

        return feed_dict

    def __tensorise_edge_sequence(self, edges, num_nodes=None) \
            -> Tuple[np.ndarray, List[List[np.ndarray]], List[List[np.ndarray]], List[np.ndarray]]:
        sending_nodes = []  # type: List[List[np.ndarray]]
        msg_targets = []  # type: List[List[np.ndarray]]
        receiving_nodes = []  # type: List[np.ndarray]
        all_nodes = set()
        if num_nodes is not None:
            all_nodes.update(range(int(num_nodes)))
        for step_edges in edges:
            msg_targets_uniq = set(w for (_, __, w) in step_edges)
            recv_nodes = list(sorted(msg_targets_uniq))
            recv_nodes_to_uniq_id = {v: i for (i, v) in enumerate(recv_nodes)}

            sending_nodes_in_step = []
            msg_targets_in_step = []
            for target_e_typ in range(self.num_edge_types):
                sending_nodes_in_step.append(
                    np.array([v for (v, e_typ, _) in step_edges if e_typ == target_e_typ], dtype=np.int32))
                msg_targets_in_step.append(
                    np.array([recv_nodes_to_uniq_id[w] for (_, e_typ, w) in step_edges if e_typ == target_e_typ],
                             dtype=np.int32))
            msg_targets.append(msg_targets_in_step)
            sending_nodes.append(sending_nodes_in_step)
            receiving_nodes.append(np.array(recv_nodes, dtype=np.int32))
            all_nodes.update(v for (v, _, __) in step_edges)
            all_nodes.update(w for (_, __, w) in step_edges)

        all_updated_nodes = set()
        all_updated_nodes.update(v for step_receiving_nodes in receiving_nodes
                                 for v in step_receiving_nodes)
        initial_nodes = list(sorted(all_nodes - all_updated_nodes))

        return np.array(initial_nodes, dtype=np.int32), sending_nodes, msg_targets, receiving_nodes
#TMP Schedule generation from graph
    def __graph_to_propagation_schedules(self, graph, num_nodes=None) \
            -> List[Tuple[np.ndarray, List[List[np.ndarray]], List[List[np.ndarray]], List[np.ndarray]]]:
        if len(graph) == 0:
            n = int(num_nodes) if num_nodes is not None else 0
            schedules = []
            for _ in range(int(self.params['propagation_rounds'])):
                schedules.append((np.arange(n, dtype=np.int32), [], [], []))
            return schedules

        node_degree = defaultdict(lambda: 0)
        outgoing_edges = defaultdict(lambda: [])
        num_fwd_edge_types = self.num_edge_types if self.params['tie_fwd_bkwd'] else (self.num_edge_types // 2)
        # Build bidirectional adjacency for message passing and depth estimation.
        for (v, typ, w) in graph:
            node_degree[v] += 1
            node_degree[w] += 1
            edge_bwd_typ = typ if self.params['tie_fwd_bkwd'] else (num_fwd_edge_types + typ)
            outgoing_edges[v].append((v, typ, w))
            outgoing_edges[w].append((w, edge_bwd_typ, v))

        # Deterministic seed diversification across rounds (hub-first).
        seed_order = [node for (node, _) in sorted(node_degree.items(), key=lambda t: (-t[1], t[0]))]
        tensorised_prop_schedules = []
        for prop_round in range(int(self.params[
                                        'propagation_rounds'])):  # propagation_rounds=1 #for prop_round in range(int(self.params['propagation_rounds'] / 2)):
            dag_seed = seed_order[prop_round % len(seed_order)]
            node_depths = bfs_depths(outgoing_edges, dag_seed)

            # Compute schedule horizon from reached BFS nodes first.
            max_depth = (
                min(max(node_depths.values()) + 1, self.params['propagation_substeps'])
                if node_depths else 1
            )
            cap = max_depth - 1

            # Disconnected/unreached nodes: place them at cap depth (last schedulable depth).
            if num_nodes is not None:
                for n in range(int(num_nodes)):
                    if n not in node_depths:
                        node_depths[n] = cap
            else:
                for n in node_degree.keys():
                    if n not in node_depths:
                        node_depths[n] = cap

            # Now split edge into forward/backward sets, by using their depths.
            # Intuitively, a nodes with depth h will get updated in step h.
            max_depth = min(max(node_depths.values()) + 1, self.params['propagation_substeps'])
            fwd_pass_edges = [[] for _ in range(max_depth)]
            bwd_pass_edges = [[] for _ in range(max_depth)]
            cap = max_depth - 1
            for (v, typ, w) in graph:
                edge_bwd_type = typ if self.params['tie_fwd_bkwd'] else (num_fwd_edge_types + typ)
                v_depth = min(node_depths[v], cap)
                w_depth = min(node_depths[w], cap)
                if v_depth < w_depth:  # "Forward": We are going up in depth:
                    fwd_pass_edges[w_depth - 1].append((v, typ, w))
                    bwd_pass_edges[-v_depth - 1].append((w, edge_bwd_type, v))
                elif w_depth < v_depth:  # "Backward": We are going down in depth
                    fwd_pass_edges[v_depth - 1].append((w, edge_bwd_type, v))
                    bwd_pass_edges[-w_depth - 1].append((v, typ, w))
                else:
                    if v == w:
                        continue
                    # Equal-depth tie-break for deterministic scheduling.
                    if v < w:
                        fwd_pass_edges[max(0, w_depth - 1)].append((v, typ, w))
                        bwd_pass_edges[-max(0, v_depth) - 1].append((w, edge_bwd_type, v))
                    else:
                        fwd_pass_edges[max(0, v_depth - 1)].append((w, edge_bwd_type, v))
                        bwd_pass_edges[-max(0, w_depth) - 1].append((v, typ, w))

            combined_edges = []
            num_steps = max_depth
            for step in range(num_steps):
                f = fwd_pass_edges[step]
                b = bwd_pass_edges[num_steps - 1 - step]
                combined_edges.append(f + b)

            tensorised_prop_schedules.append(self.__tensorise_edge_sequence(combined_edges, num_nodes=num_nodes))

        return tensorised_prop_schedules

    def make_minibatch_iterator(self, data: Any, is_training: bool):
        """Create minibatches by flattening graphs into a single one with multiple disconnected components."""
        if is_training:
            np.random.shuffle(data)

        dropout_keep_prob = self.params['graph_state_dropout_keep_prob'] if is_training else 1.

        # Pack until we cannot fit more graphs in the batch
        num_graphs = 0
        while num_graphs < len(data):
            num_graphs_in_batch = 0
            batch_graphs = []
            batch_target_task_values = []
            batch_target_task_mask = []
            batch_global_mask = []
            local_node_offset = 0
            global_node_offset = 0

            while num_graphs < len(data):
                cur_graph = data[num_graphs]
                local_nodes = len(cur_graph['local_init'])
                global_nodes = len(cur_graph['global_init'])

                local_would_fit = local_node_offset + local_nodes < self.params['num_nodes']
                global_would_fit = global_node_offset + global_nodes < self.params['num_nodes']

                if num_graphs_in_batch > 0 and not (local_would_fit and global_would_fit):
                    break
                if num_graphs_in_batch == 0 and not (local_would_fit and global_would_fit):
                    raise ValueError("Single graph too large for batch: increase num_nodes or trim graphs.")

                target_task_values = []
                target_task_mask = []
                for target_val in cur_graph['target_values']:
                    if target_val is None:  # This is one of the examples we didn't sample...
                        target_task_values.append(0.)
                        target_task_mask.append(0.)
                    else:
                        target_task_values.append(target_val)
                        target_task_mask.append(1.)
                batch_target_task_values.append(target_task_values)
                batch_target_task_mask.append(target_task_mask)
                batch_global_mask.append([float(cur_graph.get("global_mask", 0.0))])
                batch_graphs.append(cur_graph)

                num_graphs += 1
                num_graphs_in_batch += 1
                local_node_offset += local_nodes
                global_node_offset += global_nodes

            if num_graphs_in_batch == 0:
                raise ValueError("Single graph too large for batch: increase num_nodes or trim graphs.")

            batch_feed_dict = {
                self.placeholders['target_values']: np.transpose(batch_target_task_values, axes=[1, 0]),
                self.placeholders['target_mask']: np.transpose(batch_target_task_mask, axes=[1, 0]),
                self.placeholders['num_graphs']: num_graphs_in_batch,
                self.placeholders['graph_state_keep_prob']: dropout_keep_prob,
                self.placeholders['global_mask']: np.array(batch_global_mask, dtype=np.float32),
            }

            batch_feed_dict.update(self.pack_stream_graphs(batch_graphs, "local"))
            batch_feed_dict.update(self.pack_stream_graphs(batch_graphs, "global"))

            if os.environ.get("LTMGA_CHECK_INVARIANTS", "0") == "1" and not hasattr(self, "_checked_once"):
                try:
                    self.check_batch_invariants(batch_feed_dict)
                    print("[LTMGA] schedule invariant check passed on first batch.")
                except AssertionError:
                    print("[LTMGA][WARN] schedule invariant check failed on first batch; training continues.")
                self._checked_once = True

            yield batch_feed_dict

    def check_batch_invariants(self, batch_feed_dict):
        for prop_round in range(self.params['propagation_rounds']):
            initialised_nodes = set()
            initialised_nodes.update(batch_feed_dict[self.placeholders['initial_nodes'][prop_round]])
            for step in range(self.params['propagation_substeps']):
                sending_nodes = set()
                for edge_typ in range(self.num_edge_types):
                    sending_nodes.update(
                        batch_feed_dict[self.placeholders['sending_nodes'][prop_round][step][edge_typ]])
                for v in sending_nodes:
                    assert v in initialised_nodes

                recv_nodes = batch_feed_dict[self.placeholders['receiving_nodes'][prop_round][step]]
                for v in recv_nodes:
                    assert v not in initialised_nodes
                initialised_nodes.update(recv_nodes)


def main():
    args = docopt(__doc__)
    model = TMGAModel(args)
    model.train()



if __name__ == "__main__":
    main()
