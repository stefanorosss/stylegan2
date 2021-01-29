# Copyright (c) 2019, NVIDIA Corporation. All rights reserved.
# This work is made available under the Nvidia Source Code License-NC
# https://nvlabs.github.io/stylegan2/license.html
"""Tool for creating multi-resolution TFRecords datasets."""

import os
import sys
import argparse
import numpy as np
import PIL.Image
from turbojpeg import TurboJPEG
jpeg = TurboJPEG()

import tensorflow; tf = tensorflow.compat.v1 if hasattr(tensorflow.compat, 'v1') else tensorflow

sys.path.append(os.path.dirname(os.path.dirname(__file__))) # upper dir

import dnnlib

try: # progress bar for notebooks 
    get_ipython().__class__.__name__
    from util.progress_bar import ProgressIPy as ProgressBar
except: # normal console
    from util.progress_bar import ProgressBar

class TFRecordExporter:
    def __init__(self, data_dir, expected_images, print_progress=False, progress_interval=10):
        # self.tfr_prefix = os.path.join(self.data_dir, os.path.basename(self.data_dir))
        self.tfr_prefix = os.path.splitext(data_dir)[0]
        self.expected_images = expected_images
        self.cur_images = 0
        self.shape = None
        self.res_log2 = None
        self.tfr_writer = None
        self.print_progress = print_progress
        self.progress_interval = progress_interval

        if self.print_progress:
            print('Creating dataset "%s"' % data_dir)

    def close(self):
        if self.print_progress:
            print('%-40s\r' % 'Flushing data...', end='', flush=True)
        if self.tfr_writer is not None:
            self.tfr_writer.close()
        if self.print_progress:
            print('%-40s\r' % '', end='', flush=True)
            print('Added %d images.' % self.cur_images)

    def choose_shuffled_order(self):  # Note: Images and labels must be added in shuffled order.
        order = np.arange(self.expected_images)
        np.random.RandomState(123).shuffle(order)
        return order

    def set_shape(self, shape):
        self.shape = shape # [c,h,w]
        assert self.shape[0] in [1,3,4]
        tfr_opt = tf.python_io.TFRecordOptions(tf.python_io.TFRecordCompressionType.NONE)
        self.tfr_file = self.tfr_prefix + '-%dx%d.tfr' % (self.shape[2], self.shape[1])
        self.tfr_writer = tf.python_io.TFRecordWriter(self.tfr_file, tfr_opt)

    def add_image(self, img_path, jpg=False, size=None):
        if self.print_progress and self.cur_images % self.progress_interval == 0:
            print('%d / %d\r' % (self.cur_images, self.expected_images), end='', flush=True)

        def get_img(img_path):
            img = np.asarray(PIL.Image.open(img_path))
            if img.shape[2] == 1: # monochrome
                img = img[:, :, np.newaxis] # HW => HWC
            return img.transpose([2,0,1]) # HWC => CHW

        if self.shape is None:
            img = get_img(img_path)
            self.set_shape(img.shape)
            assert img.shape == self.shape
        
        if jpg is True:
            with tf.gfile.GFile(img_path, 'rb') as jpg_file:
                raw_jpg = jpg_file.read()
                (width, height, jpeg_subsample, jpeg_colorspace) = jpeg.decode_header(raw_jpg)
                jpg_shape = [self.shape[0], height, width]
                ex = tf.train.Example(features = tf.train.Features(feature={
                    "shape": tf.train.Feature(int64_list=tf.train.Int64List(value = jpg_shape)),
                    "data":  tf.train.Feature(bytes_list=tf.train.BytesList(value = [raw_jpg]))}))
                self.tfr_writer.write(ex.SerializeToString())
        else:
            img = get_img(img_path)
            if size is not None:
                img = img.resize(size, PIL.Image.ANTIALIAS)
            assert img.shape == self.shape, ' Image %s has shape %s (must be %s)' % (os.path.basename(img_path), str(img.shape), str(self.shape))
            quant = np.rint(img).clip(0, 255).astype(np.uint8)
            ex = tf.train.Example(features=tf.train.Features(feature={
                'shape': tf.train.Feature(int64_list=tf.train.Int64List(value = quant.shape)),
                'data': tf.train.Feature(bytes_list=tf.train.BytesList(value = [quant.tostring()]))}))
            self.tfr_writer.write(ex.SerializeToString())

        self.cur_images += 1

    def add_labels(self, labels):
        if self.print_progress:
            print('%-40s\r' % 'Saving labels...', end='', flush=True)
        assert labels.shape[0] == self.cur_images
        with open(self.tfr_prefix + '-rxx.labels', 'wb') as f:
            np.save(f, labels.astype(np.int32))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

def img_list(path):
    files = [os.path.join(path, f) for f in os.listdir(path)]
    files = [f for f in files if os.path.splitext(f.lower())[1][1:] in ['jpg', 'jpeg', 'png', 'ppm', 'tif']]
    return sorted([f for f in files if os.path.isfile(f)])

def create_from_images(datadir, jpg=False, shuffle=True, size=None):
    assert os.path.isdir(datadir)
    imgs = sorted(img_list(datadir))
    assert len(imgs) > 0, ' No input images found!'

    sample_img = np.asarray(PIL.Image.open(imgs[0]))
    sample_shape = sample_img.shape
    channels = sample_shape[2] if sample_img.ndim == 3 else 1
    assert channels in [1,3,4], ' Weird color dim: %d' % channels
    print(' Making dataset ..', datadir, sample_shape)
    if jpg is True: print(' Loading JPG as is!')

    with TFRecordExporter(datadir, len(imgs)) as tfr:
        order = tfr.choose_shuffled_order() if shuffle else np.arange(len(imgs))
        pbar = ProgressBar(order.size)
        for idx in range(order.size):
            img_path = imgs[order[idx]]
            tfr.add_image(img_path, jpg=jpg, size=size)
            pbar.upd()
    return tfr.tfr_file, len(imgs)

def create_from_image_folders(datadir, jpg=False, shuffle=True, size=None):
    assert os.path.isdir(datadir)
    imgs = []
    labels = []
    for root, subdirs, files in os.walk(datadir):
        for i, subdir in enumerate(subdirs):
            tmp_list = img_list(os.path.join(root, subdir))
            imgs = imgs + tmp_list
            labels = labels + [i] * len(tmp_list)
    labels = np.array(labels)  
    onehot = np.zeros((labels.size, np.max(labels) + 1), dtype=np.float32)
    onehot[np.arange(labels.size), labels] = 1.

    assert len(imgs) > 0, ' No input images found!'
    sample_img = np.asarray(PIL.Image.open(imgs[0]))
    sample_shape = sample_img.shape
    channels = sample_shape[2] if sample_img.ndim == 3 else 1
    assert channels in [1,3,4], ' Weird color dim: %d' % channels
    print(' Making dataset ..', datadir, sample_shape, '%d labels' % (np.max(labels)+1))
    if jpg is True: print(' Loading JPG as is!')

    with TFRecordExporter(datadir, len(imgs)) as tfr:
        order = tfr.choose_shuffled_order() if shuffle else np.arange(len(imgs))
        pbar = ProgressBar(order.size)
        for idx in range(order.size):
            img_path = imgs[order[idx]]
            tfr.add_image(img_path, jpg=jpg, size=size)
            pbar.upd()
        tfr.add_labels(onehot[order])
    return tfr.tfr_file, len(imgs)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', required=True, help='Directory containing the images')
    parser.add_argument('--shuffle', type=bool, default=True, help='Randomize image order (default: 1)')
    parser.add_argument('--jpg', action='store_true', help='save as jpg directly from file')
    parser.add_argument('--labels', action='store_true', help='use folders to generate labels')
    args = parser.parse_args()

    if args.labels is True:
        create_from_image_folders(args.dataset, args.jpg, args.shuffle)
    else:
        create_from_images(args.dataset, args.jpg, args.shuffle)

if __name__ == "__main__":
    main()

