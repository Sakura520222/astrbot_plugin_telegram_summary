#!/usr/bin/env python3
import re
import sys
import os


def extract_changelog(version, changelog_file="CHANGELOG.md"):
    """从CHANGELOG.md中提取指定版本的变更日志"""
    with open(changelog_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 匹配指定版本的变更日志
    pattern = rf'## \[{version}\] - .+?\n(.*?)(?=## \[|$)'
    match = re.search(pattern, content, re.DOTALL)

    if match:
        changelog = match.group(1).strip()
        return changelog
    else:
        print(f"No changelog found for version {version}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python extract_changelog.py <version>")
        sys.exit(1)
    
    version = sys.argv[1]
    changelog = extract_changelog(version)
    print(changelog)
