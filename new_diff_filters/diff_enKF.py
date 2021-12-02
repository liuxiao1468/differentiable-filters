# Copyright (c) 2020 Max Planck Gesellschaft

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 
import math
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial.transform import Rotation as Rot
import random
import tensorflow as tf
import time
import pickle
import pdb
from tensorflow.compat.v1 import ConfigProto
from tensorflow.compat.v1 import InteractiveSession
import tensorflow_probability as tfp

config = ConfigProto()
config.gpu_options.allow_growth = True
session = InteractiveSession(config=config)
'''
This is the code for setting up a differentiable version of the ensemble kalman filter
The filter is trained using simulated data where we only have access to the ground truth state at each timestep
The filter is suppose to learn the process noise model Q, observation noise model R, the process model f(.) 
and the observation model h(.)
Author: Xiao -> I have made decent amount of changes to the original codebase.
'''
class ProcessModel(tf.keras.Model):
    '''
    process model is taking the state and get a prediction state and 
    calculate the jacobian matrix based on the previous state and the 
    predicted state.
    new_state = [batch_size, 1, dim_x]
            F = [batch_size, dim_x, dim_x]
    state vector 4 -> fc 32 -> fc 64 -> 2
    '''
    def __init__(self, batch_size, num_ensemble, dim_x, jacobian, rate):
        super(ProcessModel, self).__init__()
        self.batch_size = batch_size
        self.num_ensemble = num_ensemble
        self.jacobian = jacobian
        self.dim_x = dim_x
        self.rate = rate

    def build(self, input_shape):
        self.process_fc1 = tf.keras.layers.Dense(
            units=32,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='process_fc1')
        self.process_fc_add1 = tf.keras.layers.Dense(
            units=64,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='process_fc_add1')
        self.process_fc2 = tf.keras.layers.Dense(
            units=64,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='process_fc2')
        self.process_fc_add2 = tf.keras.layers.Dense(
            units=32,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='process_fc_add2')
        self.process_fc3 = tf.keras.layers.Dense(
            units=self.dim_x,
            activation=None,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='process_fc3')

    def call(self, last_state, training):
        last_state = tf.reshape(last_state, [self.batch_size * self.num_ensemble, self.dim_x])

        fc1 = self.process_fc1(last_state)
        # fc1 = tf.nn.dropout(fc1, rate=self.rate)
        fcadd1 = self.process_fc_add1(fc1)
        # fcadd1 = tf.nn.dropout(fcadd1, rate=self.rate)
        fc2 = self.process_fc2(fcadd1)
        fc2 = tf.nn.dropout(fc2, rate=self.rate)
        fcadd2 = self.process_fc_add2(fc2)
        fcadd2 = tf.nn.dropout(fcadd2, rate=self.rate)
        update = self.process_fc3(fcadd2)

        new_state = last_state + update
        new_state = tf.reshape(new_state, [self.batch_size, self.num_ensemble, self.dim_x])

        return new_state

class addAction(tf.keras.Model):
    '''
    action models serves in the prediction step and it will be added to predicted state before the 
    updating steps. In this toy example.
     State: [batch_size, 1, dim_x]
         B: [batch_size, dim_x, 2]
    action: [batch_size, 1, 2]
    '''
    def __init__(self, batch_size, dim_x):
        super(addAction, self).__init__()
        self.batch_size = batch_size
        self.dim_x = dim_x

    def call(self, state, action, training):
        DT = 0.1
        for i in range (self.batch_size):
            if i == 0:
                B_tmp = np.array([
                    [DT * np.cos(state[i][0][2]), 0],
                    [DT * np.sin(state[i][0][2]), 0],
                    [0.0, DT],
                    [1.0, 0.0]])
                B = tf.reshape(B_tmp, [1,self.dim_x,2])
            else:
                B_tmp = np.array([
                    [DT * np.cos(state[i][0][2]), 0],
                    [DT * np.sin(state[i][0][2]), 0],
                    [0.0, DT],
                    [1.0, 0.0]])
                B_tmp = tf.reshape(B_tmp, [1,self.dim_x,2])
                B = tf.concat([B, B_tmp], axis = 0)
        B = tf.cast(B, tf.float32)
        state = state + tf.transpose(tf.matmul(B, tf.transpose(action, perm=[0,2,1])), perm=[0,2,1])
        return state

class ObservationModel(tf.keras.Model):
    '''
    process model is taking the state and get a prediction state and 
    calculate the jacobian matrix based on the previous state and the 
    predicted state.
    new_state = [batch_size, 1, dim_x]
            F = [batch_size, dim_x, dim_x]
    state vector 4 -> fc 32 -> fc 64 -> 2
    '''
    def __init__(self, batch_size, num_ensemble, dim_x, dim_z, jacobian):
        super(ObservationModel, self).__init__()
        self.batch_size = batch_size
        self.num_ensemble = num_ensemble
        self.jacobian = jacobian
        self.dim_x = dim_x
        self.dim_z = dim_z

    def build(self, input_shape):
        self.observation_fc1 = tf.keras.layers.Dense(
            units=32,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='observation_fc1')
        self.observation_fc_add1 = tf.keras.layers.Dense(
            units=64,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='observation_fc_add1')
        self.observation_fc2 = tf.keras.layers.Dense(
            units=64,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='observation_fc2')
        self.observation_fc_add2 = tf.keras.layers.Dense(
            units=32,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='observation_fc_add2')
        self.observation_fc3 = tf.keras.layers.Dense(
            units=self.dim_z,
            activation=None,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='observation_fc3')

    def call(self, state, training, learn):
        state = tf.reshape(state, [self.batch_size* self.num_ensemble, 1, self.dim_x])
        if learn == False:
            H = tf.concat(
                [tf.tile(np.array([[[1, 0, 0, 0, 0]]], dtype=np.float32),
                         [self.batch_size* self.num_ensemble, 1, 1]),
                 tf.tile(np.array([[[0, 1, 0, 0, 0]]], dtype=np.float32),
                         [self.batch_size* self.num_ensemble, 1, 1])], axis=1)
            z_pred = tf.matmul(H, tf.transpose(state, perm=[0,2,1]))
            Z_pred = tf.transpose(z_pred, perm=[0,2,1])
            z_pred = tf.reshape(z_pred, [self.batch_size, self.num_ensemble, self.dim_z])
        else:
            fc1 = self.observation_fc1(state)
            fcadd1 = self.observation_fc_add1(fc1)
            fc2 = self.observation_fc2(fcadd1)
            fcadd2 = self.observation_fc_add2(fc2)
            z_pred = self.observation_fc3(fcadd2)
            z_pred = tf.reshape(z_pred, [self.batch_size, self.num_ensemble, self.dim_z])

        return z_pred


# class ObservationModel(tf.keras.Model):
#     '''
#     Observation matrix H is given, which does not require learning
#     the jacobians. It requires one's knowledge of the whole system  
#     z_pred = [batch_size, 1, dim_z]
#     '''
#     def __init__(self, batch_size, num_ensemble, dim_x, dim_z, jacobian):
#         super(ObservationModel, self).__init__()
#         self.batch_size = batch_size
#         self.num_ensemble = num_ensemble
#         self.jacobian = jacobian
#         self.dim_x = dim_x
#         self.dim_z = dim_z

#     def call(self, state, training):
#         state = tf.reshape(state, [self.batch_size* self.num_ensemble, 1, self.dim_x])
#         H = tf.concat(
#                 [tf.tile(np.array([[[1, 0, 0, 0]]], dtype=np.float32),
#                          [self.batch_size* self.num_ensemble, 1, 1]),
#                  tf.tile(np.array([[[0, 1, 0, 0]]], dtype=np.float32),
#                          [self.batch_size* self.num_ensemble, 1, 1])], axis=1)
#         z_pred = tf.matmul(H, tf.transpose(state, perm=[0,2,1]))
#         Z_pred = tf.transpose(z_pred, perm=[0,2,1])
#         z_pred = tf.reshape(z_pred, [self.batch_size, self.num_ensemble, 2])

#         return z_pred, H


class SensorModel(tf.keras.Model):
    '''
    sensor model is used for modeling H with given states to get observation z
    it is not required for this model to take states only, if the obervation is 
    an image or higher dimentional tensor, it is supposed to learn a lower demention
    representation from the observation space.
    observation = [batch_size, dim_z]
    encoding = [batch_size, dim_fc2]
    '''
    def __init__(self, batch_size, dim_z):
        super(SensorModel, self).__init__()
        self.batch_size = batch_size
        self.dim_z = dim_z

    def build(self, input_shape):
        self.sensor_fc1 = tf.keras.layers.Dense(
            units=32,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='sensor_fc1')
        self.sensor_fc_add1 = tf.keras.layers.Dense(
            units=64,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='sensor_fc_add1')
        self.sensor_fc2 = tf.keras.layers.Dense(
            units=64,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='sensor_fc2')
        self.sensor_fc_add2 = tf.keras.layers.Dense(
            units=32,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='sensor_fc_add2')
        self.sensor_fc3 = tf.keras.layers.Dense(
            units=self.dim_z,
            activation=None,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='sensor_fc3')

    def call(self, state, training, learn):
        if learn == True:
            fc1 = self.sensor_fc1(state)
            fcadd1 = self.sensor_fc_add1(fc1)
            fc2 = self.sensor_fc2(fcadd1)
            fcadd2 = self.sensor_fc_add2(fc2)
            observation = self.sensor_fc3(fcadd2)
            encoding = fcadd2
        else:
            observation = state
            encoding = state

        return observation, encoding


class ProcessNoise(tf.keras.Model):
    '''
    Noise model is asuming the noise to be heteroscedastic
    The noise is not constant at each step
    The fc neural network is designed for learning the diag(Q)
    Q = [batch_size, dim_x, dim_x]
    i.e., 
    if the state has 4 inputs
    state vector 4 -> fc 32 -> fc 64 -> 4
    the result is the diag of Q where Q is a 4x4 matrix
    '''
    def __init__(self, batch_size, num_ensemble, dim_x, q_diag):
        super(ProcessNoise, self).__init__()
        self.batch_size = batch_size
        self.num_ensemble = num_ensemble
        self.dim_x = dim_x
        self.q_diag = q_diag

    def build(self, input_shape):
        constant = np.ones(self.dim_x)* 1e-3
        init = np.sqrt(np.square(self.q_diag) - constant)
        self.fixed_process_noise_bias = self.add_weight(
            name = 'fixed_process_noise_bias',
            shape = [self.dim_x],
            regularizer = tf.keras.regularizers.l2(l=1e-3),
            initializer = tf.constant_initializer(constant))
        self.process_noise_fc1 = tf.keras.layers.Dense(
            units=32,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='process_noise_fc1')
        self.process_noise_fc_add1 = tf.keras.layers.Dense(
            units=64,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='process_noise_fc_add1')
        self.process_noise_fc2 = tf.keras.layers.Dense(
            units=64,
            activation=tf.nn.relu,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='process_noise_fc2')
        self.process_noise_fc3 = tf.keras.layers.Dense(
            units=self.dim_x,
            activation=None,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='process_noise_fc3')
        self.learned_process_noise_bias = self.add_weight(
            name = 'learned_process_noise_bias',
            shape = [self.dim_x],
            regularizer = tf.keras.regularizers.l2(l=1e-3),
            initializer = tf.constant_initializer(init))

    def call(self, state, training, learn):
        if learn == True:
            fc1 = self.process_noise_fc1(state)
            fcadd1 = self.process_noise_fc_add1(fc1)
            fc2 = self.process_noise_fc2(fcadd1)
            diag = self.process_noise_fc3(fc2)
            diag = tf.square(diag + self.learned_process_noise_bias)
        else:
            diag = tf.square(self.learned_process_noise_bias)
            diag = tf.stack([diag] * (self.batch_size))

        diag = diag + self.fixed_process_noise_bias
        mean = np.zeros((self.dim_x))
        mean = tf.convert_to_tensor(mean, dtype=tf.float32)
        mean = tf.stack([mean] * self.batch_size)
        nd = tfp.distributions.MultivariateNormalDiag(loc=mean, scale_diag=diag)
        Q = tf.reshape(nd.sample(self.num_ensemble), [self.batch_size, self.num_ensemble, self.dim_x])

        return Q, diag


class ObservationNoise(tf.keras.Model):
    '''
    Noise model is asuming the noise to be heteroscedastic
    The noise is not constant at each step
    inputs: an intermediate representation of the raw observation
    denoted as encoding 
    R = [batch_size, dim_z, dim_z]
    The fc neural network is designed for learning the diag(R)
    i.e., 
    if the state has 4 inputs, the encoding has size 64,
    observation vector z is with size 2, the R has the size
    2 + (64 -> fc 2 -> 2) + fixed noise,
    the result is the diag of R where R is a 2x2 matrix
    '''
    def __init__(self, batch_size, num_ensemble, dim_z, r_diag, jacobian):
        super(ObservationNoise, self).__init__()
        self.batch_size = batch_size
        self.num_ensemble = num_ensemble
        self.jacobian = jacobian
        self.dim_z = dim_z
        self.r_diag = r_diag

    def build(self, input_shape):
        constant = np.ones(self.dim_z)* 1e-3
        init = np.sqrt(np.square(self.r_diag) - constant)
        self.fixed_observation_noise_bias = self.add_weight(
            name = 'fixed_observation_noise_bias',
            shape = [self.dim_z],
            regularizer = tf.keras.regularizers.l2(l=1e-3),
            initializer = tf.constant_initializer(constant))

        self.observation_noise_fc1 = tf.keras.layers.Dense(
            units=self.dim_z,
            activation=None,
            kernel_initializer=tf.initializers.glorot_normal(),
            kernel_regularizer=tf.keras.regularizers.l2(l=1e-3),
            bias_regularizer=tf.keras.regularizers.l2(l=1e-3),
            name='observation_noise_fc1')

        self.learned_observation_noise_bias = self.add_weight(
            name = 'learned_observation_noise_bias',
            shape = [self.dim_z],
            regularizer = tf.keras.regularizers.l2(l=1e-3),
            initializer = tf.constant_initializer(init))

    def call(self, inputs, training, learn):
        if learn == True:
            diag = self.observation_noise_fc1(inputs)
            diag = tf.square(diag + self.learned_observation_noise_bias)
        else:
            diag = tf.square(self.learned_observation_noise_bias)
            diag = tf.stack([diag] * (self.batch_size))

        diag = diag + self.fixed_observation_noise_bias
        R = tf.linalg.diag(diag)
        # pdb.set_trace()
        R = tf.reshape(R, [self.batch_size, self.dim_z, self.dim_z])
        diag = tf.reshape(diag, [self.batch_size, self.dim_z])

        return R, diag


class getloss():
    def _mse(self, diff):
        """
        Returns the mean squared error of diff = label - pred plus their
        euclidean distance (dist)
        Parameters
        ----------
        diff : tensor
            difference between label and prediction
        reduce_mean : bool, optional
            if true, return the mean errors over the complete tensor. The
            default is False.
        Returns
        -------
        loss : tensor
            the mean squared error
        dist : tensor
            the euclidean distance
        """
        diff_a = tf.expand_dims(diff, axis=-1)
        diff_b = tf.expand_dims(diff, axis=-2)

        loss = tf.matmul(diff_b, diff_a)

        # the loss needs to be finite and positive
        loss = tf.where(tf.math.is_finite(loss), loss,
                        tf.ones_like(loss)*1e20)
        loss = tf.where(tf.greater_equal(loss, 0), loss,
                        tf.ones_like(loss)*1e20)

        loss = tf.squeeze(loss, axis=-1)
        dist = tf.sqrt(loss)

        loss = tf.reduce_mean(loss)
        dist = tf.reduce_mean(dist)

        loss = dist + loss

        return loss


class DiffenKF(tf.keras.layers.AbstractRNNCell):
    def __init__(self, batch_size, num_ensemble, dropout_rate,**kwargs):

        super(DiffenKF, self).__init__(**kwargs)

        # initialization
        self.batch_size = batch_size
        self.num_ensemble = num_ensemble
        
        self.dim_x = 5
        self.dim_z = 2

        self.jacobian = True

        self.q_diag = np.ones((self.dim_x)).astype(np.float32) * 0.5
        self.q_diag = self.q_diag.astype(np.float32)

        self.r_diag = np.ones((self.dim_z)).astype(np.float32) * 0.3
        self.r_diag = self.r_diag.astype(np.float32)

        self.scale = 1

        self.dropout_rate = dropout_rate


        # predefine all the necessary sub-models
        # learned sensor model for processing the images

        # learned process model
        self.process_model = ProcessModel(self.batch_size, self.num_ensemble, self.dim_x, self.jacobian, self.dropout_rate)

        # # learned process noise
        # self.process_noise_model = ProcessNoise(self.batch_size, self.num_ensemble, self.dim_x, self.q_diag)

        # learned observation model
        self.observation_model = ObservationModel(self.batch_size, self.num_ensemble, self.dim_x, self.dim_z, self.jacobian)

        # learned observation noise
        self.observation_noise_model = ObservationNoise(self.batch_size, self.num_ensemble, self.dim_z, self.r_diag, self.jacobian)

        # learned sensor model
        self.sensor_model = SensorModel(self.batch_size, self.dim_z)

        # # optional: if action is needed
        # self.add_actions = addAction(self.batch_size, self.dim_x)


    @property
    def state_size(self):
        """size(s) of state(s) used by this cell.
        It can be represented by an Integer, a TensorShape or a tuple of
        Integers or TensorShapes.
        """
        # estimated state, its covariance, and the step number
        return [[self.num_ensemble * self.dim_x], [self.dim_x], [1]]

    @property
    def output_size(self):
        """Integer or TensorShape: size of outputs produced by this cell."""
        # estimated state, observations, Q, R
        return ([self.dim_x], [self.num_ensemble * self.dim_x], 
                [self.dim_z], [self.dim_z])

    ###########################################################################
    # convenience functions for ensuring stability

    ###########################################################################
    def _condition_number(self, s):
        """
        Compute the condition number of a matrix based on its eigenvalues s
        Parameters
        ----------
        s : tensor
            the eigenvalues of a matrix
        Returns
        -------
        r_corrected : tensor
            the condition number of the matrix
        """
        r = s[..., 0] / s[..., -1]

        # Replace NaNs in r with infinite
        r_nan = tf.math.is_nan(r)
        r_inf = tf.fill(tf.shape(r), tf.constant(np.Inf, r.dtype))
        r_corrected = tf.where(r_nan, r_inf, r)

        return r_corrected

    def _is_invertible(self, s, epsilon=1e-6):
        """
        Check if a matrix is invertible based on its eigenvalues s
        Parameters
        ----------
        s : tensor
            the eigenvalues of a matrix
        epsilon : float, optional
            threshold for the condition number
        Returns
        -------
        invertible : tf.bool tensor
            true if the matrix is invertible
        """
        # "c"
        # Epsilon may be smaller with tf.float64
        eps_inv = tf.cast(1. / epsilon, s.dtype)
        cond_num = self._condition_number(s)
        invertible = tf.logical_and(tf.math.is_finite(cond_num),
                                    tf.less(cond_num, eps_inv))
        return invertible

    def _make_valid(self, covar):
        """
        Trys to make a possibly degenerate covariance valid by
          - replacing nans and infs with high values/zeros
          - making the matrix symmetric
          - trying to make the matrix invertible by adding small offsets to
            the smallest eigenvalues
        Parameters
        ----------
        covar : tensor
            a covariance matrix that is possibly degenerate
        Returns
        -------
        covar_valid : tensor
            a covariance matrix that is hopefully valid
        """
        # eliminate nans and infs (replace them with high values on the
        # diagonal and zeros else)
        bs = covar.get_shape()[0]
        dim = covar.get_shape()[-1]
        covar = tf.where(tf.math.is_finite(covar), covar,
                         tf.eye(dim, batch_shape=[bs])*1e6)

        # make symmetric
        covar = (covar + tf.linalg.matrix_transpose(covar)) / 2.

        # add a bit of noise to the diagonal of covar to prevent
        # nans in the gradient of the svd
        noise = tf.random.uniform(covar.get_shape().as_list()[:-1], minval=0,
                                  maxval=0.001/self.scale**2)
        s, u, v = tf.linalg.svd(covar + tf.linalg.diag(noise))
        # test if the matrix is invertible
        invertible = self._is_invertible(s)
        # test if the matrix is positive definite
        pd = tf.reduce_all(tf.greater(s, 0), axis=-1)

        # try making a valid version of the covariance matrix by ensuring that
        # the minimum eigenvalue is at least 1e-4/self.scale
        min_eig = s[..., -1:]
        eps = tf.tile(tf.maximum(1e-4/self.scale - min_eig, 0),
                      [1, s.get_shape()[-1] ])
        covar_invertible = tf.matmul(u, tf.matmul(tf.linalg.diag(s + eps), v,
                                                  adjoint_b=True))

        # if the covariance matrix is valid, leave it as is, else replace with
        # the modified variant
        covar_valid = tf.where(tf.logical_and(invertible, pd)[:, None, None],
                               covar, covar_invertible)

        # make symmetric again
        covar_valid = \
            (covar_valid + tf.linalg.matrix_transpose(covar_valid)) / 2.

        return covar_valid
    ###########################################################################


    def call(self, inputs, states):
        """
        inputs: KF input, velocity/angular velocity
        state: x, y, psi, v
        mode: training or testing 
        """
        # decompose inputs and states
        raw_sensor, actions = inputs

        raw_sensor = tf.reshape(raw_sensor, [self.batch_size, 1, self.dim_z])
        # actions = tf.reshape(actions, [self.batch_size, 1, 2])

        state_old, m_state, step = states

        state_old = tf.reshape(state_old, [self.batch_size, self.num_ensemble, self.dim_x])

        m_state = tf.reshape(m_state, [self.batch_size, self.dim_x])

        training = True

        '''
        prediction step
        state_pred: x_{t}
                 Q: process noise
        '''
        # get prediction and noise of next state
        state_pred = self.process_model(state_old, training)


        # Q, diag_Q = self.process_noise_model(m_state, training, True)


        # # state_pred = state_pred
        # state_pred = state_pred + Q

        '''
        update step
        state_new: hat_x_{t}
                H: observation Jacobians
                S: innovation matrix
                K: kalman gain

        '''
        # get predicted observations
        learn = True
        H_X = self.observation_model(state_pred, training, learn)

        # get the emsemble mean of the observations
        m = tf.reduce_mean(H_X, axis = 1)
        for i in range (self.batch_size):
            if i == 0:
                mean = tf.reshape(tf.stack([m[i]] * self.num_ensemble), [self.num_ensemble, self.dim_z])
            else:
                tmp = tf.reshape(tf.stack([m[i]] * self.num_ensemble), [self.num_ensemble, self.dim_z])
                mean = tf.concat([mean, tmp], 0)

        mean = tf.reshape(mean, [self.batch_size, self.num_ensemble, self.dim_z])
        H_A = H_X - mean

        final_H_A = tf.transpose(H_A, perm=[0,2,1])
        final_H_X = tf.transpose(H_X, perm=[0,2,1])

        # get sensor reading
        z, encoding = self.sensor_model(raw_sensor, training, True)

        # enable each ensemble to have a observation
        z = tf.reshape(z, [self.batch_size, self.dim_z])
        for i in range (self.batch_size):
            if i == 0:
                ensemble_z = tf.reshape(tf.stack([z[i]] * self.num_ensemble), [1, self.num_ensemble, self.dim_z])
            else:
                tmp = tf.reshape(tf.stack([z[i]] * self.num_ensemble), [1, self.num_ensemble, self.dim_z])
                ensemble_z = tf.concat([ensemble_z, tmp], 0)

        # make sure the ensemble shape matches
        ensemble_z = tf.reshape(ensemble_z, [self.batch_size, self.num_ensemble, self.dim_z])


        # get observation noise
        R, diag_R = self.observation_noise_model(encoding, training, True)

        # incorporate the measurement with stochastic noise
        r_mean = np.zeros((self.dim_z))
        r_mean = tf.convert_to_tensor(r_mean, dtype=tf.float32)
        r_mean = tf.stack([r_mean] * self.batch_size)
        nd_r = tfp.distributions.MultivariateNormalDiag(loc=r_mean, scale_diag=diag_R)
        epsilon = tf.reshape(nd_r.sample(self.num_ensemble), [self.batch_size, self.num_ensemble, self.dim_z])

        # the measurement y
        y = ensemble_z + epsilon
        y = tf.transpose(y, perm=[0,2,1])


        # calculated innovation matrix s
        innovation = (1/(self.num_ensemble -1)) * tf.matmul(final_H_A,  H_A) + R

        # A matrix
        m_A = tf.reduce_mean(state_pred, axis = 1)
        for i in range (self.batch_size):
            if i == 0:
                mean_A = tf.reshape(tf.stack([m_A[i]] * self.num_ensemble), [1, self.num_ensemble, self.dim_x])
            else:
                tmp = tf.reshape(tf.stack([m_A[i]] * self.num_ensemble), [1, self.num_ensemble, self.dim_x])
                mean_A = tf.concat([mean_A, tmp], 0)
        A = state_pred - mean_A
        A = tf.transpose(A, perm = [0,2,1])

        try:
            innovation_inv = tf.linalg.inv(innovation)
        except:
            innovation = self._make_valid(innovation)
            innovation_inv = tf.linalg.inv(innovation)


        # calculating Kalman gain
        K = (1/(self.num_ensemble -1)) * tf.matmul(tf.matmul(A, H_A), innovation_inv)


        # update state of each ensemble
        y_bar = y - final_H_X
        state_new = state_pred +  tf.transpose(tf.matmul(K, y_bar), perm=[0,2,1])

        # the ensemble state mean
        m_state_new = tf.reduce_mean(state_new, axis = 1)

        # change the shape as defined in the property function
        state_new = tf.reshape(state_new, [self.batch_size, -1])

        # tuple structure of updated state
        state_hat = (state_new, m_state_new, step+1)

        # tuple structure of the output
        z = tf.reshape(z, [self.batch_size, -1])

        # output = (m_state_new, state_new, z, 
        #     tf.reshape(diag_R, [self.batch_size, -1]), 
        #     tf.reshape(diag_Q, [self.batch_size, -1]))
        output = (m_state_new, state_new, z, 
            tf.reshape(diag_R, [self.batch_size, -1]))

        # print('===========')
        # print('output: ',state_hat)
        # print('===========')

        return output, state_hat

class RNNmodel(tf.keras.Model):
    def __init__(self, batch_size, num_ensemble, dropout_rate, hetero_q=False, hetero_r=True, **kwargs):

        super(RNNmodel, self).__init__(**kwargs)

        self.batch_size = batch_size

        self.num_ensemble = num_ensemble

        self.dropout_rate = dropout_rate

        # instantiate the filter
        self.filter = DiffenKF(batch_size, num_ensemble, dropout_rate)

        # wrap the filter in a RNN layer
        self.rnn_layer = tf.keras.layers.RNN(self.filter, return_sequences=True,unroll=False)

        # define the initial belief of the filter
        X = np.array([0,0,0,0,1])
        X = tf.convert_to_tensor(X, dtype=tf.float32)

        ensemble_X = tf.stack([X] * (self.num_ensemble * self.batch_size))
        m_X = tf.stack([X] * self.batch_size)

        ensemble_X = tf.reshape(ensemble_X, [batch_size, -1])
        m_X = tf.reshape(m_X, [batch_size, -1])

        step = tf.zeros([batch_size,1])

        self.state_0 = (ensemble_X, m_X, step)

    def call(self, inputs):
        raw_sensor = inputs

        # action is B*u, which is added to x_{t} after the prediction step 
        action = np.array([1, np.radians(0.1)])
        action = tf.convert_to_tensor(action, dtype=tf.float32)
        action = tf.stack([action] * self.batch_size)
        action = tf.reshape(action, [self.batch_size, 1, 2])

        # fake_actions = tf.zeros([batch_size, 1, 2])
        real_actions = action
        inputs = (raw_sensor, real_actions)

        # print('-------------------- ',inputs)

        outputs = self.rnn_layer(inputs, initial_state=self.state_0)
        
        return outputs

    @classmethod
    def from_config(cls, config):
        return cls(**config)



# '''
# data loader for training the toy example
# observation = [timestep, batch_size, 1, dim_z] -> input data
# states_true = [timestep, batch_size, 1, dim_x] -> ground truth
# '''
# def data_loader_function(data_path):
#     name = ['constant', 'exp']
#     num_sensors = 100

#     observations = []
#     states_true = []

#     s = 1/25.
#     with open(data_path, 'rb') as f:
#         traj = pickle.load(f)
#     for i in range (len(traj['xTrue'])):
#         observation = []
#         state = []
#         for j in range (num_sensors):
#             observe = [traj['sensors'][i][0][j]*s, traj['sensors'][i][1][j]*s]
#             observation.append(observe)
#             angles = traj['xTrue'][i][2]
#             xTrue = [traj['xTrue'][i][0]*s, traj['xTrue'][i][1]*s, np.cos(angles), np.sin(angles), traj['xTrue'][i][3]]
#             state.append(xTrue)
#         observations.append(observation)
#         states_true.append(state)
#     observations = np.array(observations)
#     observations = tf.reshape(observations, [len(traj['xTrue']), num_sensors, 1, 2])
#     states_true = np.array(states_true)
#     states_true = tf.reshape(states_true, [len(traj['xTrue']), num_sensors, 1, 5])
#     return observations, states_true

# '''
# define the training loop
# '''
# def run_filter(mode):
#     if mode == True:
#         # define batch_sizepython
#         batch_size = 64

#         # define number of ensemble
#         num_ensemble = 32

#         # define dropout rate
#         dropout_rate = 0.4

#         # load the model
#         model = RNNmodel(batch_size, num_ensemble, dropout_rate)

#         optimizer = tf.keras.optimizers.Adam(learning_rate=1e-4)
#         epoch = 100

#         for k in range (epoch):
#             '''
#             data preprocessing steps
#             '''
#             select = random.sample(range(0, 90), batch_size)
#             raw_sensor = []
#             gt = []
#             for idx in select:
#                 raw_sensor.append(observations[:, idx, :,:])
#                 gt.append(states_true[:, idx, :,:])
#             raw_sensor = tf.convert_to_tensor(raw_sensor, dtype=tf.float32)
#             raw_sensor = tf.reshape(raw_sensor, [observations.shape[0], batch_size, 1, 2])
#             gt = tf.convert_to_tensor(gt, dtype=tf.float32)
#             gt = tf.reshape(gt, [states_true.shape[0], batch_size, 1, 5])

#             print("========================================= working on epoch %d =========================================: " % (k))

#             for i in range(states_true.shape[0]):

#                 start = time.time()

#                 with tf.GradientTape() as tape:
#                     out = model(raw_sensor[i])
#                     state_h = out[0]
#                     loss = get_loss._mse( gt[i] - state_h)

#                 # grads = tape.gradient(loss, model.variables)
#                 # optimizer.apply_gradients(grads_and_vars=zip(grads, model.variables))
#                 grads = tape.gradient(loss, model.trainable_weights)
#                 optimizer.apply_gradients(zip(grads, model.trainable_weights))
#                 end = time.time()
#                 # print(model.summary())
                

#                 # Log every 50 batches.
#                 if i % 100 == 0:
#                     print("Training loss at step %d: %.4f (took %.3f seconds) " %
#                           (i, float(loss), float(end-start)))
#                     print(state_h[0])
#                     print(gt[i][0])
#                     print(out[4][0])
#                     print('---')
#             if (k+1) % 20 == 0:
#                 model.save_weights('./models/ensemble_model_v2.7_weights_'+name[index]+str(k).zfill(3)+'.h5')
#                 print('model is saved at this epoch')
#     else:
#         # define batch_size
#         batch_size = 1

#         num_ensemble = 32

#         dropout_rate = 0.4

#         # load the model
#         model = RNNmodel(batch_size, num_ensemble, dropout_rate)

#         test_demo = observations[:, 98, :,:]
#         test_demo = tf.reshape(test_demo, [observations.shape[0], 1, 1, 2])
#         dummy = model(test_demo[0])
#         model.load_weights('./models/ensemble_model_v2.7_weights_'+name[index]+str(59).zfill(3)+'.h5')
#         model.summary()

#         '''
#         run a test demo and save the state of the test demo
#         '''
#         data = {}
#         data_save = []
#         emsemble_save = []

#         for t in range (states_true.shape[0]):
#             out = model(test_demo[t])
#             state_out = np.array(out[0])
#             ensemble = np.array(tf.reshape(out[1], [num_ensemble, 5]))
#             # print('----------')
#             # print(ensemble)
#             data_save.append(state_out)
#             emsemble_save.append(ensemble)
#         data['state'] = data_save
#         data['ensemble'] = emsemble_save

#         with open('./output/ensemble_v2.7_norm_'+ name[index] +'_02.pkl', 'wb') as f:
#             pickle.dump(data, f)


# '''
# load loss functions
# '''
# get_loss = getloss()

# '''
# load data for training
# '''
# global name 
# name = ['constant', 'exp']
# global index
# index = 1
# observations, states_true = data_loader_function('./dataset/100_demos_'+name[index]+'.pkl')
# global count
# count = 0

# def main():

#     training = True
#     run_filter(training)

#     training = False
#     run_filter(training)

# if __name__ == "__main__":
#     main()