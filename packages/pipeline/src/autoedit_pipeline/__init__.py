"""AutoEdit analysis pipeline."""

import os as _os

# Keep MediaPipe / TFLite native logging quiet unless the operator asks for it.
_os.environ.setdefault("GLOG_minloglevel", "2")
_os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

__version__ = "0.1.0"
