// 全局变量
let currentData = [];
let currentReviewType = 'mr';

// 已生效的筛选条件（最近一次 loadData 用过的那一份）。
// 统计图用这个而不是现读输入框：输入框里的日期改了但没点「查询」时，
// 记录表、统计卡、统计图必须显示同一批数据，不能三处各说各话。
let appliedFilters = null;

// 默认筛选范围：最近 30 天
const DEFAULT_RANGE_DAYS = 30;

// 返回默认的起止日期
function defaultDateRange() {
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - DEFAULT_RANGE_DAYS);
    return { start, end };
}

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    // 先定语言：静态文案要立刻换成对应语言，再加载数据
    initLanguage();

    // 设置默认日期
    const range = defaultDateRange();
    document.getElementById('startDate').value = formatDate(range.start);
    document.getElementById('endDate').value = formatDate(range.end);

    // 监听审查类型变化
    document.querySelectorAll('input[name="reviewType"]').forEach(radio => {
        radio.addEventListener('change', function() {
            currentReviewType = this.value;
            applyTableHeaders();
            // 重新加载筛选器选项和数据
            loadFilterOptions().then(() => {
                // 切类型后作者/项目下拉框的选项变了（原来的选中项可能已经不存在），
                // 重新读一次输入框，保证表头、统计卡、图表用的是同一份筛选条件
                const filters = readFilters();
                appliedFilters = filters;
                return refreshVisibleViews(filters);
            });
        });
    });

    // 初始加载筛选器选项和数据
    loadFilterOptions().then(() => applyFilters());

    // 定时更新（每30秒）
    setInterval(updateLastUpdateTime, 30000);
    updateLastUpdateTime();
});

// 格式化日期
function formatDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

// 显示筛选提示
function showFilterNotice(message) {
    const notice = document.getElementById('filterNotice');
    if (!notice) return;
    notice.textContent = message;
    notice.classList.remove('hidden');
}

// 格式化时间戳
function formatDateTime(date) {
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
         + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// 更新最后更新时间（只写时间，「最后更新」这个标签在 HTML 里）
function updateLastUpdateTime() {
    document.getElementById('lastUpdate').textContent = formatDateTime(new Date());
}

// 切换标签页
function switchTab(tab) {
    document.querySelectorAll('.tab-button').forEach(btn => {
        btn.classList.toggle('active', btn.id === (tab === 'data' ? 'tabData' : 'tabCharts'));
    });
    document.getElementById('tabContentData').classList.toggle('hidden', tab !== 'data');
    document.getElementById('tabContentCharts').classList.toggle('hidden', tab !== 'charts');
    if (tab === 'charts') {
        loadCharts();
    }
}

// 统计图当前是不是可见的
function isChartsVisible() {
    const el = document.getElementById('tabContentCharts');
    return !!el && !el.classList.contains('hidden');
}

// 刷新当前可见的视图。
//
// 统计卡（记录数 / 平均分 / 失败数 / 参与人数）在两个 tab 上方，是共用的，
// 所以筛选条件或审查类型一变，记录数据（loadData）必须重载。
//
// 统计图是另一份请求，只在「统计」tab 上可见。之前审查类型切换只调 loadData，
// 于是停在统计页时点 Push / Merge Request：上面的统计卡变成了新类型的数据，
// 下面的图表还留在旧类型的数据上 —— 同一个页面里两套数字对不上。
//
// filters 省略时沿用已生效的条件（appliedFilters），
// 保证记录表、统计卡、统计图三者永远来自同一份筛选条件。
async function refreshVisibleViews(filters) {
    await loadData(filters);
    if (isChartsVisible()) {
        await loadCharts(filters);
    }
}

// 应用输入框里的筛选条件（查询按钮）。
// 输入框改完不点查询，页面上的一切都还是上一次的筛选结果。
async function applyFilters() {
    const filters = readFilters();
    appliedFilters = filters;
    await refreshVisibleViews(filters);
}

// 按审查类型切换表头
function applyTableHeaders() {
    const branchHeader = document.getElementById('branchHeader');
    const urlHeader = document.getElementById('urlHeader');
    if (currentReviewType === 'push') {
        branchHeader.textContent = t('col.branch');
        urlHeader.textContent = t('col.commitLink');
    } else {
        branchHeader.textContent = t('col.sourceBranch');
        urlHeader.textContent = t('col.link');
    }
}

// 读取当前筛选条件
// 开始日期晚于结束日期时，查询必然为空（updated_at >= gte AND updated_at <= lte）。
// 这种情况下页面只显示「没有数据」，看起来像记录没写进去。
// 这里直接交换两个日期，并提示用户。
function readFilters() {
    let startDate = document.getElementById('startDate').value;
    let endDate = document.getElementById('endDate').value;

    if (startDate && endDate && startDate > endDate) {
        const swapped = startDate;
        startDate = endDate;
        endDate = swapped;
        document.getElementById('startDate').value = startDate;
        document.getElementById('endDate').value = endDate;
        console.warn('开始日期晚于结束日期，已自动交换:', startDate, '~', endDate);
        showFilterNotice(t('notice.dateSwapped') + ' ' + startDate + ' ~ ' + endDate);
    }

    const authors = Array.from(document.getElementById('authors').selectedOptions).map(opt => opt.value);
    const projectNames = Array.from(document.getElementById('projectNames').selectedOptions).map(opt => opt.value);
    return { startDate, endDate, authors, projectNames };
}

// 构造查询参数
function buildParams(filters) {
    const params = new URLSearchParams();
    params.append('type', currentReviewType);
    if (filters.startDate) {
        params.append('updated_at_gte', Math.floor(new Date(filters.startDate + ' 00:00:00').getTime() / 1000));
    }
    if (filters.endDate) {
        params.append('updated_at_lte', Math.floor(new Date(filters.endDate + ' 23:59:59').getTime() / 1000));
    }
    filters.authors.forEach(author => params.append('authors', author));
    filters.projectNames.forEach(project => params.append('project_names', project));
    return params;
}

// 加载数据
// filters 省略时用已生效的那一份（appliedFilters），
// 避免把输入框里改了但没点「查询」的值偷带进请求。
async function loadData(filters) {
    const tbody = document.getElementById('dataTableBody');
    const colCount = 10;
    try {
        const active = filters || appliedFilters || readFilters();
        appliedFilters = active;
        const params = buildParams(active);

        // 显示加载状态
        tbody.innerHTML = `<tr><td colspan="${colCount}" class="empty">${t('state.loading')}</td></tr>`;

        const response = await fetch(`/api/review/logs?${params.toString()}`);
        const result = await response.json();

        if (result.error) {
            throw new Error(result.error);
        }

        currentData = result.data || [];

        // 更新统计信息
        document.getElementById('totalRecords').textContent = result.total || 0;
        const avgScore = result.average_score || 0;
        document.getElementById('averageScore').textContent = avgScore.toFixed(2);
        // 强调边表示「需要看一眼」，不是装饰：平均分偏低时才出现
        document.getElementById('averageScoreCard').classList.toggle('accent', avgScore > 0 && avgScore < 70);
        document.getElementById('statFailed').textContent = result.failed || 0;
        document.getElementById('statAuthors').textContent =
            new Set(currentData.map(i => i.author).filter(Boolean)).size;

        // 更新表格
        renderTable(currentData);

        // 更新筛选器选项
        updateFilterOptions(currentData);

        updateLastUpdateTime();
    } catch (error) {
        console.error('加载数据失败:', error);
        tbody.innerHTML = `<tr><td colspan="${colCount}" class="empty">${t('state.loadFailed')}: ${escapeHtml(error.message)}</td></tr>`;
    }
}

// 渲染表格
function renderTable(data) {
    const tbody = document.getElementById('dataTableBody');
    applyTableHeaders();

    // 「只看失败」开关打开时，只渲染失败行 —— 重试入口就在这些行上
    const rows = failedOnly ? data.filter(isFailedRow) : data;

    if (rows.length === 0) {
        const colCount = 10;
        const emptyText = failedOnly ? t('state.noFailed') : t('state.noRecords');
        tbody.innerHTML = `<tr><td colspan="${colCount}" class="empty">${emptyText}</td></tr>`;
        return;
    }

    tbody.innerHTML = rows.map(item => {
        const score = item.score || 0;
        const level = score >= 80 ? 'good' : (score >= 60 ? 'mid' : 'bad');

        const sourceBranch = item.source_branch || item.branch || '';

        // 失败记录：没有分数，状态列只留标记（失败原因放在 title 里），
        // 重试入口放在行末的「操作」列
        const failed = item.status && item.status !== 'success';
        const statusCell = failed
            ? `<span class="status failed" title="${escapeHtml(item.error_message || t('status.failedTitle'))}">`
              + `<span class="dot"></span>${t('status.failed')}</span>`
            : `<span class="status"><span class="dot good"></span>${t('status.done')}</span>`;

        let row = `
            <tr>
                <td class="mono nowrap">${escapeHtml(item.project_name || '')}</td>
                <td class="author nowrap">${escapeHtml(item.author || '')}</td>
                <td class="mono branch" title="${escapeHtml(sourceBranch)}">${escapeHtml(sourceBranch)}</td>
                <td class="muted nowrap">${escapeHtml(item.updated_at || '')}</td>
                <td class="commit" title="${escapeHtml(item.commit_messages || '')}">${escapeHtml(item.commit_messages || '')}</td>
                <td class="mono nowrap">${escapeHtml(item.delta || '')}</td>
                <td class="nowrap">${failed ? '' : `<span class="score"><span class="dot ${level}"></span>${score.toFixed(1)}</span>`}</td>
                <td class="nowrap">${statusCell}</td>
        `;

        const url = item.url || '';
        row += url
            ? `<td class="nowrap"><a href="${escapeHtml(url)}" target="_blank" rel="noopener" class="pr-link">${linkLabel(url, currentReviewType)}</a></td>`
            : '<td></td>';

        // 行末的操作列：成功行给一个「查看」跳到对应的 PR，
        // 失败行的动作是「重试」，不再重复给查看入口
        const actions = [];
        if (!failed && url) {
            actions.push(`<a class="row-action" href="${escapeHtml(url)}" target="_blank" `
                + `rel="noopener" title="${escapeHtml(url)}">${t('action.view')}</a>`);
        }
        if (failed) {
            actions.push(retryButton(item.id));
        }
        row += `<td class="action-cell">${actions.join('')}</td>`;

        row += '</tr>';
        return row;
    }).join('');
}

// 加载所有筛选器选项
async function loadFilterOptions() {
    try {
        const response = await fetch(`/api/review/filter-options?type=${currentReviewType}`);
        const result = await response.json();

        if (result.error) {
            console.error('加载筛选器选项失败:', result.error);
            return;
        }

        const authorsSelect = document.getElementById('authors');
        const projectsSelect = document.getElementById('projectNames');
        const selectedAuthor = authorsSelect.value;
        const selectedProject = projectsSelect.value;

        authorsSelect.innerHTML = `<option value="">${t('filter.all')}</option>` +
            (result.authors || []).map(author =>
                `<option value="${escapeHtml(author)}" ${selectedAuthor === author ? 'selected' : ''}>${escapeHtml(author)}</option>`
            ).join('');

        projectsSelect.innerHTML = `<option value="">${t('filter.all')}</option>` +
            (result.project_names || []).map(project =>
                `<option value="${escapeHtml(project)}" ${selectedProject === project ? 'selected' : ''}>${escapeHtml(project)}</option>`
            ).join('');
    } catch (error) {
        console.error('加载筛选器选项失败:', error);
    }
}

// 更新筛选器选项（保留已存在的选项，添加新的选项）
function updateFilterOptions(data) {
    const newAuthors = [...new Set(data.map(item => item.author).filter(Boolean))];
    const newProjects = [...new Set(data.map(item => item.project_name).filter(Boolean))];

    const authorsSelect = document.getElementById('authors');
    const projectsSelect = document.getElementById('projectNames');
    const existingAuthors = Array.from(authorsSelect.options).map(opt => opt.value).filter(v => v !== '');
    const existingProjects = Array.from(projectsSelect.options).map(opt => opt.value).filter(v => v !== '');
    const selectedAuthor = authorsSelect.value;
    const selectedProject = projectsSelect.value;

    const allAuthors = [...new Set([...existingAuthors, ...newAuthors])].sort();
    const allProjects = [...new Set([...existingProjects, ...newProjects])].sort();

    authorsSelect.innerHTML = `<option value="">${t('filter.all')}</option>` +
        allAuthors.map(author =>
            `<option value="${escapeHtml(author)}" ${selectedAuthor === author ? 'selected' : ''}>${escapeHtml(author)}</option>`
        ).join('');

    projectsSelect.innerHTML = `<option value="">${t('filter.all')}</option>` +
        allProjects.map(project =>
            `<option value="${escapeHtml(project)}" ${selectedProject === project ? 'selected' : ''}>${escapeHtml(project)}</option>`
        ).join('');
}

// 加载图表
// filters 省略时沿用已生效的条件（appliedFilters），
// 保证统计图和记录表是同一批数据。
async function loadCharts(filters) {
    try {
        const active = filters || appliedFilters || readFilters();
        const params = buildParams(active);

        const response = await fetch(`/api/review/stats?${params.toString()}`);
        const result = await response.json();

        if (result.error) {
            throw new Error(result.error);
        }

        renderBars('projectCountChart', result.project_counts || [], 'count');
        renderBars('projectScoreChart', result.project_scores || [], 'score');
        renderBars('authorCountChart', result.author_counts || [], 'count');
        renderBars('authorScoreChart', result.author_scores || [], 'score');
        renderBars('authorCodeLineChart', result.author_code_lines || [], 'lines');
    } catch (error) {
        console.error('加载图表失败:', error);
    }
}

// 人员代码变更总行数：每个人一个固定颜色。
// 按名字哈希取色 —— 同一个人在任何筛选条件下都是同一个颜色；
// 撞色了往后挪一格，避免两个人同色。
const PERSON_COLOR_COUNT = 8;

// 手动指定颜色的人：优先于哈希分配，槽位会先占住，
// 后面的人自动避开，不会撞色。
const PERSON_COLOR_OVERRIDES = {
    'liyahong': 'c1',   // 青蓝
};

function assignPersonColors(names) {
    const used = new Set();
    const map = {};

    // 先占住手动指定的颜色
    for (const name of names) {
        const override = PERSON_COLOR_OVERRIDES[String(name).toLowerCase()];
        if (!override) continue;
        const slot = Number(override.slice(1));
        if (!used.has(slot)) {
            used.add(slot);
            map[name] = override;
        }
    }

    // 其余的人按名字哈希取色，避开已占用的槽位
    for (const name of names) {
        if (map[name]) continue;
        let hash = 0;
        for (let i = 0; i < name.length; i++) {
            hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
        }
        const start = hash % PERSON_COLOR_COUNT;
        let slot = start;
        for (let k = 0; k < PERSON_COLOR_COUNT; k++) {
            const candidate = (start + k) % PERSON_COLOR_COUNT;
            if (!used.has(candidate)) { slot = candidate; break; }
        }
        used.add(slot);
        map[name] = 'c' + slot;
    }
    return map;
}

// 渲染横向条形图
// kind: count | score | lines —— 决定配色和数值格式
function renderBars(containerId, data, kind) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!data || data.length === 0) {
        container.innerHTML = `<div class="no-data">${t('state.noChartData')}</div>`;
        return;
    }

    const rows = data.map(item => {
        const value = kind === 'count' ? (item.count || 0)
            : kind === 'score' ? (item.average_score || 0)
            : (item.code_lines || 0);
        return { name: item.name || '—', value };
    });

    // 代码变更总行数按人分配固定颜色；其它图表保持各自的语义配色
    const personColors = kind === 'lines'
        ? assignPersonColors(rows.map(r => r.name))
        : null;

    const max = Math.max(...rows.map(r => r.value), 1);

    container.innerHTML = rows.map(row => {
        const width = Math.max((row.value / max) * 100, 1.5);
        const shown = kind === 'score' ? row.value.toFixed(1) : String(row.value);
        // 分数条按高低着色，跟表格里的分数圆点同一套语义：
        // 高分=olive、中等=clay、低分=rust。
        let tone = '';
        if (kind === 'score') {
            tone = row.value >= 80 ? 'good' : (row.value >= 60 ? 'mid' : 'bad');
        } else if (kind === 'lines') {
            tone = personColors[row.name] || 'c0';
        }
        return `
            <div class="bar-row">
                <span class="bar-label" title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</span>
                <span class="bar-track"><span class="bar-fill ${tone}" style="width: ${width.toFixed(2)}%"></span></span>
                <span class="bar-value">${shown}</span>
            </div>`;
    }).join('');
}

// 语言切换后的回调：动态生成的表格和图表要重画
function onLanguageChanged() {
    applyTableHeaders();
    refreshVisibleViews();
}

// 重置筛选器
function resetFilters() {
    const range = defaultDateRange();

    document.getElementById('startDate').value = formatDate(range.start);
    document.getElementById('endDate').value = formatDate(range.end);
    document.getElementById('authors').value = '';
    document.getElementById('projectNames').value = '';
    document.getElementById('typeMr').checked = true;
    currentReviewType = 'mr';
    document.getElementById('filterNotice').classList.add('hidden');
    applyTableHeaders();

    loadFilterOptions().then(() => applyFilters());
}

// 从 PR URL 里提取编号，各平台路径都兼容：
//   Bitbucket  .../pull-requests/29/overview
//   GitHub     .../pull/29
//   GitLab     .../merge_requests/29
//   Gitea      .../pulls/29
function extractPrNumber(url) {
    if (!url) return null;
    const match = url.match(/\/(\d+)(?:\/[^/?#]*)?(?:[?#]|$)/);
    return match ? match[1] : null;
}

// 链接列的显示文本
//   Merge Request -> #29
//   Push          -> commit 短 hash，例如 a1b2c3d
function linkLabel(url, reviewType) {
    if (reviewType === 'push') {
        const parts = url.split('/');
        const sha = (parts[parts.length - 1] || '').split('?')[0];
        return sha ? sha.slice(0, 7) : t('action.viewFallback');
    }
    const number = extractPrNumber(url);
    return number ? `#${number}` : t('action.viewFallback');
}

// 重试按钮
function retryButton(recordId) {
    if (!recordId) return '';
    return `<button class="retry-btn" onclick="doRetry(${recordId}, this)">${t('action.retry')}</button>`;
}

// 只看失败的审查 —— 重试入口就在这些行上
let failedOnly = false;

// 判断一行是不是失败记录
function isFailedRow(item) {
    return !!(item.status && item.status !== 'success');
}

// 点击「审查失败」统计卡切换筛选
function toggleFailedFilter() {
    failedOnly = !failedOnly;
    document.getElementById('failedCard').classList.toggle('active', failedOnly);
    renderTable(currentData);
}

// 重跑一次失败的审查
async function doRetry(recordId, button) {
    if (button) {
        button.disabled = true;
        button.textContent = t('action.retrying');
    }
    try {
        const response = await fetch('/api/review/retry', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: currentReviewType, id: recordId })
        });
        const result = await response.json();
        if (!result.success) {
            alert(t('retry.failed') + (result.message || t('retry.unknownError')));
        }
    } catch (error) {
        console.error('重试请求失败:', error);
        alert(t('retry.requestFailed') + error.message);
    } finally {
        // 无论成败都刷新列表：重试成功会变成成功记录，还是失败会换一条新的失败记录
        await loadData();
    }
}

// HTML 转义
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}