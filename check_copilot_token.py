#!/usr/bin/env python3
"""
GitHub Copilot —— 诊断 token 是否可用，以及模型名是否存在。

用法：
    python3 check_copilot_token.py
    python3 check_copilot_token.py --token ghp_xxx
    python3 check_copilot_token.py --model gpt-6-luna

会做三件事：
1. 判断配置的 token 是 GitHub PAT 还是已换好的 Copilot token，必要时自动换一次
2. 列出 Copilot 当前可用的模型
3. 确认配置的模型在列表里

读取顺序：--token > GITHUB_COPILOT_TOKEN > GITHUB_TOKEN
"""
import argparse
import os
import sys
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 优先从项目的 .env 里读取配置
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', '.env'))
except ImportError:
    pass

GITHUB_PAT_PREFIXES = ('ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_')
TOKEN_URL = 'https://api.github.com/copilot_internal/v2/token'
USER_URL = 'https://api.github.com/copilot_internal/user'
DEFAULT_BASE_URL = 'https://api.githubcopilot.com'
USER_AGENT = 'GitHubCopilotChat/0.26.7'
EDITOR_VERSION = 'vscode/1.95.0'
INTEGRATION_ID = 'vscode-chat'


def mask(token):
    if not token:
        return '(空)'
    return f'{token[:6]}...(len={len(token)})'


def discover_endpoint(github_token):
    """查账号真正对应的 endpoint（不同订阅类型域名不同）。"""
    try:
        r = requests.get(USER_URL, headers={
            'Authorization': f'token {github_token}',
            'Accept': 'application/json',
            'User-Agent': USER_AGENT,
        }, timeout=30)
    except Exception as e:
        print(f'  查账号信息失败: {e}')
        return None
    if r.status_code != 200:
        print(f'  查账号信息失败: HTTP {r.status_code}')
        return None
    try:
        data = r.json()
    except Exception:
        return None
    print(f'  订阅类型: {data.get("copilot_plan")}   chat_enabled={data.get("chat_enabled")}')
    return (data.get('endpoints') or {}).get('api')


def exchange(github_token):
    """用 GitHub token 换 Copilot token（失败不算致命）。"""
    print('  看起来是 GitHub token，尝试换 Copilot token ...')
    r = requests.get(TOKEN_URL, headers={
        'Authorization': f'Bearer {github_token}',
        'Accept': 'application/vnd.github+json',
        'User-Agent': USER_AGENT,
    }, timeout=30)
    if r.status_code != 200:
        print(f'  换 token 失败: HTTP {r.status_code}')
        print(f'  {r.text[:200]}')
        print('  -> 没关系，部分订阅类型换不了，但原始 token 直接当 Bearer 是可行的')
        return None
    data = r.json()
    token = data.get('token')
    expires_at = data.get('expires_at')
    if expires_at:
        print(f'  换到了，{int((expires_at - time.time()) / 60)} 分钟后过期')
    return token


def list_models(base_url, token):
    try:
        r = requests.get(f'{base_url}/models', headers={
            'Authorization': f'Bearer {token}',
            'Editor-Version': EDITOR_VERSION,
            'Copilot-Integration-Id': INTEGRATION_ID,
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
        }, timeout=30)
    except Exception as e:
        print(f'  拉模型列表失败: {e}')
        return []
    if r.status_code != 200:
        print(f'  拉模型列表失败: HTTP {r.status_code} {r.text[:200]}')
        return []
    try:
        return [m.get('id') for m in r.json().get('data', []) if m.get('id')]
    except Exception as e:
        print(f'  解析模型列表失败: {e}')
        return []


def main():
    parser = argparse.ArgumentParser(description='诊断 GitHub Copilot 的 token 与模型配置')
    parser.add_argument('--token', help='token（默认读 GITHUB_COPILOT_TOKEN，其次 GITHUB_TOKEN）')
    parser.add_argument('--model', help='要检查的模型（默认读 GITHUB_COPILOT_MODEL）')
    parser.add_argument('--base-url', help='Copilot API 地址（默认读 GITHUB_COPILOT_API_BASE_URL）')
    args = parser.parse_args()

    base_url = (args.base_url or os.getenv('GITHUB_COPILOT_API_BASE_URL')
                or DEFAULT_BASE_URL).rstrip('/')
    model = args.model or os.getenv('GITHUB_COPILOT_MODEL') or 'gpt-6-luna'
    source = args.token or os.getenv('GITHUB_COPILOT_TOKEN') or os.getenv('GITHUB_TOKEN') or ''

    print('=' * 74)
    print(f'API 地址 : {base_url}')
    print(f'目标模型 : {model}')
    print(f'token    : {mask(source)}')
    print('=' * 74)

    if not source:
        print('\n没有找到 token。')
        print('请设置 GITHUB_COPILOT_TOKEN（或 GITHUB_TOKEN），或用 --token 指定。')
        return 1

    print('\n[1] token 类型判断')
    token = None
    if source.startswith(GITHUB_PAT_PREFIXES):
        print('  -> GitHub token')
        endpoint = discover_endpoint(source)
        if endpoint:
            print(f'  -> 该订阅对应的 endpoint: {endpoint}')
            # 没显式指定地址时，用查到的那个
            if not args.base_url and not os.getenv('GITHUB_COPILOT_API_BASE_URL'):
                base_url = endpoint.rstrip('/')
                print(f'  -> 改用该 endpoint: {base_url}')
        token = exchange(source)
    else:
        print('  -> 已经是 Copilot token，直接使用')
        token = source
    if not token:
        print('  换 token 不可用，改用原始 token 直接当 Bearer')
        token = source

    print('\n[2] 拉取可用模型列表')
    models = list_models(base_url, token)
    if models:
        print(f'  共 {len(models)} 个模型:')
        for m in models:
            mark = '  <= 正在使用的' if m == model else ''
            print(f'    {m}{mark}')
    else:
        print('  (没拿到列表)')

    print('\n[3] 模型检查')
    if models:
        if model in models:
            print(f'  模型 "{model}" 存在，可以直接用。')
        else:
            print(f'  模型 "{model}" 不在列表里。')
            print(f'  请把 GITHUB_COPILOT_MODEL 改成上面列表里的一个。')
    else:
        print(f'  拿不到模型列表，无法确认 "{model}" 是否存在。')
        print('  可以手动试一次：')
        print(f'    curl -sS {base_url}/chat/completions \\')
        print(f'      -H "Authorization: Bearer <copilot_token>" \\')
        print(f'      -H "Editor-Version: {EDITOR_VERSION}" \\')
        print(f'      -H "Copilot-Integration-Id: {INTEGRATION_ID}" \\')
        print(f'      -H "User-Agent: {USER_AGENT}" \\')
        print(f'      -H "Content-Type: application/json" \\')
        print(f'      -d \'{{"model":"{model}","messages":[{{"role":"user","content":"hi"}}]}}\'')

    print('\n[4] 发一次真实请求验证（Responses API）')
    headers = {
        'Authorization': f'Bearer {token}',
        'Editor-Version': EDITOR_VERSION,
        'Copilot-Integration-Id': INTEGRATION_ID,
        'User-Agent': USER_AGENT,
        'Content-Type': 'application/json',
        'OpenAI-Intent': 'responses',
    }
    body = {'model': model, 'input': [{'role': 'user', 'content': 'hi'}]}
    try:
        r = requests.post(f'{base_url}/responses', headers=headers, json=body, timeout=60)
        print(f'  POST {base_url}/responses -> HTTP {r.status_code}')
        if r.status_code == 200:
            try:
                data = r.json()
                content = data.get('output_text')
                if not content:
                    parts = []
                    for item in data.get('output') or []:
                        for c in (item.get('content') or []):
                            if isinstance(c, dict) and c.get('text'):
                                parts.append(c['text'])
                    content = '\n'.join(parts)
                print(f'  模型回复: {str(content)[:160]!r}')
                print('\n  ==> token 和模型都可用。')
            except Exception as e:
                print(f'  解析回复失败: {e}  body={r.text[:200]}')
        else:
            print(f'  {r.text[:400]}')
            print('\n  ==> token 或模型有问题，见上面的错误信息。')
            if r.status_code in (404, 405):
                print('  如果这个实例只支持 chat/completions，可以把 '
                      'GITHUB_COPILOT_API_MODE 设成 chat_completions。')
    except Exception as e:
        print(f'  请求失败: {e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
