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

# 检查是否使用 sparse-checkout 模式
SPARSE_CHECKOUT = os.getenv('SPARSE_CHECKOUT', 'false').lower() == 'true'

# 从 index.yaml 文件中读取 Havoc ID 映射
BASE_URL = None
havoc_ids = {}
hidden_packages = []
package_title_mappings = {}
with open('index.yaml', 'r', encoding='utf-8') as file:
    data = yaml.safe_load(file)
    BASE_URL = data['base-url']
    havoc_ids = data['havoc-mappings']
    hidden_packages = data['hidden-packages']
    package_title_mappings = data['package-title-mappings']


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


def get_newly_added_deb_files():
    """获取此次提交新增的 deb 文件"""
    try:
        # 获取已暂存的新文件
        result = subprocess.run(['git', 'diff', '--cached', '--name-only', '--diff-filter=A'], 
                               capture_output=True, text=True, check=True)
        staged_files = result.stdout.strip().split('\n') if result.stdout.strip() else []
        
        # 获取工作区新增的文件  
        result = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'], 
                               capture_output=True, text=True, check=True)
        untracked_files = result.stdout.strip().split('\n') if result.stdout.strip() else []
        
        # 合并并过滤 deb 文件
        all_new_files = staged_files + untracked_files
        deb_files = [f for f in all_new_files if f.endswith('.deb') and f.startswith('downloads/')]
        
        print(Fore.CYAN + f"Found {len(deb_files)} newly added deb files")
        for deb_file in deb_files:
            print(Style.DIM + f"  - {deb_file}")
            
        return deb_files
    except subprocess.CalledProcessError as e:
        print(Fore.RED + f"Error getting newly added files: {e}")
        return []


def parse_existing_packages(packages_file):
    """解析现有的 Packages 文件，返回包信息字典"""
    packages = {}
    if not os.path.exists(packages_file):
        return packages
        
    with open(packages_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 分割每个包的信息
    package_blocks = content.split('\n\n')
    for block in package_blocks:
        if not block.strip():
            continue
            
        lines = block.strip().split('\n')
        package_info = {}
        package_name = None
        version = None
        architecture = None
        filename = None
        
        for line in lines:
            if ': ' in line:
                key, value = line.split(': ', 1)
                package_info[key] = value
                if key == 'Package':
                    package_name = value
                elif key == 'Version':
                    version = value
                elif key == 'Architecture':
                    architecture = value
                elif key == 'Filename':
                    filename = value
        
        if package_name and version and architecture:
            # 使用 Package + Version + Architecture 作为唯一标识
            package_key = f"{package_name}_{version}_{architecture}"
            packages[package_key] = {
                'info': package_info,
                'block': block,
                'filename': filename,
                'package_name': package_name,
                'version': version,
                'architecture': architecture
            }
    
    return packages


def process_single_deb_file(deb_path):
    """处理单个 deb 文件，返回包信息字符串和包标识"""
    print(Fore.CYAN + f"Processing {deb_path}...")

    # 获取 dpkg-deb 输出
    result = subprocess.run(
        ['dpkg-deb', '-f', deb_path], capture_output=True, text=True, check=True)
    control_content = result.stdout

    # 提取包基本信息
    package_name = re.search(r"^Package: (.+)$", control_content, re.MULTILINE)
    version = re.search(r"^Version: (.+)$", control_content, re.MULTILINE)
    architecture = re.search(r"^Architecture: (.+)$", control_content, re.MULTILINE)
    
    if not package_name:
        print(Fore.RED + f"Package name not found in control file of {deb_path}")
        return None, None, None
    
    package_name = package_name.group(1)
    version = version.group(1) if version else "unknown"
    architecture = architecture.group(1) if architecture else "unknown"
    
    # 生成唯一标识
    package_key = f"{package_name}_{version}_{architecture}"

    # 预处理名称
    if package_name in package_title_mappings:
        control_content = re.sub(
            r"^Name: .+$", f"Name: {package_title_mappings[package_name]}",
            control_content, flags=re.MULTILINE)
    if package_name in hidden_packages:
        print(Style.DIM + Fore.YELLOW + f"Package {package_name} is hidden")
        return None, None, None

    # 构建完整的包信息
    package_info = control_content

    # 添加 Havoc ID 和图标字段
    havoc_id = havoc_ids.get(package_name, None)
    if havoc_id:
        print(Style.DIM + f"Package: {package_name}, Version: {version}, Architecture: {architecture}, Havoc ID: {havoc_id}")
        package_info += f"Depiction: https://havoc.app/depiction/{havoc_id}\n"
        package_info += f"SileoDepiction: https://havoc.app/package/{havoc_id}/depiction.json\n"
        icon_name = icon_mappings.get(package_name, None)
        if BASE_URL and icon_name:
            package_info += f"Icon: {BASE_URL}/icons/{icon_name}\n"
    else:
        print(Style.DIM + f"Package: {package_name}, Version: {version}, Architecture: {architecture}")

    # 添加 Filename 和 Size 字段
    package_info += f"Filename: {deb_path}\n"
    package_info += f"Size: {os.path.getsize(deb_path)}\n"

    # 计算并添加文件哈希
    hashes = get_file_hashes(deb_path)
    for hash_name, hash_value in hashes.items():
        package_info += f"{hash_name}: {hash_value}\n"

    return package_key, package_info, deb_path


def generate_packages_file(deb_directory, output_file):
    """在正常模式下生成完整的 Packages 文件"""
    with open(output_file, 'w', encoding='utf-8') as packages_file:
        for deb_file in sorted(os.listdir(deb_directory)):
            if not deb_file.endswith('.deb'):
                continue

            deb_path = os.path.join(deb_directory, deb_file)
            package_key, package_info, _ = process_single_deb_file(deb_path)
            
            if package_key and package_info:
                packages_file.write(package_info)
                packages_file.write('\n')


def merge_packages_file(output_file):
    """在 sparse-checkout 模式下合并新包到现有的 Packages 文件"""
    # 获取新增的 deb 文件
    new_deb_files = get_newly_added_deb_files()
    if not new_deb_files:
        print(Fore.YELLOW + "No new deb files to process")
        return
    
    # 解析现有的 Packages 文件
    existing_packages = parse_existing_packages(output_file)
    print(Fore.CYAN + f"Found {len(existing_packages)} existing package entries")
    
    # 处理新的 deb 文件
    new_packages = {}
    updated_filenames = set()  # 记录所有新处理的文件名
    
    for deb_file in new_deb_files:
        if os.path.exists(deb_file):  # 确保文件存在
            package_key, package_info, filename = process_single_deb_file(deb_file)
            if package_key and package_info:
                new_packages[package_key] = package_info
                updated_filenames.add(filename)
                
                # 检查是否是更新现有包
                if package_key in existing_packages:
                    print(Fore.YELLOW + f"Updating existing package: {package_key}")
                else:
                    print(Fore.GREEN + f"Adding new package: {package_key}")
    
    # 合并包信息
    all_packages = {}
    
    # 首先添加现有包（除了被更新的文件）
    for package_key, package_data in existing_packages.items():
        existing_filename = package_data['filename']
        # 如果现有包的文件名不在新处理的文件中，则保留
        if existing_filename not in updated_filenames:
            all_packages[package_key] = package_data['block']
    
    # 然后添加新包
    for package_key, package_info in new_packages.items():
        all_packages[package_key] = package_info
    
    # 按包名+版本+架构排序并写入文件
    with open(output_file, 'w', encoding='utf-8') as packages_file:
        for package_key in sorted(all_packages.keys()):
            packages_file.write(all_packages[package_key])
            if not all_packages[package_key].endswith('\n'):
                packages_file.write('\n')
            packages_file.write('\n')
    
    print(Fore.GREEN + f"Updated {len(new_packages)} package entries in Packages file")
    print(Fore.CYAN + f"Total package entries: {len(all_packages)}")


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
    
    if SPARSE_CHECKOUT:
        print(Fore.CYAN + "Running in sparse-checkout mode")
        merge_packages_file(output_file)
    else:
        print(Fore.CYAN + "Running in normal mode")
        generate_packages_file(deb_directory, output_file)
        
    print(Fore.GREEN + f"Packages file updated at {output_file}")

    # 压缩 Packages 文件
    compress_file(output_file, 'Packages.gz', gzip.open, 'wb')
    compress_file(output_file, 'Packages.bz2', bz2.open, 'wb')
    compress_file(output_file, 'Packages.xz', lzma.open, 'wb')
    compress_zst(output_file, 'Packages.zst')
    print(Fore.GREEN + "Packages file compressed into gz, bz2, xz and zst formats.")


if __name__ == '__main__':
    main()
