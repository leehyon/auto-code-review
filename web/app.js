// 全局变量
let currentData = [];
let currentReviewType = 'mr';

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    // 设置默认日期
    const today = new Date();
    const sevenDaysAgo = new Date(today);
    sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);

    document.getElementById('startDate').value = formatDate(sevenDaysAgo);
    document.getElementById('endDate').value = formatDate(today);

    // 监听审查类型变化
    document.querySelectorAll('input[name="reviewType"]').forEach(radio => {
        radio.addEventListener('change', function() {
            currentReviewType = this.value;
            applyTableHeaders();
            // 重新加载筛选器选项和数据
            loadFilterOptions().then(() => loadData());
        });
    });

    // 初始加载筛选器选项和数据
    loadFilterOptions().then(() => loadData());

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

// 按审查类型切换表头
function applyTableHeaders() {
    const branchHeader = document.getElementById('branchHeader');
    const urlHeader = document.getElementById('urlHeader');
    if (currentReviewType === 'push') {
        branchHeader.textContent = '分支';
        urlHeader.style.display = 'none';
    } else {
        branchHeader.textContent = '源分支';
        urlHeader.style.display = '';
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
        showFilterNotice('开始日期晚于结束日期，已自动交换为 ' + startDate + ' ~ ' + endDate);
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
async function loadData() {
    const tbody = document.getElementById('dataTableBody');
    const colCount = currentReviewType === 'push' ? 7 : 8;
    try {
        const filters = readFilters();
        const params = buildParams(filters);

        // 显示加载状态
        tbody.innerHTML = `<tr><td colspan="${colCount}" class="empty">加载中…</td></tr>`;

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
        document.getElementById('statProjects').textContent =
            new Set(currentData.map(i => i.project_name).filter(Boolean)).size;
        document.getElementById('statAuthors').textContent =
            new Set(currentData.map(i => i.author).filter(Boolean)).size;

        // 更新表格
        renderTable(currentData);

        // 更新筛选器选项
        updateFilterOptions(currentData);

        updateLastUpdateTime();
    } catch (error) {
        console.error('加载数据失败:', error);
        tbody.innerHTML = `<tr><td colspan="${colCount}" class="empty">加载失败: ${escapeHtml(error.message)}</td></tr>`;
    }
}

// 渲染表格
function renderTable(data) {
    const tbody = document.getElementById('dataTableBody');
    applyTableHeaders();

    if (data.length === 0) {
        const colCount = currentReviewType === 'push' ? 7 : 8;
        tbody.innerHTML = `<tr><td colspan="${colCount}" class="empty">当前筛选条件下没有记录</td></tr>`;
        return;
    }

    tbody.innerHTML = data.map(item => {
        const score = item.score || 0;
        const level = score >= 80 ? 'good' : (score >= 60 ? 'mid' : 'bad');

        const sourceBranch = item.source_branch || item.branch || '';

        let row = `
            <tr>
                <td class="mono nowrap">${escapeHtml(item.project_name || '')}</td>
                <td class="author nowrap">${escapeHtml(item.author || '')}</td>
                <td class="mono branch" title="${escapeHtml(sourceBranch)}">${escapeHtml(sourceBranch)}</td>
                <td class="muted nowrap">${escapeHtml(item.updated_at || '')}</td>
                <td class="commit" title="${escapeHtml(item.commit_messages || '')}">${escapeHtml(item.commit_messages || '')}</td>
                <td class="mono nowrap">${escapeHtml(item.delta || '')}</td>
                <td class="nowrap"><span class="score"><span class="dot ${level}"></span>${score.toFixed(1)}</span></td>
        `;

        if (currentReviewType === 'mr') {
            const url = item.url || '';
            row += url
                ? `<td class="nowrap"><a href="${escapeHtml(url)}" target="_blank" rel="noopener" class="pr-link">${prLabel(url)}</a></td>`
                : '<td></td>';
        }

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

        authorsSelect.innerHTML = '<option value="">全部</option>' +
            (result.authors || []).map(author =>
                `<option value="${escapeHtml(author)}" ${selectedAuthor === author ? 'selected' : ''}>${escapeHtml(author)}</option>`
            ).join('');

        projectsSelect.innerHTML = '<option value="">全部</option>' +
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

    authorsSelect.innerHTML = '<option value="">全部</option>' +
        allAuthors.map(author =>
            `<option value="${escapeHtml(author)}" ${selectedAuthor === author ? 'selected' : ''}>${escapeHtml(author)}</option>`
        ).join('');

    projectsSelect.innerHTML = '<option value="">全部</option>' +
        allProjects.map(project =>
            `<option value="${escapeHtml(project)}" ${selectedProject === project ? 'selected' : ''}>${escapeHtml(project)}</option>`
        ).join('');
}

// 加载图表
async function loadCharts() {
    try {
        const filters = readFilters();
        const params = buildParams(filters);

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

// 渲染横向条形图
// kind: count | score | lines —— 决定配色和数值格式
function renderBars(containerId, data, kind) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!data || data.length === 0) {
        container.innerHTML = '<div class="no-data">当前筛选条件下没有数据</div>';
        return;
    }

    const rows = data.map(item => {
        const value = kind === 'count' ? (item.count || 0)
            : kind === 'score' ? (item.average_score || 0)
            : (item.code_lines || 0);
        return { name: item.name || '—', value };
    });

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
            tone = 'bad';
        }
        return `
            <div class="bar-row">
                <span class="bar-label" title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</span>
                <span class="bar-track"><span class="bar-fill ${tone}" style="width: ${width.toFixed(2)}%"></span></span>
                <span class="bar-value">${shown}</span>
            </div>`;
    }).join('');
}

// 重置筛选器
function resetFilters() {
    const today = new Date();
    const sevenDaysAgo = new Date(today);
    sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);

    document.getElementById('startDate').value = formatDate(sevenDaysAgo);
    document.getElementById('endDate').value = formatDate(today);
    document.getElementById('authors').value = '';
    document.getElementById('projectNames').value = '';
    document.getElementById('typeMr').checked = true;
    currentReviewType = 'mr';
    document.getElementById('filterNotice').classList.add('hidden');
    applyTableHeaders();

    loadFilterOptions().then(() => loadData());
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
function prLabel(url) {
    const number = extractPrNumber(url);
    return number ? `#${number}` : '查看 →';
}

// HTML 转义
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
