"""KEMET FC AI video analysis, phase A1: person tracking, pose and image-plane body movement evidence.

This package produces EVIDENCE, never a score. Everything here is measured in the image plane (pixels of the
upright video frame). Nothing is converted to metres, m/s or a rating. It reuses the repository's existing
building blocks (pose_features angle maths and landmark map, full_match_analyzer's YOLO loader, the bundled
MediaPipe pose model and YOLO weights) instead of adding a second computer-vision stack.
"""
