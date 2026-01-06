from pathlib import Path

_DLL_DIR_HANDLES: list[object] = []


def _iter_site_package_dirs() -> list[Path]:
    try:
        import site

        candidates: list[Path] = []
        for raw in (site.getsitepackages() or []) + [site.getusersitepackages()]:
            if raw:
                candidates.append(Path(raw))
        return candidates
    except Exception:
        return []


def _add_nvidia_cuda_dll_dirs() -> None:
    """
    Add DLL search paths for NVIDIA PyPI runtime packages (Windows).

    Installing `nvidia-cudnn-cu12` / `nvidia-cublas-cu12` via pip places runtime DLLs under
    site-packages\\nvidia\\...\\bin, which are NOT automatically added to PATH.
    """

    import os

    if os.name != "nt":
        return

    try:
        add_dir = os.add_dll_directory  # type: ignore[attr-defined]
    except Exception:
        add_dir = None

    if add_dir is None:
        return

    bin_suffixes = (
        Path("nvidia/cudnn/bin"),
        Path("nvidia/cublas/bin"),
    )

    seen: set[str] = set()
    current_path = os.environ.get("PATH") or ""
    for raw in current_path.split(";"):
        p = raw.strip()
        if p:
            seen.add(p.lower())

    for base in _iter_site_package_dirs():
        # site.getsitepackages() might return the Python root; normalize to site-packages.
        site_packages = base
        if site_packages.name.lower() != "site-packages":
            candidate = site_packages / "Lib" / "site-packages"
            if candidate.exists():
                site_packages = candidate

        for suffix in bin_suffixes:
            dll_dir = (site_packages / suffix).resolve()
            if not dll_dir.exists():
                continue

            key = str(dll_dir).lower()
            if key in seen:
                continue

            try:
                handle = add_dir(str(dll_dir))
                _DLL_DIR_HANDLES.append(handle)
                os.environ["PATH"] = f"{dll_dir};{os.environ.get('PATH') or ''}"
                seen.add(key)
            except Exception:
                continue


def is_cuda_available() -> bool:
    """
    Check whether CUDA is available for the backend runtime.

    This project uses `faster-whisper` (CTranslate2) for Whisper inference, which
    can use CUDA without requiring PyTorch.
    """

    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() <= 0:
            return False
    except Exception:
        return False

    # CTranslate2's CUDA path requires cuDNN runtime libraries. On Windows these are not
    # bundled with the NVIDIA driver; missing cuDNN will hard-fail at runtime.
    try:
        import os
        import ctypes
        import ctypes.util

        if os.name == "nt":
            _add_nvidia_cuda_dll_dirs()
            cudnn = ctypes.WinDLL("cudnn_ops64_9.dll")
            # Ensure the expected symbol exists (wrong/old cuDNN on PATH can also break).
            getattr(cudnn, "cudnnCreateTensorDescriptor")
            return True

        # Non-Windows: best-effort detection via dynamic loader.
        for lib in ("cudnn_ops", "cudnn"):
            name = ctypes.util.find_library(lib)
            if not name:
                continue
            try:
                handle = ctypes.CDLL(name)
                if hasattr(handle, "cudnnCreateTensorDescriptor"):
                    return True
            except Exception:
                continue

        return False
    except Exception:
        return False


def is_torch_installed() -> bool:
    try:
        import torch

        return True
    except ImportError:
        return False

