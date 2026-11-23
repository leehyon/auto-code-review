"""从审查报告里提取 Summary 段。

完整报告已经作为评论发到 PR 上了，数据库没必要再存一份 ——
它是记录里最大的一列，占 90% 以上。

Summary 段是统计口径的来源（风险等级、问题数、阻断数、分数、合并建议），
外加一段概述。留着它就够审计和追溯了。
"""
import re
from typing import Optional

# Summary 段：从 "## Summary" 到下一个 "## " 标题之前
_SUMMARY_RE = re.compile(r'(^##\s+Summary\b.*?)(?=^##\s|\Z)', re.MULTILINE | re.DOTALL)

# 没有 Summary 段时的兜底上限
_MAX_FALLBACK = 1000


def extract_summary(review_result: Optional[str]) -> str:
    """取出报告的 Summary 段。

    报告结构固定（base prompt 约束）：
        ## Summary
        - Overall Risk Level: ...
        - Total Issues: ...
        - Blocking Issues: ...
        - Review Score: ...
        - Merge Recommendation: ...

        <一段概述>

        ## Issues
        ...

    找不到 Summary 段时（异常输出、老格式报告）退回到截断，
    至少别把整篇塞进数据库。
    """
    if not review_result:
        return ''
    text = review_result.strip()
    match = _SUMMARY_RE.search(text)
    if match:
        return match.group(1).strip()
    return text[:_MAX_FALLBACK]
