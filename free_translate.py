"""Offline English-to-Chinese translation; installation needs Internet access, translation uses no cloud API."""
from functools import lru_cache
import os
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "private" / "argos-models"


def configure():
    # Use certifi for macOS Python installations without configured CA certificates; keep TLS verification enabled.
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ["ARGOS_PACKAGES_DIR"] = str(MODEL_DIR)
    os.environ["ARGOS_MODEL_PROVIDER"] = "OPENNMT"


def install():
    configure()
    try:
        import argostranslate.package as package
    except ImportError:
        raise RuntimeError("First run .venv/bin/python -m pip install -r requirements-free.txt")
    if any(p.from_code == "en" and p.to_code == "zh" for p in package.get_installed_packages()):
        print("The English-to-Chinese model is already installed.")
        return
    print("Downloading the free English-to-Chinese model; initial setup requires Internet access.", flush=True)
    package.update_package_index()
    available = [p for p in package.get_available_packages() if p.from_code == "en" and p.to_code == "zh"]
    if not available:
        raise RuntimeError("No English-to-Chinese model is available in the index. Try again later.")
    package.install_from_path(available[0].download())
    print("Model installed in private/argos-models.", flush=True)


@lru_cache(maxsize=2)
def engine(allow_download=False):
    configure()
    try:
        import argostranslate.package as package
        import argostranslate.translate as translator
    except ImportError:
        raise RuntimeError("First run .venv/bin/python -m pip install -r requirements-free.txt")
    if not any(p.from_code == "en" and p.to_code == "zh" for p in package.get_installed_packages()):
        raise RuntimeError("Run python mail_translator.py --setup-free to install the free translation model first.")
    translation = translator.get_translation_from_codes("en", "zh")
    # The Argos Stanza sentencizer normally checks its online resource index on startup.
    # Disable downloads during translation; only --setup-free may prepare model resources.
    from argostranslate.sbd import StanzaSentencizer
    packaged = getattr(translation, "underlying", translation)
    sentencizer = getattr(packaged, "sentencizer", None)
    if isinstance(sentencizer, StanzaSentencizer):
        import stanza
        try:
            sentencizer.stanza_pipeline = stanza.Pipeline(
                lang=sentencizer.stanza_lang_code,
                dir=str(sentencizer.pkg.package_path / "stanza"),
                processors="tokenize", use_gpu=False, logging_level="ERROR",
                download_method="REUSE_RESOURCES" if allow_download else None,
            )
        except Exception as exc:
            raise RuntimeError("The local sentence model is not ready. Run --setup-free with Internet access to complete setup.") from exc
    return translation


def verify_installation():
    return engine(allow_download=True).translate("Please submit your course registration by Friday at 5 pm.")


def translate(text):
    return engine().translate(text)
