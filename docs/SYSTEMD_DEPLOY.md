# 用 systemd 常驻运行

把服务变成开机自启、崩溃自动重启的后台服务。

> 只想用 Docker 的话不需要看这一篇 —— `docker compose up -d` 本身就是常驻的
> （容器里的 `supervisord.app.conf` 已经在管两个进程）。

## 进程构成

| 进程 | 启动命令 | 端口 | 作用 |
| --- | --- | --- | --- |
| API 服务 | `python api.py` | `SERVER_PORT`（默认 5001） | 接收 Webhook、触发审查、跑日报定时任务 |
| Dashboard | `python ui_server.py` | `UI_PORT`（默认 5002） | 前端看板 |

**审查任务不需要单独的 worker 进程**。默认 `QUEUE_DRIVER=async` 时，
`handle_queue()` 会直接 `multiprocessing.Process` fork 一个子进程处理审查，
子进程挂在 API 进程下面。

只有用 RQ 模式（`QUEUE_DRIVER=rq`）才需要额外起 `rq worker`，见文末。

## 1. 固定安装目录

```bash
sudo mkdir -p /opt/ai-code-review
sudo cp -r /path/to/Easy-AI-CodeReview/. /opt/ai-code-review/
```

目录路径要固定 —— 后面的 unit 文件、`WorkingDirectory`、虚拟环境都依赖它。

## 2. 建服务账号和虚拟环境

```bash
# 系统账号，不能登录
sudo useradd --system --no-create-home --shell /usr/sbin/nologin ai-review

sudo -u ai-review python3 -m venv /opt/ai-code-review/venv
sudo -u ai-review /opt/ai-code-review/venv/bin/pip install \
    -r /opt/ai-code-review/requirements.txt
```

Python 版本要求 3.10+。

## 3. 目录权限

```bash
sudo chown -R ai-review:ai-review /opt/ai-code-review
# 配置文件里全是 token，别让其他人读到
sudo chmod 600 /opt/ai-code-review/config/.env
sudo chmod 700 /opt/ai-code-review/config
```

服务运行时会写 `logs/` 和 `data/`，这两个目录必须对服务账号可写。
`config/` 里有全部的 token，注意别让它进 git（`.gitignore` 已经覆盖
`config/.env` 和 `config/.env-*`）。

## 4. systemd unit 文件

### `/etc/systemd/system/ai-code-review-api.service`

```ini
[Unit]
Description=Easy AI CodeReview - API 服务
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ai-review
Group=ai-review

# 必须指向仓库根目录：config/.env、logs/、data/ 都是相对路径
WorkingDirectory=/opt/ai-code-review

# 不加这个，输出会卡在缓冲区里，journalctl 看不到实时日志
Environment=PYTHONUNBUFFERED=1
Environment=SERVER_PORT=5001
Environment=LOG_LEVEL=INFO

ExecStart=/opt/ai-code-review/venv/bin/python api.py

Restart=always
RestartSec=10

# 停止时把 fork 出去的审查子进程一起收掉，避免留下孤儿进程
KillMode=control-group
TimeoutStopSec=30

StandardOutput=journal
StandardError=journal
SyslogIdentifier=ai-code-review-api

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/ai-code-review-ui.service`

```ini
[Unit]
Description=Easy AI CodeReview - Dashboard
After=network-online.target ai-code-review-api.service
Wants=network-online.target

[Service]
Type=simple
User=ai-review
Group=ai-review
WorkingDirectory=/opt/ai-code-review

Environment=PYTHONUNBUFFERED=1
Environment=UI_PORT=5002

ExecStart=/opt/ai-code-review/venv/bin/python ui_server.py

Restart=always
RestartSec=10
KillMode=control-group
TimeoutStopSec=30

StandardOutput=journal
StandardError=journal
SyslogIdentifier=ai-code-review-ui

[Install]
WantedBy=multi-user.target
```

装好：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-code-review-api ai-code-review-ui
```

## 5. 验证

```bash
systemctl status ai-code-review-api ai-code-review-ui
sudo ss -lntp | grep -E '5001|5002'

# 实时日志
journalctl -u ai-code-review-api -f
journalctl -u ai-code-review-api --since '10 min ago'

# 文件日志（两个都有）
tail -f /opt/ai-code-review/logs/app.log
```

`api.py` 启动时会跑一次 `check_config()`，配置有问题会直接报错并退出 ——
`journalctl` 里能看到具体缺哪个变量。

## 常用操作

```bash
sudo systemctl restart ai-code-review-api   # 改完 config/.env 后要重启才生效
sudo systemctl stop ai-code-review-api
sudo systemctl status ai-code-review-api
journalctl -u ai-code-review-api -p err --since today   # 只看错误
```

## RQ 模式（可选）

`QUEUE_DRIVER=rq` 时审查任务进 Redis 队列，需要额外跑 worker：

```ini
# /etc/systemd/system/ai-code-review-worker.service
[Unit]
Description=Easy AI CodeReview - RQ worker
After=network-online.target redis-server.service
Requires=redis-server.service

[Service]
Type=simple
User=ai-review
Group=ai-review
WorkingDirectory=/opt/ai-code-review
Environment=PYTHONUNBUFFERED=1
Environment=QUEUE_DRIVER=rq
Environment=REDIS_HOST=127.0.0.1
Environment=REDIS_PORT=6379
Environment=WORKER_QUEUE=git_test_com
ExecStart=/opt/ai-code-review/venv/bin/rq worker %E{WORKER_QUEUE} --url redis://127.0.0.1:6379
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`WORKER_QUEUE` 要和 `config/.env` 里的保持一致 —— Webhook 事件是按这个队列名分发的，
对不上会一直积压在队列里没人处理。

## 常见问题

**启动就退出，日志报找不到配置**
`WorkingDirectory` 没指到仓库根目录。`config/.env`、`logs/`、`data/` 全是相对路径。

**日志文件写不出来**
服务账号对 `logs/` 没有写权限：

```bash
sudo chown -R ai-review:ai-review /opt/ai-code-review/logs /opt/ai-code-review/data
```

**端口被占用**
```bash
sudo ss -lntp | grep 5001
```
改 unit 里的 `Environment=SERVER_PORT=xxxx` 即可。注意 Webhook 地址也要跟着改。

**Bitbucket 报 SSL 错误**
自建实例用自签证书时，在 `config/.env` 里设 `BITBUCKET_VERIFY_SSL=0`（默认就是 0）。

**审查任务卡住不动**
默认模式下审查在 API 进程里 fork 子进程执行。看看是不是 fork 出的子进程卡住了：

```bash
ps -ef | grep -E "api.py|python" | grep -v grep
```

日志里搜 `代码过长` / `截断` 可以确认是不是被 token 预算截断了。

**内存占用高**
Flask 自带服务器每个请求会 fork 一个审查子进程，同时进来的审查多时内存会涨。
需要更高并发就换成 gunicorn：

```ini
ExecStart=/opt/ai-code-review/venv/bin/gunicorn -w 2 -b 0.0.0.0:5001 api:api_app
```

但注意 `api.py` 的 `__main__` 块里还启了日报调度器（APScheduler），
用 gunicorn 起的话定时任务不会跑，得单独处理。

## 升级

```bash
sudo systemctl stop ai-code-review-api ai-code-review-ui
cd /opt/ai-code-review && git pull
sudo -u ai-review /opt/ai-code-review/venv/bin/pip install -r requirements.txt
sudo systemctl start ai-code-review-api ai-code-review-ui
```

`config/.env` 不在 git 里，不会被 `git pull` 覆盖。
