#!/usr/bin/env python3

import os
import sys
import subprocess
import requests
import yaml
from colorama import init, Fore, Style

# 初始化 colorama
init(autoreset=True)

# 从环境变量中读取 GitHub 个人访问令牌
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
if not GITHUB_TOKEN:
    raise ValueError("Please set the GITHUB_TOKEN environment variable")

# 检查是否使用 sparse-checkout 模式
SPARSE_CHECKOUT = os.getenv('SPARSE_CHECKOUT', 'false').lower() == 'true'

# 缓存从 Packages 文件中读取的文件大小信息
_packages_file_sizes_cache = None

# 缓存从 git 中读取的文件存在性信息
_git_file_exists_cache = None

def get_packages_file_sizes():
    """从现有的 Packages 文件中获取文件大小信息"""
    global _packages_file_sizes_cache
    if _packages_file_sizes_cache is not None:
        return _packages_file_sizes_cache
    
    _packages_file_sizes_cache = {}
    if not SPARSE_CHECKOUT:
        return _packages_file_sizes_cache
    
    packages_file = 'Packages'
    if not os.path.exists(packages_file):
        print(Style.DIM + Fore.YELLOW + 
              "Packages file not found, will download all files", file=sys.stderr)
        return _packages_file_sizes_cache
    
    try:
        with open(packages_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 分割每个包的信息块
        package_blocks = content.split('\n\n')
        
        for block in package_blocks:
            if not block.strip():
                continue
            
            filename = None
            size = None
            
            for line in block.strip().split('\n'):
                if line.startswith('Filename: '):
                    filename = line[10:].strip()  # 去掉 "Filename: " 前缀
                elif line.startswith('Size: '):
                    try:
                        size = int(line[6:].strip())  # 去掉 "Size: " 前缀
                    except ValueError:
                        continue
            
            if filename and size is not None and filename.endswith('.deb'):
                _packages_file_sizes_cache[filename] = size
        
        print(Style.DIM + Fore.CYAN + 
              f"Loaded size info for {len(_packages_file_sizes_cache)} files from Packages file", file=sys.stderr)
        
    except (IOError, UnicodeDecodeError) as e:
        print(Style.DIM + Fore.YELLOW + 
              f"Failed to read Packages file: {e}, will download all files", file=sys.stderr)
    
    return _packages_file_sizes_cache

def get_git_file_list():
    """从 git 中获取文件存在性信息（不获取大小以避免性能问题）"""
    global _git_file_exists_cache
    if _git_file_exists_cache is not None:
        return _git_file_exists_cache
    
    _git_file_exists_cache = set()
    if not SPARSE_CHECKOUT:
        return _git_file_exists_cache
    
    try:
        # 使用 git ls-tree 但不带 -l 参数，只获取文件名列表
        result = subprocess.run(['git', 'ls-tree', '-r', '--name-only', 'HEAD', 'downloads/'], 
                               capture_output=True, text=True, check=True)
        
        for line in result.stdout.strip().split('\n'):
            if line.strip() and line.endswith('.deb'):
                _git_file_exists_cache.add(line.strip())
        
        print(Style.DIM + Fore.CYAN + 
              f"Found {len(_git_file_exists_cache)} deb files in git repository", file=sys.stderr)
        
    except subprocess.CalledProcessError:
        print(Style.DIM + Fore.YELLOW + 
              "Failed to get git file list, will download all files", file=sys.stderr)
    
    return _git_file_exists_cache

def check_file_in_git(file_path, expected_size):
    """综合检查：文件必须存在于 git 中，且 Packages 文件中的大小匹配"""
    # 首先检查文件是否存在于 git 仓库中
    git_files = get_git_file_list()
    if file_path not in git_files:
        return False
    
    # 然后检查 Packages 文件中记录的大小是否匹配
    packages_sizes = get_packages_file_sizes()
    actual_size = packages_sizes.get(file_path)
    return actual_size is None or actual_size == expected_size


# 从 index.yaml 文件中读取仓库列表
repos = []
with open('index.yaml', 'r', encoding='utf-8') as file:
    data = yaml.safe_load(file)
    repos = data['repos']


def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def download_file(url, dest_folder, asset_size=0, sparse_checkout=False):
    if not os.path.exists(dest_folder):
        os.makedirs(dest_folder)
    local_filename = os.path.join(dest_folder, url.split('/')[-1])

    if asset_size == 0:
        # 使用 HEAD 请求检查文件大小
        head_response = requests.head(url, timeout=30)
        head_response.raise_for_status()
        asset_size = int(head_response.headers.get('Content-Length', 0))

    # 在 sparse-checkout 模式下，使用 git 检查文件
    if SPARSE_CHECKOUT and sparse_checkout:
        if check_file_in_git(local_filename, asset_size):
            print(Style.DIM + Fore.YELLOW +
                  f"{local_filename} already exists in git with correct size, skipping download.", file=sys.stderr)
            return local_filename
        else:
            print(Fore.CYAN +
                  f"Downloading {local_filename} (sparse-checkout mode)...", file=sys.stderr)
        file_size = 0
    else:
        # 原有的文件系统检查逻辑
        if os.path.exists(local_filename):
            file_size = os.path.getsize(local_filename)
            if file_size == asset_size:
                print(Style.DIM + Fore.YELLOW +
                      f"{local_filename} already exists, skipping download.", file=sys.stderr)
                return local_filename
            # 如果文件大小不一致，删除文件
            os.remove(local_filename)
            print(Fore.YELLOW +
                  f"File {local_filename} already exists, but size mismatch, redownloading.",
                  file=sys.stderr)
            file_size = 0
        else:
            file_size = 0

    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total_size = int(r.headers.get('Content-Length', 0)) + file_size
        mode = 'ab' if file_size > 0 else 'wb'
        with open(local_filename, mode) as f:
            downloaded = file_size
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                done = int(80 * downloaded / total_size)
                sys.stdout.write(
                    f"\r[{'=' * done}{' ' * (80-done)}] {format_size(downloaded)} / {format_size(total_size)}")
                sys.stdout.flush()
        print()  # 换行

    return local_filename


def download_repo_manifests(repo_url):
    repo_host = repo_url.split('/')[2]
    manifests_url = f'{repo_url}/Packages.gz'
    print(Fore.CYAN +
          f"[{repo_url}] Downloading {manifests_url}...", file=sys.stderr)
    download_file(manifests_url, f'.cache/{repo_host}')


def download_deb_files_from_repo(repo_url):
    repo_name = '/'.join(repo_url.split('/')[-2:])
    releases_url = f'https://api.github.com/repos/{repo_name}/releases?per_page=100'
    response = requests.get(releases_url, headers={
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }, timeout=30)
    response.raise_for_status()
    releases = response.json()

    for release in releases:
        for asset in release['assets']:
            if asset['name'].endswith('.deb'):
                print(Fore.CYAN +
                      f"[{repo_name}] Downloading {asset['name']}...", file=sys.stderr)
                download_file(asset['browser_download_url'],
                              'downloads', asset_size=asset['size'], sparse_checkout=True)


def main():
    download_repo_manifests('https://havoc.app')
    for repo in repos:
        download_deb_files_from_repo(repo)

    print(Fore.GREEN + "Operation completed successfully.", file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except (requests.RequestException, subprocess.CalledProcessError, KeyError, ValueError) as e:
        # 只打印错误信息到 stderr 并退出
        print(Fore.RED + str(e), file=sys.stderr)
        sys.exit(1)
