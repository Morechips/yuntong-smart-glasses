"""Stable softmax fallback for boards without SciPy; validated separately."""
import numpy as np


def softmax(x, axis=None):
    values = np.asarray(x)
    maximum = np.max(values, axis=axis, keepdims=True)
    exponential = np.exp(values - maximum)
    return exponential / np.sum(exponential, axis=axis, keepdims=True)
