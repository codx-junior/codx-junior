"""TEMPORARY structural verification for the Log Explorer SFCs; deletes itself."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPLORER = os.path.join(HERE, 'src/components/logs/explorer')
FILES = ['LogVirtualList.vue', 'LogEntryRow.vue', 'LogDetailPanel.vue', 'LogExplorer.vue']

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
TAG_RE = re.compile(r'<(/?)([A-Za-z][\w-]*)((?:[^>"\']|"[^"]*"|\'[^\']*\')*)>', re.S)
COMMENT_RE = re.compile(r'<!--.*?-->', re.S)
REGEX_START = set('(,=:[!&|?{};+-*%<>~^')
failures = []


def check_template(name, template):
    stack = []
    for match in TAG_RE.finditer(template):
        closing, tag, rest = match.group(1), match.group(2).lower(), match.group(3)
        if tag in VOID:
            continue
        if closing:
            if not stack:
                failures.append(f'{name}: unexpected </{tag}>')
                continue
            expected = stack.pop()
            if expected != tag:
                failures.append(f'{name}: </{tag}> closes <{expected}>')
        elif not rest.rstrip().endswith('/'):
            stack.append(tag)
    if stack:
        failures.append(f'{name}: unclosed tags {stack}')


def check_script(name, script):
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    index = 0
    length = len(script)
    prev = ' '
    while index < length:
        char = script[index]
        nxt = script[index + 1] if index + 1 < length else ''
        if char == '/' and nxt == '/':
            newline = script.find('\n', index)
            index = length if newline == -1 else newline
            continue
        if char == '/' and nxt == '*':
            end = script.find('*/', index + 2)
            index = length if end == -1 else end + 2
            continue
        if char == '/' and prev in REGEX_START:
            index += 1
            in_class = False
            while index < length:
                current = script[index]
                if current == '\\':
                    index += 2
                    continue
                if current == '[':
                    in_class = True
                elif current == ']':
                    in_class = False
                elif current == '/' and not in_class:
                    break
                index += 1
            index += 1
            continue
        if char in '\'"`':
            quote = char
            index += 1
            while index < length:
                if script[index] == '\\':
                    index += 2
                    continue
                if script[index] == quote:
                    break
                index += 1
            index += 1
            prev = quote
            continue
        if char in '([{':
            stack.append(char)
        elif char in ')]}':
            if not stack or stack.pop() != pairs[char]:
                failures.append(f'{name}: unbalanced {char!r} in <script>')
                return
        if not char.isspace():
            prev = char
        index += 1
    if stack:
        failures.append(f'{name}: unclosed brackets {stack} in <script>')


for filename in FILES:
    path = os.path.join(EXPLORER, filename)
    with open(path, encoding='utf-8') as handle:
        source = handle.read()

    if 'v-html' in source:
        failures.append(f'{filename}: v-html found')

    templates = re.findall(r'<template>(.*)</template>\s*\n\s*<script', source, re.S)
    if not templates:
        failures.append(f'{filename}: no <template> block found')
    else:
        check_template(filename, COMMENT_RE.sub('', templates[0]))

    scripts = re.findall(r'<script>(.*?)</script>', source, re.S)
    if not scripts:
        failures.append(f'{filename}: no <script> block found')
    for script in scripts:
        check_script(filename, script)
    print(f'checked {filename}')

with open(os.path.join(HERE, 'src/config/appComponentsMap.js'), encoding='utf-8') as handle:
    registry = handle.read()
if "'log-viewer': lazy(() => import('../components/logs/explorer/LogExplorer.vue'))" not in registry:
    failures.append('registry: log-viewer does not point to LogExplorer.vue')

for dirpath, _dirnames, filenames in os.walk(EXPLORER):
    for filename in filenames:
        if filename.endswith('.vue'):
            with open(os.path.join(dirpath, filename), encoding='utf-8') as handle:
                if 'v-html' in handle.read():
                    failures.append(f'{filename}: v-html found in the explorer folder')

os.remove(os.path.abspath(__file__))

if failures:
    print('\nFAILURES')
    for failure in failures:
        print(' -', failure)
    sys.exit(1)

print('\nOK: no v-html, templates balanced, scripts balanced, registry wired.')
