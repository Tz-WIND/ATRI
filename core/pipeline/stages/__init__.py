"""Import all stages so they get registered via @register_stage."""

from .waking import WakingCheckStage
from .preprocess import PreProcessStage
from .process import ProcessStage
from .respond import RespondStage
