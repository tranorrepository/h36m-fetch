#!/usr/bin/env python3

from os import path, makedirs, listdir
from shutil import move
from spacepy import pycdf
import numpy as np
import h5py
from subprocess import call
from tempfile import TemporaryDirectory
from itertools import product
from tqdm import tqdm


subjects = {
    'S1': 1,
    'S5': 5,
    'S6': 6,
    'S7': 7,
    'S8': 8,
    'S9': 9,
    'S11': 11,
}
actions = {
    'Directions': 1,
    'Discussion': 2,
    'Eating': 3,
    'Greeting': 4,
    'Phoning': 5,
    'Posing': 6,
    'Purchases': 7,
    'Sitting': 8,
    'SittingDown': 9,
    'Smoking': 10,
    'TakingPhoto': 11,
    'Waiting': 12,
    'Walking': 13,
    'WalkingDog': 14,
    'WalkTogether': 15,
}
cameras = {
    '54138969': 0,
    '55011271': 1,
    '58860488': 2,
    '60457274': 3,
}


class MissingDataException(Exception):
    pass


def select_frame_indices_to_include(subject, poses_3d):
    # To process every single frame, uncomment the following line:
    # return np.arange(0, len(poses_3d))

    # Take every 64th frame for the protocol #2 test subjects
    # (see the "Compositional Human Pose Regression" paper)
    if subject == 'S9' or subject == 'S11':
        return np.arange(0, len(poses_3d), 64)

    # Take only frames where movement has occurred for the protocol #2 train subjects
    frame_indices = []
    prev_joints3d = None
    threshold = 40 ** 2  # Skip frames until at least one joint has moved by 40mm
    for i, joints3d in enumerate(poses_3d):
        if prev_joints3d is not None:
            max_move = ((joints3d - prev_joints3d) ** 2).sum(axis=-1).max()
            if max_move < threshold:
                continue
        prev_joints3d = joints3d
        frame_indices.append(i)
    return np.array(frame_indices)


def process_view(subject, action, camera):
    subj_dir = path.join('extracted', subject)
    act_cam = '.'.join([action, camera])

    file_found = False
    for i in range(1, 100):
        if path.isfile(path.join(subj_dir, 'Poses_D2_Positions', act_cam + '.cdf')):
            file_found = True
            break
        act_cam = '{} {:d}.{}'.format(action, i, camera)

    # Workaround for corrupt video file
    if subject == 'S11' and action == 'Directions' and camera == '54138969':
        act_cam = 'Directions 1.54138969'

    if not file_found:
        raise MissingDataException('missing data for {}/{}.{}'.format(subject, action, camera))

    with pycdf.CDF(path.join(subj_dir, 'Poses_D2_Positions', act_cam + '.cdf')) as cdf:
        poses_2d = np.array(cdf['Pose'])
        poses_2d = poses_2d.reshape(poses_2d.shape[1], 32, 2)
    with pycdf.CDF(path.join(subj_dir, 'Poses_D3_Positions_mono_universal', act_cam + '.cdf')) as cdf:
        poses_3d = np.array(cdf['Pose'])
        poses_3d = poses_3d.reshape(poses_3d.shape[1], 32, 3)

    # Infer camera intrinsics
    pose2d = poses_2d.reshape(len(poses_2d) * 32, 2)
    pose3d = poses_3d.reshape(len(poses_3d) * 32, 3)
    x3d = np.stack([pose3d[:, 0], pose3d[:, 2]], axis=-1)
    x2d = (pose2d[:, 0] * pose3d[:, 2])
    alpha_x, x_0 = list(np.linalg.lstsq(x3d, x2d, rcond=-1)[0].flatten())
    y3d = np.stack([pose3d[:, 1], pose3d[:, 2]], axis=-1)
    y2d = (pose2d[:, 1] * pose3d[:, 2])
    alpha_y, y_0 = list(np.linalg.lstsq(y3d, y2d, rcond=-1)[0].flatten())

    frame_indices = select_frame_indices_to_include(subject, poses_3d)
    frames = frame_indices + 1
    video_file = path.join(subj_dir, 'Videos', act_cam + '.mp4')
    frames_dir = path.join('processed', subject, action, 'imageSequence', camera)
    makedirs(frames_dir, exist_ok=True)

    existing_files = {f for f in listdir(frames_dir)}
    skip = True
    for i in frames:
        filename = 'img_%06d.jpg' % i
        if filename not in existing_files:
            skip = False
            break

    if not skip:
        with TemporaryDirectory() as tmp_dir:
            call([
                'ffmpeg',
                '-nostats', '-loglevel', '0',
                '-i', video_file,
                '-qscale:v', '3',
                path.join(tmp_dir, 'img_%06d.jpg')
            ])

            for i in frames:
                filename = 'img_%06d.jpg' % i
                move(
                    path.join(tmp_dir, filename),
                    path.join(frames_dir, filename)
                )

    return {
        'pose/2d': poses_2d[frame_indices],
        'pose/3d-univ': poses_3d[frame_indices],
        'intrinsics/' + camera: np.array([alpha_x, x_0, alpha_y, y_0]),
        'frame': frames,
        'camera': np.full(frames.shape, int(camera)),
        'subject': np.full(frames.shape, subjects[subject]),
        'action': np.full(frames.shape, actions[action]),
    }


def process_sequence(subject, action):
    datasets = {}

    for camera in tqdm(list(sorted(cameras.keys())), ascii=True, leave=False):
        try:
            annots = process_view(subject, action, camera)
        except MissingDataException as ex:
            print(str(ex))
            continue
        for k, v in annots.items():
            if k in datasets:
                datasets[k].append(v)
            else:
                datasets[k] = [v]

    if len(datasets) == 0:
        return

    datasets = {k: np.concatenate(v) for k, v in datasets.items()}

    out_dir = path.join('processed', subject, action)
    makedirs(out_dir, exist_ok=True)
    with h5py.File(path.join(out_dir, 'annot.h5'), 'w') as f:
        for name, data in datasets.items():
            f.create_dataset(name, data=data)


def process_all():
    for subject, action in tqdm(list(product(subjects.keys(), actions.keys())), ascii=True, leave=False):
        process_sequence(subject, action)


if __name__ == '__main__':
  process_all()
