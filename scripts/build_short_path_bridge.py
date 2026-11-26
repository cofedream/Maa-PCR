"""Package the official R95→R96 patch with its offline Windows launcher."""

import argparse
import hashlib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRIES = ROOT / "scripts/v026_short_path_update"
PATCH = "Maa-PCR-v0.2.7-to-v0.2.8.patch.zip"
PATCH_SHA256 = "d4995bf0a1fc50892a982337a13c3047f225012f87731c5bf4176fd4f6820949"
BATCH = "双击更新到 v0.2.8.bat"
ARCHIVE = "Maa-PCR-v0.2.7-to-v0.2.8-双击更新.zip"
INSTRUCTIONS = """适用于官方 Maa-PCR v0.2.7 下载或导入更新后提示 unsafe_root 的情况。

1. 等任务结束，完全退出 Maa-PCR。
2. 将本工具完整解压到原安装目录以外的可写本地文件夹，保留里面的补丁 ZIP。
3. 双击“双击更新到 v0.2.8.bat”，选择原安装目录中的 MFAAvalonia.exe。
4. 更新完成后软件会自动重开。确认显示 v0.2.8，账号、设置和账本都在原目录。

更新中断时，保留解压目录和原安装目录，按软件内“恢复中断的更新”处理。
"""


def windows_text(path):
    # Windows PowerShell 5.1 requires a BOM to read Chinese UTF-8 literals.
    encoding = "utf-8-sig" if path.suffix == ".ps1" else "ascii"
    return path.read_text(encoding=encoding).replace("\n", "\r\n").encode(encoding)


def build(patch, output):
    content = patch.read_bytes()
    if hashlib.sha256(content).hexdigest() != PATCH_SHA256:
        raise ValueError("官方 v0.2.7 → v0.2.8 补丁的 SHA-256 不匹配")
    members = {
        BATCH: windows_text(ENTRIES / BATCH),
        "install.ps1": windows_text(ENTRIES / "install.ps1"),
        PATCH: content,
        "使用说明.txt": INSTRUCTIONS.replace("\n", "\r\n").encode("utf-8-sig"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        try:
            with zipfile.ZipFile(stream, "w") as bundle:
                for name, data in members.items():
                    bundle.writestr(zipfile.ZipInfo(name), data)
        except BaseException:
            stream.close()
            output.unlink()
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", type=Path, help="官方 v0.2.7 至 v0.2.8 增量 ZIP")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/bridge-r96" / ARCHIVE)
    args = parser.parse_args()
    build(args.patch, args.output)
    print(args.output)
