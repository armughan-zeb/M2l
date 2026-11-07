import os
import zipfile
import shutil
from pathlib import Path

# =============================================================================
# VIDNETS STUDIO - NEW PACKAGE GENERATOR
# =============================================================================
# Creates a lightweight, garbage-free zip archive representing the pure, 
# operational state of the web app without any bloating caches, temp folders, 
# node_modules, virtual envs, or test scrap files.
# =============================================================================

ROOT_DIR = Path(__file__).parent.resolve()
OUTPUT_ZIP_NAME = "VidnetsStudio-New-Package.zip"
OUTPUT_ZIP_PATH = ROOT_DIR / OUTPUT_ZIP_NAME

# Dirs that are explicitly essential
ESSENTIAL_DIRS = {
    "server",
    "src",
    "public",
    "fonts"
}

# Explicitly essential root files needed to kickstart the program or build
ESSENTIAL_ROOT_FILES = {
    "main.py",
    "main.js",
    "preload.js",
    "package.json",
    "package-lock.json",
    "INSTALL.bat",
    "START.bat",
    "BUILD.bat",
    "README.md",
    "Vidnets Studio.spec",
    "Vidnets Studio.vbs",
    "CREATE_PACKAGE.bat",
    ".env.example"
}

# Directories to exclude fully (Garbage, Caches, Modules)
EXCLUDED_DIRS = {
    "__pycache__",
    "node_modules",
    "venv", "venv310",
    "tools",          # Tools are downloaded dynamically via INSTALL.bat
    ".git", ".vscode",
    "tmp", "temp", "cache", "logs", "output", "scratch", "voice_tests",
    "Youtube Scraper", "AudiogramTTS-Consumer",
    "_dead", "dist", "build"
}

def is_garbage_file(filename: str) -> bool:
    """Identify scratch files, caches, testing scripts, and other non-essential files."""
    name = filename.lower()
    if name.endswith('.pyc') or name.endswith('.log') or name.endswith('.tmp'):
        return True
    if name == OUTPUT_ZIP_NAME.lower():
        return True
    
    # We strip out debug/test scrap files created during prototyping
    if name.startswith("test_") or name.startswith("tmp_") or name.startswith("debug_"):
        return True
    if name.startswith("patch") or name.startswith("auto"):
        return True
    if name.startswith("kaggle_"):
        return True
    if name == "create_new_package.py":
        return True # exclude the generator itself or keep it? we can keep or omit. omitted for purity.
        
    return False

def create_package():
    print("Starting pure package build...")
    
    # If the zip already exists, remove it
    if OUTPUT_ZIP_PATH.exists():
        OUTPUT_ZIP_PATH.unlink()

    files_added = 0
    with zipfile.ZipFile(OUTPUT_ZIP_PATH, 'w', zipfile.ZIP_DEFLATED) as zipf:
        
        # 1. Add Essential Root Files
        for f_name in ESSENTIAL_ROOT_FILES:
            file_path = ROOT_DIR / f_name
            if file_path.exists() and file_path.is_file():
                zipf.write(file_path, f_name)
                files_added += 1
                
        # 2. Add Essential Directories deeply, skipping garbage
        for d_name in ESSENTIAL_DIRS:
            dir_path = ROOT_DIR / d_name
            if not dir_path.exists() or not dir_path.is_dir():
                continue
                
            for root, dirs, files in os.walk(dir_path):
                # Filter out excluded directories in-place so os.walk ignores them
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
                
                for file in files:
                    if is_garbage_file(file):
                        continue
                        
                    file_full_path = Path(root) / file
                    arc_name = file_full_path.relative_to(ROOT_DIR)
                    zipf.write(file_full_path, arc_name)
                    files_added += 1

        # 3. Create empty scaffold folders inside the zip to ensure it runs out of the box
        empty_scaffolds = ["output/", "temp/", "tmp/", "logs/", "tools/"]
        for scaffold in empty_scaffolds:
            zip_info = zipfile.ZipInfo(scaffold)
            zipf.writestr(zip_info, '')
            files_added += 1

    size_mb = OUTPUT_ZIP_PATH.stat().st_size / (1024 * 1024)
    print("==========================================================================")
    print(f"Pristine lightweight package created: {OUTPUT_ZIP_NAME}")
    print(f"Total files archived: {files_added}")
    print(f"File Size: {size_mb:.2f} MB")
    print("==========================================================================")

if __name__ == "__main__":
    create_package()
