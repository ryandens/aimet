# /usr/bin/env python3.5
# -*- mode: python -*-
# =============================================================================
#  @@-COPYRIGHT-START-@@
#
#  Copyright (c) 2019, Qualcomm Innovation Center, Inc. All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#
#  1. Redistributions of source code must retain the above copyright notice,
#     this list of conditions and the following disclaimer.
#
#  2. Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions and the following disclaimer in the documentation
#     and/or other materials provided with the distribution.
#
#  3. Neither the name of the copyright holder nor the names of its contributors
#     may be used to endorse or promote products derived from this software
#     without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
#  ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
#  LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
#  CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
#  SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
#  INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
#  CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
#  ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
#  POSSIBILITY OF SUCH DAMAGE.
#
#  SPDX-License-Identifier: BSD-3-Clause
#
#  @@-COPYRIGHT-END-@@
# =============================================================================

import unittest
import copy
import shutil
from decimal import Decimal
import numpy as np

import tensorflow as tf

from aimet_torch.winnow.winnow_utils import to_numpy
from aimet_common.utils import AimetLogger
from aimet_common.defs import CostMetric, LayerCompRatioPair

from aimet_tensorflow import layer_database as lad
from aimet_tensorflow.layer_database import LayerDatabase
from aimet_tensorflow.examples import mnist_tf_model
from aimet_tensorflow.svd_spiltter import SpatialSvdModuleSplitter
from aimet_tensorflow.svd_pruner import SpatialSvdPruner
from aimet_tensorflow.utils.common import get_succeeding_bias_op
logger = AimetLogger.get_area_logger(AimetLogger.LogAreas.Test)


class TestSpatialSvdLayerSplit(unittest.TestCase):

    def test_split_layer(self):
        """ test the output after and before the split_module call"""

        num_examples = 2000
        g = tf.Graph()

        with g.as_default():

            inp_tensor = tf.get_variable('inp_tensor', shape=[num_examples, 20, 5, 5],
                                         initializer=tf.random_normal_initializer())
            filter_tensor = tf.get_variable('filter_tensor', shape=[5, 5, 20, 50],
                                            initializer=tf.random_normal_initializer())

            conv1 = tf.nn.conv2d(input=inp_tensor, filter=filter_tensor, strides=[1, 1, 1, 1], padding='VALID',
                                 data_format="NCHW", name='Conv2D_1')

            bias_tensor = tf.get_variable('bias_tensor', shape=[50], initializer=tf.random_normal_initializer())

            bias = tf.nn.bias_add(value=conv1, bias=bias_tensor, data_format="NCHW")

            init = tf.global_variables_initializer()

        orig_conv_op = g.get_operation_by_name('Conv2D_1')

        # output shape in NCHW format
        shape = orig_conv_op.outputs[0].get_shape().as_list()
        self.assertEqual(shape, [num_examples, 50, 1, 1])

        sess = tf.Session(graph=g)

        # initialize all the variables in the graph
        sess.run(init)

        orig_conv_output = to_numpy(sess.run(orig_conv_op.outputs[0]))

        layer1 = lad.Layer(model=sess, op=orig_conv_op, output_shape=shape)

        split_conv_op1, split_conv_op2 = SpatialSvdModuleSplitter.split_module(layer=layer1, rank=100)

        split_conv_output = to_numpy(sess.run(split_conv_op2.outputs[0]))

        self.assertTrue(np.allclose(split_conv_output, orig_conv_output, atol=1e-4))

        # check the output after bias
        for consumer in orig_conv_op.outputs[0].consumers():
            orig_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        for consumer in split_conv_op2.outputs[0].consumers():
            split_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        self.assertTrue(np.allclose(orig_bias_out, split_bias_out, atol=1e-4))

        tf.reset_default_graph()
        sess.close()

    def test_split_layer_channels_last(self):
        """ test the split after and before split_module call with channel last"""

        num_examples = 2000
        g = tf.Graph()

        with g.as_default():

            inp_tensor = tf.get_variable('inp_tensor', shape=[num_examples, 5, 5, 20],
                                         initializer=tf.random_normal_initializer())
            filter_tensor = tf.get_variable('filter_tensor', shape=[5, 5, 20, 50],
                                            initializer=tf.random_normal_initializer())

            conv1 = tf.nn.conv2d(input=inp_tensor, filter=filter_tensor, strides=[1, 1, 1, 1], padding='VALID',
                                 data_format="NHWC", name='Conv2D_1')

            bias_tensor = tf.get_variable('bias_tensor', shape=[50], initializer=tf.random_normal_initializer())

            bias = tf.nn.bias_add(value=conv1, bias=bias_tensor, data_format="NHWC")

            init = tf.global_variables_initializer()

        orig_conv_op = g.get_operation_by_name('Conv2D_1')

        shape = orig_conv_op.outputs[0].get_shape().as_list()
        self.assertEqual(shape, [num_examples, 1, 1, 50])

        sess = tf.Session(graph=g)

        # initialize all the variables in the graph
        sess.run(init)
        orig_conv_output = to_numpy(sess.run(orig_conv_op.outputs[0]))

        # but layer  expects output shape in NCHW format similar to PyTorch
        shape = (shape[0], shape[3], shape[1], shape[2])
        layer1 = lad.Layer(model=sess, op=orig_conv_op, output_shape=shape)

        split_conv_op1, split_conv_op2 = SpatialSvdModuleSplitter.split_module(layer=layer1, rank=100)

        split_conv_output = to_numpy(sess.run(split_conv_op2.outputs[0]))

        self.assertTrue(np.allclose(split_conv_output, orig_conv_output, atol=1e-4))

        # check the output after bias
        for consumer in orig_conv_op.outputs[0].consumers():
            orig_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        for consumer in split_conv_op2.outputs[0].consumers():
            split_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        self.assertTrue(np.allclose(orig_bias_out, split_bias_out, atol=1e-4))

        tf.reset_default_graph()
        sess.close()

    def test_split_layer_with_stirde(self):
        """test the conv2d split after and before split_module call with stride option """

        num_examples = 2000
        g = tf.Graph()

        with g.as_default():

            inp_tensor = tf.get_variable('inp_tensor', shape=[num_examples, 20, 5 + 2, 5 + 2],
                                         initializer=tf.random_normal_initializer())

            filter_tensor = tf.get_variable('filter_tensor', shape=[5, 5, 20, 50],
                                            initializer=tf.random_normal_initializer())

            conv1 = tf.nn.conv2d(input=inp_tensor, filter=filter_tensor, strides=[1, 1, 2, 2], padding='VALID',
                                 data_format="NCHW", name='Conv2D_1')

            bias_tensor = tf.get_variable('bias_tensor', shape=[50], initializer=tf.random_normal_initializer())

            bias = tf.nn.bias_add(value=conv1, bias=bias_tensor, data_format="NCHW")

            init = tf.global_variables_initializer()

        orig_conv_op = g.get_operation_by_name('Conv2D_1')

        # output shape in NCHW format
        shape = orig_conv_op.outputs[0].get_shape().as_list()

        self.assertEqual(shape, [num_examples, 50, 2, 2])

        sess = tf.Session(graph=g)

        # initialize all the variables in the graph
        sess.run(init)

        orig_conv_output = to_numpy(sess.run(orig_conv_op.outputs[0]))

        layer1 = lad.Layer(model=sess, op=orig_conv_op, output_shape=shape)

        split_conv_op1, split_conv_op2 = SpatialSvdModuleSplitter.split_module(layer=layer1, rank=100)

        split_conv_output = to_numpy(sess.run(split_conv_op2.outputs[0]))

        self.assertTrue(np.allclose(split_conv_output, orig_conv_output, atol=1e-4))

        # check the output after bias
        for consumer in orig_conv_op.outputs[0].consumers():
            orig_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        for consumer in split_conv_op2.outputs[0].consumers():
            split_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        self.assertTrue(np.allclose(orig_bias_out, split_bias_out, atol=1e-4))

        tf.reset_default_graph()
        sess.close()

    def test_split_layer_with_stirde_channels_last(self):
        """test the conv2d split after and before split_module call with stride option and channel last"""

        num_examples = 2000
        g = tf.Graph()

        with g.as_default():

            inp_tensor = tf.get_variable('inp_tensor', shape=[num_examples, 5 + 2, 5 + 2, 20],
                                         initializer=tf.random_normal_initializer())

            filter_tensor = tf.get_variable('filter_tensor', shape=[5, 5, 20, 50],
                                            initializer=tf.random_normal_initializer())

            conv1 = tf.nn.conv2d(input=inp_tensor, filter=filter_tensor, strides=[1, 2, 2, 1], padding='VALID',
                                 data_format="NHWC", name='Conv2D_1')

            bias_tensor = tf.get_variable('bias_tensor', shape=[50], initializer=tf.random_normal_initializer())

            bias = tf.nn.bias_add(value=conv1, bias=bias_tensor, data_format="NHWC")

            init = tf.global_variables_initializer()

        orig_conv_op = g.get_operation_by_name('Conv2D_1')

        shape = orig_conv_op.outputs[0].get_shape().as_list()
        self.assertEqual(shape, [num_examples, 2, 2, 50])

        sess = tf.Session(graph=g)

        # initialize all the variables in the graph
        sess.run(init)

        orig_conv_output = to_numpy(sess.run(orig_conv_op.outputs[0]))

        # but layer  expects output shape in NCHW format similar to PyTorch
        shape = (shape[0], shape[3], shape[1], shape[2])

        layer1 = lad.Layer(model=sess, op=orig_conv_op, output_shape=shape)

        split_conv_op1, split_conv_op2 = SpatialSvdModuleSplitter.split_module(layer=layer1, rank=100)

        split_conv_output = to_numpy(sess.run(split_conv_op2.outputs[0]))

        self.assertTrue(np.allclose(split_conv_output, orig_conv_output, atol=1e-4))

        # check the output after bias
        for consumer in orig_conv_op.outputs[0].consumers():
            orig_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        for consumer in split_conv_op2.outputs[0].consumers():
            split_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        self.assertTrue(np.allclose(orig_bias_out, split_bias_out, atol=1e-4))

        tf.reset_default_graph()
        sess.close()

    def test_split_layer_rank_reduced(self):
        """ test the conv2d split after and before split_module call with reduced rank 100 --> 96"""

        num_examples = 2000
        g = tf.Graph()
        with g.as_default():

            inp_tensor = tf.get_variable('inp_tensor', shape=[num_examples, 20, 5, 5],
                                         initializer=tf.random_normal_initializer())

            filter_tensor = tf.get_variable('filter_tensor', shape=[5, 5, 20, 50],
                                            initializer=tf.random_normal_initializer())

            conv1 = tf.nn.conv2d(input=inp_tensor, filter=filter_tensor, strides=[1, 1, 1, 1], padding='VALID',
                                 data_format="NCHW", name='Conv2D_1')

            bias_tensor = tf.get_variable('bias_tensor', shape=[50], initializer=tf.random_normal_initializer())

            bias = tf.nn.bias_add(value=conv1, bias=bias_tensor, data_format="NCHW")

            init = tf.global_variables_initializer()

        orig_conv_op = g.get_operation_by_name('Conv2D_1')

        shape = orig_conv_op.outputs[0].get_shape().as_list()
        self.assertEqual(shape, [num_examples, 50, 1, 1])

        sess = tf.Session(graph=g)

        # initialize all the variables in the graph
        sess.run(init)

        orig_conv_output = to_numpy(sess.run(orig_conv_op.outputs[0]))

        layer1 = lad.Layer(model=sess, op=orig_conv_op, output_shape=shape)

        split_conv_op1, split_conv_op2 = SpatialSvdModuleSplitter.split_module(layer=layer1, rank=96)

        split_conv_output = to_numpy(sess.run(split_conv_op2.outputs[0]))

        # relaxed absolute tolerance
        self.assertTrue(np.allclose(split_conv_output, orig_conv_output, atol=1e+2))

        # check the output after bias
        for consumer in orig_conv_op.outputs[0].consumers():
            orig_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        for consumer in split_conv_op2.outputs[0].consumers():
            split_bias_out = to_numpy(sess.run(consumer.outputs[0]))

        # relaxed absolute tolerance
        self.assertTrue(np.allclose(orig_bias_out, split_bias_out, atol=1e+2))

        tf.reset_default_graph()
        sess.close()


class TestSpatialSvdPruning(unittest.TestCase):

    def test_prune_layer(self):
        """ Pruning single layer with 0.5 comp-ratio in MNIST"""

        # create tf.Session and initialize the weights and biases with zeros
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True

        # create session with graph
        sess = tf.Session(graph=tf.Graph(), config=config)

        with sess.graph.as_default():
            # by default, model will be constructed in default graph
            _ = mnist_tf_model.create_model(data_format='channels_last')
            sess.run(tf.global_variables_initializer())

        # Create a layer database
        orig_layer_db = LayerDatabase(model=sess, input_shape=(1, 28, 28, 1), working_dir=None)
        # Copy the db
        comp_layer_db = copy.deepcopy(orig_layer_db)
        conv1 = comp_layer_db.find_layer_by_name('conv2d/Conv2D')

        # before the splitting
        bias_op = get_succeeding_bias_op(conv1.module)
        for consumer in bias_op.outputs[0].consumers():
            self.assertEqual(consumer.name, "conv2d/Relu")

        spatial_svd_pruner = SpatialSvdPruner()
        spatial_svd_pruner._prune_layer(orig_layer_db, comp_layer_db, conv1, 0.5, CostMetric.mac)

        conv1_a = comp_layer_db.find_layer_by_name('conv2d/Conv2D_a')
        conv1_b = comp_layer_db.find_layer_by_name('conv2d/Conv2D_b')

        # [Noc, Nic, kh, kw]
        self.assertEqual([2, 1, 5, 1], conv1_a.weight_shape)
        self.assertEqual([32, 2, 1, 5], conv1_b.weight_shape)

        # after the splitting
        bias_op = get_succeeding_bias_op(conv1_b.module)

        for consumer in bias_op.outputs[0].consumers():
            self.assertEqual(consumer.name, "conv2d/Relu")

        # original layer should be not there in the database
        self.assertRaises(KeyError, lambda:  comp_layer_db.find_layer_by_name('conv2d/Conv2D'))

        # check if the layer replacement is done correctly
        orig_conv_op = comp_layer_db.model.graph.get_operation_by_name('conv2d/Conv2D')
        bias_op = get_succeeding_bias_op(orig_conv_op)

        # consumers list should be empty
        consumers = [consumer for consumer in bias_op.outputs[0].consumers()]
        self.assertEqual(len(consumers), 0)

        tf.reset_default_graph()
        sess.close()
        # delete temp directory
        shutil.rmtree(str('./temp_meta/'))

    def test_prune_model_2_layers(self):
        """ Punning two layers with 0.5 comp-ratio in MNIST"""

        # create tf.Session and initialize the weights and biases with zeros
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True

        # create session with graph
        sess = tf.Session(graph=tf.Graph(), config=config)

        with sess.graph.as_default():
            # by default, model will be constructed in default graph
            _ = mnist_tf_model.create_model(data_format='channels_last')
            sess.run(tf.global_variables_initializer())

        # Create a layer database
        orig_layer_db = LayerDatabase(model=sess, input_shape=(1, 28, 28, 1), working_dir=None)
        conv1 = orig_layer_db.find_layer_by_name('conv2d/Conv2D')
        conv2 = orig_layer_db.find_layer_by_name('conv2d_1/Conv2D')

        layer_comp_ratio_list = [LayerCompRatioPair(conv1, Decimal(0.5)),
                                 LayerCompRatioPair(conv2, Decimal(0.5))]

        spatial_svd_pruner = SpatialSvdPruner()
        comp_layer_db = spatial_svd_pruner.prune_model(orig_layer_db, layer_comp_ratio_list, CostMetric.mac,
                                                       trainer=None)

        conv1_a = comp_layer_db.find_layer_by_name('conv2d/Conv2D_a')
        conv1_b = comp_layer_db.find_layer_by_name('conv2d/Conv2D_b')

        # Weights shape [kh, kw, Nic, Noc]
        self.assertEqual([5, 1, 1, 2], conv1_a.module.inputs[1].get_shape().as_list())
        self.assertEqual([1, 5, 2, 32], conv1_b.module.inputs[1].get_shape().as_list())

        conv2_a = comp_layer_db.find_layer_by_name('conv2d_1/Conv2D_a')
        conv2_b = comp_layer_db.find_layer_by_name('conv2d_1/Conv2D_b')

        self.assertEqual([5, 1, 32, 53], conv2_a.module.inputs[1].get_shape().as_list())
        self.assertEqual([1, 5, 53, 64], conv2_b.module.inputs[1].get_shape().as_list())

        for layer in comp_layer_db:
            print("Layer: " + layer.name)
            print("   Module: " + str(layer.module.name))

        tf.reset_default_graph()
        sess.close()
        # delete temp directory
        shutil.rmtree(str('./temp_meta/'))