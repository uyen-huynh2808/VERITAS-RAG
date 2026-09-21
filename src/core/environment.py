import os
import random
import numpy as np


def setup_environment(seed: int = 42) -> None:
    """Fix global random seed across all libraries to ensure experimental reproducibility.
    
    Args:
        seed (int): The integer random seed value. Defaults to 42.
    """
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
        # PyTorch is optional at initialization phase
        pass