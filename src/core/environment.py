import os
import random
from typing import Optional

import numpy as np

from src.core.config import SystemConfig, load_system_config


def setup_environment(
    config: Optional[SystemConfig] = None,
    seed: Optional[int] = None,
) -> int:
    """
    Configure random seeds for reproducible execution.

    Seed priority:
        explicit seed
        > config.seed
        > default configuration seed
    """

    if seed is None:
        if config is None:
            config = load_system_config()

        seed = config.seed

    random.seed(seed)

    os.environ["PYTHONHASHSEED"] = str(seed)

    np.random.seed(seed)

    try:
        import torch

        torch.manual_seed(seed)

        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False

    except ImportError:
        pass

    return seed