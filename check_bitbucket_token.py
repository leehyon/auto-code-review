#!/usr/bin/env python3
"""
Bitbucket Server/Data Center —— 判定 HTTP access token 是「个人 token」还是「项目/仓库级 token」。

用法：
    python3 check_bitbucket_token.py
    python3 check_bitbucket_token.py --url https://bitbucket.example.com --token BBDC-xxx
    python3 check_bitbucket_token.py --probe-basic        # 额外的 x-token-auth 探测（会发一个 Basic 请求）

原理：Bitbucket 的 REST API 没有 token 自省接口（``/users/{name}/tokens`` 返回 404），
只能靠行为指纹区分。

默认只发 Bearer 请求，不使用 Basic 认证。判定依据：

  1. 可见项目数量
     个人 token = 该用户有权限的所有项目（通常几十个）
     项目/仓库级 token = 只有它绑定的那一个项目

  2. 能否访问其它项目
     项目/仓库级 token 越权访问其它项目会 403/404

  3. 能否列出全部用户
     项目/仓库级 token 没有用户身份，这个接口应当 403/404

  4.（可选，--probe-basic）Basic x-token-auth 探测
     官方规定：项目/仓库级 token 用 Basic 时用户名必须是 x-token-auth；
     个人 token 必须用 token 所属的真实用户名。
     => x-token-auth 能过 = 项目/仓库级 token；401 = 个人 token
     这个探测最直接，但需要发一个 HTTP Basic 请求，默认关闭。
"""
import argparse
import base64
import os
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 优先从项目的 .env 里读取配置
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', '.env'))
except ImportError:
    pass


def basic_header(username, token):
    cred = base64.b64encode(f'{username}:{token}'.encode()).decode()
    return 'Basic ' + cred


def main():
    parser = argparse.ArgumentParser(description='判定 Bitbucket HTTP access token 的类型')
    parser.add_argument('--url', help='Bitbucket 实例地址（默认读 BITBUCKET_URL 环境变量）')
    parser.add_argument('--token', help='token（默认读 BITBUCKET_ACCESS_TOKEN 环境变量）')
    parser.add_argument('--project', help='token 所属项目的 key（可选，用于交叉验证）')
    parser.add_argument('--probe-basic', action='store_true',
                        help='额外发送一个 Basic x-token-auth 请求做最直接的判定（默认关闭）')
    args = parser.parse_args()

    base = (args.url or os.getenv('BITBUCKET_URL') or '').rstrip('/')
    token = args.token or os.getenv('BITBUCKET_ACCESS_TOKEN') or ''

    if not base or not token:
        print('需要 --url 和 --token，或设置 BITBUCKET_URL / BITBUCKET_ACCESS_TOKEN 环境变量', file=sys.stderr)
        return 2

    H = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}

    def get(path, headers=H, **kw):
        return requests.get(f'{base}{path}', headers=headers, verify=False, timeout=45, **kw)

    print('=' * 74)
    print(f'实例地址 : {base}')
    print(f'token    : {token[:10]}...(len={len(token)})')
    print('=' * 74)

    # 1. 可见项目数量
    print('\n[1] 可见项目数量')
    r = get('/rest/api/1.0/projects', params={'limit': 100})
    projects = []
    if r.status_code == 200:
        projects = r.json().get('values', [])
        print(f'    GET /projects -> {r.status_code}, 可见 {len(projects)} 个项目')
        for p in projects[:8]:
            print(f'        {p.get("key"):<20} type={p.get("type")}')
        if len(projects) > 8:
            print(f'        ... 还有 {len(projects) - 8} 个')
    else:
        print(f'    GET /projects -> {r.status_code} {r.text[:120]}')

    # 2. 跨项目访问
    print('\n[2] 跨项目访问探测')
    cross = []
    for p in projects:
        key = p.get('key')
        if args.project and key == args.project:
            continue
        if p.get('type') == 'PERSONAL':
            continue
        rr = get(f'/rest/api/1.0/projects/{key}')
        cross.append((key, rr.status_code))
        if len(cross) >= 3:
            break
    for key, code in cross:
        print(f'    GET /projects/{key} -> {code}')
    multi_project = any(code == 200 for _, code in cross)

    # 3. 列出全部用户
    print('\n[3] 列出全部用户')
    r = get('/rest/api/1.0/users', params={'limit': 1})
    print(f'    GET /users -> {r.status_code}')
    users_ok = r.status_code == 200

    # 4.（可选）Basic x-token-auth 探测
    x_token_ok = None
    if args.probe_basic:
        print('\n[4] Basic x-token-auth 探测（--probe-basic）')
        r = get('/rest/api/1.0/projects',
                headers={'Authorization': basic_header('x-token-auth', token),
                         'Accept': 'application/json'})
        x_token_ok = r.status_code == 200
        print(f'    GET /projects  (Basic x-token-auth) -> {r.status_code}')
        print(f'    => {"项目/仓库级 token" if x_token_ok else "个人 token"}')
    else:
        print('\n[4] Basic x-token-auth 探测: 跳过（加 --probe-basic 可开启）')

    # 结论
    print('\n' + '=' * 74)
    print('判定结论')
    print('=' * 74)
    votes = []
    votes.append(('可见项目数', '个人' if len(projects) > 1 else '项目/仓库级'))
    votes.append(('跨项目访问', '个人' if multi_project else '项目/仓库级'))
    votes.append(('列出全部用户', '个人' if users_ok else '项目/仓库级'))
    if x_token_ok is not None:
        votes.append(('x-token-auth 探测', '项目/仓库级' if x_token_ok else '个人'))

    for name, verdict in votes:
        print(f'    {name:<18} -> {verdict}')

    personal = sum(1 for _, v in votes if v == '个人')
    project = sum(1 for _, v in votes if v == '项目/仓库级')
    verdict = '个人 token' if personal > project else '项目/仓库级 token'
    print(f'\n    => {verdict}  (个人特征 {personal} 项 / 项目特征 {project} 项)')

    print('\n认证方式：两种 token 都用 Bearer，无需用户名。')
    if x_token_ok is None:
        print('    （想用最直接的 x-token-auth 探测确认，可加 --probe-basic 参数）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
