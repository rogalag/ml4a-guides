import os
from random import random, sample
import argparse
from tqdm import tqdm
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw
from imutils import video
import cv2

import hed_processing
from processing import *
from face_processing import *


allowable_actions = ['none', 'quantize', 'trace', 'hed', 'segment', 'simplify', 'face']


# input, output
parser = argparse.ArgumentParser()
parser.add_argument("--input_src", help="input: directory of input images or movie file")
parser.add_argument("--max_num_images", type=int, help="maximum number of images to take (omit to use all)", default=None)
parser.add_argument("--shuffle", action="store_true", help="shuffle input images")
parser.add_argument("--min_dim", type=int, help="minimum width/height to allow for images", default=0)
parser.add_argument("--output_dir", help="where to put output images")
parser.add_argument("--pct_test", type=float, help="percentage that goes to test set (default 0)", default=0)
parser.add_argument("--save_mode", help="save output combined (pix2pix-style), split into directories, or just output", choices=['split','combined','output_only'], default='output_only')
parser.add_argument("--save_ext", help="image save extension (jpg/png)", choices=['jpg','png'], default='png')

# augmentation
parser.add_argument("--w", type=int, help="output image width", default=256)
parser.add_argument("--h", type=int, help="output image height", default=256)
parser.add_argument("--num_per", type=int, help="how many copies of original, augmented", default=1)
parser.add_argument("--frac", type=float, help="cropping ratio before resizing", default=1.0)
parser.add_argument("--frac_vary", type=float, help="cropping ratio vary", default=0.0)
parser.add_argument("--max_ang_rot", type=float, help="max rotation angle (degrees)", default=0)
parser.add_argument("--max_stretch", type=float, help="maximum stretching factor (0=none)", default=0)
parser.add_argument("--centered", action="store_true", help="to use centered crops instead of random ones")
    
# actions
parser.add_argument("--action", type=str, help="comma-separated: lis of actions from {%s} to take, e.g. trace,hed" % ','.join(allowable_actions), required=True, default="")
parser.add_argument("--target_face_image", type=str, help="image of target face to extract (if None, extract first found one)", default=None)
parser.add_argument("--face_crop", type=float, help="crop around target face first, with face fitting this fraction of the crop (default None, don't crop)", default=None)
parser.add_argument("--face_crop_lerp", type=float, help="smoothing parameter for shifting around lerp (default 1, no lerp)", default=1.0)
               
# data files
parser.add_argument("--hed_model_path", type=str, default='../data/HED_reproduced.npz', help="model path for HED")
parser.add_argument("--landmarks_path", type=str, default='../data/shape_predictor_68_face_landmarks.dat', help="path to face landmarks file")



def try_make_dir(new_dir):
    if not os.path.isdir(new_dir):
        os.mkdir(new_dir)


def setup_output_dirs(output_dir, save_mode, include_test):
    train_dir = os.path.join(output_dir, 'train')
    test_dir = os.path.join(output_dir, 'test')
    trainA_dir, trainB_dir, testA_dir, testB_dir = None, None, None, None

    if include_test:
        if save_mode == 'split':
            trainA_dir = os.path.join(train_dir, 'train_A')
            testA_dir = os.path.join(test_dir, 'test_A')
            trainB_dir = os.path.join(train_dir, 'train_B')
            testB_dir = os.path.join(test_dir, 'test_B')
        else:
            trainA_dir = train_dir
            testA_dir = test_dir
            trainB_dir = train_dir
            testB_dir = test_dir

    elif save_mode == 'split':
        train_dir = output_dir
        trainA_dir = os.path.join(output_dir, 'train_A')
        trainB_dir = os.path.join(output_dir, 'train_B')
    
    else:
        train_dir = output_dir
        trainA_dir = output_dir
        trainB_dir = output_dir

    try_make_dir(output_dir)   

    try_make_dir(train_dir)
    try_make_dir(trainA_dir)
    try_make_dir(trainB_dir)

    if include_test:
        try_make_dir(test_dir)
        try_make_dir(testA_dir)
        try_make_dir(testB_dir)
    
    return trainA_dir, trainB_dir, testA_dir, testB_dir



def get_frame_indexes(max_num_images, num_images, shuffle):
    num_samples = min(max_num_images if max_num_images is not None else 1e8, num_images)
    sort_order = sample(range(num_images), num_samples) if shuffle else sorted(range(num_samples))
    return sort_order



def augmentation(img, num_per, out_w, out_h, frac, frac_vary, max_ang_rot, max_stretch, centered):
    imgs = []
    for i in range(num_per):
        ang = max_ang_rot * (-1.0 + 2.0 * random())
        frac_amt = frac + frac_vary * (-1.0 + 2.0 * random())
        stretch = max_stretch * (-1.0 + 2.0 * random())
        newimg = crop_rot_resize(img, frac_amt, out_w, out_h, ang, stretch, centered)
        imgs.append(newimg)
    return imgs



def main(args):
    input_src, shuffle, max_num_images, min_w, min_h = args.input_src, args.shuffle, args.max_num_images, args.min_dim, args.min_dim
    output_dir, out_w, out_h, pct_test, save_mode, save_ext = args.output_dir, args.w, args.h, args.pct_test, args.save_mode, args.save_ext
    num_per, frac, frac_vary, max_ang_rot, max_stretch, centered = args.num_per, args.frac, args.frac_vary, args.max_ang_rot, args.max_stretch, args.centered
    action, target_face_image, face_crop, face_crop_lerp, landmarks_path, hed_model_path = args.action, args.target_face_image, args.face_crop, args.face_crop_lerp, args.landmarks_path, args.hed_model_path
    
    os.system('rm -rf %s'%output_dir)
    
    # get list of actions
    actions = action.split(',')
    if False in [a in allowable_actions for a in actions]:
        raise Exception('one of your actions does not exist')

    # initialize face_processing if needed
    if 'face' in actions:
        initialize_face_processing(landmarks_path)
        target_encodings = get_encodings(target_face_image) if target_face_image else None

    # setup output directories
    trainA_dir, trainB_dir, testA_dir, testB_dir = setup_output_dirs(output_dir, save_mode, pct_test>0) 

    # initialize input 
    ext = os.path.splitext(input_src)[1]
    is_movie = ext.lower() in ['.mp4','.mov','.avi']
    if is_movie:
        cap = cv2.VideoCapture(input_src)
        fps = video.FPS().start()
        num_images = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        pct_frames = list(np.linspace(0, 1, num_images))
        all_frames = get_frame_indexes(max_num_images, num_images, shuffle)

    else:
        images = [f for f in os.listdir(input_src) if os.path.isfile(os.path.join(input_src, f)) ]
        num_images = len(images)
        all_frames = get_frame_indexes(max_num_images, num_images, shuffle)

    # training/test split
    training = [1] * len(all_frames) * num_per
    if pct_test > 0:
        n_test = int(len(all_frames) * num_per * pct_test)
        test_per = 1.0 / pct_test
        test_idx = [int(test_per * (i+1) - 1) for i in range(n_test)]
        for t in test_idx:
            training[t] = 0

    # iterate through each input
    for idx_frame in tqdm(all_frames):
        
        if is_movie:
            pct_frame = pct_frames[idx_frame]
            frame = int(pct_frame * num_images)
            cap.set(1, frame);
            ret, img = cap.read()
            frame_name = 'frame%06d' % frame
            img = cv2pil(img)
        else:
            img_path = images[idx_frame]
            frame_name = os.path.splitext(img_path)[0]
            img = Image.open(os.path.join(input_src, img_path)).convert("RGB")

        # skip images which are too small
        if img.width < min_w or img.height < min_h:
            continue

        # first crop around face if requested
        if face_crop is not None:
            jx, jy, jw, jh = get_crop_around_face(img, target_encodings, out_w/out_h, face_crop, face_crop_lerp)
            img = img.crop((jx, jy, jx + jw, jy + jh))

        # preprocess/augment and produce input images
        imgs0, imgs1 = augmentation(img, num_per, out_w, out_h, frac, frac_vary, max_ang_rot, max_stretch, centered), []
        
        # process each input image to make output
        for img0 in imgs0:
            img = img0
            for a in actions:
                if a == 'segment':
                    img = segment(img)
                elif a == 'colorize':
                    colors = [[255,255,255], [0,0,0], [127,0,0], [0, 0, 127], [0, 127, 0]]
                    img = quantize_colors(img, colors)
                elif a == 'trace':
                    img = trace(img)
                elif a == 'hed':
                    img = hed_processing.run_hed(img, hed_model_path)
                elif a == 'simplify':
                    img = simplify(img, hed_model_path)
                elif a == 'face':
                    img = extract_face(img, target_encodings)
                elif a == 'none' or a == '':
                    pass
            imgs1.append(img)
        
        # save the images
        for i, (img0, img1) in enumerate(zip(imgs0, imgs1)):
            out_name = 'f%05d%s_%s.%s' % (idx_frame, '_%02d'%i if num_per>1 else '', frame_name, save_ext)
            is_train = training[num_per * idx_frame + i]

            if save_mode == 'combined':
                output_dir = trainA_dir if is_train else testA_dir
                img2 = Image.new('RGB', (out_w * 2, out_h))     
                img2.paste(img1.convert('RGB'), (0, 0))
                img2.paste(img0.convert('RGB'), (out_w, 0))
                img2.save(os.path.join(output_dir, out_name))
                
            else:
                outputA_dir = trainA_dir if is_train else testA_dir
                img1.convert('RGB').save(os.path.join(outputA_dir, out_name))
                if save_mode == 'split':
                    outputB_dir = trainB_dir if is_train else testB_dir
                    img0.convert('RGB').save(os.path.join(outputB_dir, out_name))
            
            #plt.figure(figsize=(20,10))
            #plt.imshow(np.concatenate([img0, img1], axis=1))


if __name__ == '__main__':
    args = parser.parse_args()
    main(args)
