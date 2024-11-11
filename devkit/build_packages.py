#!/usr/bin/env python3

import os
import re
import subprocess
import hashlib
import pathlib
import gzip
import bz2
import lzma
import yaml
import zstandard as zstd
from colorama import init, Fore, Style

# 初始化 colorama
init(autoreset=True)

# 从 index.yaml 文件中读取 Havoc ID 映射
BASE_URL = None
havoc_ids = {}
with open('index.yaml', 'r', encoding='utf-8') as file:
    data = yaml.safe_load(file)
    BASE_URL = data['base-url']
    havoc_ids = data['havoc-mappings']


# 从 icons/index.yaml 文件中读取图标文件名映射
icon_mappings = {}
try:
    with open('icons/index.yaml', 'r', encoding='utf-8') as file:
        icon_mappings = yaml.safe_load(file)
except FileNotFoundError:
    pass


def get_file_hashes(file_path):
    hashes = {}
    with open(file_path, 'rb') as f:
        contents = f.read()
        hashes['MD5sum'] = hashlib.md5(contents).hexdigest()
        hashes['SHA1'] = hashlib.sha1(contents).hexdigest()
        hashes['SHA256'] = hashlib.sha256(contents).hexdigest()
        hashes['SHA512'] = hashlib.sha512(contents).hexdigest()
    return hashes


def generate_packages_file(deb_directory, output_file):
    with open(output_file, 'w', encoding='utf-8') as packages_file:
        for deb_file in os.listdir(deb_directory):
            if not deb_file.endswith('.deb'):
                continue

            deb_path = os.path.join(deb_directory, deb_file)
            print(Fore.CYAN + f"Processing {deb_path}...")

            # 获取 dpkg-deb 输出
            result = subprocess.run(
                ['dpkg-deb', '-f', deb_path], capture_output=True, text=True, check=True)
            packages_file.write(result.stdout)

            # 添加 Havoc 展示字段
            control_content = result.stdout
            package_name = re.search(
                r"^Package: (.+)$", control_content, re.MULTILINE)
            if package_name:
                package_name = package_name.group(1)
                havoc_id = havoc_ids.get(package_name, None)
                if havoc_id:
                    print(
                        Style.DIM + f"Package name: {package_name}, Havoc ID: {havoc_id}")
                    packages_file.write(
                        f"Depiction: https://havoc.app/depiction/{havoc_id}\n")
                    packages_file.write(
                        f"SileoDepiction: https://havoc.app/package/{havoc_id}/depiction.json\n")
                    icon_name = icon_mappings.get(package_name, None)
                    if BASE_URL and icon_name:
                        packages_file.write(
                            f"Icon: {BASE_URL}/icons/{icon_name}\n")
                else:
                    print(Style.DIM + f"Package name: {package_name}")

            # 添加 Filename 和 Size 字段
            packages_file.write(f"Filename: {deb_path}\n")
            packages_file.write(f"Size: {os.path.getsize(deb_path)}\n")

            # 计算并添加文件哈希
            hashes = get_file_hashes(deb_path)
            for hash_name, hash_value in hashes.items():
                packages_file.write(f"{hash_name}: {hash_value}\n")

            packages_file.write('\n')


def compress_file(input_file, output_file, compress_func, mode):
    with open(input_file, 'rb') as f_in:
        with compress_func(output_file, mode) as f_out:
            f_out.write(f_in.read())


def compress_zst(input_file, output_file):
    cctx = zstd.ZstdCompressor()
    with open(input_file, 'rb') as f_in, open(output_file, 'wb') as f_out:
        f_out.write(cctx.compress(f_in.read()))


def main():
    deb_directory = 'downloads'
    output_file = pathlib.Path('Packages')
    generate_packages_file(deb_directory, output_file)
    print(Fore.GREEN + f"Packages file generated at {output_file}")

    # 压缩 Packages 文件
    compress_file(output_file, 'Packages.gz', gzip.open, 'wb')
    compress_file(output_file, 'Packages.bz2', bz2.open, 'wb')
    compress_file(output_file, 'Packages.xz', lzma.open, 'wb')
    compress_zst(output_file, 'Packages.zst')
    print(Fore.GREEN + "Packages file compressed into gz, bz2, xz and zst formats.")


if __name__ == '__main__':
    main()
