/**
 * 多语言支持：中文 / English
 *
 * 静态文案在 HTML 里用 data-i18n 标注，脚本动态生成的文案用 t(key) 取。
 * 「Code Review」主标题不参与翻译（品牌名）。
 */
const I18N = {
  zh: {
    'meta.subtitle': 'Merge Request 与 Push 的自动审查记录',
    'header.lastUpdated': '最后更新',
    'lang.label': '语言',

    'filter.startDate': '开始日期',
    'filter.endDate': '结束日期',
    'filter.author': '作者',
    'filter.project': '项目',
    'filter.all': '全部',
    'filter.type': '审查类型',
    'filter.query': '查询',
    'filter.reset': '重置',

    'stat.records': '审查记录',
    'stat.average': '平均分数',
    'stat.failed': '审查失败',
    'stat.failedHint': '点击只看失败的审查（有重试入口的行）',
    'stat.authors': '参与人数',

    'tab.records': '记录',
    'tab.charts': '统计',

    'col.project': '项目',
    'col.author': '作者',
    'col.sourceBranch': '源分支',
    'col.branch': '分支',
    'col.updated': '更新时间',
    'col.commits': '提交信息',
    'col.delta': '代码变更',
    'col.score': '分数',
    'col.status': '状态',
    'col.link': '链接',
    'col.commitLink': '提交',
    'col.action': '操作',

    'chart.projectCount': '项目提交次数',
    'chart.projectScore': '项目平均分数',
    'chart.authorCount': '人员提交次数',
    'chart.authorScore': '人员平均分数',
    'chart.authorLines': '人员代码变更总行数',
    'chart.countCaption': '当前筛选范围内的审查数量',
    'chart.scoreCaption': '满分 100，越高表示问题越少',
    'chart.linesCaption': '新增与删除行数合计',

    'footer.desc': '基于 AI 的自动代码审查与统计面板',

    'state.loading': '加载中…',
    'state.loadFailed': '加载失败',
    'state.noRecords': '当前筛选条件下没有记录',
    'state.noFailed': '当前没有失败的审查',
    'state.noChartData': '当前筛选条件下没有数据',

    'status.failed': '失败',
    'status.done': '完成',
    'status.failedTitle': '审查失败',

    'action.view': '查看',
    'action.retry': '重试',
    'action.retrying': '重试中…',
    'action.viewFallback': '查看 →',

    'notice.dateSwapped': '开始日期晚于结束日期，已自动交换为',
    'retry.failed': '重试失败：',
    'retry.unknownError': '未知错误',
    'retry.requestFailed': '重试请求失败：',
  },

  en: {
    'meta.subtitle': 'Automatic review records for Merge Requests and Pushes',
    'header.lastUpdated': 'Last updated',
    'lang.label': 'Language',

    'filter.startDate': 'Start date',
    'filter.endDate': 'End date',
    'filter.author': 'Author',
    'filter.project': 'Project',
    'filter.all': 'All',
    'filter.type': 'Review type',
    'filter.query': 'Query',
    'filter.reset': 'Reset',

    'stat.records': 'Reviews',
    'stat.average': 'Average score',
    'stat.failed': 'Failed',
    'stat.failedHint': 'Click to show only failed reviews (rows with a retry action)',
    'stat.authors': 'Contributors',

    'tab.records': 'Records',
    'tab.charts': 'Statistics',

    'col.project': 'Project',
    'col.author': 'Author',
    'col.sourceBranch': 'Source branch',
    'col.branch': 'Branch',
    'col.updated': 'Updated',
    'col.commits': 'Commit message',
    'col.delta': 'Changed lines',
    'col.score': 'Score',
    'col.status': 'Status',
    'col.link': 'Link',
    'col.commitLink': 'Commit',
    'col.action': 'Actions',

    'chart.projectCount': 'Reviews per project',
    'chart.projectScore': 'Average score per project',
    'chart.authorCount': 'Reviews per author',
    'chart.authorScore': 'Average score per author',
    'chart.authorLines': 'Changed lines per author',
    'chart.countCaption': 'Number of reviews in the current filter',
    'chart.scoreCaption': 'Out of 100 — higher means fewer issues',
    'chart.linesCaption': 'Added and deleted lines combined',

    'footer.desc': 'AI-powered automated code review and statistics dashboard',

    'state.loading': 'Loading…',
    'state.loadFailed': 'Failed to load',
    'state.noRecords': 'No records under the current filter',
    'state.noFailed': 'No failed reviews',
    'state.noChartData': 'No data under the current filter',

    'status.failed': 'Failed',
    'status.done': 'Done',
    'status.failedTitle': 'Review failed',

    'action.view': 'View',
    'action.retry': 'Retry',
    'action.retrying': 'Retrying…',
    'action.viewFallback': 'View →',

    'notice.dateSwapped': 'Start date was after end date, swapped to',
    'retry.failed': 'Retry failed: ',
    'retry.unknownError': 'unknown error',
    'retry.requestFailed': 'Retry request failed: ',
  },
};

let currentLang = 'zh';

// 取译文。找不到就退回中文，再找不到就返回 key 本身（方便发现漏翻）。
function t(key, fallback) {
  const table = I18N[currentLang] || {};
  if (table[key] != null) return table[key];
  if (I18N.zh[key] != null) return I18N.zh[key];
  return fallback != null ? fallback : key;
}

// 把 data-i18n 标注的静态文案刷到 DOM 上
function applyI18n(root) {
  const scope = root || document;
  scope.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.getAttribute('data-i18n'), el.textContent);
  });
  scope.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.setAttribute('title', t(el.getAttribute('data-i18n-title'), el.getAttribute('title')));
  });
  scope.querySelectorAll('[data-i18n-aria]').forEach(el => {
    el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria'), el.getAttribute('aria-label')));
  });
}

// 切换按钮的高亮状态
function syncLangButtons() {
  document.querySelectorAll('[data-lang-btn]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-lang-btn') === currentLang);
  });
}

// 切换语言。会记住选择，下次打开还是这个语言。
function setLanguage(lang) {
  currentLang = I18N[lang] ? lang : 'zh';
  document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
  try { localStorage.setItem('acr-lang', currentLang); } catch (e) { /* 隐私模式下忽略 */ }
  applyI18n();
  syncLangButtons();
  // 动态生成的表格/图表要重画
  if (typeof onLanguageChanged === 'function') onLanguageChanged();
}

// 初始化语言：优先用上次的选择，默认中文
function initLanguage() {
  let saved = null;
  try { saved = localStorage.getItem('acr-lang'); } catch (e) { /* 忽略 */ }
  currentLang = I18N[saved] ? saved : 'zh';
  document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
  applyI18n();
  syncLangButtons();
}
