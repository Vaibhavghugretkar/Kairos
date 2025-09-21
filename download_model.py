# download_model.py

from huggingface_hub import snapshot_download
import os

repo_id = "Aryan-2511/gemma-legal-assistant"
local_dir = "./gemma-local-model"

os.makedirs(local_dir, exist_ok=True)

print(f"Downloading model files from '{repo_id}' to '{local_dir}'...")

snapshot_download(
    repo_id=repo_id,
    local_dir=local_dir,
    local_dir_use_symlinks=False 
)

print(f"✅ Download complete. Model is saved in the '{local_dir}' folder.")