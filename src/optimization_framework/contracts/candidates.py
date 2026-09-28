"""Compatibility conversion for binary ask/tell implementations."""
import numpy as np

from .problems import CandidateSchema


def validate_design(design, n_cells):
    return np.asarray(CandidateSchema(representation="binary", dimensions=n_cells).canonicalize(design), dtype=np.uint8)
