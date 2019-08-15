from . import layers
from . import loaders
from . import definitions
import numpy as np
import tensorflow as tf
import os
import json
import datetime
import time
import warnings
import copy
from collections.abc import Sequence
import math
from scipy.special import expit
from PIL import Image
from tqdm import tqdm


class DPPModel(object):
    """
    The DPPModel class represents a model which can either be trained, or loaded from an existing checkpoint file.
        This class is the singular point of contact for the DPP module.
    """
    # Operation settings
    _problem_type = definitions.ProblemType.CLASSIFICATION
    _loss_fn = 'softmax cross entropy'
    _with_patching = False
    _has_trained = False
    _save_checkpoints = None
    _save_dir = None
    _validation = True
    _testing = True
    _hyper_param_search = False

    # Input options
    _total_classes = 0
    _total_raw_samples = 0
    _total_training_samples = 0
    _total_validation_samples = 0
    _total_testing_samples = 0

    _image_width = None
    _image_height = None
    _image_width_original = None
    _image_height_original = None
    _image_depth = None
    _patch_height = None
    _patch_width = None
    _resize_bbox_coords = False

    _crop_or_pad_images = False
    _resize_images = False

    _processed_images_dir = './DPP-Processed'

    # supported implementations, we may add more to in future
    _supported_problem_types = ['classification', 'regression', 'semantic_segmentation', 'object_detection']
    _supported_optimizers = ['adam', 'adagrad', 'adadelta', 'sgd', 'sgd_momentum']
    _supported_weight_initializers = ['normal', 'xavier']
    _supported_activation_functions = ['relu', 'tanh', 'lrelu', 'selu']
    _supported_pooling_types = ['max', 'avg']
    _supported_loss_fns_cls = ['softmax cross entropy']  # supported loss functions for classification
    _supported_loss_fns_reg = ['l2', 'l1', 'smooth l1', 'log loss']  # ... regression
    _supported_loss_fns_ss = ['sigmoid cross entropy']  # ... semantic segmentation
    _supported_loss_fns_od = ['yolo']  # ... object detection
    _supported_predefined_models = ['vgg-16', 'alexnet', 'yolov2', 'xsmall', 'small', 'medium', 'large']

    # Augmentation options
    _augmentation_flip_horizontal = False
    _augmentation_flip_vertical = False
    _augmentation_crop = False
    _crop_amount = 0.75
    _augmentation_contrast = False
    _augmentation_rotate = False
    _rotate_crop_borders = False

    # Dataset storage
    _all_ids = None

    _all_images = None
    _train_images = None
    _test_images = None
    _val_images = None

    _all_labels = None
    _train_labels = None
    _test_labels = None
    _val_labels = None
    _split_labels = True

    _images_only = False

    _raw_image_files = None
    _raw_labels = None

    _raw_test_image_files = None
    _raw_train_image_files = None
    _raw_val_image_files = None
    _raw_test_labels = None
    _raw_train_labels = None
    _raw_val_labels = None

    _all_moderation_features = None
    _has_moderation = False
    _moderation_features_size = None
    _train_moderation_features = None
    _test_moderation_features = None
    _val_moderation_features = None

    _training_augmentation_images = None
    _training_augmentation_labels = None

    # Network internal representation
    _session = None
    _graph = None
    _graph_ops = {}
    _layers = []
    _global_epoch = 0

    _num_layers_norm = 0
    _num_layers_conv = 0
    _num_layers_upsample = 0
    _num_layers_pool = 0
    _num_layers_fc = 0
    _num_layers_dropout = 0
    _num_layers_batchnorm = 0

    # Network options
    _batch_size = 1
    _test_split = 0.10
    _validation_split = 0.10
    _maximum_training_batches = None
    _reg_coeff = None
    _optimizer = 'adam'
    _weight_initializer = 'xavier'

    _learning_rate = 0.001
    _lr_decay_factor = None
    _epochs_per_decay = None
    _lr_decay_epochs = None

    _num_regression_outputs = 1

    # Yolo parameters, non-default values defined by set_yolo_parameters
    _grid_w = 7
    _grid_h = 7
    _LABELS = ['plant']
    _NUM_CLASSES = 1
    _RAW_ANCHORS = [(159, 157), (103, 133), (91, 89), (64, 65), (142, 101)]
    _ANCHORS = None  # Scaled version, but grid and image sizes are needed so default is deferred
    _NUM_BOXES = 5
    _THRESH_SIG = 0.6
    _THRESH_OVERLAP = 0.3
    _THRESH_CORRECT = 0.5

    # Wrapper options
    _debug = None
    _load_from_saved = None
    _tb_dir = None
    _queue_capacity = 50
    _report_rate = None

    # Multithreading
    _num_threads = 1
    _coord = None
    _threads = None

    def __init__(self, debug=False, load_from_saved=False, save_checkpoints=True, initialize=True, tensorboard_dir=None,
                 report_rate=100, save_dir=None):
        """
        Create a new model object

        :param debug: If True, debug messages are printed to the console.
        :param load_from_saved: Optionally, pass the name of a directory containing the checkpoint file.
        :param save_checkpoints: If True, trainable parameters will be saved at intervals during training.
        :param initialize: If False, a new Tensorflow session will not be initialized with the instance.
        :param tensorboard_dir: Optionally, provide the path to your Tensorboard logs directory.
        :param report_rate: Set the frequency at which progress is reported during training (also the rate at which new
        timepoints are recorded to Tensorboard).
        """
        self._debug = debug
        self._load_from_saved = load_from_saved
        self._tb_dir = tensorboard_dir
        self._report_rate = report_rate
        self._save_checkpoints = save_checkpoints
        self._save_dir = save_dir

        # Add the run level to the tensorboard path
        if self._tb_dir is not None:
            self._tb_dir = "{0}/{1}".format(self._tb_dir, datetime.datetime.now().strftime("%d%B%Y%I:%M%p"))

        if initialize:
            self.__log('TensorFlow loaded...')

            self.__reset_graph()
            self.__reset_session()

    def __log(self, message):
        if self._debug:
            print('{0}: {1}'.format(datetime.datetime.now().strftime("%I:%M%p"), message))

    def __last_layer(self):
        return self._layers[-1]

    def __last_layer_outputs_volume(self):
        return isinstance(self.__last_layer().output_size, (list,))

    def __first_layer(self):
        return next(layer for layer in self._layers if
                    isinstance(layer, layers.convLayer) or isinstance(layer, layers.fullyConnectedLayer))

    def __reset_session(self):
        self._session = tf.Session(graph=self._graph)

    def __reset_graph(self):
        self._graph = tf.Graph()

    def __initialize_queue_runners(self):
        self.__log('Initializing queue runners...')
        self._coord = tf.train.Coordinator()
        self._threads = tf.train.start_queue_runners(sess=self._session, coord=self._coord)

    def set_number_of_threads(self, num_threads):
        """Set number of threads for input queue runners and preprocessing tasks"""
        if not isinstance(num_threads, int):
            raise TypeError("num_threads must be an int")
        if num_threads <= 0:
            raise ValueError("num_threads must be positive")

        self._num_threads = num_threads

    def set_processed_images_dir(self, im_dir):
        """Set the directory for storing processed images when pre-processing is used"""
        if not isinstance(im_dir, str):
            raise TypeError("im_dir must be a str")

        self._processed_images_dir = im_dir

    def set_batch_size(self, size):
        """Set the batch size"""
        if not isinstance(size, int):
            raise TypeError("size must be an int")
        if size <= 0:
            raise ValueError("size must be positive")

        self._batch_size = size
        self._queue_capacity = size * 5

    def set_train_test_split(self, ratio):
        """DEPRECATED
        Set a ratio for the total number of samples to use as a training set, using the rest of the samples for testing
        (i.e. no validation samples)"""
        if not isinstance(ratio, float) and ratio != 1:
            raise TypeError("ratio must be a float or 1")
        if ratio <= 0 or ratio > 1:
            raise ValueError("ratio must be between 0 and 1")
        warnings.warn("set_train_test_split() is deprecated and will be removed soon. " +
                      "Use set_test_split() and set_validation_split() instead. See docs for more information.")

        self._test_split = 1 - ratio
        if ratio == 1 or ratio is None:
            self._testing = False
        else:
            self._testing = True
        self._validation = False
        self._validation_split = 0

    def set_test_split(self, ratio):
        """Set a ratio for the total number of samples to use as a testing set"""
        if not isinstance(ratio, float) and ratio != 0:
            raise TypeError("ratio must be a float or 0")
        if ratio < 0 or ratio > 1:
            raise ValueError("ratio must be between 0 and 1")

        if ratio == 0 or ratio is None:
            self._testing = False
            ratio = 0
        else:
            self._testing = True
        self._test_split = ratio
        if self._test_split + self._validation_split > 0.5:
            warnings.warn('WARNING: Less than 50% of data is being used for training. ' +
                          '({test}% testing and {val}% validation)'.format(test=int(self._test_split * 100),
                                                                           val=int(self._validation_split * 100)))

    def set_validation_split(self, ratio):
        """Set a ratio for the total number of samples to use as a validation set"""
        if not isinstance(ratio, float) and ratio != 0:
            raise TypeError("ratio must be a float or 0")
        if ratio < 0 or ratio > 1:
            raise ValueError("ratio must be between 0 and 1")

        if ratio == 0 or ratio is None:
            self._validation = False
            ratio = 0
        else:
            self._validation = True
        self._validation_split = ratio
        if self._test_split + self._validation_split > 0.5:
            warnings.warn('WARNING: Less than 50% of data is being used for training. ' +
                          '({test}% testing and {val}% validation)'.format(test=int(self._test_split * 100),
                                                                           val=int(self._validation_split * 100)))

    def set_maximum_training_epochs(self, epochs):
        """Set the max number of training epochs"""
        if not isinstance(epochs, int):
            raise TypeError("epochs must be an int")
        if epochs <= 0:
            raise ValueError("epochs must be positive")

        self._maximum_training_batches = epochs

    def set_learning_rate(self, rate):
        """Set the initial learning rate"""
        if not isinstance(rate, float):
            raise TypeError("rate must be a float")
        if rate <= 0:
            raise ValueError("rate must be positive")

        self._learning_rate = rate

    def set_crop_or_pad_images(self, crop_or_pad):
        """Apply padding or cropping images to, which is required if the dataset has images of different sizes"""
        if not isinstance(crop_or_pad, bool):
            raise TypeError("crop_or_pad must be a bool")

        self._crop_or_pad_images = crop_or_pad

    def set_resize_images(self, resize):
        """Up-sample or down-sample images to specified size"""
        if not isinstance(resize, bool):
            raise TypeError("resize must be a bool")

        self._resize_images = resize

    def set_augmentation_flip_horizontal(self, flip):
        """Randomly flip training images horizontally"""
        if not isinstance(flip, bool):
            raise TypeError("flip must be a bool")
        if self._problem_type not in [definitions.ProblemType.CLASSIFICATION,
                                      definitions.ProblemType.REGRESSION]:
            raise RuntimeError("Flip augmentations are incompatible with the current problem type")

        self._augmentation_flip_horizontal = flip

    def set_augmentation_flip_vertical(self, flip):
        """Randomly flip training images vertically"""
        if not isinstance(flip, bool):
            raise TypeError("flip must be a bool")
        if self._problem_type not in [definitions.ProblemType.CLASSIFICATION,
                                      definitions.ProblemType.REGRESSION]:
            raise RuntimeError("Flip augmentations are incompatible with the current problem type")

        self._augmentation_flip_vertical = flip

    def set_augmentation_crop(self, resize, crop_ratio=0.75):
        """Randomly crop images during training, and crop images to center during testing"""
        if not isinstance(resize, bool):
            raise TypeError("resize must be a bool")
        if not isinstance(crop_ratio, float):
            raise TypeError("crop_ratio must be a float")
        if crop_ratio <= 0 or crop_ratio > 1:
            raise ValueError("crop_ratio must be in (0, 1]")
        if self._problem_type not in [definitions.ProblemType.CLASSIFICATION,
                                      definitions.ProblemType.REGRESSION]:
            raise RuntimeError("Crop augmentations are incompatible with the current problem type")

        self._augmentation_crop = resize
        self._crop_amount = crop_ratio

    def set_augmentation_brightness_and_contrast(self, contr):
        """Randomly adjust contrast and/or brightness on training images"""
        if not isinstance(contr, bool):
            raise TypeError("contr must be a bool")

        self._augmentation_contrast = contr

    def set_augmentation_rotation(self, rot, crop_borders=False):
        """Randomly rotate training images"""
        if not isinstance(rot, bool):
            raise TypeError("rot must be a bool")
        if not isinstance(crop_borders, bool):
            raise TypeError("crop_borders must be a bool")
        if self._problem_type not in [definitions.ProblemType.CLASSIFICATION,
                                      definitions.ProblemType.REGRESSION]:
            raise RuntimeError("Rotation augmentations are incompatible with the current problem type")

        self._augmentation_rotate = rot
        self._rotate_crop_borders = crop_borders

    def set_regularization_coefficient(self, lamb):
        """Set lambda for L2 weight decay"""
        if not isinstance(lamb, float):
            raise TypeError("lamb must be a float")
        if lamb <= 0:
            raise ValueError("lamb must be positive")

        self._reg_coeff = lamb

    def set_learning_rate_decay(self, decay_factor, epochs_per_decay):
        """Set learning rate decay"""
        if not isinstance(decay_factor, float):
            raise TypeError("decay_factor must be a float")
        if decay_factor <= 0:
            raise ValueError("decay_factor must be positive")
        if not isinstance(epochs_per_decay, int):
            raise TypeError("epochs_per_day must be an int")
        if epochs_per_decay <= 0:
            raise ValueError("epochs_per_day must be positive")

        self._lr_decay_factor = decay_factor
        self._epochs_per_decay = epochs_per_decay

    def set_optimizer(self, optimizer):
        """Set the optimizer to use"""
        if not isinstance(optimizer, str):
            raise TypeError("optimizer must be a str")
        if optimizer.lower() in self._supported_optimizers:
            optimizer = optimizer.lower()
        else:
            raise ValueError("'" + optimizer + "' is not one of the currently supported optimizers. Choose one of " +
                             " ".join("'" + x + "'" for x in self._supported_optimizers))

        self._optimizer = optimizer

    def set_loss_function(self, loss_fn):
        """Set the loss function to use"""
        if not isinstance(loss_fn, str):
            raise TypeError("loss_fn must be a str")
        loss_fn = loss_fn.lower()

        type_loss_map = {definitions.ProblemType.CLASSIFICATION: self._supported_loss_fns_cls,
                         definitions.ProblemType.REGRESSION: self._supported_loss_fns_reg,
                         definitions.ProblemType.SEMANTIC_SEGMETNATION: self._supported_loss_fns_ss,
                         definitions.ProblemType.OBJECT_DETECTION: self._supported_loss_fns_od}

        if self._problem_type not in type_loss_map.keys():
            warnings.warn("Problem type not recognized. See documentation for list of supported functions and problem "
                          "types.")
            exit()

        if loss_fn not in type_loss_map[self._problem_type]:
            raise ValueError("'" + loss_fn + "' is not one of the currently supported loss functions for " +
                             self._problem_type.name + ". Make sure you have the correct problem type set with " +
                             "DPPModel.set_problem_type() first, or choose one of " +
                             " ".join("'" + x + "'" for x in type_loss_map[self._problem_type]))

        self._loss_fn = loss_fn

    def set_weight_initializer(self, initializer):
        """Set the initialization scheme used by convolutional and fully connected layers"""
        if not isinstance(initializer, str):
            raise TypeError("initializer must be a str")
        initializer = initializer.lower()
        if not initializer in self._supported_weight_initializers:
            raise ValueError("'" + initializer + "' is not one of the currently supported weight initializers." +
                             " Choose one of: " + " ".join("'"+x+"'" for x in self._supported_weight_initializers))

        self._weight_initializer = initializer

    def set_image_dimensions(self, image_height, image_width, image_depth):
        """Specify the image dimensions for images in the dataset (depth is the number of channels)"""
        if not isinstance(image_height, int):
            raise TypeError("image_height must be an int")
        if image_height <= 0:
            raise ValueError("image_height must be positive")
        if not isinstance(image_width, int):
            raise TypeError("image_width must be an int")
        if image_width <= 0:
            raise ValueError("image_width must be positive")
        if not isinstance(image_depth, int):
            raise TypeError("image_depth must be an int")
        if image_depth <= 0:
            raise ValueError("image_depth must be positive")

        self._image_width = image_width
        self._image_height = image_height
        self._image_depth = image_depth

        # Generate image-scaled anchors for YOLO object detection
        if self._RAW_ANCHORS:
            scale_w = self._grid_w / self._image_width
            scale_h = self._grid_h / self._image_height
            self._ANCHORS = [(anchor[0] * scale_w, anchor[1] * scale_h) for anchor in self._RAW_ANCHORS]

    def set_original_image_dimensions(self, image_height, image_width):
        """
        Specify the original size of the image, before resizing.
        This is only needed in special cases, for instance, if you are resizing input images but using image coordinate
        labels which reference the original size.
        """
        if not isinstance(image_height, int):
            raise TypeError("image_height must be an int")
        if image_height <= 0:
            raise ValueError("image_height must be positive")
        if not isinstance(image_width, int):
            raise TypeError("image_width must be an int")
        if image_width <= 0:
            raise ValueError("image_width must be positive")

        self._image_width_original = image_width
        self._image_height_original = image_height

    def add_moderation_features(self, moderation_features):
        """Specify moderation features for examples in the dataset"""
        self._has_moderation = True
        self._moderation_features_size = moderation_features.shape[1]
        self._all_moderation_features = moderation_features

    def set_problem_type(self, p_type):
        """Set the problem type to be solved, either classification or regression"""
        if not isinstance(p_type, str):
            raise TypeError("p_type must be a str")
        if not p_type in self._supported_problem_types:
            raise ValueError("'" + p_type + "' is not one of the currently supported problem types." +
                             " Choose one of: " + " ".join("'"+x+"'" for x in self._supported_problem_types))

        if p_type == 'classification':
            self._problem_type = definitions.ProblemType.CLASSIFICATION
            self._loss_fn = self._supported_loss_fns_cls[0]
        elif p_type == 'regression':
            self._problem_type = definitions.ProblemType.REGRESSION
            self._loss_fn = self._supported_loss_fns_reg[0]
        elif p_type == 'semantic_segmentation':
            self._problem_type = definitions.ProblemType.SEMANTIC_SEGMETNATION
            self._loss_fn = self._supported_loss_fns_ss[0]
        elif p_type == 'object_detection':
            self._problem_type = definitions.ProblemType.OBJECT_DETECTION
            self._loss_fn = self._supported_loss_fns_od[0]
        else:
            warnings.warn('Problem p_type specified not supported')
            exit()

    def set_patch_size(self, height, width):
        if not isinstance(height, int):
            raise TypeError("height must be an int")
        if height <= 0:
            raise ValueError("height must be positive")
        if not isinstance(width, int):
            raise TypeError("width must be an int")
        if width <= 0:
            raise ValueError("width must be positive")

        self._patch_height = height
        self._patch_width = width
        self._with_patching = True

    def _yolo_compute_iou(self, pred_box, true_box):
        """Helper function to compute the intersection over union of pred_box and true_box
        pred_box and true_box represent multiple boxes with coords being x,y,w,h (0-indexed 0-3)"""
        # numerator
        # get coords of intersection rectangle, then compute intersection area
        x1 = tf.maximum(pred_box[..., 0] - 0.5 * pred_box[..., 2],
                        true_box[..., 0:1] - 0.5 * true_box[..., 2:3])
        y1 = tf.maximum(pred_box[..., 1] - 0.5 * pred_box[..., 3],
                        true_box[..., 1:2] - 0.5 * true_box[..., 3:4])
        x2 = tf.minimum(pred_box[..., 0] + 0.5 * pred_box[..., 2],
                        true_box[..., 0:1] + 0.5 * true_box[..., 2:3])
        y2 = tf.minimum(pred_box[..., 1] + 0.5 * pred_box[..., 3],
                        true_box[..., 1:2] + 0.5 * true_box[..., 3:4])
        intersection_area = tf.multiply(tf.maximum(0., x2 - x1), tf.maximum(0., y2 - y1))

        # denominator
        # compute area of pred and truth, compute union area
        pred_area = tf.multiply(pred_box[..., 2], pred_box[..., 3])
        true_area = tf.multiply(true_box[..., 2:3], true_box[..., 3:4])
        union_area = tf.subtract(tf.add(pred_area, true_area), intersection_area)

        # compute iou
        iou = tf.divide(intersection_area, union_area)
        return iou

    def _yolo_loss_function(self, y_true, y_pred):
        """
        Loss function based on YOLO
        See the paper for details: https://pjreddie.com/media/files/papers/yolo.pdf

        :param y_true: Tensor with ground truth bounding boxes for each grid square in each image. Labels have 6
        elements: [object/no-object, class, x, y, w, h]
        :param y_pred: Tensor with predicted bounding boxes for each grid square in each image. Predictions consist of
        one box and confidence [x, y, w, h, conf] for each anchor plus 1 element for specifying the class (only one atm)
        :return Scalar Tensor with the Yolo loss for the bounding box predictions
        """

        prior_boxes = tf.convert_to_tensor(self._ANCHORS)

        # object/no-object masks #
        # create masks for grid cells with objects and with no objects
        obj_mask = tf.cast(y_true[..., 0], dtype=bool)
        no_obj_mask = tf.logical_not(obj_mask)
        obj_pred = tf.boolean_mask(y_pred, obj_mask)
        obj_true = tf.boolean_mask(y_true, obj_mask)
        no_obj_pred = tf.boolean_mask(y_pred, no_obj_mask)

        # bbox coordinate loss #
        # build a tensor of the predicted bounding boxes and confidences, classes will be stored separately
        # [x1,y1,w1,h1,conf1,x2,y2,w2,h2,conf2,x3,y3,w3,h3,conf3,...]
        pred_classes = obj_pred[..., self._NUM_BOXES * 5:]
        # we take the x,y,w,h,conf's that are altogether (dim is 1xB*5) and turn into Bx5, where B is num_boxes
        obj_pred = tf.reshape(obj_pred[..., 0:self._NUM_BOXES * 5], [-1, self._NUM_BOXES, 5])
        no_obj_pred = tf.reshape(no_obj_pred[..., 0:self._NUM_BOXES * 5], [-1, self._NUM_BOXES, 5])
        t_x, t_y, t_w, t_h = obj_pred[..., 0], obj_pred[..., 1], obj_pred[..., 2], obj_pred[..., 3]
        t_o = obj_pred[..., 4]
        pred_x = tf.sigmoid(t_x) + 0.00001  # concerned about underflow (might not actually be necessary)
        pred_y = tf.sigmoid(t_y) + 0.00001
        pred_w = (tf.exp(t_w) + 0.00001) * prior_boxes[:, 0]
        pred_h = (tf.exp(t_h) + 0.00001) * prior_boxes[:, 1]
        pred_conf = tf.sigmoid(t_o) + 0.00001
        predicted_boxes = tf.stack([pred_x, pred_y, pred_w, pred_h, pred_conf], axis=2)

        # find responsible boxes by computing iou's and select the best one
        ious = self._yolo_compute_iou(
            predicted_boxes, obj_true[..., 1 + self._NUM_CLASSES:1 + self._NUM_CLASSES + 4])
        greatest_iou_indices = tf.argmax(ious, 1)
        argmax_one_hot = tf.one_hot(indices=greatest_iou_indices, depth=5)
        resp_box_mask = tf.cast(argmax_one_hot, dtype=bool)
        responsible_boxes = tf.boolean_mask(predicted_boxes, resp_box_mask)

        # compute loss on responsible boxes
        loss_xy = tf.square(tf.subtract(responsible_boxes[..., 0:2],
                                        obj_true[..., 1+self._NUM_CLASSES:1 + self._NUM_CLASSES + 2]))
        loss_wh = tf.square(tf.subtract(tf.sqrt(responsible_boxes[..., 2:4]),
                                        tf.sqrt(obj_true[..., 1 + self._NUM_CLASSES + 2:1 + self._NUM_CLASSES + 4])))
        coord_loss = tf.reduce_sum(tf.add(loss_xy, loss_wh))

        # confidence loss #
        # grids that do contain an object, 1 * iou means we simply take the difference between the
        # iou's and the predicted confidence

        # this was to make responsible boxes confidences aim to go to 1 instead of their current iou score, this is
        # still being tested
        # non_resp_box_mask = tf.logical_not(resp_box_mask)
        # non_responsible_boxes = tf.boolean_mask(predicted_boxes, non_resp_box_mask)
        # non_responsible_ious = tf.boolean_mask(ious, non_resp_box_mask)
        # loss1 = tf.reduce_sum(tf.square(1 - responsible_boxes[..., 4]))
        # loss2 = tf.reduce_sum(tf.square(tf.subtract(non_responsible_ious, non_responsible_boxes[..., 4])))
        # loss_obj = loss1 + loss2

        # this is how the paper does it, the above 6 lines is experimental
        obj_num_grids = tf.shape(predicted_boxes)[0]  # [num_boxes, 5, 5]
        loss_obj = tf.cast((1/obj_num_grids), dtype='float32') * tf.reduce_sum(
            tf.square(tf.subtract(ious, predicted_boxes[..., 4])))

        # grids that do not contain an object, 0 * iou means we simply take the predicted confidences of the
        # grids that do not have an object and square and sum (because they should be 0)
        no_obj_confs = tf.sigmoid(no_obj_pred[..., 4])
        no_obj_num_grids = tf.shape(no_obj_confs)[0]  # [number_of_grids_without_an_object, 5]
        loss_no_obj = tf.cast(1/no_obj_num_grids, dtype='float32') * tf.reduce_sum(tf.square(no_obj_confs))
        # incase obj_pred or no_obj_confs is empty (e.g. no objects in the image) we need to make sure we dont
        # get nan's in our losses...
        loss_obj = tf.cond(tf.count_nonzero(y_true[..., 4]) > 0, lambda: loss_obj, lambda: 0.)
        loss_no_obj = tf.cond(tf.count_nonzero(y_true[..., 4]) < self._grid_w * self._grid_h,
                              lambda: loss_no_obj, lambda: 0.)
        conf_loss = tf.add(loss_obj, loss_no_obj)

        # classification loss #
        # currently only one class, plant, will need to be made more general for multi-class in the future
        class_probs_pred = tf.nn.softmax(pred_classes)
        class_diffs = tf.subtract(obj_true[..., 1:1+self._NUM_CLASSES], class_probs_pred)
        class_loss = tf.reduce_sum(tf.square(class_diffs))

        total_loss = coord_loss + conf_loss + class_loss

        # for some debug/checking, otherwise leave commented #
        # init_op = tf.global_variables_initializer()
        # self._session.run(init_op)
        # self.__initialize_queue_runners()
        # print('printing losses')
        # print(self._session.run([loss_obj, loss_no_obj]))
        # print(self._session.run([coord_loss, conf_loss, class_loss]))
        # print(self._session.run([loss_obj, loss_no_obj]))
        # print(self._session.run([coord_loss, conf_loss, class_loss]))
        # print(self._session.run([loss_obj, loss_no_obj]))
        # print(self._session.run([coord_loss, conf_loss, class_loss]))

        return total_loss

    def __add_layers_to_graph(self):
        """
        Adds the layers in self.layers to the computational graph.

        Currently __assemble_graph is doing too many things, so this is needed as a separate function so that other
        functions such as load_state can add layers to the graph without performing everything else in asseble_graph
        """
        for layer in self._layers:
            if callable(getattr(layer, 'add_to_graph', None)):
                layer.add_to_graph()

    def __assemble_graph(self):
        with self._graph.as_default():

            self.__log('Parsing dataset...')

            if self._raw_test_labels is not None:
                # currently think of moderation features as None so they are passed in hard-coded
                self.__parse_dataset(self._raw_train_image_files, self._raw_train_labels, None,
                                     self._raw_test_image_files, self._raw_test_labels, None,
                                     self._raw_val_image_files, self._raw_val_labels, None)
            elif self._images_only:
                self.__parse_images(self._raw_image_files)
            else:
                # split the data into train/val/test sets, if there is no validation set or no moderation features
                # being used they will be returned as 0 (val) or None (moderation features)
                train_images, train_labels, train_mf, \
                test_images, test_labels, test_mf, \
                val_images, val_labels, val_mf, = \
                    loaders.split_raw_data(self._raw_image_files, self._raw_labels, self._test_split,
                                           self._validation_split, self._all_moderation_features,
                                           self._training_augmentation_images, self._training_augmentation_labels,
                                           self._split_labels)
                # parse the images and set the appropriate environment variables
                self.__parse_dataset(train_images, train_labels, train_mf,
                                     test_images, test_labels, test_mf,
                                     val_images, val_labels, val_mf)

            self.__log('Creating layer parameters...')

            self.__add_layers_to_graph()

            self.__log('Assembling graph...')

            # Define batches
            if self._has_moderation:
                x, y, mod_w = tf.train.shuffle_batch(
                    [self._train_images, self._train_labels, self._train_moderation_features],
                    batch_size=self._batch_size,
                    num_threads=self._num_threads,
                    capacity=self._queue_capacity,
                    min_after_dequeue=self._batch_size)
            else:
                x, y = tf.train.shuffle_batch([self._train_images, self._train_labels],
                                              batch_size=self._batch_size,
                                              num_threads=self._num_threads,
                                              capacity=self._queue_capacity,
                                              min_after_dequeue=self._batch_size)

            # Reshape input to the expected image dimensions
            x = tf.reshape(x, shape=[-1, self._image_height, self._image_width, self._image_depth])
            if self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                y = tf.reshape(y, shape=[-1, self._image_height, self._image_width, 1])

            # If this is a regression problem, unserialize the label
            if self._problem_type == definitions.ProblemType.REGRESSION:
                y = loaders.label_string_to_tensor(y, self._batch_size, self._num_regression_outputs)
            elif self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                y = loaders.label_string_to_tensor(y, self._batch_size)
                vec_size = 1 + self._NUM_CLASSES + 4
                y = tf.reshape(y, [self._batch_size, self._grid_w * self._grid_h, vec_size])

            # if using patching we extract a patch of image here (object detection patching is different
            # and is done when data is loaded)
            if self._with_patching and self._problem_type != definitions.ProblemType.OBJECT_DETECTION:
                # Take a slice
                patch_width = self._patch_width
                patch_height = self._patch_height
                offset_h = np.random.randint(patch_height // 2, self._image_height - (patch_height // 2),
                                             self._batch_size)
                offset_w = np.random.randint(patch_width // 2, self._image_width - (patch_width // 2),
                                             self._batch_size)
                offsets = [x for x in zip(offset_h, offset_w)]
                x = tf.image.extract_glimpse(x, [patch_height, patch_width], offsets,
                                             normalized=False, centered=False)
                if self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                    y = tf.image.extract_glimpse(y, [patch_height, patch_width], offsets, normalized=False,
                                                 centered=False)

            # Run the network operations
            if self._has_moderation:
                xx = self.forward_pass(x, deterministic=False, moderation_features=mod_w)
            else:
                xx = self.forward_pass(x, deterministic=False)

            # Define regularization cost
            if self._reg_coeff is not None:
                l2_cost = tf.squeeze(tf.reduce_sum(
                    [layer.regularization_coefficient * tf.nn.l2_loss(layer.weights) for layer in self._layers
                     if isinstance(layer, layers.fullyConnectedLayer)]))
            else:
                l2_cost = 0.0

            # Define cost function
            if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                # define cost function based on which one was selected via set_loss_function
                if self._loss_fn == 'softmax cross entropy':
                    sf_logits = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=xx, labels=tf.argmax(y, 1))
                # define the cost
                self._graph_ops['cost'] = tf.add(tf.reduce_mean(tf.concat([sf_logits], axis=0)), l2_cost)
            elif self._problem_type == definitions.ProblemType.REGRESSION:
                # define cost function based on which one was selected via set_loss_function
                if self._loss_fn == 'l2':
                    regression_loss = self.__batch_mean_l2_loss(tf.subtract(xx, y))
                elif self._loss_fn == 'l1':
                    regression_loss = self.__batch_mean_l1_loss(tf.subtract(xx, y))
                elif self._loss_fn == 'smooth l1':
                    regression_loss = self.__batch_mean_smooth_l1_loss(tf.subtract(xx, y))
                elif self._loss_fn == 'log loss':
                    regression_loss = self.__batch_mean_log_loss(tf.subtract(xx, y))
                # define the cost
                self._graph_ops['cost'] = tf.add(regression_loss, l2_cost)
            elif self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                # define cost function based on which one was selected via set_loss_function
                if self._loss_fn == 'sigmoid cross entropy':
                    pixel_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=xx, labels=y))
                # define the cost
                self._graph_ops['cost'] = tf.squeeze(tf.add(pixel_loss, l2_cost))
            elif self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                # define cost function based on which one was selected via set_loss_function
                if self._loss_fn == 'yolo':
                    yolo_loss = self._yolo_loss_function(
                        y, tf.reshape(xx, [self._batch_size,
                                           self._grid_w * self._grid_h,
                                           self._NUM_BOXES * 5 + self._NUM_CLASSES]))
                # define the cost
                self._graph_ops['cost'] = tf.squeeze(tf.add(yolo_loss, l2_cost))

            # Identify which optimizer we are using
            if self._optimizer == 'adagrad':
                self._graph_ops['optimizer'] = tf.train.AdagradOptimizer(self._learning_rate)
                self.__log('Using Adagrad optimizer')
            elif self._optimizer == 'adadelta':
                self._graph_ops['optimizer'] = tf.train.AdadeltaOptimizer(self._learning_rate)
                self.__log('Using adadelta optimizer')
            elif self._optimizer == 'sgd':
                self._graph_ops['optimizer'] = tf.train.GradientDescentOptimizer(self._learning_rate)
                self.__log('Using SGD optimizer')
            elif self._optimizer == 'adam':
                self._graph_ops['optimizer'] = tf.train.AdamOptimizer(self._learning_rate)
                self.__log('Using Adam optimizer')
            elif self._optimizer == 'sgd_momentum':
                self._graph_ops['optimizer'] = tf.train.MomentumOptimizer(self._learning_rate, 0.9, use_nesterov=True)
                self.__log('Using SGD with momentum optimizer')
            else:
                warnings.warn('Unrecognized optimizer requested')
                exit()

            # Compute gradients, clip them, the apply the clipped gradients
            # This is broken up so that we can add gradients to tensorboard
            # need to make the 5.0 an adjustable hyperparameter
            gradients, variables = zip(*self._graph_ops['optimizer'].compute_gradients(self._graph_ops['cost']))
            gradients, global_grad_norm = tf.clip_by_global_norm(gradients, 5.0)
            self._graph_ops['optimizer'] = self._graph_ops['optimizer'].apply_gradients(zip(gradients, variables))

            # for classification problems we will compute the training accuracy, this is also used for tensorboard
            if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                class_predictions = tf.argmax(tf.nn.softmax(xx), 1)
                correct_predictions = tf.equal(class_predictions, tf.argmax(y, 1))
                self._graph_ops['accuracy'] = tf.reduce_mean(tf.cast(correct_predictions, tf.float32))

            # Calculate test accuracy
            if self._has_moderation:
                if self._testing:
                    x_test, self._graph_ops['y_test'], mod_w_test = tf.train.batch(
                        [self._test_images, self._test_labels, self._test_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'], mod_w_val = tf.train.batch(
                        [self._val_images, self._val_labels, self._val_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
            else:
                if self._testing:
                    x_test, self._graph_ops['y_test'] = tf.train.batch([self._test_images, self._test_labels],
                                                                       batch_size=self._batch_size,
                                                                       num_threads=self._num_threads,
                                                                       capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'] = tf.train.batch([self._val_images, self._val_labels],
                                                                     batch_size=self._batch_size,
                                                                     num_threads=self._num_threads,
                                                                     capacity=self._queue_capacity)
            if self._testing:
                x_test = tf.reshape(x_test, shape=[-1, self._image_height, self._image_width, self._image_depth])
            if self._validation:
                x_val = tf.reshape(x_val, shape=[-1, self._image_height, self._image_width, self._image_depth])

            if self._problem_type == definitions.ProblemType.REGRESSION:
                if self._testing:
                    self._graph_ops['y_test'] = loaders.label_string_to_tensor(self._graph_ops['y_test'],
                                                                               self._batch_size,
                                                                               self._num_regression_outputs)
                if self._validation:
                    self._graph_ops['y_val'] = loaders.label_string_to_tensor(self._graph_ops['y_val'],
                                                                              self._batch_size,
                                                                              self._num_regression_outputs)

            if self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                if self._testing:
                    self._graph_ops['y_test'] = tf.reshape(self._graph_ops['y_test'],
                                                           shape=[-1, self._image_height, self._image_width, 1])
                if self._validation:
                    self._graph_ops['y_val'] = tf.reshape(self._graph_ops['y_val'],
                                                          shape=[-1, self._image_height, self._image_width, 1])

            if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                vec_size = 1 + self._NUM_CLASSES + 4
                if self._testing:
                    self._graph_ops['y_test'] = loaders.label_string_to_tensor(self._graph_ops['y_test'],
                                                                               self._batch_size)
                    self._graph_ops['y_test'] = tf.reshape(self._graph_ops['y_test'],
                                                           shape=[self._batch_size,
                                                                  self._grid_w * self._grid_h,
                                                                  vec_size])
                if self._validation:
                    self._graph_ops['y_val'] = loaders.label_string_to_tensor(self._graph_ops['y_val'],
                                                                              self._batch_size)
                    self._graph_ops['y_val'] = tf.reshape(self._graph_ops['y_val'],
                                                          shape=[self._batch_size,
                                                                 self._grid_w * self._grid_h,
                                                                 vec_size])

            # if using patching we need to properly pull patches from the images (object detection patching is different
            # and is done when data is loaded)
            if self._with_patching and self._problem_type != definitions.ProblemType.OBJECT_DETECTION:
                # Take a slice of image. Same size and location (offsets) as the slice from training.
                patch_width = self._patch_width
                patch_height = self._patch_height
                if self._testing:
                    x_test = tf.image.extract_glimpse(x_test, [patch_height, patch_width], offsets,
                                                      normalized=False, centered=False)
                if self._validation:
                    x_val = tf.image.extract_glimpse(x_val, [patch_height, patch_width], offsets,
                                                     normalized=False, centered=False)
                if self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                    if self._testing:
                        self._graph_ops['y_test'] = tf.image.extract_glimpse(self._graph_ops['y_test'],
                                                                             [patch_height, patch_width], offsets,
                                                                             normalized=False, centered=False)
                    if self._validation:
                        self._graph_ops['y_val'] = tf.image.extract_glimpse(self._graph_ops['y_val'],
                                                                            [patch_height, patch_width], offsets,
                                                                            normalized=False, centered=False)

            if self._has_moderation:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True,
                                                                            moderation_features=mod_w_test)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True,
                                                                           moderation_features=mod_w_val)
            else:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True)

            # For object detection, the network outputs need to be reshaped to match y_test and y_val
            if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = tf.reshape(self._graph_ops['x_test_predicted'],
                                                                     [self._batch_size,
                                                                      self._grid_w * self._grid_h,
                                                                      self._NUM_BOXES * 5 + self._NUM_CLASSES])
                if self._validation:
                    self._graph_ops['x_val_predicted'] = tf.reshape(self._graph_ops['x_val_predicted'],
                                                                    [self._batch_size,
                                                                     self._grid_w * self._grid_h,
                                                                     self._NUM_BOXES * 5 + self._NUM_CLASSES])

            # compute the loss and accuracy based on problem type
            if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                if self._testing:
                    test_class_predictions = tf.argmax(tf.nn.softmax(self._graph_ops['x_test_predicted']), 1)
                    test_correct_predictions = tf.equal(test_class_predictions,
                                                        tf.argmax(self._graph_ops['y_test'], 1))
                    self._graph_ops['test_losses'] = test_correct_predictions
                    self._graph_ops['test_accuracy'] = tf.reduce_mean(tf.cast(test_correct_predictions, tf.float32))
                if self._validation:
                    val_class_predictions = tf.argmax(tf.nn.softmax(self._graph_ops['x_val_predicted']), 1)
                    val_correct_predictions = tf.equal(val_class_predictions, tf.argmax(self._graph_ops['y_val'], 1))
                    self._graph_ops['val_losses'] = val_correct_predictions
                    self._graph_ops['val_accuracy'] = tf.reduce_mean(tf.cast(val_correct_predictions, tf.float32))
            elif self._problem_type == definitions.ProblemType.REGRESSION:
                if self._testing:
                    if self._num_regression_outputs == 1:
                        self._graph_ops['test_losses'] = tf.squeeze(tf.stack(
                            tf.subtract(self._graph_ops['x_test_predicted'], self._graph_ops['y_test'])))
                    else:
                        self._graph_ops['test_losses'] = self.__l2_norm(
                            tf.subtract(self._graph_ops['x_test_predicted'], self._graph_ops['y_test']))
                if self._validation:
                    if self._num_regression_outputs == 1:
                        self._graph_ops['val_losses'] = tf.squeeze(
                            tf.stack(tf.subtract(self._graph_ops['x_val_predicted'], self._graph_ops['y_val'])))
                    else:
                        self._graph_ops['val_losses'] = self.__l2_norm(
                            tf.subtract(self._graph_ops['x_val_predicted'], self._graph_ops['y_val']))
                    self._graph_ops['val_cost'] = tf.reduce_mean(tf.abs(self._graph_ops['val_losses']))
            elif self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                if self._testing:
                    self._graph_ops['test_losses'] = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(
                        logits=self._graph_ops['x_test_predicted'],
                        labels=self._graph_ops['y_test']),
                        axis=2)
                    self._graph_ops['test_losses'] = tf.reshape(tf.reduce_mean(self._graph_ops['test_losses'],
                                                                               axis=1),
                                                                [self._batch_size])
                if self._validation:
                    self._graph_ops['val_losses'] = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(
                        logits=self._graph_ops['x_val_predicted'], labels=self._graph_ops['y_val']),
                        axis=2)
                    self._graph_ops['val_losses'] = tf.transpose(
                        tf.reduce_mean(self._graph_ops['val_losses'], axis=1))
                    self._graph_ops['val_cost'] = tf.reduce_mean(self._graph_ops['val_losses'])
            elif self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                if self._testing:
                    if self._loss_fn == 'yolo':
                        self._graph_ops['test_losses'] = self._yolo_loss_function(self._graph_ops['y_test'],
                                                                                  self._graph_ops['x_test_predicted'])
                if self._validation:
                    if self._loss_fn == 'yolo':
                        self._graph_ops['val_losses'] = self._yolo_loss_function(self._graph_ops['y_val'],
                                                                                 self._graph_ops['x_val_predicted'])

            # Epoch summaries for Tensorboard
            if self._tb_dir is not None:
                self.__log('Creating Tensorboard summaries...')

                # Summaries for any problem type
                tf.summary.scalar('train/loss', self._graph_ops['cost'], collections=['custom_summaries'])
                tf.summary.scalar('train/learning_rate', self._learning_rate, collections=['custom_summaries'])
                tf.summary.scalar('train/l2_loss', l2_cost, collections=['custom_summaries'])
                filter_summary = self.__get_weights_as_image(self.__first_layer().weights)
                tf.summary.image('filters/first', filter_summary, collections=['custom_summaries'])

                # Summaries for classification problems
                if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                    tf.summary.scalar('train/accuracy', self._graph_ops['accuracy'], collections=['custom_summaries'])
                    tf.summary.histogram('train/class_predictions', class_predictions, collections=['custom_summaries'])
                    if self._validation:
                        tf.summary.scalar('validation/accuracy', self._graph_ops['val_accuracy'],
                                          collections=['custom_summaries'])
                        tf.summary.histogram('validation/class_predictions', val_class_predictions,
                                             collections=['custom_summaries'])

                # Summaries for regression
                if self._problem_type == definitions.ProblemType.REGRESSION:
                    if self._num_regression_outputs == 1:
                        tf.summary.scalar('train/regression_loss', regression_loss, collections=['custom_summaries'])
                        if self._validation:
                            tf.summary.scalar('validation/loss', self._graph_ops['val_cost'],
                                              collections=['custom_summaries'])
                            tf.summary.histogram('validation/batch_losses', self._graph_ops['val_losses'],
                                                 collections=['custom_summaries'])

                # Summaries for semantic segmentation
                # we send in the last layer's output size (i.e. the final image dimensions) to get_weights_as_image
                # because xx and x_test_predicted have dynamic dims [?,?,?,?], so we need actual numbers passed in
                if self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                    train_images_summary = self.__get_weights_as_image(
                        tf.transpose(tf.expand_dims(xx, -1), (1, 2, 3, 0)),
                        self._layers[-1].output_size)
                    tf.summary.image('masks/train', train_images_summary, collections=['custom_summaries'])
                    if self._validation:
                        tf.summary.scalar('validation/loss', self._graph_ops['val_cost'],
                                          collections=['custom_summaries'])
                        val_images_summary = self.__get_weights_as_image(
                            tf.transpose(tf.expand_dims(self._graph_ops['x_val_predicted'], -1), (1, 2, 3, 0)),
                            self._layers[-1].output_size)
                        tf.summary.image('masks/validation', val_images_summary, collections=['custom_summaries'])

                if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                    tf.summary.scalar('train/yolo_loss', yolo_loss, collections=['custom_summaries'])
                    if self._validation:
                        tf.summary.scalar('validation/loss', self._graph_ops['val_losses'],
                                          collections=['custom_sumamries'])

                # Summaries for each layer
                for layer in self._layers:
                    if hasattr(layer, 'name') and not isinstance(layer, layers.batchNormLayer):
                        tf.summary.histogram('weights/' + layer.name, layer.weights, collections=['custom_summaries'])
                        tf.summary.histogram('biases/' + layer.name, layer.biases, collections=['custom_summaries'])

                        # At one point the graph would hang on session.run(graph_ops['merged']) inside of begin_training
                        # and it was found that if you commented the below line then the code wouldn't hang. Never
                        # fully understood why, as it only happened if you tried running with train/test and no
                        # validation. But after adding more features and just randomly trying to uncomment the below
                        # line to see if it would work, it appears to now be working, but still don't know why...
                        tf.summary.histogram('activations/' + layer.name, layer.activations,
                                             collections=['custom_summaries'])

                # Summaries for gradients
                # we variables[index].name[:-2] because variables[index].name will have a ':0' at the end of
                # the name and tensorboard does not like this so we remove it with the [:-2]
                # We also currently seem to get None's for gradients when performing a hyperparameter search
                # and as such it is simply left out for hyper-param searches, needs to be fixed
                if not self._hyper_param_search:
                    for index, grad in enumerate(gradients):
                        tf.summary.histogram("gradients/" + variables[index].name[:-2], gradients[index],
                                             collections=['custom_summaries'])

                    tf.summary.histogram("gradient_global_norm/", global_grad_norm, collections=['custom_summaries'])

                self._graph_ops['merged'] = tf.summary.merge_all(key='custom_summaries')

    def begin_training(self, return_test_loss=False):
        """
        Initialize the network and either run training to the specified max epoch, or load trainable variables.
        The full test accuracy is calculated immediately afterward. Finally, the trainable parameters are saved and
        the session is shut down.
        Before calling this function, the images and labels should be loaded, as well as all relevant hyperparameters.
        """
        # if None in [self._train_images, self._test_images,
        #             self._train_labels, self._test_labels]:
        #     raise RuntimeError("Images and Labels need to be loaded before you can begin training. " +
        #                        "Try first using one of the methods starting with 'load_...' such as " +
        #                        "'DPPModel.load_dataset_from_directory_with_csv_labels()'")
        # if (len(self._layers) < 1):
        #     raise RuntimeError("There are no layers currently added to the model when trying to begin training. " +
        #                        "Add layers first by using functions such as 'DPPModel.add_input_layer()' or " +
        #                        "'DPPModel.add_convolutional_layer()'. See documentation for a complete list of
        #                        layers.")

        with self._graph.as_default():
            self.__assemble_graph()
            print('assembled the graph')

            # Either load the network parameters from a checkpoint file or start training
            if self._load_from_saved is not False:
                self.load_state()

                self.__initialize_queue_runners()

                self.compute_full_test_accuracy()

                self.shut_down()
            else:
                if self._tb_dir is not None:
                    train_writer = tf.summary.FileWriter(self._tb_dir, self._session.graph)

                self.__log('Initializing parameters...')
                init_op = tf.global_variables_initializer()
                self._session.run(init_op)

                self.__initialize_queue_runners()

                self.__log('Beginning training...')

                self.__set_learning_rate()

                # Needed for batch norm
                update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
                self._graph_ops['optimizer'] = tf.group([self._graph_ops['optimizer'], update_ops])

                # for i in range(self._maximum_training_batches):
                tqdm_range = tqdm(range(self._maximum_training_batches))
                for i in tqdm_range:
                    start_time = time.time()

                    self._global_epoch = i
                    self._session.run(self._graph_ops['optimizer'])
                    if self._global_epoch > 0 and self._global_epoch % self._report_rate == 0:
                        elapsed = time.time() - start_time

                        if self._tb_dir is not None:
                            summary = self._session.run(self._graph_ops['merged'])
                            train_writer.add_summary(summary, i)
                        if self._validation:
                            if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                                loss, epoch_accuracy, epoch_val_accuracy = self._session.run(
                                    [self._graph_ops['cost'],
                                     self._graph_ops['accuracy'],
                                     self._graph_ops['val_accuracy']])

                                samples_per_sec = self._batch_size / elapsed

                                tqdm_range.set_description(
                                    "{}: Results for batch {} (epoch {:.1f}) - Loss: {:.5f}, Training Accuracy: {:.4f}, samples/sec: {:.2f}"
                                    .format(datetime.datetime.now().strftime("%I:%M%p"),
                                            i,
                                            i / (self._total_training_samples / self._batch_size),
                                            loss,
                                            epoch_accuracy,
                                            samples_per_sec))

                            elif self._problem_type == definitions.ProblemType.REGRESSION or \
                                    self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION or \
                                    self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                                loss, epoch_test_loss = self._session.run([self._graph_ops['cost'],
                                                                           self._graph_ops['val_cost']])

                                samples_per_sec = self._batch_size / elapsed

                                tqdm_range.set_description(
                                    "{}: Results for batch {} (epoch {:.1f}) - Loss: {}, samples/sec: {:.2f}"
                                    .format(datetime.datetime.now().strftime("%I:%M%p"),
                                            i,
                                            i / (self._total_training_samples / self._batch_size),
                                            loss,
                                            samples_per_sec))

                        else:
                            if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                                loss, epoch_accuracy = self._session.run(
                                    [self._graph_ops['cost'],
                                     self._graph_ops['accuracy']])

                                samples_per_sec = self._batch_size / elapsed

                                tqdm_range.set_description(
                                    "{}: Results for batch {} (epoch {:.1f}) - Loss: {:.5f}, Training Accuracy: {:.4f}, samples/sec: {:.2f}"
                                    .format(datetime.datetime.now().strftime("%I:%M%p"),
                                            i,
                                            i / (self._total_training_samples / self._batch_size),
                                            loss,
                                            epoch_accuracy,
                                            samples_per_sec))

                            elif self._problem_type == definitions.ProblemType.REGRESSION or \
                                    self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION or \
                                    self._problem_type == definitions.ProblemType.OBJECT_DETECTION:

                                loss = self._session.run([self._graph_ops['cost']])

                                samples_per_sec = self._batch_size / elapsed

                                tqdm_range.set_description(
                                    "{}: Results for batch {} (epoch {:.1f}) - Loss: {}, samples/sec: {:.2f}"
                                    .format(datetime.datetime.now().strftime("%I:%M%p"),
                                            i,
                                            i / (self._total_training_samples / self._batch_size),
                                            loss,
                                            samples_per_sec))

                        if self._save_checkpoints and self._global_epoch % (self._report_rate * 100) == 0:
                            self.save_state(self._save_dir)
                    else:
                        loss = self._session.run([self._graph_ops['cost']])

                    if loss == 0.0:
                        self.__log('Stopping due to zero loss')
                        break

                    if i == self._maximum_training_batches - 1:
                        self.__log('Stopping due to maximum epochs')

                self.save_state(self._save_dir)

                final_test_loss = None
                if self._testing:
                    final_test_loss = self.compute_full_test_accuracy()

                self.shut_down()

                if return_test_loss:
                    return final_test_loss
                else:
                    return

    def begin_training_with_hyperparameter_search(self, l2_reg_limits=None, lr_limits=None, num_steps=3):
        """
        Performs grid-based hyperparameter search given the ranges passed. Parameters are optional.

        :param l2_reg_limits: array representing a range of L2 regularization coefficients in the form [low, high]
        :param lr_limits: array representing a range of learning rates in the form [low, high]
        :param num_steps: the size of the grid. Larger numbers are exponentially slower.
        """
        self._hyper_param_search = True

        base_tb_dir = self._tb_dir

        unaltered_image_height = self._image_height
        unaltered_image_width = self._image_width
        unaltered_epochs = self._maximum_training_batches

        if l2_reg_limits is None:
            all_l2_reg = [self._reg_coeff]
        else:
            step_size = (l2_reg_limits[1] - l2_reg_limits[0]) / np.float32(num_steps-1)
            all_l2_reg = np.arange(l2_reg_limits[0], l2_reg_limits[1], step_size)
            all_l2_reg = np.append(all_l2_reg, l2_reg_limits[1])

        if lr_limits is None:
            all_lr = [self._learning_rate]
        else:
            step_size = (lr_limits[1] - lr_limits[0]) / np.float32(num_steps-1)
            all_lr = np.arange(lr_limits[0], lr_limits[1], step_size)
            all_lr = np.append(all_lr, lr_limits[1])

        all_loss_results = np.empty([len(all_l2_reg), len(all_lr)])

        for i, current_l2 in enumerate(all_l2_reg):
            for j, current_lr in enumerate(all_lr):
                self.__log('HYPERPARAMETER SEARCH: Doing l2reg=%f, lr=%f' % (current_l2, current_lr))

                # Make a new graph, associate a new session with it.
                self.__reset_graph()
                self.__reset_session()

                self._learning_rate = current_lr
                self._reg_coeff = current_l2

                # Set calculated variables back to their unaltered form
                self._image_height = unaltered_image_height
                self._image_width = unaltered_image_width
                self._maximum_training_batches = unaltered_epochs

                # Reset the reg. coef. for all fc layers.
                with self._graph.as_default():
                    for layer in self._layers:
                        if isinstance(layer, layers.fullyConnectedLayer):
                            layer.regularization_coefficient = current_l2

                if base_tb_dir is not None:
                    self._tb_dir = base_tb_dir + '_lr:' + current_lr.astype('str') + '_l2:' + current_l2.astype('str')

                try:
                    current_loss = self.begin_training(return_test_loss=True)
                    all_loss_results[i][j] = current_loss
                except Exception as e:
                    self.__log('HYPERPARAMETER SEARCH: Run threw an exception, this result will be NaN.')
                    print("Exception message: "+str(e))
                    all_loss_results[i][j] = np.nan

        self.__log('Finished hyperparameter search, failed runs will appear as NaN.')
        self.__log('All l2 coef. tested:')
        self.__log('\n'+np.array2string(np.transpose(all_l2_reg)))
        self.__log('All learning rates tested:')
        self.__log('\n'+np.array2string(all_lr))
        self.__log('Loss/error grid:')
        self.__log('\n'+np.array2string(all_loss_results, precision=4))

    def compute_full_test_accuracy(self):
        """Returns statistics of the test losses depending on the type of task"""

        self.__log('Computing total test accuracy/regression loss...')

        with self._graph.as_default():
            num_test = self._total_raw_samples - self._total_training_samples
            num_batches = int(np.ceil(num_test / self._batch_size))

            if num_batches == 0:
                warnings.warn('Less than a batch of testing data')
                exit()

            use_losses = [definitions.ProblemType.REGRESSION, definitions.ProblemType.SEMANTIC_SEGMETNATION]
            use_y = [definitions.ProblemType.REGRESSION, definitions.ProblemType.OBJECT_DETECTION]
            use_predictions = [definitions.ProblemType.REGRESSION, definitions.ProblemType.OBJECT_DETECTION]

            # Initialize storage for the retreived test variables
            if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                # Object detection needs some special care given to its variable's shape
                all_y = np.empty(shape=(1,
                                        self._grid_w * self._grid_h,
                                        1 + self._NUM_CLASSES + 4))
                all_predictions = np.empty(shape=(1,
                                                  self._grid_w * self._grid_h,
                                                  5 * self._NUM_BOXES + self._NUM_CLASSES))
            else:
                if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                    loss_sum = 0.0
                if self._problem_type in use_losses:
                    all_losses = np.empty(shape=1)
                if self._problem_type in use_y:
                    all_y = np.empty(shape=1)
                if self._problem_type in use_predictions:
                    all_predictions = np.empty(shape=1)

            # Main test loop
            for _ in tqdm(range(num_batches)):
                if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                    batch_mean = self._session.run([self._graph_ops['test_losses']])
                    loss_sum = loss_sum + np.mean(batch_mean)
                elif self._problem_type == definitions.ProblemType.REGRESSION:
                    r_losses, r_y, r_predicted = self._session.run([self._graph_ops['test_losses'],
                                                                    self._graph_ops['y_test'],
                                                                    self._graph_ops['x_test_predicted']])
                    all_losses = np.concatenate((all_losses, r_losses), axis=0)
                    all_y = np.concatenate((all_y, np.squeeze(r_y)), axis=0)
                    all_predictions = np.concatenate((all_predictions, np.squeeze(r_predicted)), axis=0)
                elif self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                    r_losses = self._session.run(self._graph_ops['test_losses'])
                    all_losses = np.concatenate((all_losses, r_losses), axis=0)
                elif self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                    r_y, r_predicted = self._session.run([self._graph_ops['y_test'],
                                                          self._graph_ops['x_test_predicted']])
                    all_y = np.concatenate((all_y, r_y), axis=0)
                    all_predictions = np.concatenate((all_predictions, r_predicted), axis=0)

            if self._problem_type != definitions.ProblemType.CLASSIFICATION:
                # Delete the weird first entries in losses, y values, and predictions (because creating empty arrays
                # isn't a thing)
                if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                    # These are multi-dimensional for object detection, so first entry = first slice
                    all_y = np.delete(all_y, 0, axis=0)
                    all_predictions = np.delete(all_predictions, 0, axis=0)
                else:
                    if self._problem_type in use_losses:
                        all_losses = np.delete(all_losses, 0)
                    if self._problem_type in use_y:
                        all_y = np.delete(all_y, 0)
                    if self._problem_type in use_predictions:
                        all_predictions = np.delete(all_predictions, 0)

                # Delete the extra entries (e.g. batch_size is 4 and 1 sample left, it will loop and have 3 repeats that
                # we want to get rid of)
                extra = self._batch_size - (self._total_testing_samples % self._batch_size)
                if extra != self._batch_size:
                    mask_extra = np.ones(self._batch_size * num_batches, dtype=bool)
                    mask_extra[range(self._batch_size * num_batches - extra, self._batch_size * num_batches)] = False
                    if self._problem_type in use_losses:
                        all_losses = all_losses[mask_extra, ...]
                    if self._problem_type in use_y:
                        all_y = all_y[mask_extra, ...]
                    if self._problem_type in use_predictions:
                        all_predictions = all_predictions[mask_extra, ...]

            if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                # For classification problems (assumed to be multi-class), we want accuracy and confusion matrix
                mean = (loss_sum / num_batches)

                self.__log('Average test accuracy: {:.5f}'.format(mean))

                return 1.0-mean.astype(np.float32)
            elif self._problem_type == definitions.ProblemType.REGRESSION or \
                    self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                # For regression problems we want relative and abs mean, std of L2 norms, plus a histogram of errors
                abs_mean = np.mean(np.abs(all_losses))
                abs_var = np.var(np.abs(all_losses))
                abs_std = np.sqrt(abs_var)

                mean = np.mean(all_losses)
                var = np.var(all_losses)
                mse = np.mean(np.square(all_losses))
                std = np.sqrt(var)
                loss_max = np.amax(all_losses)
                loss_min = np.amin(all_losses)

                hist, _ = np.histogram(all_losses, bins=100)

                self.__log('Mean loss: {}'.format(mean))
                self.__log('Loss standard deviation: {}'.format(std))
                self.__log('Mean absolute loss: {}'.format(abs_mean))
                self.__log('Absolute loss standard deviation: {}'.format(abs_std))
                self.__log('Min error: {}'.format(loss_min))
                self.__log('Max error: {}'.format(loss_max))
                self.__log('MSE: {}'.format(mse))

                if self._problem_type == definitions.ProblemType.REGRESSION:
                    all_y_mean = np.mean(all_y)
                    total_error = np.sum(np.square(all_y - all_y_mean))
                    unexplained_error = np.sum(np.square(all_losses))
                    # division by zero can happen when using small test sets
                    if total_error == 0:
                        r2 = -np.inf
                    else:
                        r2 = 1. - (unexplained_error / total_error)

                    self.__log('R^2: {}'.format(r2))
                    self.__log('All test labels:')
                    self.__log(all_y)

                    self.__log('All predictions:')
                    self.__log(all_predictions)

                self.__log('Histogram of {} losses:'.format(self._loss_fn))
                self.__log(hist)

                return abs_mean.astype(np.float32)
            elif self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                # Make the images heterogeneous, storing their separate grids in a list
                test_labels = [all_y[i, ...] for i in range(all_y.shape[0])]
                test_preds = [all_predictions[i, ...] for i in range(all_predictions.shape[0])]
                n_images = len(test_labels)

                # Convert coordinates, then filter out the positive ground truth labels and significant predictions
                for i in range(n_images):
                    conv_label, conv_pred = self.__yolo_coord_convert(test_labels[i], test_preds[i])
                    truth_mask = conv_label[..., 0] == 1
                    if not np.any(truth_mask):
                        conv_label = None
                    else:
                        conv_label = conv_label[truth_mask, :]
                    conv_pred = self.__yolo_filter_predictions(conv_pred)
                    test_labels[i] = conv_label
                    test_preds[i] = conv_pred

                # Get and log the map
                yolo_map = self.__yolo_map(test_labels, test_preds)
                self.__log('Yolo mAP: {}'.format(yolo_map))
            return

    def __yolo_coord_convert(self, labels, preds):
        """
        Converts Yolo labeled and predicted bounding boxes from xywh coords to x1y1x2y2 coords. Also accounts for
        required sigmoid and exponential conversions in the predictions (including the confidences)

        :param labels: ndarray with Yolo ground-truth bounding boxes (size ?x(NUM_CLASSES+5))
        :param preds: ndarray with Yolo predicted bounding boxes (size ?x(NUM_BOXES*5))
        :return: `labels` and `preds` with the bounding box coords changed from xywh to x1y1x2y2 and predicted box
        confidences converted to percents
        """

        def xywh_to_xyxy(x, y, w, h):
            x_centre = np.arange(self._grid_w * self._grid_h) % self._grid_w
            y_centre = np.arange(self._grid_w * self._grid_h) // self._grid_w
            scale_x = self._image_width / self._grid_w
            scale_y = self._image_height / self._grid_h

            x = (x + x_centre) * scale_x
            y = (y + y_centre) * scale_y
            w = w * scale_x
            h = h * scale_y

            x1 = x - w/2
            x2 = x + w/2
            y1 = y - h/2
            y2 = y + h/2
            return x1, y1, x2, y2

        # Labels are already sensible numbers, so convert them first
        lab_coord_idx = np.arange(labels.shape[-1]-4, labels.shape[-1])
        lab_class, lab_x, lab_y, lab_w, lab_h = np.split(labels, lab_coord_idx, axis=-1)
        lab_x1, lab_y1, lab_x2, lab_y2 = xywh_to_xyxy(np.squeeze(lab_x),  # Squeezing to aid broadcasting in helper
                                                      np.squeeze(lab_y),
                                                      np.squeeze(lab_w),
                                                      np.squeeze(lab_h))
        labels = np.concatenate([lab_class,
                                 lab_x1[:, np.newaxis],  # Dummy dimensions to enable concatenation
                                 lab_y1[:, np.newaxis],
                                 lab_x2[:, np.newaxis],
                                 lab_y2[:, np.newaxis]], axis=-1)

        # Extract the class predictions and reorganize the predicted boxes
        class_preds = preds[..., self._NUM_BOXES * 5:]
        preds = np.reshape(preds[..., 0:self._NUM_BOXES * 5], preds.shape[:-1] + (self._NUM_BOXES, 5))

        # Predictions are not, so apply sigmoids and exponentials first and then convert them
        anchors = np.array(self._ANCHORS)
        pred_x = expit(preds[..., 0])
        pred_y = expit(preds[..., 1])
        pred_w = np.exp(preds[..., 2]) * anchors[:, 0]
        pred_h = np.exp(preds[..., 3]) * anchors[:, 1]
        pred_conf = expit(preds[..., 4])
        pred_x1, pred_y1, pred_x2, pred_y2 = xywh_to_xyxy(pred_x.T,  # Transposes to aid broadcasting in helper
                                                          pred_y.T,
                                                          pred_w.T,
                                                          pred_h.T)
        preds[..., :] = np.stack([pred_x1.T,  # Transposes to restore original shape
                                  pred_y1.T,
                                  pred_x2.T,
                                  pred_y2.T,
                                  pred_conf], axis=-1)

        # Reattach the class predictions
        preds = np.reshape(preds, preds.shape[:-2] + (self._NUM_BOXES * 5,))
        preds = np.concatenate([preds, class_preds], axis=-1)

        return labels, preds

    def __yolo_filter_predictions(self, preds):
        """
        Filters the predicted bounding boxes by eliminating insignificant and overlapping predictions

        :param preds: ndarray with predicted bounding boxes for one image in each grid square. Predictions
        are a list of, for each box, [x1, y1, x2, y2, conf] followed by a list of class predictions
        :return: `preds` with only the significant and maximal confidence predictions remaining
        """
        # Extract the class predictions and separate the predicted boxes
        grid_count = preds.shape[0]
        class_preds = preds[..., self._NUM_BOXES * 5:]
        preds = np.reshape(preds[..., 0:self._NUM_BOXES * 5], preds.shape[:-1] + (self._NUM_BOXES, 5))

        # In each grid square, the highest confidence box is the one responsible for prediction
        max_conf_idx = np.argmax(preds[..., 4], axis=-1)
        responsible_boxes = [preds[i, max_conf_idx[i], :] for i in range(grid_count)]
        preds = np.stack(responsible_boxes, axis=0)

        # Eliminate insignificant predicted boxes
        sig_mask = preds[:, 4] > self._THRESH_SIG
        if not np.any(sig_mask):
            return None
        class_preds = class_preds[sig_mask, :]
        preds = preds[sig_mask, :]

        # Apply non-maximal suppression (i.e. eliminate boxes that overlap with a more confidant box)
        maximal_idx = []
        sig_grid_count = preds.shape[0]
        conf_order = np.argsort(preds[:, 4])
        pair_iou = np.array([self.__compute_iou(preds[i, 0:4], preds[j, 0:4])
                             for i in range(sig_grid_count) for j in range(sig_grid_count)])
        pair_iou = pair_iou.reshape(sig_grid_count, sig_grid_count)
        while len(conf_order) > 0:
            # Take the most confidant box, then cull the list down to boxes that don't overlap with it
            cur_grid = conf_order[-1]
            maximal_idx.append(cur_grid)
            non_overlap = pair_iou[cur_grid, conf_order] < self._THRESH_OVERLAP
            if np.any(non_overlap):
                conf_order = conf_order[non_overlap]
            else:
                break

        # Stick things back together. maximal_idx is not sorted, but box and class predictions should still match up
        # and the original grid order shouldn't matter for mAP calculations
        class_preds = class_preds[maximal_idx, :]
        preds = preds[maximal_idx, :]
        preds = np.concatenate([preds, class_preds], axis=-1)

        return preds

    def __yolo_map(self, labels, preds):
        """
        Calculates the mean average precision of Yolo object and class predictions

        :param labels: List of ndarrays with ground truth bounding box labels for each image. Labels are a 6-value
        list: [object-ness, class, x1, y1, x2, y2]
        :param preds: List of ndarrays with significant predicted bounding boxes in each image. Predictions are a list
        of box parameters [x1, y1, x2, y2, conf] followed by a list of class predictions
        :return: The mean average precision (mAP) of the predictions
        """
        # Go over each prediction in each image and determine if it's a true or false positive
        detections = []
        for im_lab, im_pred in zip(labels, preds):
            # No predictions means no positives
            if im_pred is None:
                continue
            n_pred = im_pred.shape[0]

            # No labels means all false positives
            if im_lab is None:
                for i in range(n_pred):
                    detections.append((im_pred[i, 4], 0))
                continue
            n_lab = im_lab.shape[0]

            # Add a 7th value to the labels so we can tell which ones get matched up with true positives
            im_lab = np.concatenate([im_lab, np.zeros((n_lab, 1))], axis=-1)

            # Calculate the IoUs of all the prediction and label pairings, then record each detection as a true or
            # false positive with the prediction confidence
            pair_ious = np.array([self.__compute_iou(im_pred[i, 0:4], im_lab[j, 2:6])
                                  for i in range(n_pred) for j in range(n_lab)])
            pair_ious = np.reshape(pair_ious, (n_pred, n_lab))
            for i in range(n_pred):
                j = np.argmax(pair_ious[i, :])
                if pair_ious[i, j] >= self._THRESH_CORRECT and not im_lab[j, 6]:
                    detections.append((im_pred[i, 4], 1))
                    im_lab[j, 6] = 1
                else:
                    detections.append((im_pred[i, 4], 0))

        # If there are no valid predictions at all, the mAP is 0
        if not detections:
            return 0

        # With multiple classes, we would also have class tags in the detection tuples so the below code could generate
        # and iterate over class-separated detection lists, giving multiple AP values and one true mean AP. We aren't
        # doing that right now because of our one-class plant detector assumption

        # Determine the precision-recall curve from the cumulative detected true and false positives (in order of
        # descending confidence)
        detections = np.array(sorted(detections, key=lambda d: d[0], reverse=True))
        n_truths = sum([x.shape[0] if (x is not None) else 0
                        for x in labels])
        n_positives = detections.shape[0]
        true_positives = np.cumsum(detections[:, 1])
        precision = true_positives / np.arange(1, n_positives+1)
        recall = true_positives / n_truths

        # Calculate the area under the precision-recall curve (== AP)
        for i in range(precision.size - 1, 0, -1):  # Make precision values the maximum precision at further recalls
            precision[i - 1] = np.max((precision[i], precision[i-1]))
        ap = np.sum(precision[1:] * (recall[1:] - recall[0:-1]))

        return ap

    def shut_down(self):
        """Stop all queues and end session. The model cannot be used anymore after a shut down is completed."""
        self.__log('Shutdown requested, ending session...')

        self._coord.request_stop()
        self._coord.join(self._threads)

        self._session.close()

    def __get_weights_as_image(self, kernel, size=None):
        """Filter visualization, adapted with permission from https://gist.github.com/kukuruza/03731dc494603ceab0c5"""
        with self._graph.as_default():
            pad = 1
            grid_x = 4

            # pad x and y
            x1 = tf.pad(kernel, tf.constant([[pad, 0], [pad, 0], [0, 0], [0, 0]]))

            # when kernel is dynamically shaped at runtime it has [?,?,?,?] dimensions which result in None's
            # thus size needs to be passed in so we have actual dimensions to work with (this is mostly from the
            # upsampling layer) and grid_y will be determined by batch size as we want to see each img in the batch
            # However, for visualizing the weights we wont pass in a size parameter and as a result we need to
            # compute grid_y based off what is passed in and not the batch size because we want to see the
            # convolution grid for each layer, not each batch.
            if size is not None:
                # this is when visualizing the actual images
                grid_y = int(np.ceil(self._batch_size / 4))
                # x and y dimensions, w.r.t. padding
                y = size[1] + pad
                x = size[2] + pad
                num_channels = size[-1]
            else:
                # this is when visualizing the weights
                grid_y = (kernel.get_shape().as_list()[-1] / 4)
                # x and y dimensions, w.r.t. padding
                y = kernel.get_shape()[0] + pad
                x = kernel.get_shape()[1] + pad
                num_channels = kernel.get_shape().as_list()[2]

            # pack into image with proper dimensions for tf.image_summary
            x2 = tf.transpose(x1, (3, 0, 1, 2))
            x3 = tf.reshape(x2, tf.stack([grid_x, y * grid_y, x, num_channels]))
            x4 = tf.transpose(x3, (0, 2, 1, 3))
            x5 = tf.reshape(x4, tf.stack([1, x * grid_x, y * grid_y, num_channels]))
            x6 = tf.transpose(x5, (2, 1, 3, 0))
            x7 = tf.transpose(x6, (3, 0, 1, 2))

            # scale to [0, 1]
            x_min = tf.reduce_min(x7)
            x_max = tf.reduce_max(x7)
            x8 = (x7 - x_min) / (x_max - x_min)

        return x8

    def save_state(self, directory=None):
        """Save all trainable variables as a checkpoint in the current working path"""
        self.__log('Saving parameters...')

        if directory is None:
            state_dir = './saved_state'
        else:
            state_dir = directory + '/saved_state'

        if not os.path.isdir(state_dir):
            os.mkdir(state_dir)

        with self._graph.as_default():
            saver = tf.train.Saver(tf.trainable_variables())
            saver.save(self._session, state_dir + '/tfhSaved')

        self._has_trained = True

    def load_state(self):
        """
        Load all trainable variables from a checkpoint file specified from the load_from_saved parameter in the
        class constructor.
        """
        if not self._has_trained:
            self.__add_layers_to_graph()

        if self._load_from_saved is not False:
            self.__log('Loading from checkpoint file...')

            with self._graph.as_default():
                saver = tf.train.Saver(tf.trainable_variables())
                saver.restore(self._session, tf.train.latest_checkpoint(self._load_from_saved))

            self._has_trained = True
        else:
            warnings.warn('Tried to load state with no file given. Make sure load_from_saved is set in constructor.')
            exit()

    def __set_learning_rate(self):
        if self._lr_decay_factor is not None:
            # needs to be reexamined
            self._lr_decay_epochs = self._epochs_per_decay * (self._total_training_samples * (1 - self._test_split))
            self._learning_rate = tf.train.exponential_decay(self._learning_rate,
                                                             self._global_epoch,
                                                             self._lr_decay_epochs,
                                                             self._lr_decay_factor,
                                                             staircase=True)

    def forward_pass(self, x, deterministic=False, moderation_features=None):
        """
        Perform a forward pass of the network with an input tensor.
        In general, this is only used when the model is integrated into a Tensorflow graph.
        See also forward_pass_with_file_inputs.

        :param x: input tensor where the first dimension is batch
        :param deterministic: if True, performs inference-time operations on stochastic layers e.g. DropOut layers
        :return: output tensor where the first dimension is batch
        """
        with self._graph.as_default():
            for layer in self._layers:
                if isinstance(layer, layers.moderationLayer) and moderation_features is not None:
                    x = layer.forward_pass(x, deterministic, moderation_features)
                else:
                    x = layer.forward_pass(x, deterministic)

        return x

    def forward_pass_with_file_inputs(self, x):
        """
        Get network outputs with a list of filenames of images as input.
        Handles all the loading and batching automatically, so the size of the input can exceed the available memory
        without any problems.

        :param x: list of strings representing image filenames
        :return: ndarray representing network outputs corresponding to inputs in the same order
        """
        with self._graph.as_default():
            if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                total_outputs = np.empty([1, self.__last_layer().output_size])
            elif self._problem_type == definitions.ProblemType.REGRESSION:
                total_outputs = np.empty([1, self._num_regression_outputs])
            elif self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                if self._with_patching:
                    # we want the largest multiple of of patch height/width that is smaller than the original
                    # image height/width, for the final image dimensions
                    patch_height = self._patch_height
                    patch_width = self._patch_width
                    final_height = (self._image_height // patch_height) * patch_height
                    final_width = (self._image_width // patch_width) * patch_width
                    # find image differences to determine recentering crop coords, we divide by 2 so that the leftover
                    # is equal on all sides of image
                    offset_height = (self._image_height - final_height) // 2
                    offset_width = (self._image_width - final_width) // 2
                    # pre-allocate output dimensions
                    total_outputs = np.empty([1, final_height, final_width])
                else:
                    total_outputs = np.empty([1, self._image_height, self._image_width])
            elif self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                if self._with_patching:
                    # we want the largest multiple of patch height/width that is smaller than the original
                    # image height/width, for the final image dimensions
                    patch_height = self._patch_height
                    patch_width = self._patch_width
                    final_height = (self._image_height // patch_height) * patch_height
                    final_width = (self._image_width // patch_width) * patch_width
                    num_patches_vert = self._image_height // patch_height
                    num_patches_horiz = self._image_width // patch_width
                    # find image differences to determine recentering crop coords, we divide by 2 so that the leftover
                    # is equal on all sides of image
                    offset_height = (self._image_height - final_height) // 2
                    offset_width = (self._image_width - final_width) // 2
                    # pre-allocate output dimensions
                    total_outputs = np.empty([1,
                                              num_patches_horiz * num_patches_vert,
                                              self._grid_w * self._grid_h * (5 * self._NUM_BOXES + self._NUM_CLASSES)])
                else:
                    total_outputs = np.empty(
                        [1, self._grid_w * self._grid_h * (5 * self._NUM_BOXES + self._NUM_CLASSES)])
            else:
                warnings.warn('Problem type is not recognized')
                exit()

            num_batches = len(x) // self._batch_size
            remainder = len(x) % self._batch_size

            if remainder != 0:
                num_batches += 1
                remainder = self._batch_size - remainder

            # self.load_images_from_list(x) no longer calls following 2 lines so we needed to force them here
            images = x
            self.__parse_images(images)

            x_test = tf.train.batch([self._all_images], batch_size=self._batch_size, num_threads=self._num_threads)
            x_test = tf.reshape(x_test, shape=[-1, self._image_height, self._image_width, self._image_depth])
            if self._with_patching:
                x_test = tf.image.crop_to_bounding_box(x_test, offset_height, offset_width, final_height, final_width)
                # Split the images up into the multiple slices of size patch_height x patch_width
                ksizes = [1, patch_height, patch_width, 1]
                strides = [1, patch_height, patch_width, 1]
                rates = [1, 1, 1, 1]
                x_test = tf.extract_image_patches(x_test, ksizes, strides, rates, "VALID")
                x_test = tf.reshape(x_test, shape=[-1, patch_height, patch_width, self._image_depth])

            if self._load_from_saved:
                self.load_state()
            self.__initialize_queue_runners()
            # Run model on them
            x_pred = self.forward_pass(x_test, deterministic=True)

            if self._with_patching:
                if self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                    num_patch_rows = final_height // patch_height
                    num_patch_cols = final_width // patch_width
                    for i in range(num_batches):
                        xx = self._session.run(x_pred)

                        # generalized image stitching
                        for img in np.array_split(xx, self._batch_size):  # for each img in current batch
                            # we are going to build a list of rows of imgs called img_rows, where each element
                            # of img_rows is a row of img's concatenated together horizontally (axis=1), then we will
                            # iterate through img_rows concatenating the rows vertically (axis=0) to build
                            # the full img

                            img_rows = []
                            # for each row
                            for j in range(num_patch_rows):
                                curr_row = img[j*num_patch_cols]  # start new row with first img
                                # iterate through the rest of the row, concatenating img's together
                                for k in range(1, num_patch_cols):
                                    # horizontal cat
                                    curr_row = np.concatenate((curr_row, img[k+(j*num_patch_cols)]), axis=1)
                                img_rows.append(curr_row)  # add row of img's to the list

                            # start full img with the first full row of imgs
                            full_img = img_rows[0]
                            # iterate through rest of rows, concatenating rows together
                            for row_num in range(1, num_patch_rows):
                                # vertical cat
                                full_img = np.concatenate((full_img, img_rows[row_num]), axis=0)

                            # need to match total_outputs dimensions, so we add a dimension to the shape to match
                            full_img = np.array([full_img])  # shape transformation: (x,y) --> (1,x,y)
                            total_outputs = np.append(total_outputs, full_img, axis=0)  # add the final img to the list of imgs
                elif self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                    # for i in range(num_batches):
                    #     xx = self._session.run(x_pred)
                    #     # init_op = tf.global_variables_initializer()
                    #     # self._session.run(init_op)
                    #     # self.__initialize_queue_runners()
                    #     print('printing xx')
                    #     print(xx)
                    #     print(xx.shape)
                    for i in range(int(num_batches)):
                        xx = self._session.run(x_pred)
                        xx = np.reshape(xx, [self._batch_size, num_patches_vert * num_patches_horiz, -1])
                        for img in np.array_split(xx, self._batch_size):
                            total_outputs = np.append(total_outputs, img, axis=0)

            else:
                for i in range(int(num_batches)):
                    xx = self._session.run(x_pred)
                    if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                        xx = np.reshape(xx, [self._batch_size, -1])
                    for img in np.array_split(xx, self._batch_size):
                        total_outputs = np.append(total_outputs, img, axis=0)

            # delete weird first row
            total_outputs = np.delete(total_outputs, 0, 0)

            # delete any outputs which are overruns from the last batch
            if remainder != 0:
                for i in range(remainder):
                    total_outputs = np.delete(total_outputs, -1, 0)

        return total_outputs

    def forward_pass_with_interpreted_outputs(self, x):
        """
        Performs the forward pass of the network and then interprets the raw outputs into the desired format based on
        problem type and whether patching is being used.

        :param x: list of strings representing image filenames
        :return: ndarray representing network outputs corresponding to inputs in the same order
        """

        # Classification #
        if self._problem_type == definitions.ProblemType.CLASSIFICATION:
            # perform forward pass of the network to get raw outputs
            xx = self.forward_pass_with_file_inputs(x)
            # softmax
            interpreted_outputs = np.exp(xx) / np.sum(np.exp(xx), axis=1, keepdims=True)

        # Regression #
        elif self._problem_type == definitions.ProblemType.REGRESSION:
            # nothing special required for regression
            interpreted_outputs = self.forward_pass_with_file_inputs(x)

        # Semantic Segmentation #
        elif self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
            with self._graph.as_default():
                # check for patching needs
                if self._with_patching:
                    # we want the largest multiple of of patch height/width that is smaller than the original
                    # image height/width, for the final image dimensions
                    patch_height = self._patch_height
                    patch_width = self._patch_width
                    final_height = (self._image_height // patch_height) * patch_height
                    final_width = (self._image_width // patch_width) * patch_width
                    # find image differences to determine recentering crop coords, we divide by 2 so that the leftover
                    # is equal on all sides of image
                    offset_height = (self._image_height - final_height) // 2
                    offset_width = (self._image_width - final_width) // 2
                    # pre-allocate output dimensions
                    total_outputs = np.empty([1, final_height, final_width])
                else:
                    total_outputs = np.empty([1, self._image_height, self._image_width])

                num_batches = len(x) // self._batch_size
                remainder = len(x) % self._batch_size
                if remainder != 0:
                    num_batches += 1
                    remainder = self._batch_size - remainder
                # self.load_images_from_list(x) no longer calls following 2 lines so we needed to force them here
                images = x
                self.__parse_images(images)
                # set up and then initialize the queue
                x_test = tf.train.batch(
                    [self._all_images], batch_size=self._batch_size, num_threads=self._num_threads)
                x_test = tf.reshape(x_test, shape=[-1, self._image_height, self._image_width, self._image_depth])
                # if using patching we have to determine different image dimensions
                if self._with_patching:
                    x_test = tf.image.crop_to_bounding_box(
                        x_test, offset_height, offset_width, final_height, final_width)
                    # Split the images up into the multiple slices of size patch_height x patch_width
                    ksizes = [1, patch_height, patch_width, 1]
                    strides = [1, patch_height, patch_width, 1]
                    rates = [1, 1, 1, 1]
                    x_test = tf.extract_image_patches(x_test, ksizes, strides, rates, "VALID")
                    x_test = tf.reshape(x_test, shape=[-1, patch_height, patch_width, self._image_depth])
                if self._load_from_saved:
                    self.load_state()
                self.__initialize_queue_runners()
                x_pred = self.forward_pass(x_test, deterministic=True)
                # check if we need to perform patching
                if self._with_patching:
                    num_patch_rows = final_height // patch_height
                    num_patch_cols = final_width // patch_width
                    for i in range(num_batches):
                        xx = self._session.run(x_pred)
                        # generalized image stitching
                        for img in np.array_split(xx, self._batch_size):  # for each img in current batch
                            # we are going to build a list of rows of imgs called img_rows, where each element
                            # of img_rows is a row of img's concatenated together horizontally (axis=1), then we will
                            # iterate through img_rows concatenating the rows vertically (axis=0) to build
                            # the full img
                            img_rows = []
                            for j in range(num_patch_rows):  # for each row
                                curr_row = img[j*num_patch_cols]  # start new row with first img
                                # iterate through the rest of the row, concatenating img's together
                                for k in range(1, num_patch_cols):
                                    # vertical cat
                                    curr_row = np.concatenate((curr_row, img[k+(j*num_patch_cols)]), axis=1)
                                img_rows.append(curr_row)  # add row of img's to the list
                            # start full img with the first full row of imgs
                            full_img = img_rows[0]
                            # iterate through rest of rows, concatenating rows together
                            for row_num in range(1, num_patch_rows):
                                # vertical cat
                                full_img = np.concatenate((full_img, img_rows[row_num]), axis=0)
                            # need to match total_outputs dimensions, so we add a dimension to the shape to match
                            full_img = np.array([full_img])  # shape transformation: (x,y) --> (1,x,y)
                            # this appending may be causing a border, might need to rewrite and specifically index
                            total_outputs = np.append(total_outputs, full_img, axis=0)  # add the final img to the array of imgs
                else:
                    for i in range(int(num_batches)):
                        xx = self._session.run(x_pred)
                        for img in np.array_split(xx, self._batch_size):
                            total_outputs = np.append(total_outputs, img, axis=0)
                # delete weird first row
                total_outputs = np.delete(total_outputs, 0, 0)
                # delete any outputs which are overruns from the last batch
                if remainder != 0:
                    for i in range(remainder):
                        total_outputs = np.delete(total_outputs, -1, 0)
            # normalize and then threshold
            interpreted_outputs = np.zeros(total_outputs.shape, dtype=np.uint8)
            for i, img in enumerate(total_outputs):
                # normalize
                x_min = np.min(img)
                x_max = np.max(img)
                mask = (img - x_min) / (x_max - x_min)
                # threshold
                mask[mask >= 0.5] = 255
                mask[mask < 0.5] = 0
                # store
                interpreted_outputs[i, :, :] = mask

        # Object Detection #
        elif self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
            with self._graph.as_default():
                # check for patching needs
                if self._with_patching:
                    # we want the largest multiple of patch height/width that is smaller than the original
                    # image height/width, for the final image dimensions
                    patch_height = self._patch_height
                    patch_width = self._patch_width
                    final_height = (self._image_height // patch_height) * patch_height
                    final_width = (self._image_width // patch_width) * patch_width
                    num_patches_vert = self._image_height // patch_height
                    num_patches_horiz = self._image_width // patch_width
                    # find image differences to determine recentering crop coords, we divide by 2 so that the leftover
                    # is equal on all sides of image
                    offset_height = (self._image_height - final_height) // 2
                    offset_width = (self._image_width - final_width) // 2
                    # pre-allocate output dimensions
                    total_outputs = np.empty([1,
                                              num_patches_horiz * num_patches_vert,
                                              self._grid_w * self._grid_h * (5 * self._NUM_BOXES + self._NUM_CLASSES)])
                else:
                    total_outputs = np.empty(
                        [1, self._grid_w * self._grid_h * (5 * self._NUM_BOXES + self._NUM_CLASSES)])
                num_batches = len(x) // self._batch_size
                remainder = len(x) % self._batch_size

                if remainder != 0:
                    num_batches += 1
                    remainder = self._batch_size - remainder

                # self.load_images_from_list(x) no longer calls following 2 lines so we needed to force them here
                images = x
                self.__parse_images(images)

                x_test = tf.train.batch([self._all_images], batch_size=self._batch_size,
                                        num_threads=self._num_threads)
                x_test = tf.reshape(x_test, shape=[-1, self._image_height, self._image_width, self._image_depth])
                if self._with_patching:
                    x_test = tf.image.crop_to_bounding_box(x_test, offset_height, offset_width, final_height,
                                                           final_width)
                    # Split the images up into the multiple slices of size patch_height x patch_width
                    ksizes = [1, patch_height, patch_width, 1]
                    strides = [1, patch_height, patch_width, 1]
                    rates = [1, 1, 1, 1]
                    x_test = tf.extract_image_patches(x_test, ksizes, strides, rates, "VALID")
                    x_test = tf.reshape(x_test, shape=[-1, patch_height, patch_width, self._image_depth])

                if self._load_from_saved:
                    self.load_state()
                self.__initialize_queue_runners()
                # Run model on them
                x_pred = self.forward_pass(x_test, deterministic=True)
                if self._with_patching:
                    # for i in range(num_batches):
                    #     xx = self._session.run(x_pred)
                    #     # init_op = tf.global_variables_initializer()
                    #     # self._session.run(init_op)
                    #     # self.__initialize_queue_runners()
                    #     print('printing xx')
                    #     print(xx)
                    #     print(xx.shape)
                    for i in range(int(num_batches)):
                        xx = self._session.run(x_pred)
                        xx = np.reshape(xx, [self._batch_size, num_patches_vert * num_patches_horiz, -1])
                        for img in np.array_split(xx, self._batch_size):
                            total_outputs = np.append(total_outputs, img, axis=0)
                else:
                    for i in range(int(num_batches)):
                        xx = self._session.run(x_pred)
                        xx = np.reshape(xx, [self._batch_size, -1])
                        for img in np.array_split(xx, self._batch_size):
                            total_outputs = np.append(total_outputs, img, axis=0)
                # delete weird first row
                total_outputs = np.delete(total_outputs, 0, 0)
                # delete any outputs which are overruns from the last batch
                if remainder != 0:
                    for i in range(remainder):
                        total_outputs = np.delete(total_outputs, -1, 0)
            # Perform yolo needs
            # this is currently for patching, need a way to be more general or maybe just need to write both ways out
            # fully
            total_pred_boxes = []
            if self._with_patching:
                num_patches = num_patches_vert * num_patches_horiz
                for img_data in total_outputs:
                    ###################################################################################################
                    # img_data is [x,y,w,h,conf,x,y,w,h,conf,x,y,......, classes]
                    # currently 5 boxes and 1 class are fixed amounts, hence we pull 5 box confs and we use multiples
                    # of 26 because 5 (boxes) * 5 (x,y,w,h,conf) + 1 (class) = 26
                    # this may likely need to be made more general in future
                    ###################################################################################################
                    for i in range(num_patches):
                        for j in range(self._grid_w * self._grid_h):
                            # We first find the responsible box by finding the one with the highest confidence
                            box_conf1 = expit(img_data[i, j * 26 + 4])
                            box_conf2 = expit(img_data[i, j * 26 + 9])
                            box_conf3 = expit(img_data[i, j * 26 + 14])
                            box_conf4 = expit(img_data[i, j * 26 + 19])
                            box_conf5 = expit(img_data[i, j * 26 + 24])
                            box_confs = [box_conf1, box_conf2, box_conf3, box_conf4, box_conf5]
                            max_conf_idx = np.argmax(box_confs)
                            # Then we check if the responsible box is above the threshold for detecting an object
                            if box_confs[max_conf_idx] > 0.6:
                                # This box has detected an object and we extract its coords
                                pred_img = True
                                pred_box = img_data[i, j*26+5*max_conf_idx:j*26+5*max_conf_idx+4]
                            else:  # No object detected
                                pred_img = False
                            # If an object is detected we now transform the data into the desired result
                            if pred_img:
                                # centers from which x and y offsets are applied to, these are in 'grid coords'
                                c_x = j % self._grid_w
                                c_y = j // self._grid_w
                                # x and y go from 'grid coords' to 'patch coords' to 'full img coords'
                                x = (expit(pred_box[0]) + c_x) * (patch_width / self._grid_w) \
                                    + (i % num_patches_horiz)*patch_width
                                y = (expit(pred_box[1]) + c_y) * (patch_height / self._grid_h) \
                                    + (i // num_patches_horiz)*patch_height
                                # get the anchor box based on the highest conf (responsible box)
                                prior_w = self._ANCHORS[max_conf_idx][0]
                                prior_h = self._ANCHORS[max_conf_idx][1]
                                # w and h go from 'grid coords' to 'full img coords'
                                w = (np.exp(pred_box[2]) * prior_w) * (self._image_width / self._grid_w)
                                h = (np.exp(pred_box[3]) * prior_h) * (self._image_height / self._grid_h)
                                # turn into points
                                x1y1 = (int(x - w/2), int(y - h/2))
                                x2y2 = (int(x + w/2), int(y + h/2))
                                total_pred_boxes.append([x1y1[0], x1y1[1], x2y2[0], x2y2[1], box_confs[max_conf_idx]])
                    # Non - maximal suppression (Probably make into a general function)
                    all_boxes = np.array(total_pred_boxes)
                    idxs = np.argsort(all_boxes[:, 4])  # sorts them smallest to largest by confidence
                    final_boxes_idxs = []
                    while len(idxs) > 0:  # sometimes we may delete multiple boxes so we use a while instead of for
                        last = len(idxs) - 1  # since sorted in reverse order, the last one has the highest conf
                        i = idxs[last]
                        final_boxes_idxs.append(i)  # add it to the list (highest conf) then we check if there are duplicates to delete
                        suppress = [last]  # this is the list of idxs of boxes to stop checking (they will deleted)
                        for pos in range(0, last):  # search for duplicates
                            j = idxs[pos]
                            iou = self.__compute_iou(all_boxes[i], all_boxes[j])
                            if iou > 0.3:  # maybe should make this a tunable parameter
                                suppress.append(pos)
                        idxs = np.delete(idxs, suppress)  # remove the box that was added and its duplicates

                interpreted_outputs = np.array(all_boxes[final_boxes_idxs, :])  # [[x1,y1,x2,y2,conf],[x1,y1,x2,y2,conf],...]
            else:
                print('made it')
                # no patching
                print(total_outputs.shape)
                for img_data in total_outputs:
                    ###################################################################################################
                    # img_data is [x,y,w,h,conf,x,y,w,h,conf,x,y,......, classes]
                    # currently 5 boxes and 1 class are fixed amounts, hence we pull 5 box confs and we use multiples
                    # of 26 because 5 (boxes) * 5 (x,y,w,h,conf) + 1 (class) = 26
                    # this may likely need to be made more general in future
                    ###################################################################################################
                    for i in range(self._grid_w * self._grid_h):
                        # x,y,w,h,conf,x,y,w,h,cong,x,y,...... classes
                        box_conf1 = expit(img_data[i * 26 + 4])
                        box_conf2 = expit(img_data[i * 26 + 9])
                        box_conf3 = expit(img_data[i * 26 + 14])
                        box_conf4 = expit(img_data[i * 26 + 19])
                        box_conf5 = expit(img_data[i * 26 + 24])
                        box_confs = [box_conf1, box_conf2, box_conf3, box_conf4, box_conf5]
                        max_conf_idx = np.argmax(box_confs)

                        if box_confs[max_conf_idx] > 0.6:
                            pred_img = True
                            pred_box = img_data[i * 26 + 5 * max_conf_idx: i * 26 + 5 * max_conf_idx + 4]

                        else:
                            pred_img = False

                        if pred_img:
                            # centers from which x and y offsets are applied to, these are in 'grid coords'
                            c_x = i % self._grid_w
                            c_y = i // self._grid_w
                            # x and y go from 'grid coords' to 'full img coords'
                            x = (expit(pred_box[0]) + c_x) * (self._image_width / self._grid_w)
                            y = (expit(pred_box[1]) + c_y) * (self._image_height / self._grid_h)
                            # get the anchor box based on the highest conf (responsible box)
                            prior_w = self._ANCHORS[max_conf_idx][0]
                            prior_h = self._ANCHORS[max_conf_idx][1]
                            # w and h go from 'grid coords' to 'full img coords'
                            w = (np.exp(pred_box[2]) * prior_w) * (self._image_width / self._grid_w)
                            h = (np.exp(pred_box[3]) * prior_h) * (self._image_height / self._grid_h)
                            x1y1 = (int(x - w / 2), int(y - h / 2))
                            x2y2 = (int(x + w / 2), int(y + h / 2))
                            total_pred_boxes.append([x1y1[0], x1y1[1], x2y2[0], x2y2[1], box_confs[max_conf_idx]])

                    # Non - maximal suppression (Probably make into a general function)
                    all_boxes = np.array(total_pred_boxes)
                    idxs = np.argsort(all_boxes[:, 4])  # sorts them smallest to largest by confidence
                    final_boxes_idxs = []
                    while len(idxs) > 0:  # sometimes we may delete multiple boxes so we use a while instead of for
                        last = len(idxs) - 1  # since sorted in reverse order, the last one has highest conf
                        i = idxs[last]
                        final_boxes_idxs.append(i)  # add it to the list (highest conf) then we check if there are duplicates to delete
                        suppress = [last]  # this is the list of idxs of boxes to stop checking (they will deleted)
                        for pos in range(0, last):  # search for duplicates
                            j = idxs[pos]
                            iou = self.__compute_iou(all_boxes[i], all_boxes[j])
                            if iou > 0.3:  # maybe should make this a tunable parameter
                                suppress.append(pos)
                        idxs = np.delete(idxs, suppress)  # remove the box that was added and its duplicates
                interpreted_outputs = np.array(all_boxes[final_boxes_idxs, :])  # [[x1,y1,x2,y2,conf],[x1,y1,x2,y2,conf],...]
        else:
            warnings.warn('Problem type is not recognized')
            exit()

        return interpreted_outputs

    def __compute_iou(self, box1, box2):
        """
        Need to somehow merge with the iou helper function in the yolo cost function.

        :param box1: x1, y1, x2, y2
        :param box2: x1, y1, x2, y2
        :return: Intersection Over Union of box1 and box2
        """
        x1 = np.maximum(box1[0], box2[0])
        y1 = np.maximum(box1[1], box2[1])
        x2 = np.minimum(box1[2], box2[2])
        y2 = np.minimum(box1[3], box2[3])

        intersection_area = np.maximum(0., x2 - x1) * np.maximum(0., y2 - y1)
        union_area = \
            ((box1[2] - box1[0]) * (box1[3] - box1[1])) + \
            ((box2[2] - box2[0]) * (box2[3] - box2[1])) - \
            intersection_area

        return intersection_area / union_area

    def __batch_mean_l2_loss(self, x):
        """Given a batch of vectors, calculates the mean per-vector L2 norm"""
        with self._graph.as_default():
            agg = self.__l2_norm(x)
            mean = tf.reduce_mean(agg)

        return mean

    def __l2_norm(self, x):
        """Returns the L2 norm of a tensor"""
        with self._graph.as_default():
            y = tf.map_fn(lambda ex: tf.norm(ex, ord=2), x)

        return y

    def __batch_mean_l1_loss(self, x):
        """Given a batch of vectors, calculates the mean per-vector L1 norm"""
        with self._graph.as_default():
            agg = self.__l1_norm(x)
            mean = tf.reduce_mean(agg)

        return mean

    def __l1_norm(self, x):
        """Returns the L1 norm of a tensor"""
        with self._graph.as_default():
            y = tf.map_fn(lambda ex: tf.norm(ex, ord=1), x)

        return y

    def __batch_mean_smooth_l1_loss(self, x):
        """Given a batch of vectors, calculates the mean per-vector smooth L1 norm"""
        with self._graph.as_default():
            agg = self.__smooth_l1_norm(x)
            mean = tf.reduce_mean(agg)

        return mean

    def __smooth_l1_norm(self, x):
        """Returns the smooth L1 norm of a tensor"""
        huber_delta = 1  # may want to make this a tunable hyper parameter in future
        with self._graph.as_default():
            x = tf.abs(x)
            y = tf.map_fn(lambda ex: tf.where(ex < huber_delta,
                                              0.5*ex**2,
                                              huber_delta*(ex-0.5*huber_delta)), x)

        return y

    def __batch_mean_log_loss(self, x):
        """Given a batch of vectors, calculates the mean per-vector log loss"""
        with self._graph.as_default():
            x = tf.abs(x)
            x = tf.clip_by_value(x, 0, 0.9999999)
            agg = -tf.log(1-x)
            mean = tf.reduce_mean(agg)

        return mean

    def add_input_layer(self):
        """Add an input layer to the network"""
        if len(self._layers) > 0:
            raise RuntimeError("Trying to add an input layer to a model that already contains other layers. " +
                               " The input layer need to be the first layer added to the model.")

        self.__log('Adding the input layer...')

        apply_crop = (self._augmentation_crop and self._all_images is None and self._train_images is None)

        if apply_crop:
            size = [self._batch_size, int(self._image_height * self._crop_amount),
                    int(self._image_width * self._crop_amount), self._image_depth]
        else:
            size = [self._batch_size, self._image_height, self._image_width, self._image_depth]

        if self._with_patching:
            size = [self._batch_size, self._patch_height, self._patch_width, self._image_depth]

        with self._graph.as_default():
            layer = layers.inputLayer(size)

        self._layers.append(layer)

    def add_moderation_layer(self):
        """Add a moderation layer to the network"""
        self.__log('Adding moderation layer...')

        reshape = self.__last_layer_outputs_volume()

        feat_size = self._moderation_features_size

        with self._graph.as_default():
            layer = layers.moderationLayer(copy.deepcopy(
                self.__last_layer().output_size), feat_size, reshape, self._batch_size)

        self._layers.append(layer)

    def add_convolutional_layer(self, filter_dimension, stride_length, activation_function):
        """
        Add a convolutional layer to the model.

        :param filter_dimension: array of dimensions in the format [x_size, y_size, depth, num_filters]
        :param stride_length: convolution stride length
        :param activation_function: the activation function to apply to the activation map
        """
        if len(self._layers) < 1:
            raise RuntimeError("A convolutional layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")
        try:  # try to iterate through filter_dimension, checking it has 4 ints
            for idx, dim in enumerate(filter_dimension):
                if not (isinstance(dim, int) or isinstance(dim, np.int64)):  # np.int64 numpy default int
                    raise TypeError()
            if idx != 3:
                raise TypeError()
        except:
            raise TypeError("filter_dimension must be a list or array of 4 ints")
        if not isinstance(stride_length, int):
            raise TypeError("stride_length must be an int")
        if stride_length <= 0:
            raise ValueError("stride_length must be positive")
        if not isinstance(activation_function, str):
            raise TypeError("activation_function must be a str")
        activation_function = activation_function.lower()
        if not activation_function in self._supported_activation_functions:
            raise ValueError("'" + activation_function + "' is not one of the currently supported activation " +
                             "functions. Choose one of: " +
                             " ".join("'"+x+"'" for x in self._supported_activation_functions))

        self._num_layers_conv += 1
        layer_name = 'conv%d' % self._num_layers_conv
        self.__log('Adding convolutional layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.convLayer(layer_name,
                                     copy.deepcopy(self.__last_layer().output_size),
                                     filter_dimension,
                                     stride_length,
                                     activation_function,
                                     self._weight_initializer)

        self.__log('Filter dimensions: {0} Outputs: {1}'.format(filter_dimension, layer.output_size))

        self._layers.append(layer)

    def add_upsampling_layer(self, filter_size, num_filters, upscale_factor=2,
                             activation_function=None, regularization_coefficient=None):
        """
        Add a 2d upsampling layer to the model.

        :param filter_size: an int, representing the dimension of the square filter to be used
        :param num_filters: an int, representing the number of filters that will be outputted (the output tensor depth)
        :param upscale_factor: an int, or tuple of ints, representing the upsampling factor for rows and columns
        :param activation_function: the activation function to apply to the activation map
        :param regularization_coefficient: optionally, an L2 decay coefficient for this layer (overrides the coefficient
         set by set_regularization_coefficient)
        """
        self._num_layers_upsample += 1
        layer_name = 'upsample%d' % self._num_layers_upsample
        self.__log('Adding upsampling layer %s...' % layer_name)

        if regularization_coefficient is None and self._reg_coeff is not None:
            regularization_coefficient = self._reg_coeff
        elif regularization_coefficient is None and self._reg_coeff is None:
            regularization_coefficient = 0.0

        if self._with_patching:
            patches_horiz = self._image_width // self._patch_width
            patches_vert = self._image_height // self._patch_height
            batch_multiplier = patches_horiz * patches_vert
        else:
            batch_multiplier = 1

        last_layer_dims = copy.deepcopy(self.__last_layer().output_size)
        with self._graph.as_default():
            layer = layers.upsampleLayer(layer_name,
                                         last_layer_dims,
                                         filter_size,
                                         num_filters,
                                         upscale_factor,
                                         activation_function,
                                         batch_multiplier,
                                         self._weight_initializer,
                                         regularization_coefficient)

        self.__log('Filter dimensions: {0} Outputs: {1}'.format(layer.weights_shape, layer.output_size))

        self._layers.append(layer)

    def add_pooling_layer(self, kernel_size, stride_length, pooling_type='max'):
        """
        Add a pooling layer to the model.

        :param kernel_size: an integer representing the width and height dimensions of the pooling operation
        :param stride_length: convolution stride length
        :param pooling_type: optional, the type of pooling operation
        """
        if len(self._layers) < 1:
            raise RuntimeError("A pooling layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")
        if not isinstance(kernel_size, int):
            raise TypeError("kernel_size must be an int")
        if kernel_size <= 0:
            raise ValueError("kernel_size must be positive")
        if not isinstance(stride_length, int):
            raise TypeError("stride_length must be an int")
        if stride_length <= 0:
            raise ValueError("stride_length must be positive")
        if not isinstance(pooling_type, str):
            raise TypeError("pooling_type must be a str")
        pooling_type = pooling_type.lower()
        if not pooling_type in self._supported_pooling_types:
            raise ValueError("'" + pooling_type + "' is not one of the currently supported pooling types." +
                             " Choose one of: " +
                             " ".join("'"+x+"'" for x in self._supported_pooling_types))

        self._num_layers_pool += 1
        layer_name = 'pool%d' % self._num_layers_pool
        self.__log('Adding pooling layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.poolingLayer(copy.deepcopy(
                self.__last_layer().output_size), kernel_size, stride_length, pooling_type)

        self.__log('Outputs: %s' % layer.output_size)

        self._layers.append(layer)

    def add_normalization_layer(self):
        """Add a local response normalization layer to the model"""
        if len(self._layers) < 1:
            raise RuntimeError("A normalization layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")

        self._num_layers_norm += 1
        layer_name = 'norm%d' % self._num_layers_pool
        self.__log('Adding pooling layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.normLayer(copy.deepcopy(self.__last_layer().output_size))

        self._layers.append(layer)

    def add_dropout_layer(self, p):
        """
        Add a DropOut layer to the model.

        :param p: the keep-probability parameter for the DropOut operation
        """
        if len(self._layers) < 1:
            raise RuntimeError("A dropout layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")
        if not isinstance(p, float):
            raise TypeError("p must be a float")
        if p < 0 or p >= 1:
            raise ValueError("p must be in range [0, 1)")

        self._num_layers_dropout += 1
        layer_name = 'drop%d' % self._num_layers_dropout
        self.__log('Adding dropout layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.dropoutLayer(copy.deepcopy(self.__last_layer().output_size), p)

        self._layers.append(layer)

    def add_batch_norm_layer(self):
        """Add a batch normalization layer to the model."""
        if len(self._layers) < 1:
            raise RuntimeError("A batch norm layer cannot be the first layer added to the model.")

        self._num_layers_batchnorm += 1
        layer_name = 'bn%d' % self._num_layers_batchnorm
        self.__log('Adding batch norm layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.batchNormLayer(layer_name, copy.deepcopy(self.__last_layer().output_size))

        self._layers.append(layer)

    def add_fully_connected_layer(self, output_size, activation_function, regularization_coefficient=None):
        """
        Add a fully connected layer to the model.

        :param output_size: the number of units in the layer
        :param activation_function: optionally, the activation function to use
        :param regularization_coefficient: optionally, an L2 decay coefficient for this layer (overrides the coefficient
         set by set_regularization_coefficient)
        """
        if len(self._layers) < 1:
            raise RuntimeError("A fully connected layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")
        if not isinstance(output_size, int):
            raise TypeError("output_size must be an int")
        if output_size <= 0:
            raise ValueError("output_size must be positive")
        if not isinstance(activation_function, str):
            raise TypeError("activation_function must be a str")
        activation_function = activation_function.lower()
        if not activation_function in self._supported_activation_functions:
            raise ValueError("'" + activation_function + "' is not one of the currently supported activation " +
                             "functions. Choose one of: " +
                             " ".join("'"+x+"'" for x in self._supported_activation_functions))
        if regularization_coefficient is not None:
            if not isinstance(regularization_coefficient, float):
                raise TypeError("regularization_coefficient must be a float or None")
            if regularization_coefficient < 0:
                raise ValueError("regularization_coefficient must be non-negative")

        self._num_layers_fc += 1
        layer_name = 'fc%d' % self._num_layers_fc
        self.__log('Adding fully connected layer %s...' % layer_name)

        reshape = self.__last_layer_outputs_volume()

        if regularization_coefficient is None and self._reg_coeff is not None:
            regularization_coefficient = self._reg_coeff
        if regularization_coefficient is None and self._reg_coeff is None:
            regularization_coefficient = 0.0

        with self._graph.as_default():
            layer = layers.fullyConnectedLayer(layer_name,
                                               copy.deepcopy(self.__last_layer().output_size),
                                               output_size,
                                               reshape,
                                               self._batch_size,
                                               activation_function,
                                               self._weight_initializer,
                                               regularization_coefficient)

        self.__log('Inputs: {0} Outputs: {1}'.format(layer.input_size, layer.output_size))

        self._layers.append(layer)

    def add_output_layer(self, regularization_coefficient=None, output_size=None):
        """
        Add an output layer to the network (affine layer where the number of units equals the number of network outputs)

        :param regularization_coefficient: optionally, an L2 decay coefficient for this layer (overrides the coefficient
         set by set_regularization_coefficient)
        :param output_size: optionally, override the output size of this layer. Typically not needed, but required for
        use cases such as creating the output layer before loading data.
        """
        if len(self._layers) < 1:
            raise RuntimeError("An output layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")
        if regularization_coefficient is not None:
            if not isinstance(regularization_coefficient, float):
                raise TypeError("regularization_coefficient must be a float or None")
            if regularization_coefficient < 0:
                raise ValueError("regularization_coefficient must be non-negative")
        if output_size is not None:
            if not isinstance(output_size, int):
                raise TypeError("output_size must be an int or None")
            if output_size <= 0:
                raise ValueError("output_size must be positive")
            if self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                raise RuntimeError("output_size should be None for problem_type semantic_segmentation")

        self.__log('Adding output layer...')

        reshape = self.__last_layer_outputs_volume()

        if regularization_coefficient is None and self._reg_coeff is not None:
            regularization_coefficient = self._reg_coeff
        if regularization_coefficient is None and self._reg_coeff is None:
            regularization_coefficient = 0.0

        if output_size is None:
            if self._problem_type == definitions.ProblemType.CLASSIFICATION:
                num_out = self._total_classes
            elif self._problem_type == definitions.ProblemType.REGRESSION:
                num_out = self._num_regression_outputs
            elif self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                filter_dimension = [1, 1, copy.deepcopy(self.__last_layer().output_size[3]), 1]
            elif self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                # yolo S x S x (5B + K)
                num_out = self._grid_w * self._grid_h * (5 * self._NUM_BOXES + self._NUM_CLASSES)
                filter_dimension = [1, 1, copy.deepcopy(self.__last_layer().output_size[3]),
                                    (5 * self._NUM_BOXES + self._NUM_CLASSES)]
            else:
                warnings.warn('Problem type is not recognized')
                exit()
        else:
            num_out = output_size

        with self._graph.as_default():
            if self._problem_type is definitions.ProblemType.SEMANTIC_SEGMETNATION:
                layer = layers.convLayer('output',
                                         copy.deepcopy(self.__last_layer().output_size),
                                         filter_dimension,
                                         1,
                                         None,
                                         self._weight_initializer)
            elif self._problem_type is definitions.ProblemType.OBJECT_DETECTION:
                layer = layers.convLayer('output',
                                         copy.deepcopy(self.__last_layer().output_size),
                                         filter_dimension,
                                         1,
                                         None,
                                         self._weight_initializer)
            else:
                layer = layers.fullyConnectedLayer('output',
                                                   copy.deepcopy(self.__last_layer().output_size),
                                                   num_out,
                                                   reshape,
                                                   self._batch_size,
                                                   None,
                                                   self._weight_initializer,
                                                   regularization_coefficient)

        self.__log('Inputs: {0} Outputs: {1}'.format(layer.input_size, layer.output_size))

        self._layers.append(layer)

    def use_predefined_model(self, model_name):
        """
        Add network layers to build a predefined network model
        :param model_name: The predefined model name
        """
        if model_name not in self._supported_predefined_models:
            raise ValueError("'" + model_name + "' is not one of the currently supported predefined models." +
                             " Make sure you have the correct problem type set with DPPModel.set_problem_type() " +
                             "first, or choose one of " +
                             " ".join("'" + x + "'" for x in self._supported_predefined_models))

        if model_name == 'vgg-16':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 64],
                                         stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 64], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 256], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_fully_connected_layer(output_size=4096, activation_function='relu')
            self.add_dropout_layer(0.5)
            self.add_fully_connected_layer(output_size=4096, activation_function='relu')
            self.add_dropout_layer(0.5)

            self.add_output_layer()

        if model_name == 'alexnet':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[11, 11, self._image_depth, 48],
                                         stride_length=4, activation_function='relu')
            self.add_normalization_layer()
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[5, 5, 48, 256], stride_length=1, activation_function='relu')
            self.add_normalization_layer()
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 384], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 384, 384], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 384, 256], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_fully_connected_layer(output_size=4096, activation_function='relu')
            self.add_dropout_layer(0.5)
            self.add_fully_connected_layer(output_size=4096, activation_function='relu')
            self.add_dropout_layer(0.5)

            self.add_output_layer()

        if model_name == 'xsmall':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 16],
                                         stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 16, 32], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 32, 32], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_fully_connected_layer(output_size=64, activation_function='relu')

            self.add_output_layer()

        if model_name == 'small':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 64],
                                         stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_fully_connected_layer(output_size=64, activation_function='relu')

            self.add_output_layer()

        if model_name == 'medium':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 64],
                                         stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 64], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 256], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_fully_connected_layer(output_size=256, activation_function='relu')

            self.add_output_layer()

        if model_name == 'large':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 64],
                                         stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 64], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 256], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_fully_connected_layer(output_size=512, activation_function='relu')
            self.add_fully_connected_layer(output_size=384, activation_function='relu')

            self.add_output_layer()

        if model_name == 'yolov2':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 32],
                                         stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 32, 64], stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 128, 64], stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 256, 128],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256],
                                         stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 512, 256],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 512, 256],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 1024, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 1024, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 1024, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 1024, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 1024, 1024],
                                         stride_length=1, activation_function='lrelu')

            self.add_output_layer()

    def load_dataset_from_directory_with_csv_labels(self, dirname, labels_file, column_number=False):
        """
        Loads the png images in the given directory into an internal representation, using the labels provided in a CSV
        file.

        :param dirname: the path of the directory containing the images
        :param labels_file: the path of the .csv file containing the labels
        :param column_number: the column number (zero-indexed) of the column in the csv file representing the label
        """
        if not isinstance(dirname, str):
            raise TypeError("dirname must be a str")
        if not os.path.isdir(dirname):
            raise ValueError("'"+dirname+"' does not exist")
        if not isinstance(labels_file, str):
            raise TypeError("labels_file must be a str")

        image_files = [os.path.join(dirname, name) for name in os.listdir(dirname) if
                       os.path.isfile(os.path.join(dirname, name)) & name.endswith('.png')]

        labels = loaders.read_csv_labels(labels_file, column_number)

        self._total_raw_samples = len(image_files)
        self._total_classes = len(set(labels))

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Total classes is %d' % self._total_classes)

        self._raw_image_files = image_files
        self._raw_labels = labels
        self._split_labels = False  # Band-aid fix

    def load_dataset_from_directory_with_segmentation_masks(self, dirname, seg_dirname):
        """
        Loads the png images in the given directory into an internal representation, using binary segmentation
        masks from another file with the same filename as ground truth.

        :param dirname: the path of the directory containing the images
        :param seg_dirname: the path of the directory containing ground-truth binary segmentation masks
        """

        if self._problem_type is not definitions.ProblemType.SEMANTIC_SEGMETNATION:
            warnings.warn('Trying to load a segmentation dataset, but the problem type is not properly set.')
            exit()

        image_files = [os.path.join(dirname, name) for name in os.listdir(dirname) if
                       os.path.isfile(os.path.join(dirname, name)) & name.endswith('.png')]

        seg_files = [os.path.join(seg_dirname, name) for name in os.listdir(seg_dirname) if
                     os.path.isfile(os.path.join(seg_dirname, name)) & name.endswith('.png')]

        self._total_raw_samples = len(image_files)

        self.__log('Total raw examples is %d' % self._total_raw_samples)

        self._raw_image_files = image_files
        self._raw_labels = seg_files
        self._split_labels = False  # Band-aid fix

    def load_ippn_dataset_from_directory(self, dirname, column='strain'):
        """Loads the RGB images and species labels from the International Plant Phenotyping Network dataset."""

        labels = []
        ids = []
        if column == 'treatment':
            labels, ids = loaders.read_csv_labels_and_ids(os.path.join(dirname, 'Metadata.csv'), 2, 0)
        elif column == 'strain':
            labels, ids = loaders.read_csv_labels_and_ids(os.path.join(dirname, 'Metadata.csv'), 1, 0)
        elif column == 'DAG':
            labels, ids = loaders.read_csv_labels_and_ids(os.path.join(dirname, 'Metadata.csv'), 3, 0)
        else:
            warnings.warn('Unknown column in IPPN dataset')
            exit()

        image_files = [os.path.join(dirname, im_id + '_rgb.png') for im_id in ids]

        self._total_raw_samples = len(image_files)

        if self._problem_type == definitions.ProblemType.CLASSIFICATION:
            self._total_classes = len(set(labels))

            # transform into numerical one-hot labels
            with self._graph.as_default():
                labels = loaders.string_labels_to_sequential(labels)
                labels = tf.one_hot(labels, self._total_classes)

            self.__log('Total classes is %d' % self._total_classes)
        elif self._problem_type == definitions.ProblemType.REGRESSION:
            labels = [[label] for label in labels]

        self.__log('Total raw examples is %d' % self._total_raw_samples)

        self._raw_image_files = image_files
        self._raw_labels = labels

    def load_ippn_tray_dataset_from_directory(self, dirname):
        """
        Loads the RGB tray images and plant bounding box labels from the International Plant Phenotyping Network
        dataset.
        """
        self._resize_bbox_coords = True

        images = [os.path.join(dirname, name) for name in sorted(os.listdir(dirname)) if
                  os.path.isfile(os.path.join(dirname, name)) & name.endswith('_rgb.png')]

        label_files = [os.path.join(dirname, name) for name in sorted(os.listdir(dirname)) if
                       os.path.isfile(os.path.join(dirname, name)) & name.endswith('_bbox.csv')]

        # currently reads columns, need to read rows instead!!!
        labels = [loaders.read_csv_rows(label_file) for label_file in label_files]

        self._all_labels = []
        for label in labels:
            curr_label = []
            for nums in label:
                if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
                    # yolo wants x,y,w,h for coords
                    curr_label.extend(loaders.box_coordinates_to_xywh_coordinates(nums))
                else:
                    curr_label.extend(loaders.box_coordinates_to_pascal_voc_coordinates(nums))
            self._all_labels.append(curr_label)

        self._total_raw_samples = len(images)

        # need to add object-ness flag and one-hot encodings for class
        # it will be 1 or 0 for object-ness, one-hot for the class, then 4 bbox coords (x,y,w,h)
        # e.g. [1,0,0,...,1,...,0,223,364,58,62] but since there is only one class for the ippn dataset we get
        # [1,1,x,y,w,h]
        if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
            # for scaling bbox coords
            # scaling image down to the grid size
            scale_ratio_w = self._grid_w / self._image_width_original
            scale_ratio_h = self._grid_h / self._image_height_original

            labels_with_one_hot = []
            for curr_img_coords in self._all_labels:
                curr_img_labels = []
                num_boxes = len(curr_img_coords) // 4
                for i in range(num_boxes):
                    # start the current box label with the object-ness flag and class label (there is only one class
                    # for ippn)
                    curr_box = [1, 1]
                    # add scaled bbox coords
                    j = i * 4
                    # x and y offsets from grid position
                    x_grid = curr_img_coords[j] * scale_ratio_w
                    y_grid = curr_img_coords[j + 1] * scale_ratio_h
                    x_grid_offset, x_grid_loc = np.modf(x_grid)
                    y_grid_offset, y_grid_loc = np.modf(y_grid)
                    # w and h ratios from anchor box
                    w_ratio = curr_img_coords[j + 2] / self._ANCHORS[0]
                    h_ratio = curr_img_coords[j + 3] / self._ANCHORS[1]
                    curr_box.append(x_grid_offset)
                    curr_box.append(y_grid_offset)
                    curr_box.append(w_ratio)
                    curr_box.append(h_ratio)
                    curr_img_labels.extend(curr_box)
                labels_with_one_hot.append(curr_img_labels)
            self._raw_labels = labels_with_one_hot

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        self._raw_image_files = images
        self._raw_labels = self._all_labels

        # visual image check/debug, printing image and bounding boxes #
        # img = cv2.imread(self._raw_image_files[0], 1)
        # boxes = self._raw_labels[0]
        # height, width, depth = img.shape
        # print(boxes[53:59])
        # for i in range(19):
        #     # p1 = (int((float(boxes[i*54 + 1]) - (float(boxes[i*54+3]/2)))*3108/7), int((float(boxes[i*54 + 2]) - (float(boxes[i*54+4]/2)))*2324/7))
        #     # p2 = (int((float(boxes[i*54 + 1]) + (float(boxes[i*54+3]/2)))*3108/7), int((float(boxes[i*54 + 2]) + (float(boxes[i*54+4]/2)))*2324/7))
        #     grid_arr = np.array(boxes[i*54+5:i*54+54])
        #     grid_pos = np.dot(grid_arr, np.arange(49))
        #     x = int((boxes[i * 54 + 1] + grid_pos % 7) * 3108 / 7)
        #     y = int((boxes[i * 54 + 2] + grid_pos // 7) * 2324 / 7)
        #     print('ANCHOR')
        #     print(self._ANCHORS[0])
        #     w = boxes[i * 54 + 3] * self._ANCHORS[0]
        #     h = boxes[i * 54 + 4] * self._ANCHORS[1]
        #     p1 = (int(x - w/2),
        #           int(y - h/2))
        #     p2 = (int(x + w/2),
        #           int(y + h/2))
        #     print(p1, p2)
        #     cv2.rectangle(img, p1, p2, (255, 0, 0), 5)
        # cv2.namedWindow('image', cv2.WINDOW_NORMAL)
        # cv2.imshow('image', img)
        # cv2.waitKey(0)

    def load_ippn_leaf_count_dataset_from_directory(self, dirname):
        """Loads the RGB images and species labels from the International Plant Phenotyping Network dataset."""
        if self._image_height is None or self._image_width is None or self._image_depth is None:
            raise RuntimeError("Image dimensions need to be set before loading data." +
                               " Try using DPPModel.set_image_dimensions() first.")
        if self._maximum_training_batches is None:
            raise RuntimeError("The number of maximum training epochs needs to be set before loading data." +
                               " Try using DPPModel.set_maximum_training_epochs() first.")

        labels, ids = loaders.read_csv_labels_and_ids(os.path.join(dirname, 'Leaf_counts.csv'), 1, 0)

        # labels must be lists
        labels = [[label] for label in labels]

        image_files = [os.path.join(dirname, im_id + '_rgb.png') for im_id in ids]

        self._total_raw_samples = len(image_files)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        self._raw_image_files = image_files
        self._raw_labels = labels

    def load_inra_dataset_from_directory(self, dirname):
        """Loads the RGB images and labels from the INRA dataset."""

        labels, ids = loaders.read_csv_labels_and_ids(os.path.join(dirname, 'AutomatonImages.csv'), 1, 3, character=';')

        # Remove the header line
        labels.pop(0)
        ids.pop(0)

        image_files = [os.path.join(dirname, im_id) for im_id in ids]

        self._total_raw_samples = len(image_files)
        self._total_classes = len(set(labels))

        # transform into numerical one-hot labels
        labels = loaders.string_labels_to_sequential(labels)
        labels = tf.one_hot(labels, self._total_classes)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Total classes is %d' % self._total_classes)
        self.__log('Parsing dataset...')

        self._raw_image_files = image_files
        self._raw_labels = labels

    def load_cifar10_dataset_from_directory(self, dirname):
        """
        Loads the images and labels from a directory containing the CIFAR-10 image classification dataset as
        downloaded by nvidia DIGITS.
        """

        train_dir = os.path.join(dirname, 'train')
        test_dir = os.path.join(dirname, 'test')
        self._total_classes = 10
        self._queue_capacity = 60000

        train_labels, train_images = loaders.read_csv_labels_and_ids(os.path.join(train_dir, 'train.txt'), 1, 0,
                                                                     character=' ')

        def one_hot(labels, num_classes):
            return [[1 if i == label else 0 for i in range(num_classes)] for label in labels]

        # transform into numerical one-hot labels
        train_labels = [int(label) for label in train_labels]
        train_labels = one_hot(train_labels, self._total_classes)

        test_labels, test_images = loaders.read_csv_labels_and_ids(os.path.join(test_dir, 'test.txt'), 1, 0,
                                                                   character=' ')

        # transform into numerical one-hot labels
        test_labels = [int(label) for label in test_labels]
        test_labels = one_hot(test_labels, self._total_classes)

        self._total_raw_samples = len(train_images) + len(test_images)
        self._test_split = len(test_images) / self._total_raw_samples

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Total classes is %d' % self._total_classes)

        self._raw_test_image_files = test_images
        self._raw_train_image_files = train_images
        self._raw_test_labels = test_labels
        self._raw_train_labels = train_labels
        if not self._testing:
            self._raw_train_image_files.extend(self._raw_test_image_files)
            self._raw_test_image_files = []
            self._raw_train_labels.extend(self._raw_test_labels)
            self._raw_test_labels = []
            self._test_split = 0
        if self._validation:
            num_val_samples = int(self._total_raw_samples * self._validation_split)
            self._raw_val_image_files = self._raw_train_image_files[:num_val_samples]
            self._raw_train_image_files = self._raw_train_image_files[num_val_samples:]
            self._raw_val_labels = self._raw_train_labels[:num_val_samples]
            self._raw_train_labels = self._raw_train_labels[num_val_samples:]

    def load_dataset_from_directory_with_auto_labels(self, dirname):
        """Loads the png images in the given directory, using subdirectories to separate classes."""

        # Load all file names and labels into arrays
        subdirs = list(filter(lambda item: os.path.isdir(item) & (item != '.DS_Store'),
                              [os.path.join(dirname, f) for f in os.listdir(dirname)]))

        num_classes = len(subdirs)

        image_files = []
        labels = np.array([])

        for sd in subdirs:
            image_paths = [os.path.join(sd, name) for name in os.listdir(sd) if
                           os.path.isfile(os.path.join(sd, name)) & name.endswith('.png')]
            image_files = image_files + image_paths

            # for one-hot labels
            current_labels = np.zeros((num_classes, len(image_paths)))
            current_labels[self._total_classes, :] = 1
            labels = np.hstack([labels, current_labels]) if labels.size else current_labels
            self._total_classes += 1

        labels = tf.transpose(labels)

        self._total_raw_samples = len(image_files)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Total classes is %d' % self._total_classes)
        self.__log('Parsing dataset...')

        self._raw_image_files = image_files
        self._raw_labels = labels

    def load_lemnatec_images_from_directory(self, dirname):
        """
        Loads the RGB (VIS) images from a Lemnatec plant scanner image dataset. Unless you only want to do
        preprocessing, regression or classification labels MUST be loaded first.
        """

        # Load all snapshot subdirectories
        subdirs = list(filter(lambda item: os.path.isdir(item) & (item != '.DS_Store'),
                              [os.path.join(dirname, f) for f in os.listdir(dirname)]))

        image_files = []

        # Load the VIS images in each subdirectory
        for sd in subdirs:
            image_paths = [os.path.join(sd, name) for name in os.listdir(sd) if
                           os.path.isfile(os.path.join(sd, name)) & name.startswith('VIS_SV_')]

            image_files = image_files + image_paths

        # Put the image files in the order of the IDs (if there are any labels loaded)
        sorted_paths = []

        if self._all_labels is not None:
            for image_id in self._all_ids:
                path = list(filter(lambda item: item.endswith(image_id), [p for p in image_files]))
                assert len(path) == 1, 'Found no image or multiple images for %r' % image_id
                sorted_paths.append(path[0])
        else:
            sorted_paths = image_files

        self._total_raw_samples = len(sorted_paths)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        images = sorted_paths

        # prepare images for training (if there are any labels loaded)

        if self._all_labels is not None:
            labels = self._all_labels

            self._raw_image_files = images
            self._raw_labels = labels

    def load_yolo_dataset_from_directory(self, data_dir, label_file=None, image_dir=None):
        """
        Loads in labels and images for object detection tasks, converting the labels to YOLO format and automatically
        patching the images if necessary.

        :param data_dir: String, The directory where the labels and images are stored in
        :param label_file: String, The filename for the JSON file with the labels. Optional if using automatic patching
        which has been done already
        :param image_dir: String, The directory with the images. Optional if using automatic patching which has been
        done already
        """
        if self._problem_type != definitions.ProblemType.OBJECT_DETECTION:
            raise RuntimeError("YOLO datasets can only be loaded object detection problems")

        load_patched_data = self._with_patching and 'tmp_train' in os.listdir(data_dir)

        # Construct the paths to the labels and images
        if load_patched_data:
            label_path = os.path.join(data_dir, 'tmp_train/json/train_patches.json')
            image_path = os.path.join(data_dir, 'tmp_train/image_patches', '')
        else:
            label_path = os.path.join(data_dir, label_file)
            image_path = os.path.join(data_dir, image_dir, '')

        # Load the labels and images
        if load_patched_data:
            # Hack to make the label reader convert the labels to YOLO format when re-reading image patches
            self._with_patching = False
        self.load_json_labels_from_file(label_path)
        images_list = [image_path + filename for filename in sorted(os.listdir(image_path))
                       if filename.endswith('.png')]
        self.load_images_from_list(images_list)
        if load_patched_data:
            # Remove the hack
            self._with_patching = True

        # Perform automatic image patching if necessary
        if self._with_patching and 'tmp_train' not in os.listdir(data_dir):
            self._raw_image_files, self._all_labels = \
                self.object_detection_patching_and_augmentation(patch_dir=data_dir)
            self.__convert_labels_to_yolo_format()
            self._raw_labels = self._all_labels
            self._total_raw_samples = len(self._raw_image_files)
            self.__log('Total raw patch examples is %d' % self._total_raw_samples)

    def load_images_from_list(self, image_files):
        """
        Loads images from a list of file names (strings). Regression or classification labels MUST be loaded first.
        """

        self._total_raw_samples = len(image_files)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        images = image_files

        self._raw_image_files = images
        if self._all_labels is not None:
            self._raw_labels = self._all_labels
        else:
            self._images_only = True

        # visual image check, printing image and bounding boxes #
        # img = cv2.imread(self._raw_image_files[4], 1)
        # boxes = self._raw_labels[4]
        # height, width, depth = img.shape
        # scale_x = self._image_width / width
        # scale_y = self._image_height / height
        # crazy_size = cv2.resize(img, (0, 0), fx=scale_x, fy=scale_y)
        # j = 1 + self._NUM_CLASSES + 4
        # print(boxes)
        # for i in range(len(boxes)//j):
        #     # p1 = (int((float(boxes[i*54 + 1]) - (float(boxes[i*54+3]/2)))*3108/7), int((float(boxes[i*54 + 2]) - (float(boxes[i*54+4]/2)))*2324/7))
        #     # p2 = (int((float(boxes[i*54 + 1]) + (float(boxes[i*54+3]/2)))*3108/7), int((float(boxes[i*54 + 2]) + (float(boxes[i*54+4]/2)))*2324/7))
        #     if boxes[i*j] == 1:
        #         print('HEREE')
        #         # grid_arr = np.array(boxes[i*j+5:i*j+j])
        #         # grid_pos = np.dot(grid_arr, np.arange(self._grid_h*self._grid_w))
        #         grid_pos = i
        #         x = int((boxes[i * j + 1 + self._NUM_CLASSES] + grid_pos % self._grid_w) * self._image_width / self._grid_w)
        #         y = int((boxes[i * j + 1 + self._NUM_CLASSES + 1] + grid_pos // self._grid_w) * self._image_height / self._grid_h)
        #         w = boxes[i * j + 1 + self._NUM_CLASSES + 2] * self._image_width / self._grid_w
        #         h = boxes[i * j + 1 + self._NUM_CLASSES + 3] * self._image_height / self._grid_h
        #
        #         p1 = (int(x - w/2),
        #               int(y - h/2))
        #         p2 = (int(x + w/2),
        #               int(y + h/2))
        #         print(p1, p2)
        #         cv2.rectangle(crazy_size, p1, p2, (255, 0, 0), 2)
        # cv2.namedWindow('image', cv2.WINDOW_NORMAL)
        # cv2.imshow('image', crazy_size)
        # cv2.waitKey(0)

    def object_detection_patching_and_augmentation(self, patch_dir=None):
        # make the below a function
        # labels, images = function()
        img_dict = {}
        img_num = 0
        img_name_idx = 1

        if patch_dir:
            patch_dir = os.path.join(patch_dir, 'tmp_train', '')
        else:
            patch_dir = os.path.join(os.path.curdir, 'tmp_train', '')
        if not os.path.exists(patch_dir):
            os.makedirs(patch_dir)
        else:
            raise RuntimeError("Patched images already exist in " + patch_dir + ". Either delete them and run again or "
                               "use them directly (i.e. without patching).")

        img_dir_out = patch_dir + 'image_patches/'
        if not os.path.exists(img_dir_out):
            os.makedirs(img_dir_out)
        json_dir_out = patch_dir + 'json/'
        if not os.path.exists(json_dir_out):
            os.makedirs(json_dir_out)
        new_raw_image_files = []
        new_raw_labels = []

        # first add images such that each grid cell has a plant in it
        # should add num_images*grid many images (e.g. 27(images)*49(7x7grid))
        self.__log('Beginning creation of training patches. Images and json are being saved in ' + patch_dir)
        for img_name, img_boxes in zip(self._raw_image_files, self._all_labels):
            img_num += 1
            img = np.array(Image.open(img_name))

            # take patches that have a plant in each grid cell to ensure come training time that each grid cell learns
            # to recognize an object
            for i in range(self._grid_h):
                for j in range(self._grid_w):
                    # choose plant randomly (and far enough from edges)
                    found_one = False
                    failed = False
                    find_count = 0
                    random_indices = list(range(len(img_boxes)))
                    while found_one is False:
                        rand_idx = np.random.randint(0, len(random_indices))
                        rand_plant_idx = random_indices[rand_idx]
                        box_w = img_boxes[rand_plant_idx][1] - img_boxes[rand_plant_idx][0]
                        box_h = img_boxes[rand_plant_idx][3] - img_boxes[rand_plant_idx][2]
                        box_x = img_boxes[rand_plant_idx][0] + box_w / 2
                        box_y = img_boxes[rand_plant_idx][2] + box_h / 2
                        if box_x > (self._patch_width + 5) and box_x < (img.shape[1] - (self._patch_width + 5)) \
                                and box_y > (self._patch_height + 5) and box_y < (
                                img.shape[0] - (self._patch_height + 5)):
                            found_one = True
                        else:
                            del random_indices[rand_idx]
                        find_count += 1
                        if find_count == len(img_boxes):
                            failed = True
                            break
                    if failed:
                        break

                    # adjust center based on target grid location
                    center_x = self._grid_w // 2
                    center_y = self._grid_h // 2
                    delta_x = j - center_x
                    delta_y = i - center_y
                    # note we need to invert the direction of delta_x so as to move the center to where we want it to be
                    # hence subtraction
                    new_x = int(box_x - (delta_x * (self._patch_width / self._grid_w)))
                    new_y = int(box_y - (delta_y * (self._patch_height / self._grid_h)))
                    # add a little bit of noise so it isn't always perfectly centered in the grid
                    # new_x = int(new_x + np.random.randint(-center_x, center_x))
                    # new_y = int(new_y + np.random.randint(-center_y, center_y))

                    top_row = new_y - (self._patch_height // 2)
                    bot_row = top_row + self._patch_height
                    left_col = new_x - (self._patch_width // 2)
                    right_col = left_col + self._patch_width

                    img_patch = img[top_row:bot_row, left_col:right_col]

                    # search for, adjust, and add bbox coords for the json
                    new_boxes = []
                    new_raw_boxes = []
                    for box in img_boxes:
                        # check if box is inside current patch, if so convert the coords and add it to the json
                        box_w = box[1] - box[0]
                        box_h = box[3] - box[2]
                        box_x = box[0] + box_w / 2
                        box_y = box[2] + box_h / 2
                        if (box_x >= left_col) and (box_x <= right_col) and (box_y >= top_row) and (
                                box_y <= bot_row):
                            # if (box_x >= (left_col + box_w/2)) and (box_x <= (right_col - box_w/2)) and (box_y >= (top_row + box_h/2)) and (box_y <= (bot_row - box_h/2)):
                            delta_x = box_x - new_x
                            delta_y = box_y - new_y
                            new_x_center = self._patch_width // 2 + delta_x
                            new_y_center = self._patch_height // 2 + delta_y
                            new_x_min = new_x_center - box_w / 2
                            new_x_max = new_x_min + box_w
                            new_y_min = new_y_center - box_h / 2
                            new_y_max = new_y_min + box_h

                            new_boxes.append({"all_points_x": [new_x_min, new_x_max],
                                              "all_points_y": [new_y_min, new_y_max]})
                            new_raw_boxes.append([new_x_min, new_x_max, new_y_min, new_y_max])

                    # visual testing
                    # # Create figure and axes
                    # fig, ax = plt.subplots(1)
                    # # Display the image
                    # ax.imshow(img_patch)
                    # plt.show(block=True)
                    # fig, ax = plt.subplots(1)
                    # # Create a Rectangle patch
                    # for box in new_boxes:
                    #     w = box['all_points_x'][1] - box['all_points_x'][0]
                    #     h = box['all_points_y'][1] - box['all_points_y'][0]
                    #     x_min = box['all_points_x'][0]
                    #     x_max = box['all_points_x'][1]
                    #     y_min = box['all_points_y'][0]
                    #     y_max = box['all_points_y'][1]
                    #     rect = patches.Rectangle((x_min, y_min), w, h, linewidth=1, edgecolor='r',
                    #                              facecolor='none')
                    #     # Add the patch to the Axes
                    #     ax.add_patch(rect)
                    # # Display the image
                    # ax.imshow(img_patch)
                    # plt.show(block=True)

                    # save image to disk
                    # print(top_row, bot_row, left_col, right_col)
                    result = Image.fromarray(img_patch.astype(np.uint8))
                    new_img_name = img_dir_out + "{:0>6d}".format(img_name_idx) + '.png'
                    result.save(new_img_name)

                    new_raw_image_files.append(new_img_name)
                    new_raw_labels.append(new_raw_boxes)

                    img_dict["{:0>6d}".format(img_name_idx)] = {"height": self._patch_height,
                                                                "width": self._patch_width,
                                                                "file_name": "{:0>6d}".format(
                                                                    img_name_idx) + '.png',
                                                                "plants": new_boxes}
                    img_name_idx += 1
            self.__log(str(img_num) + '/' + str(len(self._all_labels)))
        self.__log('Completed baseline train patches set. Total images: ' + str(img_name_idx))

        # augmentation images: rotations, brightness, flips
        self.__log('Beginning creating of augmentation patches')
        for i in range(self._grid_h * self._grid_w):
            for img_name, img_boxes in zip(self._raw_image_files, self._all_labels):
                img = np.array(Image.open(img_name))
                # randomly grab a patch, make sure it has at least one plant in it
                max_width = img.shape[1] - (self._patch_width // 2)
                min_width = (self._patch_width // 2)
                max_height = img.shape[0] - (self._patch_height // 2)
                min_height = (self._patch_height // 2)
                found_one = False
                while found_one is False:
                    rand_x = np.random.randint(min_width, max_width + 1)
                    rand_y = np.random.randint(min_height, max_height + 1)
                    # determine patch location and slice into mask and img to create patch
                    top_row = rand_y - (self._patch_height // 2)
                    bot_row = top_row + self._patch_height
                    left_col = rand_x - (self._patch_width // 2)
                    right_col = left_col + self._patch_width
                    img_patch = img[top_row:bot_row, left_col:right_col]
                    # objects and corresponding bboxes
                    new_boxes = []
                    for box in img_boxes:
                        cent_x = box[0] + ((box[1] - box[0]) / 2)
                        cent_y = box[2] + ((box[3] - box[2]) / 2)
                        # check if box is inside current patch, if so convert the coords and add it to the json
                        if (cent_x >= left_col) and (cent_x <= right_col) and (cent_y >= top_row) and (
                                cent_y <= bot_row):
                            # if (cent_x >= (left_col + (box[1] - box[0]) / 2)) and (cent_x <= (right_col - (box[1] - box[0]) / 2)) and (
                            #             cent_y >= (top_row + (box[3] - box[2]) / 2)) and (cent_y <= (bot_row - (box[3] - box[2]) / 2)):
                            box_w = box[1] - box[0]
                            box_h = box[3] - box[2]
                            box_x = box[0] + box_w / 2
                            box_y = box[2] + box_h / 2
                            delta_x = box_x - rand_x
                            delta_y = box_y - rand_y
                            new_x_center = self._patch_width // 2 + delta_x
                            new_y_center = self._patch_height // 2 + delta_y
                            new_x_min = new_x_center - box_w / 2
                            new_x_max = new_x_min + box_w
                            new_y_min = new_y_center - box_h / 2
                            new_y_max = new_y_min + box_h
                            new_boxes.append([new_x_min, new_x_max, new_y_min, new_y_max])
                    if len(new_boxes) >= 1:
                        found_one = True

                # augmentation is a random choice of 3 options
                # 1 == rotation, 2 == brightness, 3 == flip
                aug = np.random.randint(1, 4)
                if aug == 1:
                    # rotation
                    k = np.random.randint(1, 4)
                    rot_img_patch = np.rot90(img_patch, k)
                    theta = np.radians(90 * k)
                    x0 = self._patch_width // 2
                    y0 = self._patch_height // 2
                    rot_boxes = []
                    raw_rot_boxes = []
                    for box in new_boxes:
                        # since only rotating by 90 degrees we could probably hard code in 1's, -1's, and 0's in the cases
                        # instead of using sin and cos
                        rot_x_min = x0 + (box[0] - x0) * np.cos(theta) + (box[2] - y0) * np.sin(theta)
                        rot_y_min = y0 - (box[0] - x0) * np.sin(theta) + (box[2] - y0) * np.cos(theta)
                        w = box[1] - box[0]
                        h = box[3] - box[2]
                        if k == 1:
                            # w and h flip, x_min y_min become x_min y_max
                            w, h = h, w
                            rot_y_min -= h
                        elif k == 2:
                            # w and h stay same, x_min y_min become x_max y_max
                            rot_x_min -= w
                            rot_y_min -= h
                        else:  # k == 3
                            # w and h flip, x_min y_min become x_max y_min
                            w, h = h, w
                            rot_x_min -= w
                        rot_x_max = rot_x_min + w
                        rot_y_max = rot_y_min + h

                        rot_boxes.append({"all_points_x": [rot_x_min, rot_x_max],
                                          "all_points_y": [rot_y_min, rot_y_max]})
                        raw_rot_boxes.append([rot_x_min, rot_x_max, rot_y_min, rot_y_max])
                    # save image to disk
                    result = Image.fromarray(rot_img_patch.astype(np.uint8))
                    new_img_name = img_dir_out + "{:0>6d}".format(img_name_idx) + '.png'
                    result.save(new_img_name)

                    new_raw_image_files.append(new_img_name)
                    new_raw_labels.append(raw_rot_boxes)

                    img_dict["{:0>6d}".format(img_name_idx)] = {"height": self._patch_height,
                                                                "width": self._patch_width,
                                                                "file_name": "{:0>6d}".format(img_name_idx) + '.png',
                                                                "plants": rot_boxes}
                    img_name_idx += 1
                elif aug == 2:
                    # brightness
                    value = np.random.randint(40, 76)  # just a 'nice amount' of brightness change, could be adjusted
                    k = np.random.random()
                    if k < 0.5:  # brighter
                        bright_img_patch = np.where((255 - img_patch) < value, 255, img_patch + value)
                    else:  # dimmer
                        bright_img_patch = np.where(img_patch < value, 0, img_patch - value)

                    bright_boxes = []
                    raw_bright_boxes = []
                    for box in new_boxes:
                        bright_boxes.append({"all_points_x": [box[0], box[1]],
                                             "all_points_y": [box[2], box[3]]})
                        raw_bright_boxes.append([box[0], box[1], box[2], box[3]])

                    # save image to disk
                    result = Image.fromarray(bright_img_patch.astype(np.uint8))
                    new_img_name = img_dir_out + "{:0>6d}".format(img_name_idx) + '.png'
                    result.save(new_img_name)

                    new_raw_image_files.append(new_img_name)
                    new_raw_labels.append(raw_bright_boxes)

                    img_dict["{:0>6d}".format(img_name_idx)] = {"height": self._patch_height,
                                                                "width": self._patch_width,
                                                                "file_name": "{:0>6d}".format(img_name_idx) + '.png',
                                                                "plants": bright_boxes}
                    img_name_idx += 1

                else:  # aug == 3
                    # flip
                    k = np.random.random()
                    if k < 0.5:
                        flip_img_patch = np.fliplr(img_patch)
                        flip_boxes = []
                        raw_flip_boxes = []
                        for box in new_boxes:
                            w = box[1] - box[0]
                            h = box[3] - box[2]
                            x_min = self._patch_width - (box[1])
                            x_max = x_min + w
                            y_min = box[2]
                            y_max = box[3]

                            flip_boxes.append({"all_points_x": [x_min, x_max],
                                               "all_points_y": [y_min, y_max]})
                            raw_flip_boxes.append([x_min, x_max, y_min, y_max])

                        result = Image.fromarray(flip_img_patch.astype(np.uint8))
                        new_img_name = img_dir_out + "{:0>6d}".format(img_name_idx) + '.png'
                        result.save(new_img_name)

                        new_raw_image_files.append(new_img_name)
                        new_raw_labels.append(raw_flip_boxes)

                        img_dict["{:0>6d}".format(img_name_idx)] = {"height": self._patch_height,
                                                                    "width": self._patch_width,
                                                                    "file_name": "{:0>6d}".format(
                                                                        img_name_idx) + '.png',
                                                                    "plants": flip_boxes}
                        img_name_idx += 1
                    else:
                        flip_img_patch = np.flipud(img_patch)
                        flip_boxes = []
                        raw_flip_boxes = []
                        for box in new_boxes:
                            w = box[1] - box[0]
                            h = box[3] - box[2]
                            x_min = box[0]
                            x_max = box[1]
                            y_min = self._patch_height - (box[3])
                            y_max = y_min + h

                            flip_boxes.append({"all_points_x": [x_min, x_max],
                                               "all_points_y": [y_min, y_max]})
                            raw_flip_boxes.append([x_min, x_max, y_min, y_max])

                        result = Image.fromarray(flip_img_patch.astype(np.uint8))
                        new_img_name = img_dir_out + "{:0>6d}".format(img_name_idx) + '.png'
                        result.save(new_img_name)

                        new_raw_image_files.append(new_img_name)
                        new_raw_labels.append(raw_flip_boxes)

                        img_dict["{:0>6d}".format(img_name_idx)] = {"height": self._patch_height,
                                                                    "width": self._patch_width,
                                                                    "file_name": "{:0>6d}".format(
                                                                        img_name_idx) + '.png',
                                                                    "plants": flip_boxes}
                        img_name_idx += 1
            self.__log(str(i + 1) + '/' + str(self._grid_w * self._grid_h))
        self.__log('Completed augmentation set. Total images: ' + str(img_name_idx))

        # rest are just random patches
        num_patches = img_name_idx // len(self._raw_image_files)
        self.__log('Generating random patches')
        img_num = 0
        random_imgs = 0
        for img_name, img_boxes in zip(self._raw_image_files, self._all_labels):
            img_num += 1
            img = np.array(Image.open(img_name))
            # we will randomly generate centers of the images we are extracting
            #  with size: patch_size x patch_size
            max_width = img.shape[1] - (self._patch_width // 2)
            min_width = (self._patch_width // 2)
            max_height = img.shape[0] - (self._patch_height // 2)
            min_height = (self._patch_height // 2)
            rand_x = np.random.randint(min_width, max_width + 1, num_patches)
            rand_y = np.random.randint(min_height, max_height + 1, num_patches)

            for idx, center in enumerate(zip(rand_x, rand_y)):
                # determine patch location and slice into mask and img to create patch
                top_row = center[1] - (self._patch_height // 2)
                bot_row = top_row + self._patch_height
                left_col = center[0] - (self._patch_width // 2)
                right_col = left_col + self._patch_width
                img_patch = img[top_row:bot_row, left_col:right_col]

                # objects and corresponding bboxes
                new_boxes = []
                raw_new_boxes = []
                for box in img_boxes:
                    cent_x = box[0] + ((box[1] - box[0]) / 2)
                    cent_y = box[2] + ((box[3] - box[2]) / 2)
                    # check if box is inside current patch, if so convert the coords and add it to the json
                    if (cent_x >= left_col) and (cent_x <= right_col) and (cent_y >= top_row) and (cent_y <= bot_row):
                        # if (cent_x >= (left_col + (box[1] - box[0]) / 2)) and (cent_x <= (right_col - (box[1] - box[0]) / 2)) and (cent_y >= (top_row + (box[3] - box[2]) / 2)) and (cent_y <= (bot_row - (box[3] - box[2]) / 2)):
                        box_w = box[1] - box[0]
                        box_h = box[3] - box[2]
                        box_x = box[0] + box_w / 2
                        box_y = box[2] + box_h / 2
                        delta_x = box_x - center[0]
                        delta_y = box_y - center[1]
                        new_x_center = self._patch_width // 2 + delta_x
                        new_y_center = self._patch_height // 2 + delta_y
                        new_x_min = new_x_center - box_w / 2
                        new_x_max = new_x_min + box_w
                        new_y_min = new_y_center - box_h / 2
                        new_y_max = new_y_min + box_h

                        new_boxes.append({"all_points_x": [new_x_min, new_x_max],
                                          "all_points_y": [new_y_min, new_y_max]})
                        raw_new_boxes.append([new_x_min, new_x_max, new_y_min, new_y_max])

                # save image to disk
                result = Image.fromarray(img_patch.astype(np.uint8))
                new_img_name = img_dir_out + "{:0>6d}".format(img_name_idx) + '.png'
                result.save(new_img_name)

                new_raw_image_files.append(new_img_name)
                new_raw_labels.append(raw_new_boxes)

                img_dict["{:0>6d}".format(img_name_idx)] = {"height": self._patch_height,
                                                            "width": self._patch_width,
                                                            "file_name": "{:0>6d}".format(img_name_idx) + '.png',
                                                            "plants": new_boxes}
                img_name_idx += 1

            # verbose
            random_imgs += 1
            self.__log(str(random_imgs) + '/' + str(len(self._raw_image_files)))

        # save into json
        # with open('/home/nico/yolo_data/yolo_test_imgs/blanche/test_patches_out.json', 'w') as outfile:
        #     json.dump(img_dict, outfile)
        with open(json_dir_out + 'train_patches.json', 'w') as outfile:
            json.dump(img_dict, outfile)

        return new_raw_image_files, new_raw_labels

    def load_multiple_labels_from_csv(self, filepath, id_column=0):
        """
        Load multiple labels from a CSV file, for instance values for regression.
        Parameter id_column is the column number specifying the image file name.
        """

        self._all_labels, self._all_ids = loaders.read_csv_multi_labels_and_ids(filepath, id_column)

    def load_images_with_ids_from_directory(self, im_dir):
        """Loads images from a directory, relating them to labels by the IDs which were loaded from a CSV file"""

        # Load all images in directory
        image_files = [os.path.join(im_dir, name) for name in os.listdir(im_dir) if
                       os.path.isfile(os.path.join(im_dir, name)) & name.endswith('.png')]

        # Put the image files in the order of the IDs (if there are any labels loaded)
        sorted_paths = []

        if self._all_labels is not None:
            for image_id in self._all_ids:
                path = list(filter(lambda item: item.endswith('/' + image_id), [p for p in image_files]))
                assert len(path) == 1, 'Found no image or multiple images for %r' % image_id
                sorted_paths.append(path[0])
        else:
            sorted_paths = image_files

        self._total_raw_samples = len(sorted_paths)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        processed_images = sorted_paths

        # prepare images for training (if there are any labels loaded)
        if self._all_labels is not None:
            self._raw_image_files = processed_images
            self._raw_labels = self._all_labels

    def load_training_augmentation_dataset_from_directory_with_csv_labels(self, dirname, labels_file, column_number=1,
                                                                          id_column_number=0):
        """
        Loads the png images from a directory as training augmentation images, using the labels provided in a CSV file.

        :param dirname: the path of the directory containing the images
        :param labels_file: the path of the .csv file containing the labels
        :param column_number: the column number (zero-indexed) of the column in the csv file representing the label
        :param id_column_number: the column number (zero-indexed) representing the file ID
        """

        image_files = [os.path.join(dirname, name) for name in os.listdir(dirname) if
                       os.path.isfile(os.path.join(dirname, name)) & name.endswith('.png')]

        labels, ids = loaders.read_csv_labels_and_ids(labels_file, column_number, id_column_number)

        sorted_paths = []

        for image_id in ids:
            path = filter(lambda item: item.endswith('/' + image_id), [p for p in image_files])
            assert len(path) == 1, 'Found no image or multiple images for %r' % image_id
            sorted_paths.append(path[0])

        self._training_augmentation_images = sorted_paths
        self._training_augmentation_labels = labels

    def load_pascal_voc_labels_from_directory(self, data_dir):
        """Loads single per-image bounding boxes from XML files in Pascal VOC format."""

        self._all_ids = []
        self._all_labels = []

        file_paths = [os.path.join(data_dir, name) for name in os.listdir(data_dir) if
                      os.path.isfile(os.path.join(data_dir, name)) & name.endswith('.xml')]

        for voc_file in file_paths:
            im_id, x_min, x_max, y_min, y_max = loaders.read_single_bounding_box_from_pascal_voc(voc_file)

            # re-scale coordinates if images are being resized
            if self._resize_images:
                x_min = int(x_min * (float(self._image_width) / self._image_width_original))
                x_max = int(x_max * (float(self._image_width) / self._image_width_original))
                y_min = int(y_min * (float(self._image_height) / self._image_height_original))
                y_max = int(y_max * (float(self._image_height) / self._image_height_original))

            self._all_ids.append(im_id)
            self._all_labels.append([x_min, x_max, y_min, y_max])

        # need to add object-ness flag and one-hot encodings for class
        # it will be 1 or 0 for object-ness, one-hot for the class, then 4 bbox coords (x,y,w,h)
        # e.g. [1,0,0,...,1,...,0,223,364,58,62]
        if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
            # for scaling bbox coords
            # scaling image down to the grid size
            scale_ratio_w = self._grid_w / self._image_width
            scale_ratio_h = self._grid_h / self._image_height

            labels_with_one_hot = []
            for curr_img_coords in self._all_labels:
                curr_img_grid_locs = []  # for duplicates; current hacky fix
                curr_img_labels = np.zeros((self._grid_w * self._grid_h) * (1 + self._NUM_CLASSES + 4))

                # only one object per image so no need to loop here
                # add scaled bbox coords
                # x and y offsets from grid position
                w = curr_img_coords[1] - curr_img_coords[0]
                h = curr_img_coords[3] - curr_img_coords[2]
                x_center = (w / 2) + curr_img_coords[0]
                y_center = (h / 2) + curr_img_coords[2]
                x_grid = x_center * scale_ratio_w
                y_grid = y_center * scale_ratio_h
                x_grid_offset, x_grid_loc = np.modf(x_grid)
                y_grid_offset, y_grid_loc = np.modf(y_grid)

                # for duplicate object in grid checking
                if (x_grid_loc, y_grid_loc) in curr_img_grid_locs:
                    continue
                else:
                    curr_img_grid_locs.append((x_grid_loc, y_grid_loc))

                # w and h values on grid scale
                w_grid = w * scale_ratio_w
                h_grid = h * scale_ratio_h

                # compute grid-cell location
                # grid is defined as left-right, down, left-right, down... so in a 3x3 grid the middle left cell
                # would be 4 (or 3 when 0-indexing)
                grid_loc = (y_grid_loc * self._grid_w) + x_grid_loc

                # 1 for obj then 1 since only once class <- needs to be made more general for multiple classes
                # should be [1,0,...,1,...,0,x,y,w,h] where 0,...,1,...,0 represents the one-hot encoding of classes
                # maybe define a new list inside the loop, append a 1, then extend a one-hot list, then append
                # x,y,w,h then use the in this next line below
                # cur_box = []... vec_size = len(currbox)....
                vec_size = (1 + self._NUM_CLASSES + 4)
                curr_img_labels[int(grid_loc)*vec_size:(int(grid_loc)+1)*vec_size] = \
                    [1, 1, x_grid_offset, y_grid_offset, w_grid, h_grid]
                # using extend because I had trouble with converting a list of lists to a tensor using our string
                # queues, so making it one list of all the numbers and then reshaping later when we pull y off the
                # train shuffle batch has been the current hacky fix
                labels_with_one_hot.append(curr_img_labels)

            self._all_labels = labels_with_one_hot

    def load_json_labels_from_file(self, filename):
        """Loads bounding boxes for multiple images from a single json file."""
        # these are for jsons in the structure that I got from Blanche's data #

        self._all_ids = []
        self._all_labels = []

        with open(filename, 'r') as f:
            box_data = json.load(f)
        for box in sorted(box_data.items()):
            self._all_ids.append(box[0])  # Name of corresponding image
            w_original = box[1]['width']
            h_original = box[1]['height']
            boxes = []
            for plant in box[1]['plants']:
                x_min = plant['all_points_x'][0]
                x_max = plant['all_points_x'][1]
                y_min = plant['all_points_y'][0]
                y_max = plant['all_points_y'][1]

                # re-scale coordinates if images are being resized
                if self._resize_images:
                    x_min = int(x_min * (float(self._image_width) / w_original))
                    x_max = int(x_max * (float(self._image_width) / w_original))
                    y_min = int(y_min * (float(self._image_height) / h_original))
                    y_max = int(y_max * (float(self._image_height) / h_original))

                boxes.append([x_min, x_max, y_min, y_max])
            self._all_labels.append(boxes)

        # need to add one-hot encodings for class and object existence label
        # it will 1 for object-ness, one-hot for the class, then 4 bbox coords (x,y,w,h)
        # e.g. [1,0,0,...,1,...,0,x,y,w,h]
        if self._problem_type == definitions.ProblemType.OBJECT_DETECTION:
            if not self._with_patching:
                self.__convert_labels_to_yolo_format()

    def __convert_labels_to_yolo_format(self):
        """Takes the labels that are in the json format and turns them into formatted arrays
        that the network and yolo loss function are expecting to work with"""

        # for scaling bbox coords
        # scaling image down to the grid size
        scale_ratio_w = self._grid_w / self._image_width
        scale_ratio_h = self._grid_h / self._image_height

        labels_with_one_hot = []
        for curr_img_coords in self._all_labels:
            curr_img_grid_locs = []  # for duplicates; current hacky fix
            curr_img_labels = np.zeros((self._grid_w * self._grid_h) * (1 + self._NUM_CLASSES + 4))
            num_boxes = len(curr_img_coords)
            for i in range(num_boxes):
                curr_box = []
                # add objectness
                curr_box.append(1)
                # add the class label
                # (there is only one class ATM -- needs to be fixed to be more general)
                curr_box.append(1)
                # add scaled bbox coords
                # x and y offsets from grid position
                w = curr_img_coords[i][1] - curr_img_coords[i][0]
                h = curr_img_coords[i][3] - curr_img_coords[i][2]
                x_center = (w / 2) + curr_img_coords[i][0]
                y_center = (h / 2) + curr_img_coords[i][2]
                x_grid = x_center * scale_ratio_w
                y_grid = y_center * scale_ratio_h
                x_grid_offset, x_grid_loc = np.modf(x_grid)
                y_grid_offset, y_grid_loc = np.modf(y_grid)

                # for duplicate object in grid checking
                if (x_grid_loc, y_grid_loc) in curr_img_grid_locs:
                    continue
                else:
                    curr_img_grid_locs.append((x_grid_loc, y_grid_loc))

                # w and h values on grid scale
                w_grid = w * scale_ratio_w
                h_grid = h * scale_ratio_h
                curr_box.append(x_grid_offset)
                curr_box.append(y_grid_offset)
                curr_box.append(w_grid)
                curr_box.append(h_grid)

                # compute grid-cell location
                # grid is defined as left-right, down, left-right, down... so in a 3x3 grid the middle left cell
                # would be 4 (or 3 when 0-indexing)
                grid_loc = ((y_grid_loc * self._grid_w) + x_grid_loc) % (self._grid_h * self._grid_w)
                # the % (self._grid_h*self._grid_w) is to handle the rare case we are right on the edge and
                # we want the last 0-indexed grid position (off by 1 error, get 49 for 7x7 grid when should have 48)

                # 1 for obj then 1 since only one class <- needs to be made more general for multiple classes #
                # should be [1,0,...,1,...,0,x,y,w,h] where 0,...,1,...,0 represents the one-hot encoding of classes
                # maybe define a new list inside the loop, append a 1, then extend a one-hot list, then append
                # x,y,w,h then use the in this next line below
                # cur_box = []... vec_size = len(currbox)....
                curr_box = []
                curr_box.append(1)  # obj
                curr_box.append(1)  # classes <- needs to be made more general for multiple classes
                curr_box.append(x_grid_offset)
                curr_box.append(y_grid_offset)
                curr_box.append(w_grid)
                curr_box.append(h_grid)
                vec_size = (1 + self._NUM_CLASSES + 4)
                curr_img_labels[int(grid_loc) * vec_size:(int(grid_loc) + 1) * vec_size] = curr_box
                # using extend because I had trouble with converting a list of lists to a tensor using our string
                # queues, so making it one list of all the numbers and then reshaping later when we pull y off the
                # train shuffle batch has been the current hacky fix
            labels_with_one_hot.append(curr_img_labels)

        self._all_labels = labels_with_one_hot

    def __parse_dataset(self, train_images, train_labels, train_mf,
                        test_images, test_labels, test_mf,
                        val_images, val_labels, val_mf,
                        image_type='png'):
        """Takes training and testing images and labels, creates input queues internally to this instance"""
        with self._graph.as_default():

            # Try to get the number of samples the normal way
            if isinstance(train_images, tf.Tensor):
                self._total_training_samples = train_images.get_shape().as_list()[0]
                if self._testing:
                    self._total_testing_samples = test_images.get_shape().as_list()[0]
                if self._validation:
                    self._total_validation_samples = val_images.get_shape().as_list()[0]
            elif isinstance(train_images[0], tf.Tensor):
                self._total_training_samples = train_images[0].get_shape().as_list()[0]
            else:
                self._total_training_samples = len(train_images)
                if self._testing:
                    self._total_testing_samples = len(test_images)
                if self._validation:
                    self._total_validation_samples = len(val_images)

            # Most often train/test/val_images will be a tensor with shape (?,), from tf.dynamic_partition, which
            # will have None for its size, so the above won't work and we manually calculate it here
            if self._total_training_samples is None:
                self._total_training_samples = int(self._total_raw_samples)
                if self._testing:
                    self._total_testing_samples = int(self._total_raw_samples * self._test_split)
                    self._total_training_samples = self._total_training_samples - self._total_testing_samples
                if self._validation:
                    self._total_validation_samples = int(self._total_raw_samples * self._validation_split)
                    self._total_training_samples = self._total_training_samples - self._total_validation_samples

            # Logging verbosity
            self.__log('Total training samples is {0}'.format(self._total_training_samples))
            self.__log('Total validation samples is {0}'.format(self._total_validation_samples))
            self.__log('Total testing samples is {0}'.format(self._total_testing_samples))

            # Create moderation features queues
            if train_mf is not None:
                train_moderation_queue = tf.train.slice_input_producer([train_mf], shuffle=False)
                self._train_moderation_features = tf.cast(train_moderation_queue[0], tf.float32)

            if test_mf is not None:
                test_moderation_queue = tf.train.slice_input_producer([test_mf], shuffle=False)
                self._test_moderation_features = tf.cast(test_moderation_queue[0], tf.float32)

            if val_mf is not None:
                val_moderation_queue = tf.train.slice_input_producer([val_mf], shuffle=False)
                self._val_moderation_features = tf.cast(val_moderation_queue[0], tf.float32)

            # Calculate number of batches to run
            batches_per_epoch = self._total_training_samples / float(self._batch_size)
            self._maximum_training_batches = int(self._maximum_training_batches * batches_per_epoch)

            if self._batch_size > self._total_training_samples:
                self.__log('Less than one batch in training set, exiting now')
                exit()
            self.__log('Batches per epoch: {:f}'.format(batches_per_epoch))
            self.__log('Running to {0} batches'.format(self._maximum_training_batches))

            # Create input queues
            train_input_queue = tf.train.slice_input_producer([train_images, train_labels], shuffle=False)
            if self._testing:
                test_input_queue = tf.train.slice_input_producer([test_images, test_labels], shuffle=False)
            if self._validation:
                val_input_queue = tf.train.slice_input_producer([val_images, val_labels], shuffle=False)

            # Apply pre-processing to the image labels (which are images for semantic segmentation)
            if self._problem_type is definitions.ProblemType.SEMANTIC_SEGMETNATION:
                self._train_labels = tf.image.decode_png(tf.read_file(train_input_queue[1]), channels=1)
                # normalize to 1.0
                self._train_labels = tf.image.convert_image_dtype(self._train_labels, dtype=tf.float32)
                # resize if we are using that
                if self._resize_images:
                    self._train_labels = tf.image.resize_images(self._train_labels,
                                                                [self._image_height, self._image_width])
                    # make into a binary mask
                    self._train_labels = tf.reduce_mean(self._train_labels, axis=2)

                # if using testing, do all the above for testing as well
                if self._testing:
                    self._test_labels = tf.image.decode_png(tf.read_file(test_input_queue[1]), channels=1)
                    self._test_labels = tf.image.convert_image_dtype(self._test_labels, dtype=tf.float32)
                    if self._resize_images:
                        self._test_labels = tf.image.resize_images(self._test_labels,
                                                                   [self._image_height, self._image_width])
                        self._test_labels = tf.reduce_mean(self._test_labels, axis=2)
                # if using validation, do all the above for validation as well
                if self._validation:
                    self._val_labels = tf.image.decode_png(tf.read_file(val_input_queue[1]),
                                                           channels=1)
                    self._val_labels = tf.image.convert_image_dtype(self._val_labels, dtype=tf.float32)
                    if self._resize_images:
                        self._val_labels = tf.image.resize_images(self._val_labels,
                                                                  [self._image_height, self._image_width])
                        self._val_labels = tf.reduce_mean(self._val_labels, axis=2)
            else:
                self._train_labels = train_input_queue[1]
                if self._testing:
                    self._test_labels = test_input_queue[1]
                if self._validation:
                    self._val_labels = val_input_queue[1]

            # Apply pre-processing for training and testing images
            if image_type is 'jpg':
                self._train_images = tf.image.decode_jpeg(tf.read_file(train_input_queue[0]),
                                                          channels=self._image_depth)
                if self._testing:
                    self._test_images = tf.image.decode_jpeg(tf.read_file(test_input_queue[0]),
                                                             channels=self._image_depth)
                if self._validation:
                    self._val_images = tf.image.decode_jpeg(tf.read_file(val_input_queue[0]),
                                                            channels=self._image_depth)
            else:
                self._train_images = tf.image.decode_png(tf.read_file(train_input_queue[0]),
                                                         channels=self._image_depth)
                if self._testing:
                    self._test_images = tf.image.decode_png(tf.read_file(test_input_queue[0]),
                                                            channels=self._image_depth)
                if self._validation:
                    self._val_images = tf.image.decode_png(tf.read_file(val_input_queue[0]),
                                                           channels=self._image_depth)

            # Convert images to float and normalize to 1.0
            self._train_images = tf.image.convert_image_dtype(self._train_images, dtype=tf.float32)
            if self._testing:
                self._test_images = tf.image.convert_image_dtype(self._test_images, dtype=tf.float32)
            if self._validation:
                self._val_images = tf.image.convert_image_dtype(self._val_images, dtype=tf.float32)

            if self._resize_images:
                self._train_images = tf.image.resize_images(self._train_images,
                                                            [self._image_height, self._image_width])
                if self._testing:
                    self._test_images = tf.image.resize_images(self._test_images,
                                                               [self._image_height, self._image_width])
                if self._validation:
                    self._val_images = tf.image.resize_images(self._val_images,
                                                              [self._image_height, self._image_width])

            # Apply the various augmentations to the images
            if self._augmentation_crop:
                # Apply random crops to images
                self._image_height = int(self._image_height * self._crop_amount)
                self._image_width = int(self._image_width * self._crop_amount)

                self._train_images = tf.random_crop(self._train_images, [self._image_height, self._image_width, 3])
                if self._testing:
                    self._test_images = tf.image.resize_image_with_crop_or_pad(self._test_images, self._image_height,
                                                                               self._image_width)
                if self._validation:
                    self._val_images = tf.image.resize_image_with_crop_or_pad(self._val_images, self._image_height,
                                                                              self._image_width)

            if self._crop_or_pad_images:
                # Apply padding or cropping to deal with images of different sizes
                self._train_images = tf.image.resize_image_with_crop_or_pad(self._train_images,
                                                                            self._image_height,
                                                                            self._image_width)
                if self._testing:
                    self._test_images = tf.image.resize_image_with_crop_or_pad(self._test_images,
                                                                               self._image_height,
                                                                               self._image_width)
                if self._validation:
                    self._val_images = tf.image.resize_image_with_crop_or_pad(self._val_images,
                                                                              self._image_height,
                                                                              self._image_width)

                # if doing semantic segmentation, then the corresponding mask would also need to be cropped/padded
                if self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                    self._train_labels = tf.image.resize_image_with_crop_or_pad(self._train_labels,
                                                                                self._image_height,
                                                                                self._image_width)
                    if self._testing:
                        self._test_labels = tf.image.resize_image_with_crop_or_pad(self._test_labels,
                                                                                   self._image_height,
                                                                                   self._image_width)
                    if self._validation:
                        self._val_labels = tf.image.resize_image_with_crop_or_pad(self._val_labels,
                                                                                  self._image_height,
                                                                                  self._image_width)

            if self._augmentation_flip_horizontal:
                # Apply random horizontal flips
                self._train_images = tf.image.random_flip_left_right(self._train_images)

            if self._augmentation_flip_vertical:
                # Apply random vertical flips
                self._train_images = tf.image.random_flip_up_down(self._train_images)

            if self._augmentation_contrast:
                # Apply random contrast and brightness adjustments
                self._train_images = tf.image.random_brightness(self._train_images, max_delta=63)
                self._train_images = tf.image.random_contrast(self._train_images, lower=0.2, upper=1.8)

            if self._augmentation_rotate:
                # Apply random rotations, then optionally crop out black borders and resize
                angle = tf.random_uniform([], maxval=2*math.pi)
                self._train_images = tf.contrib.image.rotate(self._train_images, angle, interpolation='BILINEAR')
                if self._rotate_crop_borders:
                    # Cropping is done using the smallest fraction possible for the image's aspect ratio to maintain a
                    # consistent scale across the images
                    small_crop_fraction = self.__smallest_crop_fraction()
                    self._train_images = tf.image.central_crop(self._train_images, small_crop_fraction)
                    self._train_images = tf.image.resize_images(self._train_images,
                                                                [self._image_height, self._image_width])

            # mean-center all inputs
            self._train_images = tf.image.per_image_standardization(self._train_images)
            if self._testing:
                self._test_images = tf.image.per_image_standardization(self._test_images)
            if self._validation:
                self._val_images = tf.image.per_image_standardization(self._val_images)

            # define the shape of the image tensors so it matches the shape of the images
            self._train_images.set_shape([self._image_height, self._image_width, self._image_depth])
            if self._testing:
                self._test_images.set_shape([self._image_height, self._image_width, self._image_depth])
            if self._validation:
                self._val_images.set_shape([self._image_height, self._image_width, self._image_depth])

    def __parse_images(self, images, image_type='png'):
        """Takes some images as input, creates producer of processed images internally to this instance"""
        with self._graph.as_default():
            input_queue = tf.train.string_input_producer(images, shuffle=False)

            reader = tf.WholeFileReader()
            key, file = reader.read(input_queue)

            # pre-processing for all images

            if image_type is 'jpg':
                input_images = tf.image.decode_jpeg(file, channels=self._image_depth)
            else:
                input_images = tf.image.decode_png(file, channels=self._image_depth)

            # convert images to float and normalize to 1.0
            input_images = tf.image.convert_image_dtype(input_images, dtype=tf.float32)

            if self._resize_images is True:
                input_images = tf.image.resize_images(input_images, [self._image_height, self._image_width])

            if self._augmentation_crop is True:
                self._image_height = int(self._image_height * self._crop_amount)
                self._image_width = int(self._image_width * self._crop_amount)
                input_images = tf.image.resize_image_with_crop_or_pad(input_images, self._image_height,
                                                                      self._image_width)

            if self._crop_or_pad_images is True:
                # pad or crop to deal with images of different sizes
                input_images = tf.image.resize_image_with_crop_or_pad(input_images, self._image_height,
                                                                      self._image_width)

            # mean-center all inputs
            input_images = tf.image.per_image_standardization(input_images)

            # define the shape of the image tensors so it matches the shape of the images
            input_images.set_shape([self._image_height, self._image_width, self._image_depth])

            self._all_images = input_images

    def __smallest_crop_fraction(self):
        """
        Determine the angle and crop fraction for rotated images that gives the maximum border-less crop area for a
        given angle but the smallest such area among all angles from 0-90 degrees. This is used during rotation
        augmentation to apply a consistent crop and maintain similar scale across all images. Using larger crop
        fractions based on the rotation angle would result in different scales.
        :return: The crop fraction that achieves the smallest area among border-less crops for rotated images
        """

        # Regardless of the aspect ratio, the smallest crop fraction always corresponds to the required crop for a 45
        # degree or pi/4 radian rotation
        angle = math.pi/4

        # Determine which sides of the original image are the shorter and longer sides
        width_is_longer = self._image_width >= self._image_height
        if width_is_longer:
            (short_length, long_length) = (self._image_height, self._image_width)
        else:
            (short_length, long_length) = (self._image_width, self._image_height)

        # Get the absolute sin and cos of the angle, since the quadrant doesn't affect us
        sin_a = abs(math.sin(angle))
        cos_a = abs(math.cos(angle))

        # There are 2 possible solutions for the width and height in general depending on the angle and aspect ratio,
        # but 45 degree rotations always fall into the solution below. This corresponds to a rectangle with one corner
        # at the midpoint of an edge and the other corner along the centre line of the rotated image, although this
        # cropped rectangle will ultimately be slid up so that it's centered inside the rotated image.
        x = 0.5 * short_length
        if width_is_longer:
            (crop_width, crop_height) = (x / sin_a, x / cos_a)
        else:
            (crop_width, crop_height) = (x / cos_a, x / sin_a)

        # Use the crop width and height to calculate the required crop ratio
        return (crop_width * crop_height) / (self._image_width * self._image_height)


class ClassificationModel(DPPModel):
    _problem_type = definitions.ProblemType.CLASSIFICATION
    _loss_fn = 'softmax cross entropy'
    _supported_loss_fns_cls = ['softmax cross entropy']

    def __init__(self, debug=False, load_from_saved=False, save_checkpoints=True, initialize=True, tensorboard_dir=None,
                 report_rate=100, save_dir=None):
        super().__init__(debug, load_from_saved, save_checkpoints, initialize, tensorboard_dir, report_rate, save_dir)

    def __assemble_graph(self):
        with self._graph.as_default():

            self.__log('Parsing dataset...')

            if self._raw_test_labels is not None:
                # currently think of moderation features as None so they are passed in hard-coded
                self.__parse_dataset(self._raw_train_image_files, self._raw_train_labels, None,
                                     self._raw_test_image_files, self._raw_test_labels, None,
                                     self._raw_val_image_files, self._raw_val_labels, None)
            elif self._images_only:
                self.__parse_images(self._raw_image_files)
            else:
                # split the data into train/val/test sets, if there is no validation set or no moderation features
                # being used they will be returned as 0 (val) or None (moderation features)
                train_images, train_labels, train_mf, \
                test_images, test_labels, test_mf, \
                val_images, val_labels, val_mf, = \
                    loaders.split_raw_data(self._raw_image_files, self._raw_labels, self._test_split,
                                           self._validation_split, self._all_moderation_features,
                                           self._training_augmentation_images, self._training_augmentation_labels,
                                           self._split_labels)
                # parse the images and set the appropriate environment variables
                self.__parse_dataset(train_images, train_labels, train_mf,
                                     test_images, test_labels, test_mf,
                                     val_images, val_labels, val_mf)

            self.__log('Creating layer parameters...')

            self.__add_layers_to_graph()

            self.__log('Assembling graph...')

            # Define batches
            if self._has_moderation:
                x, y, mod_w = tf.train.shuffle_batch(
                    [self._train_images, self._train_labels, self._train_moderation_features],
                    batch_size=self._batch_size,
                    num_threads=self._num_threads,
                    capacity=self._queue_capacity,
                    min_after_dequeue=self._batch_size)
            else:
                x, y = tf.train.shuffle_batch([self._train_images, self._train_labels],
                                              batch_size=self._batch_size,
                                              num_threads=self._num_threads,
                                              capacity=self._queue_capacity,
                                              min_after_dequeue=self._batch_size)

            # Reshape input to the expected image dimensions
            x = tf.reshape(x, shape=[-1, self._image_height, self._image_width, self._image_depth])

            # if using patching we extract a patch of image here
            if self._with_patching:
                # Take a slice
                patch_width = self._patch_width
                patch_height = self._patch_height
                offset_h = np.random.randint(patch_height // 2, self._image_height - (patch_height // 2),
                                             self._batch_size)
                offset_w = np.random.randint(patch_width // 2, self._image_width - (patch_width // 2),
                                             self._batch_size)
                offsets = [x for x in zip(offset_h, offset_w)]
                x = tf.image.extract_glimpse(x, [patch_height, patch_width], offsets,
                                             normalized=False, centered=False)

            # Run the network operations
            if self._has_moderation:
                xx = self.forward_pass(x, deterministic=False, moderation_features=mod_w)
            else:
                xx = self.forward_pass(x, deterministic=False)

            # Define regularization cost
            if self._reg_coeff is not None:
                l2_cost = tf.squeeze(tf.reduce_sum(
                    [layer.regularization_coefficient * tf.nn.l2_loss(layer.weights) for layer in self._layers
                     if isinstance(layer, layers.fullyConnectedLayer)]))
            else:
                l2_cost = 0.0

            # Define cost function based on which one was selected via set_loss_function
            if self._loss_fn == 'softmax cross entropy':
                sf_logits = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=xx, labels=tf.argmax(y, 1))
            self._graph_ops['cost'] = tf.add(tf.reduce_mean(tf.concat([sf_logits], axis=0)), l2_cost)

            # Identify which optimizer we are using
            if self._optimizer == 'adagrad':
                self._graph_ops['optimizer'] = tf.train.AdagradOptimizer(self._learning_rate)
                self.__log('Using Adagrad optimizer')
            elif self._optimizer == 'adadelta':
                self._graph_ops['optimizer'] = tf.train.AdadeltaOptimizer(self._learning_rate)
                self.__log('Using adadelta optimizer')
            elif self._optimizer == 'sgd':
                self._graph_ops['optimizer'] = tf.train.GradientDescentOptimizer(self._learning_rate)
                self.__log('Using SGD optimizer')
            elif self._optimizer == 'adam':
                self._graph_ops['optimizer'] = tf.train.AdamOptimizer(self._learning_rate)
                self.__log('Using Adam optimizer')
            elif self._optimizer == 'sgd_momentum':
                self._graph_ops['optimizer'] = tf.train.MomentumOptimizer(self._learning_rate, 0.9, use_nesterov=True)
                self.__log('Using SGD with momentum optimizer')
            else:
                warnings.warn('Unrecognized optimizer requested')
                exit()

            # Compute gradients, clip them, the apply the clipped gradients
            # This is broken up so that we can add gradients to tensorboard
            # need to make the 5.0 an adjustable hyperparameter
            gradients, variables = zip(*self._graph_ops['optimizer'].compute_gradients(self._graph_ops['cost']))
            gradients, global_grad_norm = tf.clip_by_global_norm(gradients, 5.0)
            self._graph_ops['optimizer'] = self._graph_ops['optimizer'].apply_gradients(zip(gradients, variables))

            # for classification problems we will compute the training accuracy, this is also used for tensorboard
            class_predictions = tf.argmax(tf.nn.softmax(xx), 1)
            correct_predictions = tf.equal(class_predictions, tf.argmax(y, 1))
            self._graph_ops['accuracy'] = tf.reduce_mean(tf.cast(correct_predictions, tf.float32))

            # Calculate test accuracy
            if self._has_moderation:
                if self._testing:
                    x_test, self._graph_ops['y_test'], mod_w_test = tf.train.batch(
                        [self._test_images, self._test_labels, self._test_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'], mod_w_val = tf.train.batch(
                        [self._val_images, self._val_labels, self._val_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
            else:
                if self._testing:
                    x_test, self._graph_ops['y_test'] = tf.train.batch([self._test_images, self._test_labels],
                                                                       batch_size=self._batch_size,
                                                                       num_threads=self._num_threads,
                                                                       capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'] = tf.train.batch([self._val_images, self._val_labels],
                                                                     batch_size=self._batch_size,
                                                                     num_threads=self._num_threads,
                                                                     capacity=self._queue_capacity)
            if self._testing:
                x_test = tf.reshape(x_test, shape=[-1, self._image_height, self._image_width, self._image_depth])
            if self._validation:
                x_val = tf.reshape(x_val, shape=[-1, self._image_height, self._image_width, self._image_depth])

            # if using patching we need to properly pull patches from the images (object detection patching is different
            # and is done when data is loaded)
            if self._with_patching:
                # Take a slice of image. Same size and location (offsets) as the slice from training.
                patch_width = self._patch_width
                patch_height = self._patch_height
                if self._testing:
                    x_test = tf.image.extract_glimpse(x_test, [patch_height, patch_width], offsets,
                                                      normalized=False, centered=False)
                if self._validation:
                    x_val = tf.image.extract_glimpse(x_val, [patch_height, patch_width], offsets,
                                                     normalized=False, centered=False)

            if self._has_moderation:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True,
                                                                            moderation_features=mod_w_test)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True,
                                                                           moderation_features=mod_w_val)
            else:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True)

            # compute the loss and accuracy
            if self._testing:
                test_class_predictions = tf.argmax(tf.nn.softmax(self._graph_ops['x_test_predicted']), 1)
                test_correct_predictions = tf.equal(test_class_predictions,
                                                    tf.argmax(self._graph_ops['y_test'], 1))
                self._graph_ops['test_losses'] = test_correct_predictions
                self._graph_ops['test_accuracy'] = tf.reduce_mean(tf.cast(test_correct_predictions, tf.float32))
            if self._validation:
                val_class_predictions = tf.argmax(tf.nn.softmax(self._graph_ops['x_val_predicted']), 1)
                val_correct_predictions = tf.equal(val_class_predictions, tf.argmax(self._graph_ops['y_val'], 1))
                self._graph_ops['val_losses'] = val_correct_predictions
                self._graph_ops['val_accuracy'] = tf.reduce_mean(tf.cast(val_correct_predictions, tf.float32))

            # Epoch summaries for Tensorboard
            if self._tb_dir is not None:
                self.__log('Creating Tensorboard summaries...')

                # Summaries for any problem type
                tf.summary.scalar('train/loss', self._graph_ops['cost'], collections=['custom_summaries'])
                tf.summary.scalar('train/learning_rate', self._learning_rate, collections=['custom_summaries'])
                tf.summary.scalar('train/l2_loss', l2_cost, collections=['custom_summaries'])
                filter_summary = self.__get_weights_as_image(self.__first_layer().weights)
                tf.summary.image('filters/first', filter_summary, collections=['custom_summaries'])

                # Summaries specific to classification problems
                tf.summary.scalar('train/accuracy', self._graph_ops['accuracy'], collections=['custom_summaries'])
                tf.summary.histogram('train/class_predictions', class_predictions, collections=['custom_summaries'])
                if self._validation:
                    tf.summary.scalar('validation/accuracy', self._graph_ops['val_accuracy'],
                                      collections=['custom_summaries'])
                    tf.summary.histogram('validation/class_predictions', val_class_predictions,
                                         collections=['custom_summaries'])

                # Summaries for each layer
                for layer in self._layers:
                    if hasattr(layer, 'name') and not isinstance(layer, layers.batchNormLayer):
                        tf.summary.histogram('weights/' + layer.name, layer.weights, collections=['custom_summaries'])
                        tf.summary.histogram('biases/' + layer.name, layer.biases, collections=['custom_summaries'])

                        # At one point the graph would hang on session.run(graph_ops['merged']) inside of begin_training
                        # and it was found that if you commented the below line then the code wouldn't hang. Never
                        # fully understood why, as it only happened if you tried running with train/test and no
                        # validation. But after adding more features and just randomly trying to uncomment the below
                        # line to see if it would work, it appears to now be working, but still don't know why...
                        tf.summary.histogram('activations/' + layer.name, layer.activations,
                                             collections=['custom_summaries'])

                # Summaries for gradients
                # we variables[index].name[:-2] because variables[index].name will have a ':0' at the end of
                # the name and tensorboard does not like this so we remove it with the [:-2]
                # We also currently seem to get None's for gradients when performing a hyperparameter search
                # and as such it is simply left out for hyper-param searches, needs to be fixed
                if not self._hyper_param_search:
                    for index, grad in enumerate(gradients):
                        tf.summary.histogram("gradients/" + variables[index].name[:-2], gradients[index],
                                             collections=['custom_summaries'])

                    tf.summary.histogram("gradient_global_norm/", global_grad_norm, collections=['custom_summaries'])

                self._graph_ops['merged'] = tf.summary.merge_all(key='custom_summaries')

    def begin_training(self, return_test_loss=False):
        """
        Initialize the network and either run training to the specified max epoch, or load trainable variables.
        The full test accuracy is calculated immediately afterward. Finally, the trainable parameters are saved and
        the session is shut down.
        Before calling this function, the images and labels should be loaded, as well as all relevant hyperparameters.
        """
        # if None in [self._train_images, self._test_images,
        #             self._train_labels, self._test_labels]:
        #     raise RuntimeError("Images and Labels need to be loaded before you can begin training. " +
        #                        "Try first using one of the methods starting with 'load_...' such as " +
        #                        "'DPPModel.load_dataset_from_directory_with_csv_labels()'")
        # if (len(self._layers) < 1):
        #     raise RuntimeError("There are no layers currently added to the model when trying to begin training. " +
        #                        "Add layers first by using functions such as 'DPPModel.add_input_layer()' or " +
        #                        "'DPPModel.add_convolutional_layer()'. See documentation for a complete list of
        #                        layers.")

        with self._graph.as_default():
            self.__assemble_graph()
            print('assembled the graph')

            # Either load the network parameters from a checkpoint file or start training
            if self._load_from_saved is not False:
                self.load_state()

                self.__initialize_queue_runners()

                self.compute_full_test_accuracy()

                self.shut_down()
            else:
                if self._tb_dir is not None:
                    train_writer = tf.summary.FileWriter(self._tb_dir, self._session.graph)

                self.__log('Initializing parameters...')
                init_op = tf.global_variables_initializer()
                self._session.run(init_op)

                self.__initialize_queue_runners()

                self.__log('Beginning training...')

                self.__set_learning_rate()

                # Needed for batch norm
                update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
                self._graph_ops['optimizer'] = tf.group([self._graph_ops['optimizer'], update_ops])

                # for i in range(self._maximum_training_batches):
                tqdm_range = tqdm(range(self._maximum_training_batches))
                for i in tqdm_range:
                    start_time = time.time()

                    self._global_epoch = i
                    self._session.run(self._graph_ops['optimizer'])
                    if self._global_epoch > 0 and self._global_epoch % self._report_rate == 0:
                        elapsed = time.time() - start_time

                        if self._tb_dir is not None:
                            summary = self._session.run(self._graph_ops['merged'])
                            train_writer.add_summary(summary, i)
                        if self._validation:
                            loss, epoch_accuracy, epoch_val_accuracy = self._session.run(
                                [self._graph_ops['cost'],
                                 self._graph_ops['accuracy'],
                                 self._graph_ops['val_accuracy']])

                            samples_per_sec = self._batch_size / elapsed

                            desc_str = "{}: Results for batch {} (epoch {:.1f}) " + \
                                       "- Loss: {:.5f}, Training Accuracy: {:.4f}, samples/sec: {:.2f}"
                            tqdm_range.set_description(
                                desc_str.format(datetime.datetime.now().strftime("%I:%M%p"),
                                                i,
                                                i / (self._total_training_samples / self._batch_size),
                                                loss,
                                                epoch_accuracy,
                                                samples_per_sec))

                        else:
                            loss, epoch_accuracy = self._session.run(
                                [self._graph_ops['cost'],
                                 self._graph_ops['accuracy']])

                            samples_per_sec = self._batch_size / elapsed

                            desc_str = "{}: Results for batch {} (epoch {:.1f}) " + \
                                       "- Loss: {:.5f}, Training Accuracy: {:.4f}, samples/sec: {:.2f}"
                            tqdm_range.set_description(
                                desc_str.format(datetime.datetime.now().strftime("%I:%M%p"),
                                                i,
                                                i / (self._total_training_samples / self._batch_size),
                                                loss,
                                                epoch_accuracy,
                                                samples_per_sec))

                        if self._save_checkpoints and self._global_epoch % (self._report_rate * 100) == 0:
                            self.save_state(self._save_dir)
                    else:
                        loss = self._session.run([self._graph_ops['cost']])

                    if loss == 0.0:
                        self.__log('Stopping due to zero loss')
                        break

                    if i == self._maximum_training_batches - 1:
                        self.__log('Stopping due to maximum epochs')

                self.save_state(self._save_dir)

                final_test_loss = None
                if self._testing:
                    final_test_loss = self.compute_full_test_accuracy()

                self.shut_down()

                if return_test_loss:
                    return final_test_loss
                else:
                    return


class RegressionModel(DPPModel):
    _problem_type = definitions.ProblemType.REGRESSION
    _loss_fn = 'l2'
    _supported_loss_fns_reg = ['l2', 'l1', 'smooth l1', 'log loss']
    _num_regression_outputs = 1

    def __init__(self, debug=False, load_from_saved=False, save_checkpoints=True, initialize=True, tensorboard_dir=None,
                 report_rate=100, save_dir=None):
        super().__init__(debug, load_from_saved, save_checkpoints, initialize, tensorboard_dir, report_rate, save_dir)

    def set_num_regression_outputs(self, num):
        """Set the number of regression response variables"""
        if not isinstance(num, int):
            raise TypeError("num must be an int")
        if num <= 0:
            raise ValueError("num must be positive")

        self._num_regression_outputs = num

    def __assemble_graph(self):
        with self._graph.as_default():

            self.__log('Parsing dataset...')

            if self._raw_test_labels is not None:
                # currently think of moderation features as None so they are passed in hard-coded
                self.__parse_dataset(self._raw_train_image_files, self._raw_train_labels, None,
                                     self._raw_test_image_files, self._raw_test_labels, None,
                                     self._raw_val_image_files, self._raw_val_labels, None)
            elif self._images_only:
                self.__parse_images(self._raw_image_files)
            else:
                # split the data into train/val/test sets, if there is no validation set or no moderation features
                # being used they will be returned as 0 (val) or None (moderation features)
                train_images, train_labels, train_mf, \
                test_images, test_labels, test_mf, \
                val_images, val_labels, val_mf, = \
                    loaders.split_raw_data(self._raw_image_files, self._raw_labels, self._test_split,
                                           self._validation_split, self._all_moderation_features,
                                           self._training_augmentation_images, self._training_augmentation_labels,
                                           self._split_labels)
                # parse the images and set the appropriate environment variables
                self.__parse_dataset(train_images, train_labels, train_mf,
                                     test_images, test_labels, test_mf,
                                     val_images, val_labels, val_mf)

            self.__log('Creating layer parameters...')

            self.__add_layers_to_graph()

            self.__log('Assembling graph...')

            # Define batches
            if self._has_moderation:
                x, y, mod_w = tf.train.shuffle_batch(
                    [self._train_images, self._train_labels, self._train_moderation_features],
                    batch_size=self._batch_size,
                    num_threads=self._num_threads,
                    capacity=self._queue_capacity,
                    min_after_dequeue=self._batch_size)
            else:
                x, y = tf.train.shuffle_batch([self._train_images, self._train_labels],
                                              batch_size=self._batch_size,
                                              num_threads=self._num_threads,
                                              capacity=self._queue_capacity,
                                              min_after_dequeue=self._batch_size)

            # Reshape input to the expected image dimensions
            x = tf.reshape(x, shape=[-1, self._image_height, self._image_width, self._image_depth])

            # This is a regression problem, so we should deserialize the label
            y = loaders.label_string_to_tensor(y, self._batch_size, self._num_regression_outputs)

            # if using patching we extract a patch of image here
            if self._with_patching:
                # Take a slice
                patch_width = self._patch_width
                patch_height = self._patch_height
                offset_h = np.random.randint(patch_height // 2, self._image_height - (patch_height // 2),
                                             self._batch_size)
                offset_w = np.random.randint(patch_width // 2, self._image_width - (patch_width // 2),
                                             self._batch_size)
                offsets = [x for x in zip(offset_h, offset_w)]
                x = tf.image.extract_glimpse(x, [patch_height, patch_width], offsets,
                                             normalized=False, centered=False)

            # Run the network operations
            if self._has_moderation:
                xx = self.forward_pass(x, deterministic=False, moderation_features=mod_w)
            else:
                xx = self.forward_pass(x, deterministic=False)

            # Define regularization cost
            if self._reg_coeff is not None:
                l2_cost = tf.squeeze(tf.reduce_sum(
                    [layer.regularization_coefficient * tf.nn.l2_loss(layer.weights) for layer in self._layers
                     if isinstance(layer, layers.fullyConnectedLayer)]))
            else:
                l2_cost = 0.0

            # Define cost function based on which one was selected via set_loss_function
            if self._loss_fn == 'l2':
                regression_loss = self.__batch_mean_l2_loss(tf.subtract(xx, y))
            elif self._loss_fn == 'l1':
                regression_loss = self.__batch_mean_l1_loss(tf.subtract(xx, y))
            elif self._loss_fn == 'smooth l1':
                regression_loss = self.__batch_mean_smooth_l1_loss(tf.subtract(xx, y))
            elif self._loss_fn == 'log loss':
                regression_loss = self.__batch_mean_log_loss(tf.subtract(xx, y))
            self._graph_ops['cost'] = tf.add(regression_loss, l2_cost)

            # Identify which optimizer we are using
            if self._optimizer == 'adagrad':
                self._graph_ops['optimizer'] = tf.train.AdagradOptimizer(self._learning_rate)
                self.__log('Using Adagrad optimizer')
            elif self._optimizer == 'adadelta':
                self._graph_ops['optimizer'] = tf.train.AdadeltaOptimizer(self._learning_rate)
                self.__log('Using adadelta optimizer')
            elif self._optimizer == 'sgd':
                self._graph_ops['optimizer'] = tf.train.GradientDescentOptimizer(self._learning_rate)
                self.__log('Using SGD optimizer')
            elif self._optimizer == 'adam':
                self._graph_ops['optimizer'] = tf.train.AdamOptimizer(self._learning_rate)
                self.__log('Using Adam optimizer')
            elif self._optimizer == 'sgd_momentum':
                self._graph_ops['optimizer'] = tf.train.MomentumOptimizer(self._learning_rate, 0.9, use_nesterov=True)
                self.__log('Using SGD with momentum optimizer')
            else:
                warnings.warn('Unrecognized optimizer requested')
                exit()

            # Compute gradients, clip them, the apply the clipped gradients
            # This is broken up so that we can add gradients to tensorboard
            # need to make the 5.0 an adjustable hyperparameter
            gradients, variables = zip(*self._graph_ops['optimizer'].compute_gradients(self._graph_ops['cost']))
            gradients, global_grad_norm = tf.clip_by_global_norm(gradients, 5.0)
            self._graph_ops['optimizer'] = self._graph_ops['optimizer'].apply_gradients(zip(gradients, variables))

            # Calculate test accuracy
            if self._has_moderation:
                if self._testing:
                    x_test, self._graph_ops['y_test'], mod_w_test = tf.train.batch(
                        [self._test_images, self._test_labels, self._test_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'], mod_w_val = tf.train.batch(
                        [self._val_images, self._val_labels, self._val_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
            else:
                if self._testing:
                    x_test, self._graph_ops['y_test'] = tf.train.batch([self._test_images, self._test_labels],
                                                                       batch_size=self._batch_size,
                                                                       num_threads=self._num_threads,
                                                                       capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'] = tf.train.batch([self._val_images, self._val_labels],
                                                                     batch_size=self._batch_size,
                                                                     num_threads=self._num_threads,
                                                                     capacity=self._queue_capacity)
            if self._testing:
                x_test = tf.reshape(x_test, shape=[-1, self._image_height, self._image_width, self._image_depth])
            if self._validation:
                x_val = tf.reshape(x_val, shape=[-1, self._image_height, self._image_width, self._image_depth])

            if self._testing:
                self._graph_ops['y_test'] = loaders.label_string_to_tensor(self._graph_ops['y_test'],
                                                                           self._batch_size,
                                                                           self._num_regression_outputs)
            if self._validation:
                self._graph_ops['y_val'] = loaders.label_string_to_tensor(self._graph_ops['y_val'],
                                                                          self._batch_size,
                                                                          self._num_regression_outputs)

            # if using patching we need to properly pull patches from the images
            if self._with_patching:
                # Take a slice of image. Same size and location (offsets) as the slice from training.
                patch_width = self._patch_width
                patch_height = self._patch_height
                if self._testing:
                    x_test = tf.image.extract_glimpse(x_test, [patch_height, patch_width], offsets,
                                                      normalized=False, centered=False)
                if self._validation:
                    x_val = tf.image.extract_glimpse(x_val, [patch_height, patch_width], offsets,
                                                     normalized=False, centered=False)
                if self._problem_type == definitions.ProblemType.SEMANTIC_SEGMETNATION:
                    if self._testing:
                        self._graph_ops['y_test'] = tf.image.extract_glimpse(self._graph_ops['y_test'],
                                                                             [patch_height, patch_width], offsets,
                                                                             normalized=False, centered=False)
                    if self._validation:
                        self._graph_ops['y_val'] = tf.image.extract_glimpse(self._graph_ops['y_val'],
                                                                            [patch_height, patch_width], offsets,
                                                                            normalized=False, centered=False)

            if self._has_moderation:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True,
                                                                            moderation_features=mod_w_test)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True,
                                                                           moderation_features=mod_w_val)
            else:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True)

            # compute the loss and accuracy based on problem type
            if self._testing:
                if self._num_regression_outputs == 1:
                    self._graph_ops['test_losses'] = tf.squeeze(tf.stack(
                        tf.subtract(self._graph_ops['x_test_predicted'], self._graph_ops['y_test'])))
                else:
                    self._graph_ops['test_losses'] = self.__l2_norm(
                        tf.subtract(self._graph_ops['x_test_predicted'], self._graph_ops['y_test']))
            if self._validation:
                if self._num_regression_outputs == 1:
                    self._graph_ops['val_losses'] = tf.squeeze(
                        tf.stack(tf.subtract(self._graph_ops['x_val_predicted'], self._graph_ops['y_val'])))
                else:
                    self._graph_ops['val_losses'] = self.__l2_norm(
                        tf.subtract(self._graph_ops['x_val_predicted'], self._graph_ops['y_val']))
                self._graph_ops['val_cost'] = tf.reduce_mean(tf.abs(self._graph_ops['val_losses']))

            # Epoch summaries for Tensorboard
            if self._tb_dir is not None:
                self.__log('Creating Tensorboard summaries...')

                # Summaries for any problem type
                tf.summary.scalar('train/loss', self._graph_ops['cost'], collections=['custom_summaries'])
                tf.summary.scalar('train/learning_rate', self._learning_rate, collections=['custom_summaries'])
                tf.summary.scalar('train/l2_loss', l2_cost, collections=['custom_summaries'])
                filter_summary = self.__get_weights_as_image(self.__first_layer().weights)
                tf.summary.image('filters/first', filter_summary, collections=['custom_summaries'])

                # Summaries specific to regression problems
                if self._num_regression_outputs == 1:
                    tf.summary.scalar('train/regression_loss', regression_loss, collections=['custom_summaries'])
                    if self._validation:
                        tf.summary.scalar('validation/loss', self._graph_ops['val_cost'],
                                          collections=['custom_summaries'])
                        tf.summary.histogram('validation/batch_losses', self._graph_ops['val_losses'],
                                             collections=['custom_summaries'])

                # Summaries for each layer
                for layer in self._layers:
                    if hasattr(layer, 'name') and not isinstance(layer, layers.batchNormLayer):
                        tf.summary.histogram('weights/' + layer.name, layer.weights, collections=['custom_summaries'])
                        tf.summary.histogram('biases/' + layer.name, layer.biases, collections=['custom_summaries'])

                        # At one point the graph would hang on session.run(graph_ops['merged']) inside of begin_training
                        # and it was found that if you commented the below line then the code wouldn't hang. Never
                        # fully understood why, as it only happened if you tried running with train/test and no
                        # validation. But after adding more features and just randomly trying to uncomment the below
                        # line to see if it would work, it appears to now be working, but still don't know why...
                        tf.summary.histogram('activations/' + layer.name, layer.activations,
                                             collections=['custom_summaries'])

                # Summaries for gradients
                # we variables[index].name[:-2] because variables[index].name will have a ':0' at the end of
                # the name and tensorboard does not like this so we remove it with the [:-2]
                # We also currently seem to get None's for gradients when performing a hyperparameter search
                # and as such it is simply left out for hyper-param searches, needs to be fixed
                if not self._hyper_param_search:
                    for index, grad in enumerate(gradients):
                        tf.summary.histogram("gradients/" + variables[index].name[:-2], gradients[index],
                                             collections=['custom_summaries'])

                    tf.summary.histogram("gradient_global_norm/", global_grad_norm, collections=['custom_summaries'])

                self._graph_ops['merged'] = tf.summary.merge_all(key='custom_summaries')

    def begin_training(self, return_test_loss=False):
        """
        Initialize the network and either run training to the specified max epoch, or load trainable variables.
        The full test accuracy is calculated immediately afterward. Finally, the trainable parameters are saved and
        the session is shut down.
        Before calling this function, the images and labels should be loaded, as well as all relevant hyperparameters.
        """
        # if None in [self._train_images, self._test_images,
        #             self._train_labels, self._test_labels]:
        #     raise RuntimeError("Images and Labels need to be loaded before you can begin training. " +
        #                        "Try first using one of the methods starting with 'load_...' such as " +
        #                        "'DPPModel.load_dataset_from_directory_with_csv_labels()'")
        # if (len(self._layers) < 1):
        #     raise RuntimeError("There are no layers currently added to the model when trying to begin training. " +
        #                        "Add layers first by using functions such as 'DPPModel.add_input_layer()' or " +
        #                        "'DPPModel.add_convolutional_layer()'. See documentation for a complete list of
        #                        layers.")

        with self._graph.as_default():
            self.__assemble_graph()
            print('assembled the graph')

            # Either load the network parameters from a checkpoint file or start training
            if self._load_from_saved is not False:
                self.load_state()

                self.__initialize_queue_runners()

                self.compute_full_test_accuracy()

                self.shut_down()
            else:
                if self._tb_dir is not None:
                    train_writer = tf.summary.FileWriter(self._tb_dir, self._session.graph)

                self.__log('Initializing parameters...')
                init_op = tf.global_variables_initializer()
                self._session.run(init_op)

                self.__initialize_queue_runners()

                self.__log('Beginning training...')

                self.__set_learning_rate()

                # Needed for batch norm
                update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
                self._graph_ops['optimizer'] = tf.group([self._graph_ops['optimizer'], update_ops])

                # for i in range(self._maximum_training_batches):
                tqdm_range = tqdm(range(self._maximum_training_batches))
                for i in tqdm_range:
                    start_time = time.time()

                    self._global_epoch = i
                    self._session.run(self._graph_ops['optimizer'])
                    if self._global_epoch > 0 and self._global_epoch % self._report_rate == 0:
                        elapsed = time.time() - start_time

                        if self._tb_dir is not None:
                            summary = self._session.run(self._graph_ops['merged'])
                            train_writer.add_summary(summary, i)
                        if self._validation:
                            loss, epoch_test_loss = self._session.run([self._graph_ops['cost'],
                                                                       self._graph_ops['val_cost']])

                            samples_per_sec = self._batch_size / elapsed

                            desc_str = "{}: Results for batch {} (epoch {:.1f}) - Loss: {}, samples/sec: {:.2f}"
                            tqdm_range.set_description(
                                desc_str.format(datetime.datetime.now().strftime("%I:%M%p"),
                                                i,
                                                i / (self._total_training_samples / self._batch_size),
                                                loss,
                                                samples_per_sec))

                        else:
                            loss = self._session.run([self._graph_ops['cost']])

                            samples_per_sec = self._batch_size / elapsed

                            desc_str = "{}: Results for batch {} (epoch {:.1f}) - Loss: {}, samples/sec: {:.2f}"
                            tqdm_range.set_description(
                                desc_str.format(datetime.datetime.now().strftime("%I:%M%p"),
                                                i,
                                                i / (self._total_training_samples / self._batch_size),
                                                loss,
                                                samples_per_sec))

                        if self._save_checkpoints and self._global_epoch % (self._report_rate * 100) == 0:
                            self.save_state(self._save_dir)
                    else:
                        loss = self._session.run([self._graph_ops['cost']])

                    if loss == 0.0:
                        self.__log('Stopping due to zero loss')
                        break

                    if i == self._maximum_training_batches - 1:
                        self.__log('Stopping due to maximum epochs')

                self.save_state(self._save_dir)

                final_test_loss = None
                if self._testing:
                    final_test_loss = self.compute_full_test_accuracy()

                self.shut_down()

                if return_test_loss:
                    return final_test_loss
                else:
                    return


class SemanticSegmentationModel(DPPModel):
    _problem_type = definitions.ProblemType.SEMANTIC_SEGMETNATION
    _loss_fn = 'sigmoid cross entropy'
    _supported_loss_fns_reg = ['sigmoid cross entropy']

    def __init__(self, debug=False, load_from_saved=False, save_checkpoints=True, initialize=True, tensorboard_dir=None,
                 report_rate=100, save_dir=None):
        super().__init__(debug, load_from_saved, save_checkpoints, initialize, tensorboard_dir, report_rate, save_dir)

    def __assemble_graph(self):
        with self._graph.as_default():

            self.__log('Parsing dataset...')

            if self._raw_test_labels is not None:
                # currently think of moderation features as None so they are passed in hard-coded
                self.__parse_dataset(self._raw_train_image_files, self._raw_train_labels, None,
                                     self._raw_test_image_files, self._raw_test_labels, None,
                                     self._raw_val_image_files, self._raw_val_labels, None)
            elif self._images_only:
                self.__parse_images(self._raw_image_files)
            else:
                # split the data into train/val/test sets, if there is no validation set or no moderation features
                # being used they will be returned as 0 (val) or None (moderation features)
                train_images, train_labels, train_mf, \
                test_images, test_labels, test_mf, \
                val_images, val_labels, val_mf, = \
                    loaders.split_raw_data(self._raw_image_files, self._raw_labels, self._test_split,
                                           self._validation_split, self._all_moderation_features,
                                           self._training_augmentation_images, self._training_augmentation_labels,
                                           self._split_labels)
                # parse the images and set the appropriate environment variables
                self.__parse_dataset(train_images, train_labels, train_mf,
                                     test_images, test_labels, test_mf,
                                     val_images, val_labels, val_mf)

            self.__log('Creating layer parameters...')

            self.__add_layers_to_graph()

            self.__log('Assembling graph...')

            # Define batches
            if self._has_moderation:
                x, y, mod_w = tf.train.shuffle_batch(
                    [self._train_images, self._train_labels, self._train_moderation_features],
                    batch_size=self._batch_size,
                    num_threads=self._num_threads,
                    capacity=self._queue_capacity,
                    min_after_dequeue=self._batch_size)
            else:
                x, y = tf.train.shuffle_batch([self._train_images, self._train_labels],
                                              batch_size=self._batch_size,
                                              num_threads=self._num_threads,
                                              capacity=self._queue_capacity,
                                              min_after_dequeue=self._batch_size)

            # Reshape input and labels to the expected image dimensions
            x = tf.reshape(x, shape=[-1, self._image_height, self._image_width, self._image_depth])
            y = tf.reshape(y, shape=[-1, self._image_height, self._image_width, 1])

            # if using patching we extract a patch of image and its label here
            if self._with_patching:
                # Take a slice
                patch_width = self._patch_width
                patch_height = self._patch_height
                offset_h = np.random.randint(patch_height // 2, self._image_height - (patch_height // 2),
                                             self._batch_size)
                offset_w = np.random.randint(patch_width // 2, self._image_width - (patch_width // 2),
                                             self._batch_size)
                offsets = [x for x in zip(offset_h, offset_w)]
                x = tf.image.extract_glimpse(x, [patch_height, patch_width], offsets,
                                             normalized=False, centered=False)
                y = tf.image.extract_glimpse(y, [patch_height, patch_width], offsets,
                                             normalized=False, centered=False)

            # Run the network operations
            if self._has_moderation:
                xx = self.forward_pass(x, deterministic=False, moderation_features=mod_w)
            else:
                xx = self.forward_pass(x, deterministic=False)

            # Define regularization cost
            if self._reg_coeff is not None:
                l2_cost = tf.squeeze(tf.reduce_sum(
                    [layer.regularization_coefficient * tf.nn.l2_loss(layer.weights) for layer in self._layers
                     if isinstance(layer, layers.fullyConnectedLayer)]))
            else:
                l2_cost = 0.0

            # Define cost function  based on which one was selected via set_loss_function
            if self._loss_fn == 'sigmoid cross entropy':
                pixel_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=xx, labels=y))
            self._graph_ops['cost'] = tf.squeeze(tf.add(pixel_loss, l2_cost))

            # Identify which optimizer we are using
            if self._optimizer == 'adagrad':
                self._graph_ops['optimizer'] = tf.train.AdagradOptimizer(self._learning_rate)
                self.__log('Using Adagrad optimizer')
            elif self._optimizer == 'adadelta':
                self._graph_ops['optimizer'] = tf.train.AdadeltaOptimizer(self._learning_rate)
                self.__log('Using adadelta optimizer')
            elif self._optimizer == 'sgd':
                self._graph_ops['optimizer'] = tf.train.GradientDescentOptimizer(self._learning_rate)
                self.__log('Using SGD optimizer')
            elif self._optimizer == 'adam':
                self._graph_ops['optimizer'] = tf.train.AdamOptimizer(self._learning_rate)
                self.__log('Using Adam optimizer')
            elif self._optimizer == 'sgd_momentum':
                self._graph_ops['optimizer'] = tf.train.MomentumOptimizer(self._learning_rate, 0.9, use_nesterov=True)
                self.__log('Using SGD with momentum optimizer')
            else:
                warnings.warn('Unrecognized optimizer requested')
                exit()

            # Compute gradients, clip them, the apply the clipped gradients
            # This is broken up so that we can add gradients to tensorboard
            # need to make the 5.0 an adjustable hyperparameter
            gradients, variables = zip(*self._graph_ops['optimizer'].compute_gradients(self._graph_ops['cost']))
            gradients, global_grad_norm = tf.clip_by_global_norm(gradients, 5.0)
            self._graph_ops['optimizer'] = self._graph_ops['optimizer'].apply_gradients(zip(gradients, variables))

            # Calculate test accuracy
            if self._has_moderation:
                if self._testing:
                    x_test, self._graph_ops['y_test'], mod_w_test = tf.train.batch(
                        [self._test_images, self._test_labels, self._test_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'], mod_w_val = tf.train.batch(
                        [self._val_images, self._val_labels, self._val_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
            else:
                if self._testing:
                    x_test, self._graph_ops['y_test'] = tf.train.batch([self._test_images, self._test_labels],
                                                                       batch_size=self._batch_size,
                                                                       num_threads=self._num_threads,
                                                                       capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'] = tf.train.batch([self._val_images, self._val_labels],
                                                                     batch_size=self._batch_size,
                                                                     num_threads=self._num_threads,
                                                                     capacity=self._queue_capacity)
            if self._testing:
                x_test = tf.reshape(x_test, shape=[-1, self._image_height, self._image_width, self._image_depth])
                self._graph_ops['y_test'] = tf.reshape(self._graph_ops['y_test'],
                                                       shape=[-1, self._image_height, self._image_width, 1])
            if self._validation:
                x_val = tf.reshape(x_val, shape=[-1, self._image_height, self._image_width, self._image_depth])
                self._graph_ops['y_val'] = tf.reshape(self._graph_ops['y_val'],
                                                      shape=[-1, self._image_height, self._image_width, 1])

            # if using patching we need to properly pull patches from the images and labels
            if self._with_patching:
                # Take a slice of image. Same size and location (offsets) as the slice from training.
                patch_width = self._patch_width
                patch_height = self._patch_height
                if self._testing:
                    x_test = tf.image.extract_glimpse(x_test, [patch_height, patch_width], offsets,
                                                      normalized=False, centered=False)
                    self._graph_ops['y_test'] = tf.image.extract_glimpse(self._graph_ops['y_test'],
                                                                         [patch_height, patch_width], offsets,
                                                                         normalized=False, centered=False)
                if self._validation:
                    x_val = tf.image.extract_glimpse(x_val, [patch_height, patch_width], offsets,
                                                     normalized=False, centered=False)
                    self._graph_ops['y_val'] = tf.image.extract_glimpse(self._graph_ops['y_val'],
                                                                        [patch_height, patch_width], offsets,
                                                                        normalized=False, centered=False)

            if self._has_moderation:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True,
                                                                            moderation_features=mod_w_test)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True,
                                                                           moderation_features=mod_w_val)
            else:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True)

            # compute the loss and accuracy based on problem type
            if self._testing:
                self._graph_ops['test_losses'] = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(
                    logits=self._graph_ops['x_test_predicted'],
                    labels=self._graph_ops['y_test']),
                    axis=2)
                self._graph_ops['test_losses'] = tf.reshape(tf.reduce_mean(self._graph_ops['test_losses'],
                                                                           axis=1),
                                                            [self._batch_size])
            if self._validation:
                self._graph_ops['val_losses'] = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(
                    logits=self._graph_ops['x_val_predicted'], labels=self._graph_ops['y_val']),
                    axis=2)
                self._graph_ops['val_losses'] = tf.transpose(
                    tf.reduce_mean(self._graph_ops['val_losses'], axis=1))
                self._graph_ops['val_cost'] = tf.reduce_mean(self._graph_ops['val_losses'])

            # Epoch summaries for Tensorboard
            if self._tb_dir is not None:
                self.__log('Creating Tensorboard summaries...')

                # Summaries for any problem type
                tf.summary.scalar('train/loss', self._graph_ops['cost'], collections=['custom_summaries'])
                tf.summary.scalar('train/learning_rate', self._learning_rate, collections=['custom_summaries'])
                tf.summary.scalar('train/l2_loss', l2_cost, collections=['custom_summaries'])
                filter_summary = self.__get_weights_as_image(self.__first_layer().weights)
                tf.summary.image('filters/first', filter_summary, collections=['custom_summaries'])

                # Summaries specific to semantic segmentation
                # we send in the last layer's output size (i.e. the final image dimensions) to get_weights_as_image
                # because xx and x_test_predicted have dynamic dims [?,?,?,?], so we need actual numbers passed in
                train_images_summary = self.__get_weights_as_image(
                    tf.transpose(tf.expand_dims(xx, -1), (1, 2, 3, 0)),
                    self._layers[-1].output_size)
                tf.summary.image('masks/train', train_images_summary, collections=['custom_summaries'])
                if self._validation:
                    tf.summary.scalar('validation/loss', self._graph_ops['val_cost'],
                                      collections=['custom_summaries'])
                    val_images_summary = self.__get_weights_as_image(
                        tf.transpose(tf.expand_dims(self._graph_ops['x_val_predicted'], -1), (1, 2, 3, 0)),
                        self._layers[-1].output_size)
                    tf.summary.image('masks/validation', val_images_summary, collections=['custom_summaries'])

                # Summaries for each layer
                for layer in self._layers:
                    if hasattr(layer, 'name') and not isinstance(layer, layers.batchNormLayer):
                        tf.summary.histogram('weights/' + layer.name, layer.weights, collections=['custom_summaries'])
                        tf.summary.histogram('biases/' + layer.name, layer.biases, collections=['custom_summaries'])

                        # At one point the graph would hang on session.run(graph_ops['merged']) inside of begin_training
                        # and it was found that if you commented the below line then the code wouldn't hang. Never
                        # fully understood why, as it only happened if you tried running with train/test and no
                        # validation. But after adding more features and just randomly trying to uncomment the below
                        # line to see if it would work, it appears to now be working, but still don't know why...
                        tf.summary.histogram('activations/' + layer.name, layer.activations,
                                             collections=['custom_summaries'])

                # Summaries for gradients
                # we variables[index].name[:-2] because variables[index].name will have a ':0' at the end of
                # the name and tensorboard does not like this so we remove it with the [:-2]
                # We also currently seem to get None's for gradients when performing a hyperparameter search
                # and as such it is simply left out for hyper-param searches, needs to be fixed
                if not self._hyper_param_search:
                    for index, grad in enumerate(gradients):
                        tf.summary.histogram("gradients/" + variables[index].name[:-2], gradients[index],
                                             collections=['custom_summaries'])

                    tf.summary.histogram("gradient_global_norm/", global_grad_norm, collections=['custom_summaries'])

                self._graph_ops['merged'] = tf.summary.merge_all(key='custom_summaries')

    def begin_training(self, return_test_loss=False):
        """
        Initialize the network and either run training to the specified max epoch, or load trainable variables.
        The full test accuracy is calculated immediately afterward. Finally, the trainable parameters are saved and
        the session is shut down.
        Before calling this function, the images and labels should be loaded, as well as all relevant hyperparameters.
        """
        # if None in [self._train_images, self._test_images,
        #             self._train_labels, self._test_labels]:
        #     raise RuntimeError("Images and Labels need to be loaded before you can begin training. " +
        #                        "Try first using one of the methods starting with 'load_...' such as " +
        #                        "'DPPModel.load_dataset_from_directory_with_csv_labels()'")
        # if (len(self._layers) < 1):
        #     raise RuntimeError("There are no layers currently added to the model when trying to begin training. " +
        #                        "Add layers first by using functions such as 'DPPModel.add_input_layer()' or " +
        #                        "'DPPModel.add_convolutional_layer()'. See documentation for a complete list of
        #                        layers.")

        with self._graph.as_default():
            self.__assemble_graph()
            print('assembled the graph')

            # Either load the network parameters from a checkpoint file or start training
            if self._load_from_saved is not False:
                self.load_state()

                self.__initialize_queue_runners()

                self.compute_full_test_accuracy()

                self.shut_down()
            else:
                if self._tb_dir is not None:
                    train_writer = tf.summary.FileWriter(self._tb_dir, self._session.graph)

                self.__log('Initializing parameters...')
                init_op = tf.global_variables_initializer()
                self._session.run(init_op)

                self.__initialize_queue_runners()

                self.__log('Beginning training...')

                self.__set_learning_rate()

                # Needed for batch norm
                update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
                self._graph_ops['optimizer'] = tf.group([self._graph_ops['optimizer'], update_ops])

                # for i in range(self._maximum_training_batches):
                tqdm_range = tqdm(range(self._maximum_training_batches))
                for i in tqdm_range:
                    start_time = time.time()

                    self._global_epoch = i
                    self._session.run(self._graph_ops['optimizer'])
                    if self._global_epoch > 0 and self._global_epoch % self._report_rate == 0:
                        elapsed = time.time() - start_time

                        if self._tb_dir is not None:
                            summary = self._session.run(self._graph_ops['merged'])
                            train_writer.add_summary(summary, i)
                        if self._validation:
                            loss, epoch_test_loss = self._session.run([self._graph_ops['cost'],
                                                                       self._graph_ops['val_cost']])

                            samples_per_sec = self._batch_size / elapsed

                            desc_str = "{}: Results for batch {} (epoch {:.1f}) - Loss: {}, samples/sec: {:.2f}"
                            tqdm_range.set_description(
                                desc_str.format(datetime.datetime.now().strftime("%I:%M%p"),
                                                i,
                                                i / (self._total_training_samples / self._batch_size),
                                                loss,
                                                samples_per_sec))

                        else:
                            loss = self._session.run([self._graph_ops['cost']])

                            samples_per_sec = self._batch_size / elapsed

                            desc_str = "{}: Results for batch {} (epoch {:.1f}) - Loss: {}, samples/sec: {:.2f}"
                            tqdm_range.set_description(
                                desc_str.format(datetime.datetime.now().strftime("%I:%M%p"),
                                                i,
                                                i / (self._total_training_samples / self._batch_size),
                                                loss,
                                                samples_per_sec))

                        if self._save_checkpoints and self._global_epoch % (self._report_rate * 100) == 0:
                            self.save_state(self._save_dir)
                    else:
                        loss = self._session.run([self._graph_ops['cost']])

                    if loss == 0.0:
                        self.__log('Stopping due to zero loss')
                        break

                    if i == self._maximum_training_batches - 1:
                        self.__log('Stopping due to maximum epochs')

                self.save_state(self._save_dir)

                final_test_loss = None
                if self._testing:
                    final_test_loss = self.compute_full_test_accuracy()

                self.shut_down()

                if return_test_loss:
                    return final_test_loss
                else:
                    return


class ObjectDetectionModel(DPPModel):
    _problem_type = definitions.ProblemType.OBJECT_DETECTION
    _loss_fn = 'yolo'
    _supported_loss_fns_reg = ['yolo']

    # Yolo-specific parameters, non-default values defined by set_yolo_parameters
    _grid_w = 7
    _grid_h = 7
    _LABELS = ['plant']
    _NUM_CLASSES = 1
    _RAW_ANCHORS = [(159, 157), (103, 133), (91, 89), (64, 65), (142, 101)]
    _ANCHORS = None  # Scaled version, but grid and image sizes are needed so default is deferred
    _NUM_BOXES = 5
    _THRESH_SIG = 0.6
    _THRESH_OVERLAP = 0.3
    _THRESH_CORRECT = 0.5

    def __init__(self, debug=False, load_from_saved=False, save_checkpoints=True, initialize=True, tensorboard_dir=None,
                 report_rate=100, save_dir=None):
        super().__init__(debug, load_from_saved, save_checkpoints, initialize, tensorboard_dir, report_rate, save_dir)

    def set_image_dimensions(self, image_height, image_width, image_depth):
        """Specify the image dimensions for images in the dataset (depth is the number of channels)"""
        super().set_image_dimensions(image_height, image_width, image_depth)

        # Generate image-scaled anchors for YOLO object detection
        if self._RAW_ANCHORS:
            scale_w = self._grid_w / self._image_width
            scale_h = self._grid_h / self._image_height
            self._ANCHORS = [(anchor[0] * scale_w, anchor[1] * scale_h) for anchor in self._RAW_ANCHORS]

    def set_yolo_parameters(self, grid_size=None, labels=None, anchors=None):
        """
        Set YOLO parameters for the grid size, class labels, and anchor/prior sizes
        :param grid_size: 2-element list/tuple with the width and height of the YOLO grid. Default = [7,7]
        :param labels: List of class labels for detection. Default = ['plant']
        :param anchors: List of 2-element anchor/prior widths and heights.
        Default = [[159, 157], [103, 133], [91, 89], [64, 65], [142, 101]]
        """
        if not self._image_width or not self._image_height:
            raise RuntimeError("Image dimensions need to be chosen before setting YOLO parameters")

        # Do type checks and fill in list parameters with arguments or defaults, because mutable function defaults are
        # dangerous
        if grid_size:
            if not isinstance(grid_size, Sequence) or len(grid_size) != 2 \
                    or not all([isinstance(x, int) for x in grid_size]):
                raise TypeError("grid_size should be a 2-element integer list")
            self._grid_w, self._grid_h = grid_size
        else:
            self._grid_w, self._grid_h = [7, 7]

        if labels:
            if not isinstance(labels, Sequence) or isinstance(labels, str) \
                    or not all([isinstance(lab, str) for lab in labels]):
                raise TypeError("labels should be a string list")
            self._LABELS = labels
            self._NUM_CLASSES = len(labels)
        else:
            self._LABELS = ['plant']
            self._NUM_CLASSES = 1

        if anchors:
            if not isinstance(anchors, Sequence):
                raise TypeError("anchors should be a list/tuple of integer lists/tuples")
            if not all([(isinstance(a, Sequence) and len(a) == 2
                         and isinstance(a[0], int) and isinstance(a[1], int)) for a in anchors]):
                raise TypeError("anchors should contain 2-element lists/tuples")
            self._RAW_ANCHORS = anchors
        else:
            self._RAW_ANCHORS = [(159, 157), (103, 133), (91, 89), (64, 65), (142, 101)]

        # Fill in non-mutable parameters
        self._NUM_BOXES = len(self._RAW_ANCHORS)

        # Scale anchors to the grid size
        scale_w = self._grid_w / self._image_width
        scale_h = self._grid_h / self._image_height
        self._ANCHORS = [(anchor[0] * scale_w, anchor[1] * scale_h) for anchor in self._RAW_ANCHORS]

    def set_yolo_thresholds(self, thresh_sig=0.6, thresh_overlap=0.3, thresh_correct=0.5):
        """Set YOLO IoU thresholds for bounding box significance (during output filtering), overlap (during non-maximal
        suppression), and correctness (for mAP calculation)"""
        self._THRESH_SIG = thresh_sig
        self._THRESH_OVERLAP = thresh_overlap
        self._THRESH_CORRECT = thresh_correct

    def _yolo_compute_iou(self, pred_box, true_box):
        """Helper function to compute the intersection over union of pred_box and true_box
        pred_box and true_box represent multiple boxes with coords being x,y,w,h (0-indexed 0-3)"""
        # numerator
        # get coords of intersection rectangle, then compute intersection area
        x1 = tf.maximum(pred_box[..., 0] - 0.5 * pred_box[..., 2],
                        true_box[..., 0:1] - 0.5 * true_box[..., 2:3])
        y1 = tf.maximum(pred_box[..., 1] - 0.5 * pred_box[..., 3],
                        true_box[..., 1:2] - 0.5 * true_box[..., 3:4])
        x2 = tf.minimum(pred_box[..., 0] + 0.5 * pred_box[..., 2],
                        true_box[..., 0:1] + 0.5 * true_box[..., 2:3])
        y2 = tf.minimum(pred_box[..., 1] + 0.5 * pred_box[..., 3],
                        true_box[..., 1:2] + 0.5 * true_box[..., 3:4])
        intersection_area = tf.multiply(tf.maximum(0., x2 - x1), tf.maximum(0., y2 - y1))

        # denominator
        # compute area of pred and truth, compute union area
        pred_area = tf.multiply(pred_box[..., 2], pred_box[..., 3])
        true_area = tf.multiply(true_box[..., 2:3], true_box[..., 3:4])
        union_area = tf.subtract(tf.add(pred_area, true_area), intersection_area)

        # compute iou
        iou = tf.divide(intersection_area, union_area)
        return iou

    def _yolo_loss_function(self, y_true, y_pred):
        """
        Loss function based on YOLO
        See the paper for details: https://pjreddie.com/media/files/papers/yolo.pdf

        :param y_true: Tensor with ground truth bounding boxes for each grid square in each image. Labels have 6
        elements: [object/no-object, class, x, y, w, h]
        :param y_pred: Tensor with predicted bounding boxes for each grid square in each image. Predictions consist of
        one box and confidence [x, y, w, h, conf] for each anchor plus 1 element for specifying the class (only one atm)
        :return Scalar Tensor with the Yolo loss for the bounding box predictions
        """

        prior_boxes = tf.convert_to_tensor(self._ANCHORS)

        # object/no-object masks #
        # create masks for grid cells with objects and with no objects
        obj_mask = tf.cast(y_true[..., 0], dtype=bool)
        no_obj_mask = tf.logical_not(obj_mask)
        obj_pred = tf.boolean_mask(y_pred, obj_mask)
        obj_true = tf.boolean_mask(y_true, obj_mask)
        no_obj_pred = tf.boolean_mask(y_pred, no_obj_mask)

        # bbox coordinate loss #
        # build a tensor of the predicted bounding boxes and confidences, classes will be stored separately
        # [x1,y1,w1,h1,conf1,x2,y2,w2,h2,conf2,x3,y3,w3,h3,conf3,...]
        pred_classes = obj_pred[..., self._NUM_BOXES * 5:]
        # we take the x,y,w,h,conf's that are altogether (dim is 1xB*5) and turn into Bx5, where B is num_boxes
        obj_pred = tf.reshape(obj_pred[..., 0:self._NUM_BOXES * 5], [-1, self._NUM_BOXES, 5])
        no_obj_pred = tf.reshape(no_obj_pred[..., 0:self._NUM_BOXES * 5], [-1, self._NUM_BOXES, 5])
        t_x, t_y, t_w, t_h = obj_pred[..., 0], obj_pred[..., 1], obj_pred[..., 2], obj_pred[..., 3]
        t_o = obj_pred[..., 4]
        pred_x = tf.sigmoid(t_x) + 0.00001  # concerned about underflow (might not actually be necessary)
        pred_y = tf.sigmoid(t_y) + 0.00001
        pred_w = (tf.exp(t_w) + 0.00001) * prior_boxes[:, 0]
        pred_h = (tf.exp(t_h) + 0.00001) * prior_boxes[:, 1]
        pred_conf = tf.sigmoid(t_o) + 0.00001
        predicted_boxes = tf.stack([pred_x, pred_y, pred_w, pred_h, pred_conf], axis=2)

        # find responsible boxes by computing iou's and select the best one
        ious = self._yolo_compute_iou(
            predicted_boxes, obj_true[..., 1 + self._NUM_CLASSES:1 + self._NUM_CLASSES + 4])
        greatest_iou_indices = tf.argmax(ious, 1)
        argmax_one_hot = tf.one_hot(indices=greatest_iou_indices, depth=5)
        resp_box_mask = tf.cast(argmax_one_hot, dtype=bool)
        responsible_boxes = tf.boolean_mask(predicted_boxes, resp_box_mask)

        # compute loss on responsible boxes
        loss_xy = tf.square(tf.subtract(responsible_boxes[..., 0:2],
                                        obj_true[..., 1 + self._NUM_CLASSES:1 + self._NUM_CLASSES + 2]))
        loss_wh = tf.square(tf.subtract(tf.sqrt(responsible_boxes[..., 2:4]),
                                        tf.sqrt(obj_true[..., 1 + self._NUM_CLASSES + 2:1 + self._NUM_CLASSES + 4])))
        coord_loss = tf.reduce_sum(tf.add(loss_xy, loss_wh))

        # confidence loss #
        # grids that do contain an object, 1 * iou means we simply take the difference between the
        # iou's and the predicted confidence

        # this was to make responsible boxes confidences aim to go to 1 instead of their current iou score, this is
        # still being tested
        # non_resp_box_mask = tf.logical_not(resp_box_mask)
        # non_responsible_boxes = tf.boolean_mask(predicted_boxes, non_resp_box_mask)
        # non_responsible_ious = tf.boolean_mask(ious, non_resp_box_mask)
        # loss1 = tf.reduce_sum(tf.square(1 - responsible_boxes[..., 4]))
        # loss2 = tf.reduce_sum(tf.square(tf.subtract(non_responsible_ious, non_responsible_boxes[..., 4])))
        # loss_obj = loss1 + loss2

        # this is how the paper does it, the above 6 lines is experimental
        obj_num_grids = tf.shape(predicted_boxes)[0]  # [num_boxes, 5, 5]
        loss_obj = tf.cast((1 / obj_num_grids), dtype='float32') * tf.reduce_sum(
            tf.square(tf.subtract(ious, predicted_boxes[..., 4])))

        # grids that do not contain an object, 0 * iou means we simply take the predicted confidences of the
        # grids that do not have an object and square and sum (because they should be 0)
        no_obj_confs = tf.sigmoid(no_obj_pred[..., 4])
        no_obj_num_grids = tf.shape(no_obj_confs)[0]  # [number_of_grids_without_an_object, 5]
        loss_no_obj = tf.cast(1 / no_obj_num_grids, dtype='float32') * tf.reduce_sum(tf.square(no_obj_confs))
        # incase obj_pred or no_obj_confs is empty (e.g. no objects in the image) we need to make sure we dont
        # get nan's in our losses...
        loss_obj = tf.cond(tf.count_nonzero(y_true[..., 4]) > 0, lambda: loss_obj, lambda: 0.)
        loss_no_obj = tf.cond(tf.count_nonzero(y_true[..., 4]) < self._grid_w * self._grid_h,
                              lambda: loss_no_obj, lambda: 0.)
        conf_loss = tf.add(loss_obj, loss_no_obj)

        # classification loss #
        # currently only one class, plant, will need to be made more general for multi-class in the future
        class_probs_pred = tf.nn.softmax(pred_classes)
        class_diffs = tf.subtract(obj_true[..., 1:1 + self._NUM_CLASSES], class_probs_pred)
        class_loss = tf.reduce_sum(tf.square(class_diffs))

        total_loss = coord_loss + conf_loss + class_loss

        # for some debug/checking, otherwise leave commented #
        # init_op = tf.global_variables_initializer()
        # self._session.run(init_op)
        # self.__initialize_queue_runners()
        # print('printing losses')
        # print(self._session.run([loss_obj, loss_no_obj]))
        # print(self._session.run([coord_loss, conf_loss, class_loss]))
        # print(self._session.run([loss_obj, loss_no_obj]))
        # print(self._session.run([coord_loss, conf_loss, class_loss]))
        # print(self._session.run([loss_obj, loss_no_obj]))
        # print(self._session.run([coord_loss, conf_loss, class_loss]))

        return total_loss

    def __assemble_graph(self):
        with self._graph.as_default():

            self.__log('Parsing dataset...')

            if self._raw_test_labels is not None:
                # currently think of moderation features as None so they are passed in hard-coded
                self.__parse_dataset(self._raw_train_image_files, self._raw_train_labels, None,
                                     self._raw_test_image_files, self._raw_test_labels, None,
                                     self._raw_val_image_files, self._raw_val_labels, None)
            elif self._images_only:
                self.__parse_images(self._raw_image_files)
            else:
                # split the data into train/val/test sets, if there is no validation set or no moderation features
                # being used they will be returned as 0 (val) or None (moderation features)
                train_images, train_labels, train_mf, \
                test_images, test_labels, test_mf, \
                val_images, val_labels, val_mf, = \
                    loaders.split_raw_data(self._raw_image_files, self._raw_labels, self._test_split,
                                           self._validation_split, self._all_moderation_features,
                                           self._training_augmentation_images, self._training_augmentation_labels,
                                           self._split_labels)
                # parse the images and set the appropriate environment variables
                self.__parse_dataset(train_images, train_labels, train_mf,
                                     test_images, test_labels, test_mf,
                                     val_images, val_labels, val_mf)

            self.__log('Creating layer parameters...')

            self.__add_layers_to_graph()

            self.__log('Assembling graph...')

            # Define batches
            if self._has_moderation:
                x, y, mod_w = tf.train.shuffle_batch(
                    [self._train_images, self._train_labels, self._train_moderation_features],
                    batch_size=self._batch_size,
                    num_threads=self._num_threads,
                    capacity=self._queue_capacity,
                    min_after_dequeue=self._batch_size)
            else:
                x, y = tf.train.shuffle_batch([self._train_images, self._train_labels],
                                              batch_size=self._batch_size,
                                              num_threads=self._num_threads,
                                              capacity=self._queue_capacity,
                                              min_after_dequeue=self._batch_size)

            # Reshape input to the expected image dimensions
            x = tf.reshape(x, shape=[-1, self._image_height, self._image_width, self._image_depth])

            # Deserialize the label
            y = loaders.label_string_to_tensor(y, self._batch_size)
            vec_size = 1 + self._NUM_CLASSES + 4
            y = tf.reshape(y, [self._batch_size, self._grid_w * self._grid_h, vec_size])

            # Run the network operations
            if self._has_moderation:
                xx = self.forward_pass(x, deterministic=False, moderation_features=mod_w)
            else:
                xx = self.forward_pass(x, deterministic=False)

            # Define regularization cost
            if self._reg_coeff is not None:
                l2_cost = tf.squeeze(tf.reduce_sum(
                    [layer.regularization_coefficient * tf.nn.l2_loss(layer.weights) for layer in self._layers
                     if isinstance(layer, layers.fullyConnectedLayer)]))
            else:
                l2_cost = 0.0

            # Define cost function  based on which one was selected via set_loss_function
            if self._loss_fn == 'yolo':
                yolo_loss = self._yolo_loss_function(
                    y, tf.reshape(xx, [self._batch_size,
                                       self._grid_w * self._grid_h,
                                       self._NUM_BOXES * 5 + self._NUM_CLASSES]))
            self._graph_ops['cost'] = tf.squeeze(tf.add(yolo_loss, l2_cost))

            # Identify which optimizer we are using
            if self._optimizer == 'adagrad':
                self._graph_ops['optimizer'] = tf.train.AdagradOptimizer(self._learning_rate)
                self.__log('Using Adagrad optimizer')
            elif self._optimizer == 'adadelta':
                self._graph_ops['optimizer'] = tf.train.AdadeltaOptimizer(self._learning_rate)
                self.__log('Using adadelta optimizer')
            elif self._optimizer == 'sgd':
                self._graph_ops['optimizer'] = tf.train.GradientDescentOptimizer(self._learning_rate)
                self.__log('Using SGD optimizer')
            elif self._optimizer == 'adam':
                self._graph_ops['optimizer'] = tf.train.AdamOptimizer(self._learning_rate)
                self.__log('Using Adam optimizer')
            elif self._optimizer == 'sgd_momentum':
                self._graph_ops['optimizer'] = tf.train.MomentumOptimizer(self._learning_rate, 0.9, use_nesterov=True)
                self.__log('Using SGD with momentum optimizer')
            else:
                warnings.warn('Unrecognized optimizer requested')
                exit()

            # Compute gradients, clip them, the apply the clipped gradients
            # This is broken up so that we can add gradients to tensorboard
            # need to make the 5.0 an adjustable hyperparameter
            gradients, variables = zip(*self._graph_ops['optimizer'].compute_gradients(self._graph_ops['cost']))
            gradients, global_grad_norm = tf.clip_by_global_norm(gradients, 5.0)
            self._graph_ops['optimizer'] = self._graph_ops['optimizer'].apply_gradients(zip(gradients, variables))

            # Calculate test accuracy
            if self._has_moderation:
                if self._testing:
                    x_test, self._graph_ops['y_test'], mod_w_test = tf.train.batch(
                        [self._test_images, self._test_labels, self._test_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'], mod_w_val = tf.train.batch(
                        [self._val_images, self._val_labels, self._val_moderation_features],
                        batch_size=self._batch_size,
                        num_threads=self._num_threads,
                        capacity=self._queue_capacity)
            else:
                if self._testing:
                    x_test, self._graph_ops['y_test'] = tf.train.batch([self._test_images, self._test_labels],
                                                                       batch_size=self._batch_size,
                                                                       num_threads=self._num_threads,
                                                                       capacity=self._queue_capacity)
                if self._validation:
                    x_val, self._graph_ops['y_val'] = tf.train.batch([self._val_images, self._val_labels],
                                                                     batch_size=self._batch_size,
                                                                     num_threads=self._num_threads,
                                                                     capacity=self._queue_capacity)

            vec_size = 1 + self._NUM_CLASSES + 4
            if self._testing:
                x_test = tf.reshape(x_test, shape=[-1, self._image_height, self._image_width, self._image_depth])
                self._graph_ops['y_test'] = loaders.label_string_to_tensor(self._graph_ops['y_test'],
                                                                           self._batch_size)
                self._graph_ops['y_test'] = tf.reshape(self._graph_ops['y_test'],
                                                       shape=[self._batch_size,
                                                              self._grid_w * self._grid_h,
                                                              vec_size])
            if self._validation:
                x_val = tf.reshape(x_val, shape=[-1, self._image_height, self._image_width, self._image_depth])
                self._graph_ops['y_val'] = loaders.label_string_to_tensor(self._graph_ops['y_val'],
                                                                          self._batch_size)
                self._graph_ops['y_val'] = tf.reshape(self._graph_ops['y_val'],
                                                      shape=[self._batch_size,
                                                             self._grid_w * self._grid_h,
                                                             vec_size])

            if self._has_moderation:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True,
                                                                            moderation_features=mod_w_test)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True,
                                                                           moderation_features=mod_w_val)
            else:
                if self._testing:
                    self._graph_ops['x_test_predicted'] = self.forward_pass(x_test, deterministic=True)
                if self._validation:
                    self._graph_ops['x_val_predicted'] = self.forward_pass(x_val, deterministic=True)

            # For object detection, the network outputs need to be reshaped to match y_test and y_val
            if self._testing:
                self._graph_ops['x_test_predicted'] = tf.reshape(self._graph_ops['x_test_predicted'],
                                                                 [self._batch_size,
                                                                  self._grid_w * self._grid_h,
                                                                  self._NUM_BOXES * 5 + self._NUM_CLASSES])
            if self._validation:
                self._graph_ops['x_val_predicted'] = tf.reshape(self._graph_ops['x_val_predicted'],
                                                                [self._batch_size,
                                                                 self._grid_w * self._grid_h,
                                                                 self._NUM_BOXES * 5 + self._NUM_CLASSES])

            # compute the loss and accuracy based on problem type
            if self._testing:
                if self._loss_fn == 'yolo':
                    self._graph_ops['test_losses'] = self._yolo_loss_function(self._graph_ops['y_test'],
                                                                              self._graph_ops['x_test_predicted'])
            if self._validation:
                if self._loss_fn == 'yolo':
                    self._graph_ops['val_losses'] = self._yolo_loss_function(self._graph_ops['y_val'],
                                                                             self._graph_ops['x_val_predicted'])

            # Epoch summaries for Tensorboard
            if self._tb_dir is not None:
                self.__log('Creating Tensorboard summaries...')

                # Summaries for any problem type
                tf.summary.scalar('train/loss', self._graph_ops['cost'], collections=['custom_summaries'])
                tf.summary.scalar('train/learning_rate', self._learning_rate, collections=['custom_summaries'])
                tf.summary.scalar('train/l2_loss', l2_cost, collections=['custom_summaries'])
                filter_summary = self.__get_weights_as_image(self.__first_layer().weights)
                tf.summary.image('filters/first', filter_summary, collections=['custom_summaries'])

                # Summaries specific to object detection
                tf.summary.scalar('train/yolo_loss', yolo_loss, collections=['custom_summaries'])
                if self._validation:
                    tf.summary.scalar('validation/loss', self._graph_ops['val_losses'],
                                      collections=['custom_sumamries'])

                # Summaries for each layer
                for layer in self._layers:
                    if hasattr(layer, 'name') and not isinstance(layer, layers.batchNormLayer):
                        tf.summary.histogram('weights/' + layer.name, layer.weights, collections=['custom_summaries'])
                        tf.summary.histogram('biases/' + layer.name, layer.biases, collections=['custom_summaries'])

                        # At one point the graph would hang on session.run(graph_ops['merged']) inside of begin_training
                        # and it was found that if you commented the below line then the code wouldn't hang. Never
                        # fully understood why, as it only happened if you tried running with train/test and no
                        # validation. But after adding more features and just randomly trying to uncomment the below
                        # line to see if it would work, it appears to now be working, but still don't know why...
                        tf.summary.histogram('activations/' + layer.name, layer.activations,
                                             collections=['custom_summaries'])

                # Summaries for gradients
                # we variables[index].name[:-2] because variables[index].name will have a ':0' at the end of
                # the name and tensorboard does not like this so we remove it with the [:-2]
                # We also currently seem to get None's for gradients when performing a hyperparameter search
                # and as such it is simply left out for hyper-param searches, needs to be fixed
                if not self._hyper_param_search:
                    for index, grad in enumerate(gradients):
                        tf.summary.histogram("gradients/" + variables[index].name[:-2], gradients[index],
                                             collections=['custom_summaries'])

                    tf.summary.histogram("gradient_global_norm/", global_grad_norm, collections=['custom_summaries'])

                self._graph_ops['merged'] = tf.summary.merge_all(key='custom_summaries')

    def begin_training(self, return_test_loss=False):
        """
        Initialize the network and either run training to the specified max epoch, or load trainable variables.
        The full test accuracy is calculated immediately afterward. Finally, the trainable parameters are saved and
        the session is shut down.
        Before calling this function, the images and labels should be loaded, as well as all relevant hyperparameters.
        """
        # if None in [self._train_images, self._test_images,
        #             self._train_labels, self._test_labels]:
        #     raise RuntimeError("Images and Labels need to be loaded before you can begin training. " +
        #                        "Try first using one of the methods starting with 'load_...' such as " +
        #                        "'DPPModel.load_dataset_from_directory_with_csv_labels()'")
        # if (len(self._layers) < 1):
        #     raise RuntimeError("There are no layers currently added to the model when trying to begin training. " +
        #                        "Add layers first by using functions such as 'DPPModel.add_input_layer()' or " +
        #                        "'DPPModel.add_convolutional_layer()'. See documentation for a complete list of
        #                        layers.")

        with self._graph.as_default():
            self.__assemble_graph()
            print('assembled the graph')

            # Either load the network parameters from a checkpoint file or start training
            if self._load_from_saved is not False:
                self.load_state()

                self.__initialize_queue_runners()

                self.compute_full_test_accuracy()

                self.shut_down()
            else:
                if self._tb_dir is not None:
                    train_writer = tf.summary.FileWriter(self._tb_dir, self._session.graph)

                self.__log('Initializing parameters...')
                init_op = tf.global_variables_initializer()
                self._session.run(init_op)

                self.__initialize_queue_runners()

                self.__log('Beginning training...')

                self.__set_learning_rate()

                # Needed for batch norm
                update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
                self._graph_ops['optimizer'] = tf.group([self._graph_ops['optimizer'], update_ops])

                # for i in range(self._maximum_training_batches):
                tqdm_range = tqdm(range(self._maximum_training_batches))
                for i in tqdm_range:
                    start_time = time.time()

                    self._global_epoch = i
                    self._session.run(self._graph_ops['optimizer'])
                    if self._global_epoch > 0 and self._global_epoch % self._report_rate == 0:
                        elapsed = time.time() - start_time

                        if self._tb_dir is not None:
                            summary = self._session.run(self._graph_ops['merged'])
                            train_writer.add_summary(summary, i)
                        if self._validation:
                            loss, epoch_test_loss = self._session.run([self._graph_ops['cost'],
                                                                       self._graph_ops['val_cost']])

                            samples_per_sec = self._batch_size / elapsed

                            desc_str = "{}: Results for batch {} (epoch {:.1f}) - Loss: {}, samples/sec: {:.2f}"
                            tqdm_range.set_description(
                                desc_str.format(datetime.datetime.now().strftime("%I:%M%p"),
                                                i,
                                                i / (self._total_training_samples / self._batch_size),
                                                loss,
                                                samples_per_sec))

                        else:
                            loss = self._session.run([self._graph_ops['cost']])

                            samples_per_sec = self._batch_size / elapsed

                            desc_str = "{}: Results for batch {} (epoch {:.1f}) - Loss: {}, samples/sec: {:.2f}"
                            tqdm_range.set_description(
                                desc_str.format(datetime.datetime.now().strftime("%I:%M%p"),
                                                i,
                                                i / (self._total_training_samples / self._batch_size),
                                                loss,
                                                samples_per_sec))

                        if self._save_checkpoints and self._global_epoch % (self._report_rate * 100) == 0:
                            self.save_state(self._save_dir)
                    else:
                        loss = self._session.run([self._graph_ops['cost']])

                    if loss == 0.0:
                        self.__log('Stopping due to zero loss')
                        break

                    if i == self._maximum_training_batches - 1:
                        self.__log('Stopping due to maximum epochs')

                self.save_state(self._save_dir)

                final_test_loss = None
                if self._testing:
                    final_test_loss = self.compute_full_test_accuracy()

                self.shut_down()

                if return_test_loss:
                    return final_test_loss
                else:
                    return
