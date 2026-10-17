"""下载固定公开权重；原子替换单文件，记录 SHA256，不执行远端代码。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx

E5_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
E5_NAME = "multilingual-e5-small-" + E5_REVISION[:7]
FILES = ["model.safetensors", "config.json", "modules.json", "1_Pooling/config.json",
         "sentence_bert_config.json", "sentencepiece.bpe.model", "special_tokens_map.json",
         "tokenizer.json", "tokenizer_config.json", "README.md"]


def download(repo: str, revision: str, name: str, files: list[str], prefixes: dict) -> None:
    root = Path.home() / ".local/share/sandboxd/models" / name
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"repo": repo, "revision": revision, **prefixes, "files": {}}
    with httpx.Client(timeout=120, follow_redirects=True, trust_env=False) as client:
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
                part = path.with_suffix(path.suffix + ".partial")
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with part.open("wb") as handle:
                        for block in response.iter_bytes(1024 * 1024):
                            handle.write(block)
                part.replace(path)
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            manifest["files"][name] = {"sha256": digest, "bytes": path.stat().st_size}
            print(json.dumps({"file": name, **manifest["files"][name]}), flush=True)
    (root / "snapshot.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    download("intfloat/multilingual-e5-small", E5_REVISION, E5_NAME, FILES,
             {"license": "MIT", "queryPrefix": "query: ", "passagePrefix": "passage: "})
    download("BAAI/bge-reranker-base", "2cfc18c9415c912f9d8155881c133215df768a70",
             "bge-reranker-base-2cfc18c",
             ["model.safetensors", "config.json", "sentencepiece.bpe.model", "special_tokens_map.json",
              "tokenizer.json", "tokenizer_config.json"], {})


if __name__ == "__main__":
    main()
