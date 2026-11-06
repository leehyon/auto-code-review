# GitLab / GitHub / Gitea / Bitbucket 平台处理差异对比

本文档详细说明了本系统在处理 GitLab、GitHub、Gitea 和 Bitbucket Server/Data Center 四个代码托管平台时的差异和原因。

## 📋 目录

- [快速对比表](#快速对比表)
- [详细差异说明](#详细差异说明)
- [为什么会有这些差异](#为什么会有这些差异)
- [使用建议](#使用建议)

---

## 快速对比表

| 特性 | GitLab | GitHub | Gitea | Bitbucket DC |
|------|--------|--------|-------|--------------|
| **Webhook 识别方式** | `object_kind` 字段 | `X-GitHub-Event` Header | `X-Gitea-Event` Header | `X-Event-Key` Header |
| **事件类型字段** | `merge_request` / `push` | `pull_request` / `push` | `pull_request` / `push` | `pr:*` / `repo:refs_changed` |
| **API 基础路径** | `/api/v4/` | `/api/v3/` | `/api/v1/` | `/rest/api/1.0/` |
| **认证方式** | `Private-Token` Header | `Authorization: token` Header | `Authorization: token` Header | HTTP Basic（用户名 + token）或 Bearer（仅 token） |
| **仓库标识** | 数字 project ID | `owner/repo` 路径 | `owner/repo` 路径 | `projectKey` + `repositorySlug` |
| **获取变更 API** | `/merge_requests/{iid}/changes` | `/pulls/{number}/files` | `/pulls/{number}/files` | `/pull-requests/{id}.diff`（原始文本） |
| **添加评论 API** | `/merge_requests/{iid}/notes` | `/issues/{number}/comments` | `/issues/{number}/comments` 或 `/pulls/{number}/comments` | `/pull-requests/{id}/comments` |
| **Commit 评论支持** | ✅ 支持 | ✅ 支持 | ⚠️ 1.13.0+ 支持 | ✅ 支持 |
| **Issue 模式** | ❌ 不支持 | ❌ 不支持 | ✅ 支持（推荐） | ❌ 不支持 |
| **受保护分支判断** | `/protected_branches` | `/branches?protected=true` | `/branches?protected=true` | `/branch-permissions/.../restrictions` |
| **数据格式** | 自定义格式 | GitHub 标准格式 | GitHub 兼容格式 | Atlassian 自定义格式 |
| **Diff 获取方式** | 直接返回完整 diff | 直接返回完整 diff | 可能需要多次 API 调用 | 原始 unified diff 文本，需自行切分 |

---

## 详细差异说明

### 1. Webhook 识别方式

#### GitLab
```python
# 通过 payload 中的 object_kind 字段识别
object_kind = data.get("object_kind")  # "merge_request" 或 "push"
```

**原因**：GitLab 的 Webhook 设计将事件类型直接放在 JSON payload 中，这是 GitLab 的传统设计方式。

#### GitHub
```python
# 通过 HTTP Header 识别
github_event = request.headers.get('X-GitHub-Event')  # "pull_request" 或 "push"
```

**原因**：GitHub 采用 HTTP Header 传递事件类型，这是 GitHub Webhook 的标准设计，便于在接收请求时快速判断事件类型，无需解析 JSON。

#### Gitea
```python
# 通过 HTTP Header 识别（兼容 GitHub 格式）
gitea_event = request.headers.get('X-Gitea-Event')  # "pull_request" 或 "push"
```

**原因**：Gitea 为了兼容 GitHub 的生态，采用了类似的 Header 设计。注意：Gitea 会同时发送 `X-GitHub-Event` 和 `X-Gitea-Event`，所以需要优先检查 `X-Gitea-Event`。

#### Bitbucket Server/Data Center
```python
# 通过 HTTP Header 识别
bitbucket_event = request.headers.get('X-Event-Key')  # "pr:opened" 或 "repo:refs_changed"
```

**原因**：Bitbucket 沿用 Atlassian 产品的统一事件命名，采用 `<资源>:<动作>` 的层级命名法。Bitbucket Cloud 使用同一 Header 但取值不同（`pullrequest:created`、`repo:push`），系统通过取值区分两者，避免把 Cloud 事件误判为 DC 事件。

---

### 2. 事件命名差异

| 平台 | Merge Request | Push Event |
|------|---------------|------------|
| **GitLab** | `merge_request` | `push` |
| **GitHub** | `pull_request` | `push` |
| **Gitea** | `pull_request` | `push` |
| **Bitbucket DC** | `pr:opened` / `pr:from_ref_updated` | `repo:refs_changed` |

**原因**：
- **GitLab** 使用 "Merge Request" 这个术语，所以事件名为 `merge_request`
- **GitHub** 和 **Gitea** 使用 "Pull Request" 这个术语，所以事件名为 `pull_request`
- **Bitbucket DC** 使用 "Pull Request" 术语，但事件名带 `pr:` 前缀以体现 Atlassian 的层级命名（`pr:` 下还有 `pr:reviewer:approved`、`pr:comment:added` 等子事件）
- 虽然术语不同，但功能相同：都是将代码从一个分支合并到另一个分支的请求

---

### 3. API 端点差异

#### 获取代码变更

**GitLab**
```
GET /api/v4/projects/{project_id}/merge_requests/{iid}/changes
```

**GitHub**
```
GET /api/v3/repos/{owner}/{repo}/pulls/{number}/files
```

**Gitea**
```
GET /api/v1/repos/{owner}/{repo}/pulls/{number}/files
```

**Bitbucket Server/Data Center**
```
GET /rest/api/1.0/projects/{projectKey}/repos/{repositorySlug}/pull-requests/{pullRequestId}.diff
```

**差异点**：
- GitLab 使用项目 ID 和内部 ID（iid）
- GitHub/Gitea 使用仓库路径（owner/repo）和 PR 编号
- Bitbucket DC 使用项目 Key（projectKey）+ 仓库 slug（repositorySlug）+ PR 编号
- API 版本号不同（v4 vs v3 vs v1 vs rest/api/1.0）
- Bitbucket DC 的 `.diff` 接口返回的是**原始 unified diff 文本**，需要自行按文件切分；
  其余平台的接口返回结构化的文件列表（可直接拿到每个文件的 patch）

**原因**：
- 每个平台都有自己的 API 设计理念和版本管理策略
- GitLab 使用数字 ID 更利于内部系统管理
- GitHub/Gitea 使用路径更符合 RESTful 设计，更直观
- Bitbucket DC 面向企业内网自建场景，优先保证 diff 输出与 Git 命令行一致，便于用户核对

---

#### 添加评论

**GitLab**
```
POST /api/v4/projects/{project_id}/merge_requests/{iid}/notes
```

**GitHub**
```
POST /api/v3/repos/{owner}/{repo}/issues/{number}/comments
```

**Gitea**
```
POST /api/v1/repos/{owner}/{repo}/issues/{number}/comments
或
POST /api/v1/repos/{owner}/{repo}/pulls/{number}/comments
```

**Bitbucket Server/Data Center**
```
POST /rest/api/1.0/projects/{projectKey}/repos/{repositorySlug}/pull-requests/{pullRequestId}/comments
```

**差异点**：
- GitLab 有专门的 `notes` 端点
- GitHub 将 PR 视为 Issue 的一种，所以使用 `issues` 端点
- Gitea 支持两种方式，但不同版本可能支持不同的端点
- Bitbucket DC 为 PR 提供独立的 `comments` 端点，评论内容放在 `text` 字段（而非 GitHub 的 `body`）

**原因**：
- GitLab 将 MR 评论和 Issue 评论分开管理
- GitHub 统一使用 Issue 系统，PR 是 Issue 的子类型
- Gitea 为了兼容性，同时支持两种方式
- Bitbucket DC 沿用 Atlassian 的 REST 规范，评论体统一为 `{"text": "..."}`

---

### 4. 数据格式差异

#### 仓库标识

**GitLab**
```json
{
  "project": {
    "id": 12345
  }
}
```

**GitHub / Gitea**
```json
{
  "repository": {
    "full_name": "owner/repo"
  }
}
```

**Bitbucket Server/Data Center**
```json
{
  "repository": {
    "slug": "repo",
    "name": "repo",
    "project": {
      "key": "PROJ"
    }
  }
}
```

**原因**：
- GitLab 使用数字 ID 作为主要标识
- GitHub/Gitea 使用字符串路径作为主要标识
- Bitbucket DC 使用两级标识：`project.key`（项目 Key）+ `repository.slug`（仓库 slug），
  因为同一个仓库名可以存在于不同项目中，仅用 slug 不够唯一
- 数字 ID 更利于数据库查询，字符串路径更利于人类阅读

---

#### Commit 信息格式

**GitLab**
```json
{
  "commits": [
    {
      "id": "abc123",
      "message": "fix bug",
      "author": {
        "name": "John"
      }
    }
  ]
}
```

**GitHub**
```json
{
  "commits": [
    {
      "id": "abc123",
      "message": "fix bug",
      "author": {
        "name": "John"
      }
    }
  ]
}
```

**Gitea**
```json
{
  "commits": [
    {
      "id": "abc123",
      "message": "fix bug",
      "author": {
        "name": "John"
      }
      // 或者 author 可能是字符串
    }
  ]
}
```

**差异点**：
- GitLab 和 GitHub 格式基本一致
- Gitea 的 `author` 字段可能是对象或字符串（不同版本不同）

**原因**：
- Gitea 是开源项目，不同版本之间可能存在格式差异
- 为了兼容性，代码中需要处理多种格式

---

### 5. Diff 获取方式差异

#### GitLab
- **方式**：直接通过 `/changes` API 获取，返回完整的 diff
- **特点**：简单直接，一次 API 调用即可

#### GitHub
- **方式**：通过 `/files` API 获取，每个文件包含 `patch` 字段
- **特点**：简单直接，一次 API 调用即可

#### Gitea
- **方式**：可能需要多次 API 调用
  1. 优先尝试 `/files` API（可能不包含 patch）
  2. 如果失败，尝试 `/compare` API
  3. 如果还是失败，尝试 `/git/commits/{sha}.diff`
  4. 最后尝试获取文件内容手动生成 diff

**原因**：
- Gitea 不同版本的 API 行为不一致
- 某些版本可能不直接返回 patch，需要额外处理
- 为了兼容性，代码实现了多种 fallback 机制

---

### 6. Commit 评论支持

| 平台 | 支持情况 | API 端点 |
|------|---------|----------|
| **GitLab** | ✅ 完全支持 | `/api/v4/projects/{id}/repository/commits/{sha}/comments` |
| **GitHub** | ✅ 完全支持 | `/api/v3/repos/{owner}/{repo}/commits/{sha}/comments` |
| **Gitea** | ⚠️ 部分版本支持 | `/api/v1/repos/{owner}/{repo}/commits/{sha}/comments` 或 `/api/v1/repos/{owner}/{repo}/git/commits/{sha}/comments` |

#### Gitea Commit Comments 版本支持情况

**支持的版本**：
- ✅ **Gitea 1.13.0+**：通常支持 commit comments API
- ✅ **Gitea 1.14.0+**：稳定支持
- ✅ **Gitea 1.15.0+**：完全支持
- ✅ **Gitea 1.16.0+**：完全支持
- ✅ **Gitea 1.17.0+**：完全支持
- ✅ **Gitea 1.18.0+**：完全支持
- ✅ **Gitea 1.19.0+**：完全支持
- ✅ **Gitea 1.20.0+**：完全支持
- ✅ **Gitea 1.21.0+**：完全支持
- ✅ **Gitea 1.22.0+**：完全支持
- ✅ **Gitea 1.23.0+**：完全支持
- ✅ **Gitea 1.24.0+**：完全支持
- ✅ **Gitea 1.25.0+**：完全支持

**不支持的版本**：
- ❌ **Gitea 1.12.0 及更早版本**：不支持 commit comments API

**版本判断方法**：
1. 访问 Gitea 实例的 `/api/v1/version` 端点查看版本号
2. 或者查看 Gitea 管理后台的版本信息
3. 系统会自动尝试多个 API 端点，如果都返回 404，说明该版本不支持

**系统处理机制**：
- 系统会尝试以下两个 API 端点：
  1. `/api/v1/repos/{owner}/{repo}/commits/{sha}/comments`
  2. `/api/v1/repos/{owner}/{repo}/git/commits/{sha}/comments`
- 如果两个端点都返回 404，系统会：
  1. 记录警告日志
  2. 自动启用 Issue 模式（如果 `GITEA_USE_ISSUE_MODE=1`）
  3. 将审查结果保存到数据库
  4. 通过 IM 通知发送审查结果

**快速参考表**：

| Gitea 版本 | Commit Comments 支持 | 推荐操作 |
|-----------|---------------------|---------|
| < 1.13.0 | ❌ 不支持 | 使用 Issue 模式 |
| 1.13.0 - 1.14.0 | ⚠️ 部分支持 | 建议使用 Issue 模式 |
| 1.15.0+ | ✅ 完全支持 | 可以使用 Commit Comments 或 Issue 模式 |
| 1.18.0+ | ✅ 完全支持 | 推荐使用最新版本 |

**原因**：
- GitLab 和 GitHub 是成熟的商业产品，API 稳定且向后兼容
- Gitea 是开源项目，commit comments 功能是在较新版本中引入的
- 不同版本的 Gitea 可能使用不同的 API 端点路径
- 为了兼容性，系统提供了 Issue 模式作为可靠的替代方案

---

### 7. Issue 模式（Gitea 独有）

**为什么 Gitea 需要 Issue 模式？**

1. **API 兼容性问题**：某些 Gitea 版本不支持 commit comments API
2. **稳定性考虑**：Issue API 在所有版本中都稳定可用
3. **功能优势**：
   - 可审计：所有审查记录都在 Issue 中
   - 可追踪：可以查看历史审查记录
   - 可统计：可以统计审查数量和质量
   - 可管理：支持标签、状态管理等

**如何使用 Issue 模式？**

在 `.env` 文件中配置：
```bash
GITEA_USE_ISSUE_MODE=1  # 启用 Issue 模式
GITEA_REVIEW_ISSUE_LABELS=ai-review,code-review  # 可选：设置标签
```

**Issue 模式的工作流程**：
1. 系统接收到 Push 或 PR 事件
2. 创建或查找对应的 Issue（标题格式：`[AI Review] repo@branch:commit`）
3. 在 Issue 中添加审查结果评论
4. 如果 Issue 不存在，自动创建

---

## 为什么会有这些差异？

### 1. 历史原因

- **GitLab**：最早设计，有自己的设计理念
- **GitHub**：行业标准，很多平台都参考它的设计
- **Gitea**：为了兼容 GitHub 生态，采用了类似的设计，但作为开源项目，不同版本之间可能存在差异

### 2. 设计理念不同

- **GitLab**：更注重企业级功能，使用数字 ID 便于系统管理
- **GitHub**：更注重开发者体验，使用路径更直观
- **Gitea**：轻量级，追求简单，但为了兼容性需要支持多种格式

### 3. 技术实现差异

- **API 版本管理**：每个平台都有自己的版本策略
- **数据格式**：虽然都使用 JSON，但字段名和结构不同
- **功能支持**：不同平台对某些功能的支持程度不同

---

## 使用建议

### 1. 选择平台时的考虑

- **GitLab**：适合企业内网部署，功能全面
- **GitHub**：适合开源项目，生态丰富
- **Gitea**：适合轻量级部署，资源占用少

### 2. 配置建议

#### GitLab
```bash
GITLAB_ACCESS_TOKEN=your_token
GITLAB_URL=https://your-gitlab.com
```

#### GitHub
```bash
GITHUB_ACCESS_TOKEN=your_token
GITHUB_URL=https://api.github.com
```

#### Gitea
```bash
GITEA_ACCESS_TOKEN=your_token
GITEA_URL=https://your-gitea.com
GITEA_USE_ISSUE_MODE=1  # 推荐启用
```

#### Bitbucket Server/Data Center
```bash
BITBUCKET_URL=https://bitbucket.example.com
BITBUCKET_ACCESS_TOKEN=your_token
BITBUCKET_USERNAME=your_username  # HTTP Basic 认证需要
BITBUCKET_VERIFY_SSL=0  # 自签证书场景保持关闭
```

### 3. 常见问题处理

#### 问题：Gitea 无法添加评论
**解决方案**：
1. **检查 Gitea 版本**：
   - 访问 `https://your-gitea.com/api/v1/version` 查看版本号
   - 如果版本低于 1.13.0，commit comments API 不支持
   - 推荐使用 Gitea 1.15.0 或更高版本
2. **启用 Issue 模式**（推荐）：
   ```bash
   GITEA_USE_ISSUE_MODE=1  # 启用 Issue 模式
   ```
   Issue 模式在所有 Gitea 版本中都稳定可用
3. **检查 Token 权限**：
   - 确保 Token 有 `repo` 权限
   - 确保 Token 有写入权限（write access）
4. **查看日志**：
   - 检查系统日志，查看具体的错误信息
   - 系统会自动尝试多个 API 端点，如果都失败会记录详细错误

#### 问题：GitHub Webhook 无法识别
**解决方案**：
1. 检查 `X-GitHub-Event` Header 是否正确传递
2. 确认 Webhook 配置中的 Content Type 为 `application/json`

#### 问题：GitLab 获取不到变更
**解决方案**：
1. 检查 Token 权限
2. 确认项目 ID 是否正确
3. 系统会自动重试 3 次，等待 10 秒后重试

#### 问题：Bitbucket Webhook 未被识别
**解决方案**：
1. 确认请求头包含 `X-Event-Key`（Bitbucket Server/Data Center 独有）
2. 确认事件类型为 DC 风格（`pr:opened`、`pr:from_ref_updated`、`repo:refs_changed`）；
   Bitbucket Cloud 的 `pullrequest:created` / `repo:push` 当前版本不支持

#### 问题：Bitbucket 提示缺少 access token
**解决方案**：
1. 配置环境变量 `BITBUCKET_ACCESS_TOKEN` 与 `BITBUCKET_USERNAME`
2. 或在 Bitbucket Webhook 上配置 HTTP Basic 认证（系统会解析 `Authorization` Header）
3. 确认 Token 勾选了 `Repository read` / `Repository write` 权限

#### 问题：Bitbucket 返回 401 Unauthorized
**解决方案**：
1. **确认 token 类型与认证方式匹配**（见下方说明）：项目/仓库级 token 只能用 Bearer 认证，
   若配了 `BITBUCKET_USERNAME` 系统会先试 Basic，被拒后自动回退 Bearer，日志中会出现
   `Bitbucket rejected the basic authentication, retrying with bearer authentication.`
2. **确认 token 权限足够**：读 PR/diff/commit 需 `Repository read`；
   回写评论需 `Repository write`；查询受保护分支需 `Repository admin`（否则 401）
3. **确认 token 可见范围**：项目/仓库级 token 只能访问它所属的项目/仓库，跨项目无效
4. **确认 token 未过期**：DC 的 HTTP access token 支持设置过期时间，过期后即 401

#### 关于 Bitbucket 的两种 HTTP access token
Bitbucket Data Center 同时提供**个人 token** 与**项目/仓库级 token**，二者的 REST 认证方式不同：

| Token 类型 | HTTP Basic | Bearer |
|---|---|---|
| 个人 token（Manage account → HTTP access tokens） | ✅ 用户名 + token | ✅ 仅 token |
| 项目 / 仓库级 token（Project/Repository settings → HTTP access tokens） | ⚠️ 用户名必须是 `x-token-auth` | ✅ 仅 token（官方要求） |

- 项目/仓库级 token **不绑定用户**，可见范围仅该项目/仓库，且**需要 Data Center license**；
  仓库级 token 不能授予任何项目级权限。
- 本系统**默认使用 Bearer**（两种 token 都适用），仅在配置了 `BITBUCKET_USERNAME` 时才先用 Basic，
  并在 401 时自动回退 Bearer。

#### 问题：Bitbucket 提示缺少 URL
**解决方案**：
1. 配置环境变量 `BITBUCKET_URL`（推荐，保留 context path）
2. 若未配置，系统会尝试从 payload 中的仓库链接推导；确认 payload 里包含 `links.self` 字段

#### 问题：Bitbucket 自建实例 SSL 证书校验失败
**解决方案**：
1. 保持 `BITBUCKET_VERIFY_SSL=0`（默认，自签证书场景关闭校验）
2. 若需开启校验，请将实例证书签发到系统信任的 CA，或配置系统级 CA 证书

---

## 总结

虽然各平台在 API 设计、数据格式等方面存在差异，但本系统通过统一的抽象层和适配器模式，实现了对多个平台的无缝支持。用户只需要配置相应的 Token 和 URL，系统就能自动识别平台类型并调用对应的处理逻辑。

**核心设计理念**：
- **统一接口**：对外提供统一的 Webhook 端点
- **自动识别**：根据 Header 或 Payload 自动识别平台
- **适配器模式**：每个平台有独立的处理类，但接口统一
- **容错机制**：针对不同平台的特性，实现多种 fallback 方案

这样的设计既保证了代码的可维护性，又提供了良好的用户体验。

---

## 支持范围说明

| 平台 | 支持形态 | 状态 |
|------|---------|------|
| GitLab | 自建 / SaaS | ✅ 支持 |
| GitHub | SaaS | ✅ 支持 |
| Gitea | 自建 | ✅ 支持 |
| Bitbucket | **Server / Data Center（自建）** | ✅ 支持 |
| Bitbucket | Cloud（bitbucket.org） | ❌ 暂不支持（API 与 payload 结构不同） |

