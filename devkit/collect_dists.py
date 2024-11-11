#!/usr/bin/env python3

import os
import sys
import requests
import yaml
from colorama import init, Fore, Style

# 初始化 colorama
init(autoreset=True)

# 从环境变量中读取 GitHub 个人访问令牌
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
if not GITHUB_TOKEN:
    raise ValueError("Please set the GITHUB_TOKEN environment variable")

# 从 index.yaml 文件中读取仓库列表
repos = []
with open('index.yaml', 'r', encoding='utf-8') as file:
    data = yaml.safe_load(file)
    repos = data['repos']


def format_size(size):
    """格式化文件大小为 KB 或 MB"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024


def download_file(url, dest_folder, asset_size=0):
    if not os.path.exists(dest_folder):
        os.makedirs(dest_folder)
    local_filename = os.path.join(dest_folder, url.split('/')[-1])

    headers = {}
    if asset_size == 0:
        # 使用 HEAD 请求检查文件大小
        head_response = requests.head(url, headers=headers, timeout=30)
        head_response.raise_for_status()
        asset_size = int(head_response.headers.get('Content-Length', 0))

    if os.path.exists(local_filename):
        file_size = os.path.getsize(local_filename)
        if file_size == asset_size:
            print(Style.DIM + Fore.YELLOW +
                  f"{local_filename} already exists, skipping download.", file=sys.stderr)
            return local_filename
        headers['Range'] = f'bytes={file_size}-'
        print(Fore.YELLOW + f"Resuming download of {
              local_filename} from byte {file_size}", file=sys.stderr)
    else:
        file_size = 0

    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        total_size = int(r.headers.get('Content-Length', 0)) + file_size
        if file_size >= total_size:
            print(Style.DIM + Fore.YELLOW +
                  f"File {local_filename} already fully downloaded, skipping.", file=sys.stderr)
            return local_filename

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
    releases_url = f'https://api.github.com/repos/{repo_name}/releases'
    response = requests.get(releases_url, headers={
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }, timeout=30)
    response.raise_for_status()
    releases = response.json()

    for release in releases:
        for asset in release['assets']:
            if asset['name'].endswith('.deb'):
                print(
                    Fore.CYAN + f"[{repo_name}] Downloading {asset['name']}...", file=sys.stderr)
                download_file(asset['browser_download_url'],
                              'downloads', asset_size=asset['size'])


def main():
    download_repo_manifests('https://havoc.app')
    for repo in repos:
        download_deb_files_from_repo(repo)

    print(Fore.GREEN + "Operation completed successfully.", file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        # 只打印错误信息到 stderr 并退出
        print(Fore.RED + str(e), file=sys.stderr)
        sys.exit(1)
