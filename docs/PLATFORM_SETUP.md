# GitLab / GitHub / Gitea 配置

Bitbucket Server / Data Center 的完整配置见主 README 的「配置 Bitbucket」一节。
本文档是另外三个平台的配置步骤 —— 平台识别方式不同，但配置项结构一致。

环境变量清单见 [`config/.env.dist`](../config/.env.dist)。
常见问题（Webhook 不触发、Token 无效等）见 [faq.md](faq.md)。

## GitLab Webhook 配置

**1. 创建 Access Token**

方法一：在 GitLab 个人设置中，创建一个 Personal Access Token。

方法二：在 GitLab 项目设置中，创建 Project Access Token

**2. 配置 Webhook**

在 GitLab 项目设置中，配置 Webhook：

- URL：http://your-server-ip:5001/review/webhook
- Trigger Events：勾选 Push Events 和 Merge Request Events (不要勾选其它 Event)
- Secret Token：上面配置的 Access Token

**备注**

1. Token 使用优先级
  - 系统优先使用 .env 文件中的 `GITLAB_ACCESS_TOKEN`。
  - 如果 .env 文件中没有配置 `GITLAB_ACCESS_TOKEN`，则使用 Webhook 传递的 Secret Token。
2. 网络访问要求
  - 请确保 GitLab 能够访问本系统。
  - 若内网环境受限，建议将系统部署在外网服务器上。

## GitHub Webhook 配置

**1. 创建Access Token**

在 GitHub 个人设置中，创建一个 Personal Access Token，需要 `repo` 权限。

**2. 配置 Webhook**

在 GitHub 项目设置中，进入 Settings → Webhooks → Add webhook：

- URL：http://your-server-ip:5001/review/webhook
- Content type：选择 `application/json`
- Events：选择 `Just the push event` 和 `Pull requests`（或选择 `Send me everything`）
- Secret：可选，用于签名验证

**3. 环境变量配置**

在 `.env` 文件中配置：

```bash
GITHUB_ACCESS_TOKEN=your_github_access_token_here
GITHUB_URL=https://api.github.com
```

## Gitea Webhook 配置

**1. 创建 Access Token**

在 Gitea 个人设置中，创建一个 Access Token，需要 `repo` 权限。

**2. 配置 Webhook**

在 Gitea 项目设置中，进入 Settings → Webhooks → Add Webhook：

- URL：http://your-server-ip:5001/review/webhook
- Content Type：选择 `application/json`
- Events：勾选 `Push` 和 `Pull Request`（或选择 `Send Everything`）
- Secret：可选，用于签名验证

**3. 环境变量配置**

在 `.env` 文件中配置：

```bash
GITEA_ACCESS_TOKEN=your_gitea_access_token_here
GITEA_URL=https://your-gitea-instance.com

# Gitea Issue 模式配置（可选）
# 是否使用 Issue 模式存储审核结果（1=启用，0=禁用，默认启用）
GITEA_USE_ISSUE_MODE=1

# Issue 标签（可选，需要先在 Gitea 中创建标签）
# GITEA_REVIEW_ISSUE_LABELS=ai-review,code-review
```

**Gitea 特殊说明**

- Gitea 支持两种审查结果存储方式：
  1. **PR/Comment 模式**：直接在 Pull Request 或 Commit 上添加评论（如果 API 支持）
  2. **Issue 模式**：创建或使用现有 Issue 来存储审查结果（推荐，更稳定可靠）
- Issue 模式的优势：
  - 可审计、可追踪、可统计
  - 支持工单化管理
  - 不依赖 Commit Comments API（某些 Gitea 版本可能不支持）

### 配置消息推送
