def is_cuda_available() -> bool:
    """
    Check whether CUDA is available for the backend runtime.

    This project uses `faster-whisper` (CTranslate2) for Whisper inference, which
    can use CUDA without requiring PyTorch.
    """
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
def is_torch_installed() -> bool:
    try:
        import torch
        return True
    except ImportError:
        return False
