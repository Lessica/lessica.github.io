#!/usr/bin/env python3

import os
import hashlib
import pathlib
from datetime import datetime, timezone
from colorama import init, Fore

# 初始化 colorama
init(autoreset=True)


def calculate_hash(file_path, hash_type):
    hash_func = getattr(hashlib, hash_type)()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_func.update(chunk)
    return hash_func.hexdigest()


def generate_release_file(packages_files, output_file):
    with open(output_file, 'w', encoding='utf-8') as release_file:
        release_file.write("Origin: 82Flex\n")
        release_file.write("Label: 82Flex\n")
        release_file.write("Suite: stable\n")
        release_file.write("Version: 1.0\n")
        release_file.write("Codename: 82Flex_Repo\n")
        release_file.write(
            "Architectures: iphoneos-arm iphoneos-arm64 iphoneos-arm64e\n")
        release_file.write("Components: main\n")
        release_file.write("Description: Personal repository of @Lessica\n")
        release_file.write(f"Date: {datetime.now(
            timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}\n")

        release_file.write("MD5Sum:\n")
        for file_path in packages_files:
            file_size = os.path.getsize(file_path)
            relative_path = pathlib.Path(file_path).name
            md5sum = calculate_hash(file_path, 'md5')
            release_file.write(f" {md5sum} {file_size} {relative_path}\n")

        release_file.write("SHA1:\n")
        for file_path in packages_files:
            file_size = os.path.getsize(file_path)
            relative_path = pathlib.Path(file_path).name
            sha1sum = calculate_hash(file_path, 'sha1')
            release_file.write(f" {sha1sum} {file_size} {relative_path}\n")

        release_file.write("SHA256:\n")
        for file_path in packages_files:
            file_size = os.path.getsize(file_path)
            relative_path = pathlib.Path(file_path).name
            sha256sum = calculate_hash(file_path, 'sha256')
            release_file.write(f" {sha256sum} {file_size} {relative_path}\n")

        release_file.write("SHA512:\n")
        for file_path in packages_files:
            file_size = os.path.getsize(file_path)
            relative_path = pathlib.Path(file_path).name
            sha512sum = calculate_hash(file_path, 'sha512')
            release_file.write(f" {sha512sum} {file_size} {relative_path}\n")


def main():
    packages_files = [
        'Packages',
        'Packages.gz',
        'Packages.bz2',
        'Packages.xz',
        'Packages.zst'
    ]
    output_file = pathlib.Path('Release')
    generate_release_file(packages_files, output_file)
    print(Fore.GREEN + f"Release file generated at {output_file}")


if __name__ == '__main__':
    main()
