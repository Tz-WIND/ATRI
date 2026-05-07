"""Import all stages so they get registered via @register_stage."""

from .preprocess import PreProcessStage as PreProcessStage
from .process import ProcessStage as ProcessStage
from .respond import RespondStage as RespondStage
from .waking import WakingCheckStage as WakingCheckStage
