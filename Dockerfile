# 基础镜像可通过 --build-arg PYTHON_IMAGE=... 替换。
# 国内网络拉 docker.io 元数据经常超时，可以指向镜像加速地址，例如：
#   --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.10-slim
ARG PYTHON_IMAGE=python:3.10-slim
FROM ${PYTHON_IMAGE} AS base

# 设置工作目录
WORKDIR /app

# Debian 官方源在国内经常连不上，可以用 --build-arg DEBIAN_MIRROR=... 换国内镜像。
# 实测可用：mirrors.aliyun.com / mirrors.tuna.tsinghua.edu.cn / mirrors.ustc.edu.cn
# 同时兼容两种源文件格式：Debian 12+ 用 deb822 的 debian.sources，Debian 11 及更早用 sources.list
ARG DEBIAN_MIRROR=mirrors.aliyun.com
RUN set -eux; \
    if [ -n "${DEBIAN_MIRROR}" ]; then \
        if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
            sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g; s|security.debian.org|${DEBIAN_MIRROR}|g; s|ftp.debian.org|${DEBIAN_MIRROR}|g" \
                /etc/apt/sources.list.d/debian.sources; \
        fi; \
        if [ -f /etc/apt/sources.list ]; then \
            sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g; s|security.debian.org|${DEBIAN_MIRROR}|g; s|ftp.debian.org|${DEBIAN_MIRROR}|g" \
                /etc/apt/sources.list; \
        fi; \
    fi; \
    # apt 默认没有超时：镜像站卡住时会一直挂着，最后被构建器 SIGKILL（exit 137）；
    # 显式加超时和重试，既能快速失败，也能扛过镜像站 resync 造成的临时 Hash Sum mismatch
    apt-get update -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30; \
    apt-get install -y --no-install-recommends -o Acquire::Retries=3 -o Acquire::http::Timeout=30 supervisor; \
    rm -rf /var/lib/apt/lists/*

# 复制项目文件&创建必要的文件夹
COPY requirements.txt .

# pip 源可通过 --build-arg PIP_INDEX_URL=... 替换
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/
RUN pip install --no-cache-dir --timeout 300 --retries 3 -i ${PIP_INDEX_URL} -r requirements.txt

RUN mkdir -p logs data config web

# tiktoken 的编码表（BPE 文件）默认在首次使用时从
# openaipublic.blob.core.windows.net 下载。容器运行时如果解析不了这个域名
# （很多企业网络/代理就是这样），token 计数会直接抛异常，整个审查中断、PR 上没有评论。
# 所以在构建阶段就把编码表下载好打进镜像，运行时不再依赖外网。
ENV TIKTOKEN_CACHE_DIR=/app/.tiktoken_cache
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"

COPY src ./src
COPY api.py ./api.py
COPY ui.py ./ui.py
COPY ui_server.py ./ui_server.py
COPY web ./web
COPY config/prompt_templates.yml ./config/prompt_templates.yml

# 使用 supervisord 作为启动命令
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

FROM base AS app
COPY config/supervisord.app.conf /etc/supervisor/conf.d/supervisord.conf
# 暴露 Flask 和 Streamlit 的端口
EXPOSE 5001 5002

FROM base AS worker
COPY ./config/supervisord.worker.conf /etc/supervisor/conf.d/supervisord.conf