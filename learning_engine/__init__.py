"""KEMET AI Learning Engine V1: a local, offline-by-default research sidecar for Dribbling drills.

It finds source CANDIDATES (metadata only, from an operator-approved list of YouTube channels, through the official API),
holds optional generic AI drafts (disabled by default, no children's data ever sent), and gives coaches a pending ->
approved/rejected review. It does not train models, publish to players, download media, or read production data.
Its research database is separate from the application database and its output never enters a training dataset.
"""
