#!/usr/bin/env python3

import re
import subprocess
import gzip
import sys
import requests
import yaml
from colorama import init, Fore

# 初始化 colorama
init(autoreset=True)

# 从 index.yaml 文件中读取 Havoc ID 映射
havoc_ids = {}
with open('index.yaml', 'r', encoding='utf-8') as file:
    data = yaml.safe_load(file)
    havoc_ids = data['havoc-mappings']


def compare_version_gt(ver1, ver2):
    result = subprocess.run(
        ['dpkg', '--compare-versions', ver1, 'gt', ver2], capture_output=True, check=False)
    return result.returncode == 0


def main():
    havoc_control_list = {}
    with gzip.open('.cache/havoc.app/Packages.gz', 'rt') as f:
        control_content = f.read()
        control_list = control_content.split('\n\n')
        havoc_versions = {}
        for control in control_list:
            package_name = re.search(r"^Package: (.+)$", control, re.MULTILINE)
            package_version = re.search(
                r"^Version: (.+)$", control, re.MULTILINE)
            if not package_name or not package_version:
                continue
            if package_name.group(1) not in havoc_ids.keys():
                continue
            previous_version = havoc_versions.get(package_name.group(1), None)
            if previous_version and compare_version_gt(previous_version, package_version.group(1)):
                continue
            havoc_versions[package_name.group(1)] = package_version.group(1)
            havoc_control_list[package_name.group(1)] = control
    havoc_icons = {}
    for package_name, control in havoc_control_list.items():
        icon_url = re.search(r"^Icon: (.+)$", control, re.MULTILINE)
        if package_name and icon_url:
            havoc_icons[package_name] = icon_url.group(1)
    print(f'Found {len(havoc_icons)} icons.', file=sys.stderr)
    for key, url in havoc_icons.items():
        print(Fore.CYAN + f'Downloading {url}...', file=sys.stderr)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        suffix = ''
        if response.headers['Content-Type'] == 'image/png':
            suffix = '.png'
        elif response.headers['Content-Type'] == 'image/jpeg':
            suffix = '.jpg'
        else:
            print(Fore.YELLOW + f'Unsupported image format: {response.headers["Content-Type"]}',
                  file=sys.stderr)
            continue
        icon_name = url.split("/")[-1] + suffix
        with open(f'icons/{icon_name}', 'wb') as f:
            f.write(response.content)
        havoc_icons[key] = icon_name
        print(Fore.GREEN +
              f'Saved to icons/{icon_name}', file=sys.stderr)
    print(Fore.CYAN + 'Writing index.yaml...', file=sys.stderr)
    with open('icons/index.yaml', 'w', encoding='utf-8') as f:
        yaml.dump(havoc_icons, f, allow_unicode=True)
    print(Fore.GREEN + 'Done!', file=sys.stderr)


if __name__ == '__main__':
    main()
