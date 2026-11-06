#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虎牙直播 PC 客户端 —— 去广告补丁器

本脚本不包含任何原程序文件，只按 patches.json 里的规则对已安装客户端做
等长字节替换，并同步修复 Electron 的 ASAR 完整性校验。

用法：
    python patch_asar.py            自动定位客户端并打补丁
    python patch_asar.py --restore  从备份还原
    python patch_asar.py --check    只检查，不写入
    python patch_asar.py --dir <Client\\External 路径>

原理与校验口径见 docs/TECH.md。
"""
import os
import sys
import json
import shutil
import hashlib
import struct
import argparse
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
HEADER_BLOCK = 4 * 1024 * 1024  # @electron/asar 默认块大小
BACKUP_DIRNAME = "_noad_backup"


# ----------------------------------------------------------------- asar 读写
class Asar:
    """只读解析 + 原地改写，不重建归档，保证所有偏移不变。"""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        if struct.unpack("<I", self.data[0:4])[0] != 4:
            raise SystemExit("不是有效的 asar 文件")
        pickle_size = struct.unpack("<I", self.data[4:8])[0]
        self.str_size = struct.unpack("<I", self.data[12:16])[0]
        self.header = json.loads(self.data[16:16 + self.str_size].decode("utf-8"))
        self.base_offset = 8 + pickle_size

    @staticmethod
    def _walk(node, prefix=""):
        for name, meta in node.items():
            path = prefix + "/" + name if prefix else name
            if "files" in meta:
                yield from Asar._walk(meta["files"], path)
            else:
                yield path, meta

    def find(self, keyword):
        for path, meta in self._walk(self.header["files"]):
            if "size" in meta and keyword in path and path.endswith(".js"):
                return path
        return None

    def node(self, path):
        node = self.header["files"]
        for part in [p for p in path.split("/") if p]:
            node = node[part]
            if "files" in node:
                node = node["files"]
        return node

    def read(self, path):
        meta = self.node(path)
        off = self.base_offset + int(meta["offset"])
        return self.data[off:off + meta["size"]]

    def offset(self, path):
        meta = self.node(path)
        return self.base_offset + int(meta["offset"]), meta["size"]


def hash_blocks(buf, block=HEADER_BLOCK):
    blocks = [hashlib.sha256(buf[i:i + block]).hexdigest()
              for i in range(0, len(buf), block)]
    return hashlib.sha256(buf).hexdigest(), blocks


# ----------------------------------------------------------------- 客户端定位
def find_client_dir(explicit=None):
    if explicit:
        return explicit
    base = os.path.join(os.environ["APPDATA"], "HuyaPCLive", "HuyaUpdate")
    if not os.path.isdir(base):
        raise SystemExit("未找到虎牙客户端目录，请用 --dir 指定")
    candidates = []
    for name in os.listdir(base):
        ext = os.path.join(base, name, "Client", "External")
        if os.path.isfile(os.path.join(ext, "huyapc.exe")):
            candidates.append((name, ext))
    if not candidates:
        raise SystemExit("未找到可用的客户端版本目录")
    candidates.sort(reverse=True)
    return candidates[0][1]


# ----------------------------------------------------------------- 主流程
def load_rules():
    with open(os.path.join(HERE, "patches.json"), encoding="utf-8") as f:
        return json.load(f)


def restore(client_dir):
    backup = os.path.join(client_dir, BACKUP_DIRNAME)
    if not os.path.isdir(backup):
        raise SystemExit("未找到备份目录: " + backup)
    shutil.copy2(os.path.join(backup, "app.asar"),
                 os.path.join(client_dir, "resources", "app.asar"))
    shutil.copy2(os.path.join(backup, "huyapc.exe"),
                 os.path.join(client_dir, "huyapc.exe"))
    print("[✓] 已还原原始文件，重新启动虎牙即可")


def syntax_ok(js_bytes, workdir):
    """有 node 就做一次语法校验；没有则跳过（不影响打补丁）。"""
    try:
        tmp = os.path.join(workdir, "_syntax_check.js")
        with open(tmp, "wb") as f:
            f.write(js_bytes)
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        os.remove(tmp)
        if r.returncode != 0:
            return False, (r.stderr or "")[:800]
        return True, ""
    except FileNotFoundError:
        return True, "node 不可用，跳过语法校验"


def apply_patch(client_dir, rules, check_only=False):
    asar_path = os.path.join(client_dir, "resources", "app.asar")
    exe_path = os.path.join(client_dir, "huyapc.exe")
    for p in (asar_path, exe_path):
        if not os.path.isfile(p):
            raise SystemExit("缺少文件: " + p)

    asar = Asar(asar_path)
    header = bytearray(asar.data[16:16 + asar.str_size])
    new_data = bytearray(asar.data)
    shorten = {k: v for k, v in rules.get("shorten", {}).items() if not k.startswith("$")}
    changed = []

    for tgt in rules["patchTargets"]:
        path = asar.find(tgt["file"])
        if not path:
            raise SystemExit("[%s] 未找到目标文件（%s）" % (tgt["id"], tgt["file"]))
        original = asar.read(path)
        text = original.decode("utf-8")

        for rule in tgt["replace"]:
            hits = text.count(rule["from"])
            if hits < 1:
                raise SystemExit("[%s] 在 %s 中未匹配到特征片段，可能是新版本，已中止"
                                 % (tgt["id"], os.path.basename(path)))
            text = text.replace(rule["from"], rule["to"])
            print("[%s] %-22s 替换 %d 处" % (tgt["id"], tgt["name"], hits))

        if tgt.get("compress"):
            for o, n in shorten.items():
                text = text.replace(o, n)

        delta = len(text.encode("utf-8")) - len(original)
        if delta > 0:
            raise SystemExit("[%s] 补丁后变长 %d 字节，会破坏归档偏移，已中止"
                             % (tgt["id"], delta))
        if delta < 0:
            text += " " * (-delta)
        patched = text.encode("utf-8")
        if len(patched) != len(original):
            raise SystemExit("[%s] 字节长度不一致" % tgt["id"])

        ok, err = syntax_ok(patched, os.path.dirname(asar_path))
        if not ok:
            raise SystemExit("[%s] 补丁后语法错误：\n%s" % (tgt["id"], err))

        meta = asar.node(path)
        old_hash = meta["integrity"]["hash"]
        new_hash, new_blocks = hash_blocks(patched)
        mapping = {old_hash: new_hash}
        for o, n in zip(meta["integrity"]["blocks"], new_blocks):
            if o != n:
                mapping[o] = n
        for o, n in mapping.items():
            if o == n:
                continue
            if header.count(o.encode()) == 0:
                raise SystemExit("[%s] 头部未找到旧哈希，结构异常" % tgt["id"])
            header = bytearray(bytes(header).replace(o.encode(), n.encode()))

        off, size = asar.offset(path)
        new_data[off:off + size] = patched
        changed.append(path)
        print("      -> %s  (%+d 字节)" % (os.path.basename(path), delta))

    new_data[16:16 + asar.str_size] = header
    header_hash = hashlib.sha256(bytes(header)).hexdigest()
    print("[*] 新头部哈希: " + header_hash)

    # 定位 exe 内的完整性资源
    with open(exe_path, "rb") as f:
        exe = f.read()
    marker = b'"resources\\\\app.asar","alg":"SHA256","value":"'
    pos = exe.find(marker)
    if pos < 0:
        marker = b'"resources\\\\app.asar","alg":"sha256","value":"'
        pos = exe.find(marker)
    if pos < 0:
        raise SystemExit("huyapc.exe 中未找到完整性资源（版本可能已变）")
    vstart = pos + len(marker)
    vend = exe.find(b'"', vstart)
    if vend - vstart != 64:
        raise SystemExit("exe 内完整性哈希长度异常")
    print("[*] exe 原记录: " + exe[vstart:vend].decode())

    if check_only:
        print("[check] 规则可应用，未写入任何文件")
        return

    backup = os.path.join(client_dir, BACKUP_DIRNAME)
    if not os.path.isdir(backup):
        os.makedirs(backup)
        shutil.copy2(asar_path, os.path.join(backup, "app.asar"))
        shutil.copy2(exe_path, os.path.join(backup, "huyapc.exe"))
        print("[*] 已备份原始文件 -> " + backup)

    with open(asar_path, "r+b") as f:
        f.seek(0)
        f.write(bytes(new_data))
    ne = bytearray(exe)
    ne[vstart:vend] = header_hash.encode()
    with open(exe_path, "r+b") as f:
        f.seek(0)
        f.write(bytes(ne))

    print("[✓] 完成，共修改 %d 个文件" % len(changed))


def main():
    ap = argparse.ArgumentParser(description="虎牙直播 PC 客户端去广告补丁")
    ap.add_argument("--dir", help="Client\\External 目录，留空自动定位")
    ap.add_argument("--restore", action="store_true", help="从备份还原")
    ap.add_argument("--check", action="store_true", help="只检查不写入")
    args = ap.parse_args()

    client_dir = find_client_dir(args.dir)
    print("[*] 客户端目录: " + client_dir)
    if args.restore:
        restore(client_dir)
        return
    apply_patch(client_dir, load_rules(), check_only=args.check)


if __name__ == "__main__":
    main()
