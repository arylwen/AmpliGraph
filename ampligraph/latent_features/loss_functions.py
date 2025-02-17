# Copyright 2019-2021 The AmpliGraph Authors. All Rights Reserved.
#
# This file is Licensed under the Apache License, Version 2.0.
# A copy of the Licence is available in LICENCE, or at:
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
#import tensorflow as tf
import tensorflow.compat.v1 as tf
import abc
import logging

LOSS_REGISTRY = {}

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Default margin used by pairwise and absolute margin loss
DEFAULT_MARGIN = 1

# default sampling temperature used by adversarial loss
DEFAULT_ALPHA_ADVERSARIAL = 0.5

# Default margin used by margin based adversarial loss
DEFAULT_MARGIN_ADVERSARIAL = 3

# Min score below which the values will be clipped before applying exponential
DEFAULT_CLIP_EXP_LOWER = -75.0

# Max score above which the values will be clipped before applying exponential
DEFAULT_CLIP_EXP_UPPER = 75.0

# Default label smoothing for ConvE
DEFAULT_LABEL_SMOOTHING = None

# Default label weighting for ConvE
DEFAULT_LABEL_WEIGHTING = False


def register_loss(name, external_params=None, class_params=None):
    if external_params is None:
        external_params = []

    default_class_params = {'require_same_size_pos_neg': True}

    if class_params is None:
        class_params = default_class_params

    def populate_class_params():
        LOSS_REGISTRY[name].class_params = {
            'require_same_size_pos_neg': class_params.get('require_same_size_pos_neg',
                                                          default_class_params['require_same_size_pos_neg'])
        }

    def insert_in_registry(class_handle):
        LOSS_REGISTRY[name] = class_handle
        class_handle.name = name
        LOSS_REGISTRY[name].external_params = external_params
        populate_class_params()
        return class_handle

    return insert_in_registry


def clip_before_exp(value):
    """Clip the value for stability of exponential
    """
    return tf.clip_by_value(value,
                            clip_value_min=DEFAULT_CLIP_EXP_LOWER,
                            clip_value_max=DEFAULT_CLIP_EXP_UPPER)


class Loss(abc.ABC):
    """Abstract class for loss function.
    """

    name = ""
    external_params = []
    class_params = {}

    def __init__(self, eta, hyperparam_dict, verbose=False):
        """Initialize Loss.

        Parameters
        ----------
        eta: int
            number of negatives
        hyperparam_dict : dict
            dictionary of hyperparams.
            (Keys are described in the hyperparameters section)
        """
        self._loss_parameters = {}
        self._dependencies = []

        # perform check to see if all the required external hyperparams are passed
        try:
            self._loss_parameters['eta'] = eta
            self._init_hyperparams(hyperparam_dict)
            if verbose:
                logger.info('\n--------- Loss ---------')
                logger.info('Name : {}'.format(self.name))
                for key, value in self._loss_parameters.items():
                    logger.info('{} : {}'.format(key, value))
        except KeyError as e:
            msg = 'Some of the hyperparams for loss were not passed to the loss function.\n{}'.format(e)
            logger.error(msg)
            raise Exception(msg)

    def get_state(self, param_name):
        """Get the state value.

        Parameters
        ----------
        param_name : string
            Name of the state for which one wants to query the value.
        Returns
        -------
        param_value:
            The value of the corresponding state.
        """
        try:
            param_value = LOSS_REGISTRY[self.name].class_params.get(param_name)
            return param_value
        except KeyError as e:
            msg = 'Invalid Keu.\n{}'.format(e)
            logger.error(msg)
            raise Exception(msg)

    def _init_hyperparams(self, hyperparam_dict):
        """Initializes the hyperparameters needed by the algorithm.

        Parameters
        ----------
        hyperparam_dict : dictionary
            Consists of key value pairs. The Loss will check the keys to get the corresponding params.
        """
        msg = 'This function is a placeholder in an abstract class'
        logger.error(msg)
        NotImplementedError(msg)

    def _inputs_check(self, scores_pos, scores_neg):
        """Creates any dependencies that need to be checked before performing loss computations

        Parameters
        ----------
        scores_pos : tf.Tensor
            A tensor of scores assigned to positive statements.
        scores_neg : tf.Tensor
            A tensor of scores assigned to negative statements.
        """
        logger.debug('Creating dependencies before loss computations.')
        self._dependencies = []
        if LOSS_REGISTRY[self.name].class_params['require_same_size_pos_neg'] and self._loss_parameters['eta'] != 1:
            logger.debug('Dependencies found: \n\tRequired same size positive and negative. \n\tEta is not 1.')
            self._dependencies.append(tf.Assert(tf.equal(tf.shape(scores_pos)[0], tf.shape(scores_neg)[0]),
                                                [tf.shape(scores_pos)[0], tf.shape(scores_neg)[0]]))

    def _apply(self, scores_pos, scores_neg):
        """Apply the loss function. Every inherited class must implement this function.
        (All the TF code must go in this function.)

        Parameters
        ----------
        scores_pos : tf.Tensor
            A tensor of scores assigned to positive statements.
        scores_neg : tf.Tensor
            A tensor of scores assigned to negative statements.

        Returns
        -------
        loss : tf.Tensor
            The loss value that must be minimized.
        """
        msg = 'This function is a placeholder in an abstract class.'
        logger.error(msg)
        NotImplementedError(msg)

    def apply(self, scores_pos, scores_neg):
        """Interface to external world.
        This function does the input checks, preprocesses input and finally applies loss function.

        Parameters
        ----------
        scores_pos : tf.Tensor
            A tensor of scores assigned to positive statements.
        scores_neg : tf.Tensor
            A tensor of scores assigned to negative statements.

        Returns
        -------
        loss : tf.Tensor
            The loss value that must be minimized.
        """
        self._inputs_check(scores_pos, scores_neg)
        with tf.control_dependencies(self._dependencies):
            loss = self._apply(scores_pos, scores_neg)
        return loss


@register_loss("pairwise", ['margin'])
class PairwiseLoss(Loss):
    r"""Pairwise, max-margin loss.

    Introduced in :cite:`bordes2013translating`.

    .. math::

        \mathcal{L}(\Theta) = \sum_{t^+ \in \mathcal{G}}\sum_{t^- \in \mathcal{C}}max(0, [\gamma + f_{model}(t^-;\Theta)
         - f_{model}(t^+;\Theta)])

    where :math:`\gamma` is the margin, :math:`\mathcal{G}` is the set of positives,
    :math:`\mathcal{C}` is the set of corruptions, :math:`f_{model}(t;\Theta)` is the model-specific scoring function.

    """

    def __init__(self, eta, loss_params=None, verbose=False):
        """Initialize Loss.

        Parameters
        ----------
        eta: int
            Number of negatives.
        loss_params : dict
            Dictionary of loss-specific hyperparams:

            - **'margin'**: (float). Margin to be used in pairwise loss computation (default: 1)

            Example: ``loss_params={'margin': 1}``
        """
        if loss_params is None:
            loss_params = {'margin': DEFAULT_MARGIN}
        super().__init__(eta, loss_params, verbose)

    def _init_hyperparams(self, hyperparam_dict):
        """Verifies and stores the hyperparameters needed by the algorithm.

        Parameters
        ----------
        hyperparam_dict : dictionary
            Consists of key value pairs. The Loss will check the keys to get the corresponding params

            - **margin** - Margin to be used in pairwise loss computation(default:1)
        """
        self._loss_parameters['margin'] = hyperparam_dict.get('margin', DEFAULT_MARGIN)

    def _apply(self, scores_pos, scores_neg):
        """Apply the loss function.

        Parameters
        ----------
        scores_pos : tf.Tensor, shape [n, 1]
            A tensor of scores assigned to positive statements.
        scores_neg : tf.Tensor, shape [n, 1]
            A tensor of scores assigned to negative statements.

        Returns
        -------
        loss : tf.Tensor
            The loss value that must be minimized.

        """
        margin = tf.constant(self._loss_parameters['margin'], dtype=tf.float32, name='margin')
        loss = tf.reduce_sum(tf.maximum(margin - scores_pos + scores_neg, 0))
        return loss


@register_loss("nll")
class NLLLoss(Loss):
    r"""Negative log-likelihood loss.

    As described in :cite:`trouillon2016complex`.

    .. math::

        \mathcal{L}(\Theta) = \sum_{t \in \mathcal{G} \cup \mathcal{C}}log(1 + exp(-y \, f_{model}(t;\Theta)))

    where :math:`y \in {-1, 1}` is the label of the statement, :math:`\mathcal{G}` is the set of positives,
    :math:`\mathcal{C}` is the set of corruptions, :math:`f_{model}(t;\Theta)` is the model-specific scoring function.

    """

    def __init__(self, eta, loss_params=None, verbose=False):
        """Initialize Loss.

        Parameters
        ----------
        eta: int
            Number of negatives.
        loss_params : dict
            Dictionary of hyperparams. No hyperparameters are required for this loss.
        """
        if loss_params is None:
            loss_params = {}
        super().__init__(eta, loss_params, verbose)

    def _init_hyperparams(self, hyperparam_dict):
        """Initializes the hyperparameters needed by the algorithm.

        Parameters
        ----------
        hyperparam_dict : dictionary
            Consists of key value pairs. The Loss will check the keys to get the corresponding params.
        """
        return

    def _apply(self, scores_pos, scores_neg):
        """Apply the loss function.

        Parameters
        ----------
        scores_pos : tf.Tensor, shape [n, 1]
            A tensor of scores assigned to positive statements.
        scores_neg : tf.Tensor, shape [n, 1]
            A tensor of scores assigned to negative statements.

        Returns
        -------
        loss : tf.Tensor
            The loss value that must be minimized.

        """
        scores_neg = clip_before_exp(scores_neg)
        scores_pos = clip_before_exp(scores_pos)
        scores = tf.concat([-scores_pos, scores_neg], 0)
        return tf.reduce_sum(tf.log(1 + tf.exp(scores)))


@register_loss("absolute_margin", ['margin'])
class AbsoluteMarginLoss(Loss):
    r"""Absolute margin , max-margin loss.

    Introduced in :cite:`Hamaguchi2017`.

    .. math::

        \mathcal{L}(\Theta) = \sum_{t^+ \in \mathcal{G}}\sum_{t^- \in \mathcal{C}} f_{model}(t^-;\Theta)
        - max(0, [\gamma - f_{model}(t^+;\Theta)])

    where :math:`\gamma` is the margin, :math:`\mathcal{G}` is the set of positives, :math:`\mathcal{C}` is the
    set of corruptions, :math:`f_{model}(t;\Theta)` is the model-specific scoring function.

    """

    def __init__(self, eta, loss_params=None, verbose=False):
        """Initialize Loss

        Parameters
        ----------
        eta: int
            Number of negatives.
        loss_params : dict
            Dictionary of loss-specific hyperparams:

            - **'margin'**: float. Margin to be used in pairwise loss computation (default:1)

            Example: ``loss_params={'margin': 1}``
        """
        if loss_params is None:
            loss_params = {'margin': DEFAULT_MARGIN}
        super().__init__(eta, loss_params, verbose)

    def _init_hyperparams(self, hyperparam_dict):
        """Initializes the hyperparameters needed by the algorithm.

        Parameters
        ----------
        hyperparam_dict : dict
           Consists of key value pairs. The Loss will check the keys to get the corresponding params.

           **margin** - Margin to be used in loss computation(default:1)

        Returns
        -------
        """
        self._loss_parameters['margin'] = hyperparam_dict.get('margin', DEFAULT_MARGIN)

    def _apply(self, scores_pos, scores_neg):
        """Apply the loss function.

        Parameters
        ----------
        scores_pos : tf.Tensor, shape [n, 1]
           A tensor of scores assigned to positive statements.
        scores_neg : tf.Tensor, shape [n, 1]
           A tensor of scores assigned to negative statements.

        Returns
        -------
        loss : tf.Tensor
           The loss value that must be minimized.

        """
        margin = tf.constant(self._loss_parameters['margin'], dtype=tf.float32, name='margin')
        loss = tf.reduce_sum(tf.maximum(margin + scores_neg, 0) - scores_pos)
        return loss


@register_loss("self_adversarial", ['margin', 'alpha'], {'require_same_size_pos_neg': False})
class SelfAdversarialLoss(Loss):
    r"""Self adversarial sampling loss.

    Introduced in :cite:`sun2018rotate`.

    .. math::

        \mathcal{L} = -log\, \sigma(\gamma + f_{model} (\mathbf{s},\mathbf{o}))
        - \sum_{i=1}^{n} p(h_{i}^{'}, r, t_{i}^{'} ) \ log \
        \sigma(-f_{model}(\mathbf{s}_{i}^{'},\mathbf{o}_{i}^{'}) - \gamma)

    where :math:`\mathbf{s}, \mathbf{o} \in \mathcal{R}^k` are the embeddings of the subject
    and object of a triple :math:`t=(s,r,o)`, :math:`\gamma` is the margin, :math:`\sigma` the sigmoid function,
    and :math:`p(s_{i}^{'}, r, o_{i}^{'} )` is the negatives sampling distribution which is defined as:

    .. math::

        p(s'_j, r, o'_j | \{(s_i, r_i, o_i)\}) = \frac{\exp \alpha \, f_{model}(\mathbf{s'_j}, \mathbf{o'_j})}
        {\sum_i \exp \alpha \, f_{model}(\mathbf{s'_i}, \mathbf{o'_i})}

    where :math:`\alpha` is the temperature of sampling, :math:`f_{model}` is the scoring function of
    the desired embeddings model.


    """

    def __init__(self, eta, loss_params=None, verbose=False):
        """Initialize Loss

        Parameters
        ----------
        eta: int
            number of negatives
        loss_params : dict
            Dictionary of loss-specific hyperparams:

            - **'margin'**: (float). Margin to be used for loss computation (default: 1)
            - **'alpha'** : (float). Temperature of sampling (default:0.5)

            Example: ``loss_params={'margin': 1, 'alpha': 0.5}``

        """
        if loss_params is None:
            loss_params = {'margin': DEFAULT_MARGIN_ADVERSARIAL, 'alpha': DEFAULT_ALPHA_ADVERSARIAL}
        super().__init__(eta, loss_params, verbose)

    def _init_hyperparams(self, hyperparam_dict):
        """Initializes the hyperparameters needed by the algorithm.

        Parameters
        ----------
        hyperparam_dict : dictionary
            Consists of key value pairs. The Loss will check the keys to get the corresponding params

            - **margin** - Margin to be used in adversarial loss computation (default:3)

            - **alpha** - Temperature of sampling (default:0.5)
        """
        self._loss_parameters['margin'] = hyperparam_dict.get('margin', DEFAULT_MARGIN_ADVERSARIAL)
        self._loss_parameters['alpha'] = hyperparam_dict.get('alpha', DEFAULT_ALPHA_ADVERSARIAL)

    def _apply(self, scores_pos, scores_neg):
        """Apply the loss function.

       Parameters
       ----------
       scores_pos : tf.Tensor, shape [n, 1]
           A tensor of scores assigned to positive statements.
       scores_neg : tf.Tensor, shape [n*negative_count, 1]
           A tensor of scores assigned to negative statements.

       Returns
       -------
       loss : tf.Tensor
           The loss value that must be minimized.

       """
        margin = tf.constant(self._loss_parameters['margin'], dtype=tf.float32, name='margin')
        alpha = tf.constant(self._loss_parameters['alpha'], dtype=tf.float32, name='alpha')

        # Compute p(neg_samples) based on eq 4
        scores_neg_reshaped = tf.reshape(scores_neg, [self._loss_parameters['eta'], tf.shape(scores_pos)[0]])
        p_neg = tf.nn.softmax(alpha * scores_neg_reshaped, axis=0)

        # Compute Loss based on eg 5
        loss = tf.reduce_sum(-tf.log_sigmoid(margin - tf.negative(scores_pos))) - tf.reduce_sum(
            tf.multiply(p_neg, tf.log_sigmoid(tf.negative(scores_neg_reshaped) - margin)))

        return loss


@register_loss("multiclass_nll", [], {'require_same_size_pos_neg': False})
class NLLMulticlass(Loss):
    r"""Multiclass NLL Loss.

    Introduced in :cite:`chen2015` where both the subject and objects are corrupted (to use it in this way pass
    corrupt_sides = ['s', 'o'] to embedding_model_params) .

    This loss was re-engineered in :cite:`kadlecBK17` where only the object was corrupted to get improved
    performance (to use it in this way pass corrupt_sides = 'o' to embedding_model_params).

    .. math::

        \mathcal{L(X)} = -\sum_{x_{e_1,e_2,r_k} \in X} log\,p(e_2|e_1,r_k)
         -\sum_{x_{e_1,e_2,r_k} \in X} log\,p(e_1|r_k, e_2)

    Examples
    --------
    >>> from ampligraph.latent_features import TransE
    >>> model = TransE(batches_count=1, seed=555, epochs=20, k=10,
    >>>                embedding_model_params={'corrupt_sides':['s', 'o']},
    >>>                loss='multiclass_nll', loss_params={})

    """
    def __init__(self, eta, loss_params=None, verbose=False):
        """Initialize Loss

        Parameters
        ----------
        eta: int
            number of negatives
        loss_params : dict
            Dictionary of loss-specific hyperparams:

        """
        if loss_params is None:
            loss_params = {}
        super().__init__(eta, loss_params, verbose)

    def _init_hyperparams(self, hyperparam_dict):
        """Verifies and stores the hyperparameters needed by the algorithm.

        Parameters
        ----------
        hyperparam_dict : dictionary
            Consists of key value pairs. The Loss will check the keys to get the corresponding params
        """
        pass

    def _apply(self, scores_pos, scores_neg):
        """Apply the loss function.

       Parameters
       ----------
       scores_pos : tf.Tensor, shape [n, 1]
           A tensor of scores assigned to positive statements.
       scores_neg : tf.Tensor, shape [n*negative_count, 1]
           A tensor of scores assigned to negative statements.

       Returns
       -------
       loss : float
           The loss value that must be minimized.

       """
        # Fix for numerical instability of multiclass loss
        scores_pos = clip_before_exp(scores_pos)
        scores_neg = clip_before_exp(scores_neg)

        scores_neg_reshaped = tf.reshape(scores_neg, [self._loss_parameters['eta'], tf.shape(scores_pos)[0]])
        neg_exp = tf.exp(scores_neg_reshaped)
        pos_exp = tf.exp(scores_pos)
        softmax_score = pos_exp / (tf.reduce_sum(neg_exp, axis=0) + pos_exp)

        loss = -tf.reduce_sum(tf.log(softmax_score))
        return loss


@register_loss('bce', ['label_smoothing', 'label_weighting'], {'require_same_size_pos_neg': False})
class BCELoss(Loss):
    r""" Binary Cross Entropy Loss.

        .. math::

            \mathcal{L} = - \frac{1}{N} \sum_{i=1}^{N} y_i \cdot log(p(y_i)) + (1-y_i) \cdot log(1-p(y_i))

        Examples
        --------
        >>> from ampligraph.latent_features.models import ConvE
        >>> model = ConvE(batches_count=1, seed=555, epochs=20, k=10, loss='bce', loss_params={})
    """

    def __init__(self, eta, loss_params={}, verbose=False):
        """Initialize Loss

        Parameters
        ----------
        loss_params : dict
            Dictionary of loss-specific hyperparams:

        """
        super().__init__(eta, loss_params, verbose)

    def _inputs_check(self, y_true, y_pred):
        """ Creates any dependencies that need to be checked before performing loss computations

        Parameters
        ----------
        y_true : tf.Tensor
            A tensor of ground truth values.
        y_pred : tf.Tensor
            A tensor of predicted values.
        """

        logger.debug('Creating dependencies before loss computations.')

        self._dependencies = []
        logger.debug('Dependencies found: \n\tRequired same size y_true and y_pred. ')
        self._dependencies.append(tf.Assert(tf.equal(tf.shape(y_pred)[0], tf.shape(y_true)[0]),
                                            [tf.shape(y_pred)[0], tf.shape(y_true)[0]]))

        if self._loss_parameters['label_smoothing'] is not None:
            if 'num_entities' not in self._loss_parameters.keys():
                msg = "To apply label smoothing the number of entities must be known. " \
                      "Set using '_set_hyperparams('num_entities', value)'."
                logger.error(msg)
                raise Exception(msg)

    def _init_hyperparams(self, hyperparam_dict):
        """ Verifies and stores the hyperparameters needed by the algorithm.

        Parameters
        ----------
        hyperparam_dict : dictionary
            Consists of key value pairs. The Loss will check the keys to get the corresponding params
            - **label_smoothing** (float): Apply label smoothing to vector of true labels. Can improve multi-class
            classification training by using soft targets that are a weighted average of hard targets and the
            uniform distribution over labels. Default: None
            - **label_weighting** (bool): Apply label weighting to vector of true labels. Gives lower weight to
            outputs with more positives in one-hot vector. Default: False

        """

        self._loss_parameters['label_smoothing'] = hyperparam_dict.get('label_smoothing', DEFAULT_LABEL_SMOOTHING)
        self._loss_parameters['label_weighting'] = hyperparam_dict.get('label_weighting', DEFAULT_LABEL_WEIGHTING)

    def _set_hyperparams(self, key, value):
        """ Set a hyperparameter needed by the loss function.

        Parameters
        ----------
        key : key for hyperparams dictionary
        value : value for hyperparams dictionary

        Returns
        -------

        """

        if key in self._loss_parameters.keys():
            msg = '{} already exists in loss hyperparameters dict with value {} \n' \
                  'Overriding with value {}.'.format(key, self._loss_parameters[key], value)
            logger.info(msg)

        self._loss_parameters[key] = value

    def apply(self, y_true, y_pred):
        """ Interface to external world.
        This function does the input checks, preprocesses input and finally applies loss function.

        Parameters
        ----------
        y_true : tf.Tensor
            A tensor of ground truth values.
        y_true : tf.Tensor
            A tensor of predicted values.

        Returns
        -------
        loss : tf.Tensor
            The loss value that must be minimized.
        """
        self._inputs_check(y_true, y_pred)
        with tf.control_dependencies(self._dependencies):
            loss = self._apply(y_true, y_pred)
        return loss

    def _apply(self, y_true, y_pred):
        """ Apply the loss function.

        Parameters
        ----------
        y_true : tf.Tensor
            A tensor of true values.
        y_pred : tf.Tensor
            A tensor of predicted values.

       Returns
       -------
       loss : float
           The loss value that must be minimized.

       """

        if self._loss_parameters['label_smoothing'] is not None:
            y_true = tf.add((1 - self._loss_parameters['label_smoothing']) * y_true,
                            (self._loss_parameters['label_smoothing']) / self._loss_parameters['num_entities'])

        if self._loss_parameters['label_weighting']:

            eps = 1e-6
            wt = tf.reduce_mean(y_true)
            loss = -tf.reduce_sum((1 - wt) * y_true * tf.log_sigmoid(y_pred)
                                  + wt * (1 - y_true) * tf.log(1 - tf.sigmoid(y_pred) + eps))

        else:
            loss = tf.reduce_sum(tf.nn.sigmoid_cross_entropy_with_logits(labels=y_true, logits=y_pred))

        return loss
