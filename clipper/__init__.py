"""Hypecut video pipeline."""
__version__ = "0.1.0"


def _preload_cuda_libs() -> None:
    """Preload the pip-provided CUDA/cuDNN .so files into the global namespace so
    CTranslate2 (faster-whisper) can dlopen libcublas.so.12 / libcudnn*.so.9.

    Needed because the libs live under site-packages/nvidia/*/lib (not on the
    loader path), system CUDA here is v13 (wrong major), and LD_LIBRARY_PATH set
    at runtime is ignored by an already-started process. RTLD_GLOBAL preloading
    by full path makes later dlopen-by-soname resolve to these.
    """
    import ctypes
    import glob
    import os
    import sysconfig

    site = sysconfig.get_paths()["purelib"]
    libs: list[str] = []
    # load cublasLt before cublas (cublas depends on it); cudnn last
    for sub in ("cublas", "cuda_nvrtc", "cudnn"):
        d = os.path.join(site, "nvidia", sub, "lib")
        if os.path.isdir(d):
            so = sorted(glob.glob(os.path.join(d, "*.so*")))
            so.sort(key=lambda p: ("cublasLt" not in p, "cublas" not in p))
            libs.extend(so)

    pending = list(libs)
    for _ in range(4):  # a few passes resolve inter-library dependencies
        still = []
        for so in pending:
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                still.append(so)
        if not still or still == pending:
            break
        pending = still
